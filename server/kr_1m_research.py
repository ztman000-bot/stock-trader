"""KR 1-minute research data collector v0.17.5.

Research/data only. No order endpoint is called here.
- Live: NH PLUG KRX execution WebSocket channel ``oc`` builds 1-minute OHLCV
  for a small, priority-first focus set so live REST traffic is not increased.
- After market: official /krstock/quote/v1/period is used to backfill 1-minute
  bars for the research universe, sharing collector.nh_call throttling.
- 1-minute data are kept separate from the live 5-minute strategy engine.
- Data availability alone never promotes a strategy or enables real orders.
"""
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta

from collector import (
    DB_PATH, KST, PROTECTED_CODES, active_candidates, collector, nh_call,
    regular_session,
)

try:
    from nhplug.realtime import subscribe
except Exception:  # import-safe; REST backfill can still run
    subscribe = None

KR_1M_RESEARCH_ENABLED = os.getenv('KR_1M_RESEARCH_ENABLED', 'true').lower() == 'true'
KR_1M_WS_ENABLED = os.getenv('KR_1M_WS_ENABLED', 'true').lower() == 'true'
KR_1M_LIVE_FOCUS = max(1, min(int(os.getenv('KR_1M_LIVE_FOCUS', '10')), 10))
KR_1M_HISTORY_DAYS = max(5, min(int(os.getenv('KR_1M_HISTORY_DAYS', '30')), 120))
KR_1M_HISTORY_CODES = max(5, min(int(os.getenv('KR_1M_HISTORY_CODES', '40')), 100))
KR_1M_ARRAY_COUNT = max(120, min(int(os.getenv('KR_1M_ARRAY_COUNT', '400')), 9999))
KR_1M_BACKFILL_INTERVAL_HOURS = max(4, int(os.getenv('KR_1M_BACKFILL_INTERVAL_HOURS', '12')))
KR_1M_WS_TIMEOUT_SEC = max(10, int(os.getenv('KR_1M_WS_TIMEOUT_SEC', '30')))
REGIME_PROXY_CODES = ('069500', '229200')

_STOP = threading.Event()
_THREAD = None
_LOCK = threading.RLock()
_LAST_BUCKET = {}
_FIRST_BUCKET = {}
_STATUS = {
    'enabled': KR_1M_RESEARCH_ENABLED,
    'running': False,
    'phase': 'idle',
    'lastError': None,
    'lastCycleAt': None,
    'lastLiveAt': None,
    'lastBackfillAt': None,
    'liveCodes': [],
    'wsMessages': 0,
    'barsWritten': 0,
    'historyCalls': 0,
    'paperEnabled': False,
    'realOrderEnabled': False,
    'liveTransport': 'NH_WS_oc',
    'backfillTransport': 'NH_REST_period_1m',
    'sharedRestThrottle': True,
}


def _conn():
    c = sqlite3.connect(DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    return c


def init_db():
    with _conn() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS bars_1m(
          code TEXT NOT NULL,
          bucket TEXT NOT NULL,
          session_date TEXT NOT NULL,
          open REAL NOT NULL,
          high REAL NOT NULL,
          low REAL NOT NULL,
          close REAL NOT NULL,
          volume REAL NOT NULL DEFAULT 0,
          turnover REAL NOT NULL DEFAULT 0,
          sample_count INTEGER NOT NULL DEFAULT 0,
          complete INTEGER NOT NULL DEFAULT 0,
          source TEXT NOT NULL,
          PRIMARY KEY(code,bucket)
        );
        CREATE INDEX IF NOT EXISTS idx_bars_1m_date_code
          ON bars_1m(session_date,code,bucket);
        CREATE TABLE IF NOT EXISTS kr_1m_fetch_log(
          code TEXT NOT NULL,
          session_date TEXT NOT NULL,
          status TEXT NOT NULL,
          rows INTEGER NOT NULL DEFAULT 0,
          fetched_at TEXT NOT NULL,
          PRIMARY KEY(code,session_date)
        );
        ''')


def _num(v):
    try:
        if v is None or v == '':
            return None
        return float(str(v).replace(',', '').strip())
    except Exception:
        return None


def _period_rows(payload):
    if not isinstance(payload, dict):
        return []
    for key in ('Output_1', 'output_1'):
        rows = payload.get(key)
        if isinstance(rows, list):
            return rows
    return []


def _focus_codes():
    ordered = []
    for code in list(getattr(collector, 'priority_codes', []) or []):
        if code and code not in PROTECTED_CODES and code not in ordered:
            ordered.append(code)
    try:
        for row in active_candidates(KR_1M_LIVE_FOCUS * 2):
            code = str(row.get('code') or '')
            if code and code not in PROTECTED_CODES and code not in ordered:
                ordered.append(code)
    except Exception:
        pass
    for code in list(getattr(collector, 'watchlist', []) or []):
        if code and code not in PROTECTED_CODES and code not in ordered:
            ordered.append(code)
    return ordered[:KR_1M_LIVE_FOCUS]


def _mark_previous_complete(code, new_bucket):
    prev = _LAST_BUCKET.get(code)
    if not prev or prev == new_bucket:
        return
    # The very first bucket may have started before subscription, so never label
    # it complete unless an official REST backfill later overwrites it.
    if _FIRST_BUCKET.get(code) != prev:
        with _conn() as c:
            c.execute('UPDATE bars_1m SET complete=1 WHERE code=? AND bucket=?', (code, prev))


def _on_ws_message(msg):
    try:
        if not isinstance(msg, dict):
            return
        h = msg.get('header') or {}
        body = msg.get('body') or {}
        if h.get('tr_cd') not in ('oc', 'mc') or not isinstance(body, dict):
            return
        code = str(body.get('code') or h.get('tr_key') or '').strip()
        if not code or code in PROTECTED_CODES:
            return
        price = _num(body.get('price'))
        if not price or price <= 0:
            return
        raw_time = str(body.get('time') or '').replace(':', '').strip().zfill(6)
        if len(raw_time) != 6 or not raw_time.isdigit():
            return
        now = datetime.now(KST)
        dt = now.replace(hour=int(raw_time[:2]), minute=int(raw_time[2:4]),
                         second=0, microsecond=0)
        hm = dt.hour * 60 + dt.minute
        if not 540 <= hm <= 930:
            return
        bucket = dt.isoformat()
        vol = max(0.0, _num(body.get('movolume')) or 0.0)
        turnover = price * vol
        if code not in _FIRST_BUCKET:
            _FIRST_BUCKET[code] = bucket
        _mark_previous_complete(code, bucket)
        with _conn() as c:
            c.execute('''INSERT INTO bars_1m(code,bucket,session_date,open,high,low,close,volume,turnover,sample_count,complete,source)
              VALUES(?,?,?,?,?,?,?,?,?,1,0,'ws_oc')
              ON CONFLICT(code,bucket) DO UPDATE SET
                high=MAX(bars_1m.high,excluded.high),low=MIN(bars_1m.low,excluded.low),
                close=excluded.close,volume=bars_1m.volume+excluded.volume,
                turnover=bars_1m.turnover+excluded.turnover,
                sample_count=bars_1m.sample_count+1,
                source=CASE WHEN bars_1m.source='nh_period_1m' THEN bars_1m.source ELSE 'ws_oc' END''',
              (code, bucket, dt.date().isoformat(), price, price, price, price, vol, turnover))
        _LAST_BUCKET[code] = bucket
        with _LOCK:
            _STATUS['wsMessages'] += 1
            _STATUS['lastLiveAt'] = datetime.now(KST).isoformat(timespec='seconds')
    except Exception as exc:
        with _LOCK:
            _STATUS['lastError'] = f'WS callback {type(exc).__name__}: {exc}'[:500]


def _run_live_ws():
    if subscribe is None or not KR_1M_WS_ENABLED:
        with _LOCK:
            _STATUS['phase'] = 'ws-unavailable-rest-backfill-only'
        _STOP.wait(30)
        return
    codes = _focus_codes()
    if not codes:
        with _LOCK:
            _STATUS['phase'] = 'waiting-live-focus'
        _STOP.wait(10)
        return
    with _LOCK:
        _STATUS['phase'] = 'live-ws-1m'
        _STATUS['liveCodes'] = list(codes)
        _STATUS['lastError'] = None
    try:
        # One WS session, max 10 keys. The SDK enforces NH session/key/send limits.
        subscribe(codes, _on_ws_message, tr_cd='oc', timeout=KR_1M_WS_TIMEOUT_SEC)
    except Exception as exc:
        with _LOCK:
            _STATUS['lastError'] = f'{type(exc).__name__}: {exc}'[:500]
        _STOP.wait(10)


def _parse_period(payload, code, day):
    rows = []
    target = day.strftime('%Y%m%d')
    for r in _period_rows(payload):
        ds = str(r.get('bsop_date') or '').strip()
        ts = str(r.get('bsop_time') or '').strip().zfill(6)
        if ds != target or len(ts) != 6:
            continue
        try:
            dt = datetime.strptime(ds + ts, '%Y%m%d%H%M%S').replace(tzinfo=KST)
            dt = dt.replace(second=0, microsecond=0)
            hm = dt.hour * 60 + dt.minute
            if not 540 <= hm <= 930:
                continue
            o = _num(r.get('stck_oprc'))
            hi = _num(r.get('stck_hgpr'))
            lo = _num(r.get('stck_lwpr'))
            cl = _num(r.get('stck_prpr'))
            if not all(x is not None and x > 0 for x in (o, hi, lo, cl)):
                continue
            vol = max(0.0, _num(r.get('vol')) or 0.0)
            turnover = max(0.0, _num(r.get('tr_pbmn')) or cl * vol)
            rows.append((code, dt.isoformat(), day.isoformat(), o, hi, lo, cl,
                         vol, turnover, 0, 1, 'nh_period_1m'))
        except Exception:
            continue
    # Defensive de-duplication if the API repeats a minute.
    return list({x[1]: x for x in rows}.values())


def _existing_count(code, session_date):
    with _conn() as c:
        return int(c.execute('SELECT COUNT(*) FROM bars_1m WHERE code=? AND session_date=? AND complete=1',
                             (code, session_date)).fetchone()[0])


def _log_fetch(code, session_date, n):
    status = 'COMPLETE' if n >= 360 else 'PARTIAL' if n > 0 else 'EMPTY'
    with _conn() as c:
        c.execute('''INSERT INTO kr_1m_fetch_log(code,session_date,status,rows,fetched_at)
          VALUES(?,?,?,?,?) ON CONFLICT(code,session_date) DO UPDATE SET
          status=excluded.status,rows=excluded.rows,fetched_at=excluded.fetched_at''',
          (code, session_date, status, n, datetime.now(KST).isoformat(timespec='seconds')))
    return status


def _download_day(code, day):
    day_s = day.isoformat()
    existing = _existing_count(code, day_s)
    if existing >= 360:
        _log_fetch(code, day_s, existing)
        return 0
    payload = nh_call('/krstock/quote/v1/period', {
        'market_cd': 'KRX', 'iem_cd': code, 'edate': day.strftime('%Y%m%d'),
        'array_cnt': f'{KR_1M_ARRAY_COUNT:04d}', 'maxavg': '000',
        'gubun': '5', 'xtick': '1', 'today_cls_code': '0', 'fake_tick': '1',
    })
    rows = _parse_period(payload, code, day)
    if rows:
        with _conn() as c:
            c.executemany('''INSERT INTO bars_1m(code,bucket,session_date,open,high,low,close,volume,turnover,sample_count,complete,source)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(code,bucket) DO UPDATE SET open=excluded.open,high=excluded.high,
              low=excluded.low,close=excluded.close,volume=excluded.volume,turnover=excluded.turnover,
              complete=1,source='nh_period_1m' ''', rows)
    total = _existing_count(code, day_s)
    _log_fetch(code, day_s, total)
    with _LOCK:
        _STATUS['historyCalls'] += 1
        _STATUS['barsWritten'] += len(rows)
    return len(rows)


def _history_dates(n):
    now = datetime.now(KST)
    d = now.date()
    if regular_session(now) or now.hour * 60 + now.minute < 930:
        d -= timedelta(days=1)
    out = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return list(reversed(out))


def _history_codes():
    collector.wait_for_universe(timeout=20)
    codes = [c for c in list(getattr(collector, 'watchlist', []) or [])
             if c not in PROTECTED_CODES][:KR_1M_HISTORY_CODES]
    for code in REGIME_PROXY_CODES:
        if code not in codes:
            codes.append(code)
    return codes


def _five_minute_history_busy():
    try:
        from historical_accumulator import status as history_5m_status
        return bool(history_5m_status().get('running'))
    except Exception:
        return False


def _backfill_due():
    last = _STATUS.get('lastBackfillAt')
    if not last:
        return True
    try:
        return (datetime.now(KST) - datetime.fromisoformat(last)).total_seconds() >= KR_1M_BACKFILL_INTERVAL_HOURS * 3600
    except Exception:
        return True


def _run_backfill():
    if regular_session():
        return
    if _five_minute_history_busy():
        with _LOCK:
            _STATUS['phase'] = 'deferred-for-5m-history'
        _STOP.wait(30)
        return
    if not _backfill_due():
        with _LOCK:
            _STATUS['phase'] = 'backfill-cached'
        _STOP.wait(30)
        return
    codes = _history_codes()
    dates = _history_dates(KR_1M_HISTORY_DAYS)
    with _LOCK:
        _STATUS['phase'] = 'historical-1m-backfill'
        _STATUS['lastError'] = None
    for day in dates:
        for code in codes:
            if _STOP.is_set() or regular_session():
                return
            if _five_minute_history_busy():
                return
            try:
                _download_day(code, day)
            except Exception as exc:
                with _LOCK:
                    _STATUS['lastError'] = f'{code} {day}: {type(exc).__name__}: {exc}'[:600]
    with _LOCK:
        _STATUS['lastBackfillAt'] = datetime.now(KST).isoformat(timespec='seconds')
        _STATUS['lastSuccessAt'] = _STATUS['lastBackfillAt']


def coverage():
    init_db()
    with _conn() as c:
        bars = int(c.execute('SELECT COUNT(*) FROM bars_1m').fetchone()[0])
        complete_bars = int(c.execute('SELECT COUNT(*) FROM bars_1m WHERE complete=1').fetchone()[0])
        days = int(c.execute('SELECT COUNT(DISTINCT session_date) FROM bars_1m').fetchone()[0])
        rows = c.execute('''SELECT session_date,code,COUNT(*) n FROM bars_1m
                            WHERE complete=1 GROUP BY session_date,code''').fetchall()
    complete_code_days = sum(1 for r in rows if int(r['n']) >= 360)
    partial_code_days = sum(1 for r in rows if 0 < int(r['n']) < 360)
    complete_dates = len({r['session_date'] for r in rows if int(r['n']) >= 360})
    data_ready = bool(complete_dates >= 10 and complete_code_days >= 100)
    return {
        'bars': bars, 'completeBars': complete_bars, 'days': days,
        'completeDates': complete_dates, 'completeCodeDays': complete_code_days,
        'partialCodeDays': partial_code_days, 'dataReady': data_ready,
        'dataReadyRule': '>=10 complete dates and >=100 code-days with >=360 one-minute bars',
        'exitValidationReady': False,
    }


def research_status():
    with _LOCK:
        s = dict(_STATUS)
    s.update({
        'historyDaysTarget': KR_1M_HISTORY_DAYS,
        'historyCodesTarget': KR_1M_HISTORY_CODES,
        'liveFocusMax': KR_1M_LIVE_FOCUS,
        'wsEnabled': KR_1M_WS_ENABLED,
        'coverage': coverage(),
    })
    return s


def _loop():
    with _LOCK:
        _STATUS['running'] = True
    try:
        collector.wait_for_universe(timeout=30)
        while not _STOP.is_set():
            with _LOCK:
                _STATUS['lastCycleAt'] = datetime.now(KST).isoformat(timespec='seconds')
            if regular_session():
                _run_live_ws()
            else:
                _run_backfill()
                _STOP.wait(20)
    finally:
        with _LOCK:
            _STATUS['running'] = False
            _STATUS['phase'] = 'stopped'


def start():
    global _THREAD
    init_db()
    if not KR_1M_RESEARCH_ENABLED:
        return research_status()
    with _LOCK:
        if _THREAD and _THREAD.is_alive():
            return research_status()
        _STOP.clear()
        _THREAD = threading.Thread(target=_loop, daemon=True, name='kr-1m-research')
        _THREAD.start()
    return research_status()


def stop():
    _STOP.set()
    return {'ok': True, 'status': research_status()}


init_db()
