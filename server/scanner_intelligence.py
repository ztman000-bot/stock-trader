"""Scanner Intelligence v0.17.6.

Research/shadow only. This module never sends orders and never changes Control v0.8.0.

Adds point-in-time scanner features that are difficult to reconstruct later:
- Time-of-day RVOL: 5m / 15m / 30m / cumulative-to-now versus prior sessions.
- Gap %, prior-day ATR14 %, same-market relative strength.
- Turnover, spread and top-level order-book imbalance from already collected quotes.
- Optional OpenDART catalyst flag when OPENDART_API_KEY is configured locally.
- Four shadow scanner challengers: SIP-RVOL, RVOL+Gap, RVOL+Catalyst, Relative Strength.

No new NH REST polling is performed here. Live calculations use the existing SQLite quote/5m data,
so scanner research cannot starve the live quote collector. OpenDART is optional and isolated from NH.
"""
from __future__ import annotations

import io
import json
import os
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timedelta
from statistics import mean, median

from collector import (
    DB_PATH, KST, PROTECTED_CODES, collector, instrument_meta, is_safe_code,
    latest_quotes, regular_session, universe_verified,
)

LOOKBACK_DAYS = max(5, min(int(os.getenv('SCANNER_RVOL_LOOKBACK_DAYS', '14')), 60))
SNAPSHOT_SEC = max(60, int(os.getenv('SCANNER_INTEL_SNAPSHOT_SEC', '300')))
TOP_N = max(5, min(int(os.getenv('SCANNER_INTEL_TOP_N', '20')), 50))
MIN_ATR_PCT = max(0.0, float(os.getenv('SCANNER_INTEL_MIN_ATR_PCT', '0.50')))
MIN_RVOL = max(0.1, float(os.getenv('SCANNER_INTEL_MIN_RVOL', '1.00')))
MIN_TURNOVER_EOK = max(0.0, float(os.getenv('SCANNER_INTEL_MIN_TURNOVER_EOK', '10')))
DART_KEY = (os.getenv('OPENDART_API_KEY') or '').strip()
DART_REFRESH_SEC = max(300, int(os.getenv('DART_REFRESH_SEC', '900')))

_STOP = threading.Event()
_THREAD = None
_LOCK = threading.RLock()
_STATE = {
    'running': False, 'lastSnapshotAt': None, 'lastError': None,
    'snapshots': 0, 'rows': 0, 'intervalSec': SNAPSHOT_SEC,
    'rvolLookbackDays': LOOKBACK_DAYS, 'topN': TOP_N,
    'dartEnabled': bool(DART_KEY), 'lastDartRefreshAt': None,
    'researchOnly': True, 'liveMutation': False, 'realOrder': False,
    'nhExtraRestCalls': 0,
}


def _conn():
    c = sqlite3.connect(DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    return c


def init_db():
    with _conn() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS scanner_intel_snapshots(
          snapshot_at TEXT NOT NULL,
          trade_date TEXT NOT NULL,
          code TEXT NOT NULL,
          market TEXT,
          name TEXT,
          price REAL,
          rank_sip_rvol INTEGER,
          rank_rvol_gap INTEGER,
          rank_rvol_catalyst INTEGER,
          rank_relative_strength INTEGER,
          score_sip_rvol REAL,
          score_rvol_gap REAL,
          score_rvol_catalyst REAL,
          score_relative_strength REAL,
          turnover_eok REAL,
          gap_pct REAL,
          atr14_pct REAL,
          rvol5 REAL,
          rvol15 REAL,
          rvol30 REAL,
          rvol_time REAL,
          intraday_ret_pct REAL,
          market_median_ret_pct REAL,
          relative_strength_pct REAL,
          spread_pct REAL,
          book_imbalance REAL,
          catalyst_present INTEGER,
          catalyst_count INTEGER,
          catalyst_state TEXT,
          source TEXT NOT NULL DEFAULT 'SCANNER_INTEL_SHADOW',
          PRIMARY KEY(snapshot_at,code)
        );
        CREATE INDEX IF NOT EXISTS idx_scanner_intel_day_code
          ON scanner_intel_snapshots(trade_date,code,snapshot_at);
        CREATE TABLE IF NOT EXISTS dart_corp_map(
          corp_code TEXT PRIMARY KEY,
          stock_code TEXT,
          corp_name TEXT,
          modify_date TEXT,
          fetched_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_dart_stock_code ON dart_corp_map(stock_code);
        CREATE TABLE IF NOT EXISTS dart_events(
          rcept_no TEXT PRIMARY KEY,
          rcept_dt TEXT NOT NULL,
          corp_code TEXT,
          stock_code TEXT,
          corp_name TEXT,
          report_nm TEXT,
          fetched_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_dart_events_day_stock ON dart_events(rcept_dt,stock_code);
        ''')


def _f(v, digits=3):
    try:
        return round(float(v), digits)
    except Exception:
        return None


def _floor5(now):
    return now.replace(minute=(now.minute // 5) * 5, second=0, microsecond=0)


def _load_bars(code, days=45):
    cutoff = (datetime.now(KST) - timedelta(days=max(30, days * 2))).isoformat()
    with _conn() as c:
        rows = c.execute('''SELECT bucket,open,high,low,close,volume FROM bars_5m
                            WHERE code=? AND bucket>=? ORDER BY bucket''', (str(code), cutoff)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            dt = datetime.fromisoformat(str(d['bucket'])).astimezone(KST)
            hm = dt.hour * 60 + dt.minute
            if 540 <= hm <= 930:
                d['_dt'] = dt
                out.append(d)
        except Exception:
            continue
    return out


def _group_days(rows):
    by = defaultdict(list)
    for r in rows:
        by[r['_dt'].date()].append(r)
    for d in by:
        by[d].sort(key=lambda x: x['_dt'])
    return by


def _daily_atr_pct(by, today):
    dates = sorted(d for d in by if d < today)[-15:]
    if len(dates) < 5:
        return None
    trs = []
    prev_close = None
    for d in dates:
        arr = by[d]
        if not arr:
            continue
        hi = max(float(x['high']) for x in arr)
        lo = min(float(x['low']) for x in arr)
        close = float(arr[-1]['close'])
        if prev_close is None:
            tr = hi - lo
        else:
            tr = max(hi - lo, abs(hi - prev_close), abs(lo - prev_close))
        if close > 0:
            trs.append((tr, close))
        prev_close = close
    if not trs:
        return None
    recent = trs[-14:]
    return mean(tr / close * 100 for tr, close in recent if close > 0)


def _historical_volume_average(by, today, count):
    if count <= 0:
        return None
    dates = sorted(d for d in by if d < today)[-LOOKBACK_DAYS:]
    vals = []
    for d in dates:
        arr = by[d]
        if len(arr) >= count:
            vals.append(sum(max(0, int(float(x['volume'] or 0))) for x in arr[:count]))
    return mean(vals) if vals else None


def _rvol(cur, avg):
    return (cur / avg) if avg and avg > 0 else None


def _feature_for_code(code, quote, now):
    rows = _load_bars(code, LOOKBACK_DAYS + 20)
    by = _group_days(rows)
    today = now.date()
    cur = list(by.get(today, []))
    # Do not use the currently forming 5-minute bucket.
    if regular_session(now):
        boundary = _floor5(now)
        cur = [x for x in cur if x['_dt'] < boundary]
    if not cur:
        return None
    prior_dates = sorted(d for d in by if d < today)
    prev_close = float(by[prior_dates[-1]][-1]['close']) if prior_dates else None
    op = float(cur[0]['open'])
    price = float(quote.get('price') or cur[-1]['close'] or 0)
    if op <= 0 or price <= 0:
        return None
    n = len(cur)
    volumes = [max(0, int(float(x['volume'] or 0))) for x in cur]
    cum = sum(volumes)
    v5 = sum(volumes[:1]) if n >= 1 else None
    v15 = sum(volumes[:3]) if n >= 3 else None
    v30 = sum(volumes[:6]) if n >= 6 else None
    rvol5 = _rvol(v5, _historical_volume_average(by, today, 1)) if v5 is not None else None
    rvol15 = _rvol(v15, _historical_volume_average(by, today, 3)) if v15 is not None else None
    rvol30 = _rvol(v30, _historical_volume_average(by, today, 6)) if v30 is not None else None
    rvol_time = _rvol(cum, _historical_volume_average(by, today, n))
    gap = (op / prev_close - 1) * 100 if prev_close and prev_close > 0 else None
    atr = _daily_atr_pct(by, today)
    turnover = price * max(0, int(quote.get('cumulative_volume') or 0)) / 100_000_000
    ask = float(quote.get('ask1') or 0)
    bid = float(quote.get('bid1') or 0)
    spread = ((ask - bid) / price * 100) if ask > 0 and bid > 0 and ask >= bid else None
    ask_qty = max(0.0, float(quote.get('total_ask_qty') or 0))
    bid_qty = max(0.0, float(quote.get('total_bid_qty') or 0))
    imbalance = ((bid_qty - ask_qty) / (bid_qty + ask_qty)) if (bid_qty + ask_qty) > 0 else None
    meta = instrument_meta(code)
    market = meta.get('market') or 'UNKNOWN'
    return {
        'code': str(code), 'name': meta.get('name') or quote.get('name') or str(code),
        'market': market, 'price': price, 'turnoverEok': turnover,
        'gapPct': gap, 'atr14Pct': atr,
        'rvol5': rvol5, 'rvol15': rvol15, 'rvol30': rvol30, 'rvolTime': rvol_time,
        'intradayRetPct': (price / op - 1) * 100,
        'spreadPct': spread, 'bookImbalance': imbalance,
        'barsCompleted': n,
    }


def _today_catalysts(codes):
    day = datetime.now(KST).strftime('%Y%m%d')
    wanted = set(str(x) for x in codes)
    out = {c: {'present': None if not DART_KEY else False, 'count': 0,
               'state': 'not_configured' if not DART_KEY else 'no_event'} for c in wanted}
    if not DART_KEY:
        return out
    with _conn() as c:
        rows = c.execute('''SELECT stock_code,COUNT(*) n FROM dart_events
                            WHERE rcept_dt=? AND stock_code IS NOT NULL AND stock_code!=''
                            GROUP BY stock_code''', (day,)).fetchall()
    for r in rows:
        code = str(r['stock_code'] or '')
        if code in out:
            n = int(r['n'] or 0)
            out[code] = {'present': n > 0, 'count': n, 'state': 'event' if n else 'no_event'}
    return out


def _score_rows(rows):
    by_market = defaultdict(list)
    for r in rows:
        if r.get('intradayRetPct') is not None:
            by_market[r.get('market') or 'UNKNOWN'].append(float(r['intradayRetPct']))
    medians = {m: median(v) for m, v in by_market.items() if v}
    cats = _today_catalysts([r['code'] for r in rows])
    for r in rows:
        med = medians.get(r.get('market') or 'UNKNOWN')
        rs = (float(r['intradayRetPct']) - med) if med is not None else None
        r['marketMedianRetPct'] = med
        r['relativeStrengthPct'] = rs
        cat = cats.get(r['code']) or {'present': None, 'count': 0, 'state': 'unknown'}
        r['catalystPresent'] = cat['present']; r['catalystCount'] = cat['count']; r['catalystState'] = cat['state']
        rv = r.get('rvol5') or r.get('rvol15') or r.get('rvol30') or r.get('rvolTime') or 0
        atr = r.get('atr14Pct') or 0
        turnover = r.get('turnoverEok') or 0
        spread = r.get('spreadPct')
        liquid_bonus = 10 if turnover >= MIN_TURNOVER_EOK else 0
        atr_bonus = 10 if atr >= MIN_ATR_PCT else 0
        spread_bonus = 5 if spread is None or spread <= .25 else 0
        base = min(60, max(0.0, rv) * 25) + liquid_bonus + atr_bonus + spread_bonus
        r['scoreSipRvol'] = min(100.0, base + (10 if rv >= 1.5 else 0) + (5 if rv >= 2.5 else 0))
        gap = r.get('gapPct')
        gap_bonus = 0
        if gap is not None:
            if .5 <= gap <= 5: gap_bonus = 20
            elif 0 < gap < .5 or 5 < gap <= 8: gap_bonus = 10
            elif gap < -1: gap_bonus = -10
        r['scoreRvolGap'] = max(0.0, min(100.0, r['scoreSipRvol'] + gap_bonus))
        cat_bonus = 20 if r.get('catalystPresent') is True else 0
        r['scoreRvolCatalyst'] = max(0.0, min(100.0, r['scoreSipRvol'] + cat_bonus)) if DART_KEY else None
        rs_bonus = 0 if rs is None else max(-15, min(30, rs * 10))
        r['scoreRelativeStrength'] = max(0.0, min(100.0, r['scoreSipRvol'] + rs_bonus))
    return rows


def _rank(rows, score_key):
    valid = [r for r in rows if r.get(score_key) is not None]
    valid.sort(key=lambda x: (float(x.get(score_key) or 0), float(x.get('turnoverEok') or 0)), reverse=True)
    ranks = {r['code']: i + 1 for i, r in enumerate(valid)}
    return valid, ranks


def scan(limit=TOP_N):
    init_db(); now = datetime.now(KST)
    codes = list(dict.fromkeys(getattr(collector, 'watchlist', []) or []))
    quotes = {str(q.get('code')): q for q in latest_quotes(codes)}
    rows = []
    for code in codes:
        if not code or code in PROTECTED_CODES:
            continue
        if universe_verified() and not is_safe_code(code):
            continue
        q = quotes.get(str(code))
        if not q:
            continue
        feat = _feature_for_code(code, q, now)
        if feat:
            rows.append(feat)
    _score_rows(rows)
    sip, rank_sip = _rank(rows, 'scoreSipRvol')
    gap, rank_gap = _rank(rows, 'scoreRvolGap')
    cat, rank_cat = _rank(rows, 'scoreRvolCatalyst')
    rel, rank_rel = _rank(rows, 'scoreRelativeStrength')
    for r in rows:
        r['rankSipRvol'] = rank_sip.get(r['code'])
        r['rankRvolGap'] = rank_gap.get(r['code'])
        r['rankRvolCatalyst'] = rank_cat.get(r['code'])
        r['rankRelativeStrength'] = rank_rel.get(r['code'])
    n = max(1, min(int(limit), 100))
    return {
        'ok': True, 'generatedAt': now.isoformat(timespec='seconds'), 'researchOnly': True,
        'controlStrategy': 'v0.8.0 LOCKED', 'liveMutation': False, 'realOrder': False,
        'data': {'rvolLookbackDays': LOOKBACK_DAYS, 'nhExtraRestCalls': 0,
                 'dartEnabled': bool(DART_KEY), 'orderBookDepth': 'existing top-level quote snapshot'},
        'challengers': {
            'sip_rvol': sip[:n], 'rvol_gap': gap[:n],
            'rvol_catalyst': cat[:n] if DART_KEY else [], 'relative_strength': rel[:n],
        },
        'universeRows': len(rows),
        'note': 'Scanner challenger only. No challenger is routed to live orders automatically.'
    }


def record_snapshot(limit=100):
    result = scan(limit)
    now = datetime.now(KST); bucket = _floor5(now)
    rows_by_code = {}
    for arr in (result.get('challengers') or {}).values():
        for r in arr:
            rows_by_code[r['code']] = r
    # To retain feature rows outside top-N, rescan result universe is intentionally not inferred.
    # At default limit=100 this covers the research focus without bloating SQLite.
    with _conn() as c:
        for r in rows_by_code.values():
            c.execute('''INSERT OR REPLACE INTO scanner_intel_snapshots(
              snapshot_at,trade_date,code,market,name,price,
              rank_sip_rvol,rank_rvol_gap,rank_rvol_catalyst,rank_relative_strength,
              score_sip_rvol,score_rvol_gap,score_rvol_catalyst,score_relative_strength,
              turnover_eok,gap_pct,atr14_pct,rvol5,rvol15,rvol30,rvol_time,
              intraday_ret_pct,market_median_ret_pct,relative_strength_pct,
              spread_pct,book_imbalance,catalyst_present,catalyst_count,catalyst_state,source)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (
                bucket.isoformat(), bucket.date().isoformat(), r['code'], r.get('market'), r.get('name'), r.get('price'),
                r.get('rankSipRvol'), r.get('rankRvolGap'), r.get('rankRvolCatalyst'), r.get('rankRelativeStrength'),
                r.get('scoreSipRvol'), r.get('scoreRvolGap'), r.get('scoreRvolCatalyst'), r.get('scoreRelativeStrength'),
                r.get('turnoverEok'), r.get('gapPct'), r.get('atr14Pct'), r.get('rvol5'), r.get('rvol15'), r.get('rvol30'), r.get('rvolTime'),
                r.get('intradayRetPct'), r.get('marketMedianRetPct'), r.get('relativeStrengthPct'),
                r.get('spreadPct'), r.get('bookImbalance'),
                None if r.get('catalystPresent') is None else int(bool(r.get('catalystPresent'))),
                r.get('catalystCount'), r.get('catalystState'), 'SCANNER_INTEL_SHADOW'))
    with _LOCK:
        _STATE['lastSnapshotAt'] = bucket.isoformat(); _STATE['snapshots'] += 1
        _STATE['rows'] += len(rows_by_code); _STATE['lastError'] = None
    return {'ok': True, 'snapshotAt': bucket.isoformat(), 'rows': len(rows_by_code)}


def snapshot_stats():
    init_db()
    with _conn() as c:
        total = int(c.execute('SELECT COUNT(*) FROM scanner_intel_snapshots').fetchone()[0])
        days = int(c.execute('SELECT COUNT(DISTINCT trade_date) FROM scanner_intel_snapshots').fetchone()[0])
        latest = c.execute('SELECT MAX(snapshot_at) FROM scanner_intel_snapshots').fetchone()[0]
    return {'ok': True, 'rows': total, 'days': days, 'latestSnapshotAt': latest, 'state': dict(_STATE)}


def _dart_request(url, timeout=20):
    req = urllib.request.Request(url, headers={'User-Agent': 'StockTraderResearch/0.17.6'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _dart_map_stale():
    with _conn() as c:
        row = c.execute('SELECT MAX(fetched_at) FROM dart_corp_map').fetchone()
    if not row or not row[0]:
        return True
    try:
        return (datetime.now(KST) - datetime.fromisoformat(str(row[0])).astimezone(KST)).days >= 7
    except Exception:
        return True


def _refresh_dart_map():
    if not DART_KEY or not _dart_map_stale():
        return
    url = 'https://opendart.fss.or.kr/api/corpCode.xml?' + urllib.parse.urlencode({'crtfc_key': DART_KEY})
    raw = _dart_request(url)
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        name = z.namelist()[0]
        xml_raw = z.read(name)
    root = ET.fromstring(xml_raw)
    now = datetime.now(KST).isoformat(timespec='seconds')
    batch = []
    for item in root.findall('.//list'):
        corp_code = (item.findtext('corp_code') or '').strip()
        stock_code = (item.findtext('stock_code') or '').strip()
        if not corp_code:
            continue
        batch.append((corp_code, stock_code, (item.findtext('corp_name') or '').strip(),
                      (item.findtext('modify_date') or '').strip(), now))
    with _conn() as c:
        c.executemany('''INSERT INTO dart_corp_map(corp_code,stock_code,corp_name,modify_date,fetched_at)
          VALUES(?,?,?,?,?) ON CONFLICT(corp_code) DO UPDATE SET stock_code=excluded.stock_code,
          corp_name=excluded.corp_name,modify_date=excluded.modify_date,fetched_at=excluded.fetched_at''', batch)


def _refresh_dart_events():
    if not DART_KEY:
        return {'enabled': False, 'reason': 'OPENDART_API_KEY not configured'}
    last = _STATE.get('lastDartRefreshAt')
    if last:
        try:
            if (datetime.now(KST) - datetime.fromisoformat(last).astimezone(KST)).total_seconds() < DART_REFRESH_SEC:
                return {'enabled': True, 'cached': True}
        except Exception:
            pass
    _refresh_dart_map()
    day = datetime.now(KST).strftime('%Y%m%d')
    page = 1; inserted = 0; total_pages = 1
    while page <= min(total_pages, 10):
        params = {'crtfc_key': DART_KEY, 'bgn_de': day, 'end_de': day,
                  'page_no': page, 'page_count': 100}
        url = 'https://opendart.fss.or.kr/api/list.json?' + urllib.parse.urlencode(params)
        data = json.loads(_dart_request(url).decode('utf-8'))
        status = str(data.get('status') or '')
        if status not in ('000', ''):
            # 013 means no data; keep this a normal empty result.
            if status == '013':
                break
            raise RuntimeError(f"OpenDART {status}: {data.get('message')}")
        items = data.get('list') or []
        total_pages = int(data.get('total_page') or 1)
        now = datetime.now(KST).isoformat(timespec='seconds')
        with _conn() as c:
            for it in items:
                corp_code = str(it.get('corp_code') or '')
                m = c.execute('SELECT stock_code FROM dart_corp_map WHERE corp_code=?', (corp_code,)).fetchone()
                stock_code = str(m['stock_code'] or '') if m else ''
                c.execute('''INSERT OR REPLACE INTO dart_events(rcept_no,rcept_dt,corp_code,stock_code,corp_name,report_nm,fetched_at)
                  VALUES(?,?,?,?,?,?,?)''', (str(it.get('rcept_no') or ''), str(it.get('rcept_dt') or day), corp_code,
                  stock_code, str(it.get('corp_name') or ''), str(it.get('report_nm') or ''), now))
                inserted += 1
        page += 1
    with _LOCK:
        _STATE['lastDartRefreshAt'] = datetime.now(KST).isoformat(timespec='seconds')
    return {'enabled': True, 'insertedOrRefreshed': inserted}


def _historical_0930_features(code):
    rows = _load_bars(code, 90); by = _group_days(rows); out = []
    dates = sorted(by)
    for d in dates:
        arr = by[d]
        first = [x for x in arr if 540 <= x['_dt'].hour * 60 + x['_dt'].minute < 570]
        future = [x for x in arr if x['_dt'].hour * 60 + x['_dt'].minute >= 570]
        prior = [x for pd in dates if pd < d for x in ([] if pd not in by else [by[pd][-1]])]
        if len(first) < 6 or not future:
            continue
        prev_dates = [pd for pd in dates if pd < d]
        if not prev_dates:
            continue
        prev_close = float(by[prev_dates[-1]][-1]['close'])
        op = float(first[0]['open']); p30 = float(first[-1]['close'])
        vol30 = sum(max(0, int(float(x['volume'] or 0))) for x in first)
        avg30 = _historical_volume_average(by, d, 6)
        rv = _rvol(vol30, avg30)
        if rv is None or op <= 0 or prev_close <= 0 or p30 <= 0:
            continue
        entry = float(future[0]['open'])
        if entry <= 0:
            continue
        hi = max(float(x['high']) for x in future); lo = min(float(x['low']) for x in future)
        eod = float(future[-1]['close'])
        turnover30 = sum(float(x['close']) * max(0, int(float(x['volume'] or 0))) for x in first) / 100_000_000
        out.append({
            'code': code, 'date': d.isoformat(), 'market': instrument_meta(code).get('market') or 'UNKNOWN',
            'rvol30': rv, 'gapPct': (op / prev_close - 1) * 100, 'atr14Pct': _daily_atr_pct(by, d),
            'open30RetPct': (p30 / op - 1) * 100, 'turnover30Eok': turnover30,
            'futureMaxPct': (hi / entry - 1) * 100, 'futureMinPct': (lo / entry - 1) * 100,
            'eodPct': (eod / entry - 1) * 100,
        })
    return out


def historical_lab(max_codes=40):
    try:
        from backtest_engine import available_codes
        codes = [x['code'] for x in available_codes()[:max(10, min(int(max_codes), 80))]]
    except Exception:
        codes = list(getattr(collector, 'watchlist', []) or [])[:max_codes]
    all_rows = []
    for code in codes:
        if code not in PROTECTED_CODES:
            all_rows.extend(_historical_0930_features(code))
    by_day_market = defaultdict(list)
    by_day = defaultdict(list)
    for r in all_rows:
        by_day_market[(r['date'], r['market'])].append(r['open30RetPct'])
        by_day[r['date']].append(r)
    for r in all_rows:
        vals = by_day_market.get((r['date'], r['market'])) or []
        med = median(vals) if vals else 0
        r['relativeStrengthPct'] = r['open30RetPct'] - med
    selected = defaultdict(list)
    for day, arr in by_day.items():
        ranked_rv = sorted(arr, key=lambda x: x['rvol30'], reverse=True)
        top_codes = {x['code'] for x in ranked_rv[:TOP_N]}
        for r in arr:
            liquid = r['turnover30Eok'] >= MIN_TURNOVER_EOK
            atr_ok = (r['atr14Pct'] or 0) >= MIN_ATR_PCT
            base = r['rvol30'] >= MIN_RVOL and r['code'] in top_codes and liquid and atr_ok
            if base:
                selected['sip_rvol'].append(r)
                if r['gapPct'] >= .5:
                    selected['rvol_gap'].append(r)
                if r['relativeStrengthPct'] >= 1.0:
                    selected['relative_strength'].append(r)
    def met(xs):
        if not xs:
            return {'samples': 0, 'hit2Pct': 0, 'avgFutureMaxPct': 0, 'avgFutureMinPct': 0, 'avgEodPct': 0}
        return {
            'samples': len(xs),
            'hit2Pct': round(sum(x['futureMaxPct'] >= 2 for x in xs) / len(xs) * 100, 2),
            'avgFutureMaxPct': round(mean(x['futureMaxPct'] for x in xs), 3),
            'avgFutureMinPct': round(mean(x['futureMinPct'] for x in xs), 3),
            'avgEodPct': round(mean(x['eodPct'] for x in xs), 3),
        }
    return {
        'ok': True, 'labVersion': '0.17.6', 'researchOnly': True,
        'definition': '09:30 시점까지의 정보만 사용. 이후 고가/저가/EOD는 scanner quality label로만 사용.',
        'challengers': {k: met(v) for k, v in selected.items()},
        'catalyst': {'enabled': bool(DART_KEY), 'historicalBenchmarkReady': False,
                     'reason': 'Catalyst history is accumulated point-in-time from v0.17.6; it is not retroactively guessed.'},
        'warning': 'Scanner hit-rate is not PF. Strategy entry/exit OOS/Lockbox verification remains required.'
    }


def status():
    s = dict(_STATE); s['snapshots'] = snapshot_stats(); return s


def _loop():
    with _LOCK: _STATE['running'] = True
    try:
        while not _STOP.is_set():
            try:
                if DART_KEY:
                    _refresh_dart_events()
                if regular_session():
                    record_snapshot(100)
            except Exception as exc:
                with _LOCK: _STATE['lastError'] = f'{type(exc).__name__}: {exc}'[:600]
            _STOP.wait(SNAPSHOT_SEC)
    finally:
        with _LOCK: _STATE['running'] = False


def start():
    global _THREAD
    init_db()
    with _LOCK:
        if _THREAD and _THREAD.is_alive():
            return status()
        _STOP.clear(); _THREAD = threading.Thread(target=_loop, daemon=True, name='scanner-intelligence')
        _THREAD.start()
    return status()


def stop():
    _STOP.set(); return {'ok': True, 'status': status()}


init_db()
