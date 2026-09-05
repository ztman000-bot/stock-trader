"""Research-only point-in-time decision observation for v0.17.10.

This module records what the scanner saw and why it did or did not trade. It
never sends orders, never mutates Control rules, and adds no NH API traffic.
Outcome labels are recomputed from the local bars database so later official
NH backfills can replace provisional live-sampled labels.

The v0.17.10 observer deliberately uses its own universe snapshot table name.
Older releases may already have a legacy ``universe_snapshots`` table with a
different schema; reusing that generic table name can break safe upgrades.
"""
import json
import sqlite3
import threading
from datetime import datetime, timedelta

from collector import DB_PATH, KST, collector, instrument_meta, universe_verified

HORIZONS_MIN = (5, 10, 30, 60)
OFFICIAL_5M_SOURCE = 'nh_period_5m'
UNIVERSE_TABLE = 'research_universe_snapshots_v01710'
_ALLOWED_ACTIONS = {
    'BUY_CANDIDATE', 'SETUP', 'WATCH', 'SHADOW_ONLY', 'BLOCKED',
    'PROTECTED', 'SAFETY_WAIT', 'WAIT_DATA',
}
_LOCK = threading.RLock()
_SEEN = set()
_STOP = threading.Event()
_THREAD = None
_LAST_UNIVERSE_CAPTURE_DATE = None
_STATUS = {
    'enabled': True,
    'running': False,
    'lastRecordAt': None,
    'lastLabelAt': None,
    'lastUniverseSnapshotDate': None,
    'lastError': None,
    'recorded': 0,
    'labelsRefreshed': 0,
    'controlMutation': False,
    'realOrderEnabled': False,
}


def _conn():
    c = sqlite3.connect(DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    return c


def init_observer_db():
    with _conn() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS decision_observations(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          code TEXT NOT NULL,
          name TEXT,
          observed_at TEXT NOT NULL,
          signal_bucket TEXT NOT NULL,
          signal_price REAL NOT NULL DEFAULT 0,
          action TEXT NOT NULL,
          score REAL NOT NULL DEFAULT 0,
          reasons TEXT,
          blocked_reasons TEXT,
          snapshot_json TEXT NOT NULL,
          market_json TEXT NOT NULL,
          ret_5m REAL,
          ret_10m REAL,
          ret_30m REAL,
          ret_60m REAL,
          ret_eod REAL,
          label_count INTEGER NOT NULL DEFAULT 0,
          official_label_count INTEGER NOT NULL DEFAULT 0,
          labels_official INTEGER NOT NULL DEFAULT 0,
          labeled_at TEXT,
          UNIQUE(code,signal_bucket,action)
        );
        CREATE INDEX IF NOT EXISTS idx_decision_observations_time
          ON decision_observations(signal_bucket,action,code);
        CREATE TABLE IF NOT EXISTS market_observation_snapshots(
          bucket TEXT PRIMARY KEY,
          observed_at TEXT NOT NULL,
          snapshot_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS research_universe_snapshots_v01710(
          snapshot_date TEXT NOT NULL,
          code TEXT NOT NULL,
          selected_rank INTEGER NOT NULL,
          name TEXT,
          market TEXT,
          market_cap_eok REAL,
          captured_at TEXT NOT NULL,
          PRIMARY KEY(snapshot_date,code)
        );
        CREATE INDEX IF NOT EXISTS idx_research_universe_v01710_date_rank
          ON research_universe_snapshots_v01710(snapshot_date,selected_rank);
        CREATE TABLE IF NOT EXISTS bar_5m_provenance(
          code TEXT NOT NULL,bucket TEXT NOT NULL,source TEXT NOT NULL,updated_at TEXT NOT NULL,
          PRIMARY KEY(code,bucket)
        );
        ''')


def _bucket_5m(now):
    return now.replace(minute=(now.minute // 5) * 5, second=0, microsecond=0)


def _num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def capture_universe_snapshot():
    """Store the first verified watchlist seen for each KST trading date."""
    global _LAST_UNIVERSE_CAPTURE_DATE
    if not universe_verified():
        return {'ok': False, 'reason': 'universe-not-verified'}
    now = datetime.now(KST)
    day = now.date().isoformat()
    with _LOCK:
        if _LAST_UNIVERSE_CAPTURE_DATE == day:
            return {'ok': True, 'date': day, 'cached': True}
    codes = list(getattr(collector, 'watchlist', []) or [])
    if not codes:
        return {'ok': False, 'reason': 'empty-watchlist'}
    init_observer_db()
    with _conn() as c:
        for rank, code in enumerate(codes, 1):
            meta = instrument_meta(code)
            c.execute('''INSERT OR IGNORE INTO research_universe_snapshots_v01710(
                         snapshot_date,code,selected_rank,name,market,market_cap_eok,captured_at)
                         VALUES(?,?,?,?,?,?,?)''',
                      (day, str(code), rank, meta.get('name') or str(code), meta.get('market'),
                       _num(meta.get('marketCapEok')), now.isoformat()))
    with _LOCK:
        _LAST_UNIVERSE_CAPTURE_DATE = day
        _STATUS['lastUniverseSnapshotDate'] = day
    return {'ok': True, 'date': day, 'codes': len(codes)}


def _market_context(evs):
    groups = {}
    liquidity = 0
    turnover = 0.0
    for ev in evs:
        m = ev.get('market') or {}
        market = str(m.get('market') or 'UNKNOWN')
        g = groups.setdefault(market, {'candidates': 0, 'advancers': 0, 'changeSum': 0.0, 'turnoverEok': 0.0})
        change = _num(m.get('changeRate'))
        t = max(0.0, _num(m.get('turnoverEok')))
        g['candidates'] += 1
        g['advancers'] += 1 if change > 0 else 0
        g['changeSum'] += change
        g['turnoverEok'] += t
        liquidity += 1 if m.get('liquidityOk') else 0
        turnover += t
    summary = {}
    for market, g in groups.items():
        n = g['candidates']
        summary[market] = {
            'candidates': n,
            'advancersPct': round(100 * g['advancers'] / n, 2) if n else 0,
            'avgChangeRate': round(g['changeSum'] / n, 4) if n else 0,
            'turnoverEok': round(g['turnoverEok'], 2),
        }
    breadth = _num((evs[0] if evs else {}).get('marketBreadth')) if evs else 0.0
    daily = dict((evs[0] if evs else {}).get('daily') or {})
    return {
        'candidateCount': len(evs),
        'liquidityOkCount': liquidity,
        'totalTurnoverEok': round(turnover, 2),
        'marketBreadth': round(breadth, 4),
        'markets': summary,
        'daily': daily,
    }


def _observation_snapshot(ev):
    return {
        'score': ev.get('score'),
        'action': ev.get('action'),
        'reasons': list(ev.get('reasons') or []),
        'blockedReasons': list(ev.get('blockedReasons') or []),
        'marketBreadth': ev.get('marketBreadth'),
        'indicators': dict(ev.get('indicators') or {}),
        'market': dict(ev.get('market') or {}),
        'daily': dict(ev.get('daily') or {}),
    }


def record_scan_observations(evs):
    """Persist each code/action once per 5-minute signal bucket."""
    if not evs:
        return {'ok': True, 'inserted': 0}
    init_observer_db()
    capture_universe_snapshot()
    now = datetime.now(KST)
    market = _market_context(evs)
    buckets = [str((ev.get('indicators') or {}).get('bucket') or '') for ev in evs]
    market_bucket = max((b for b in buckets if b), default=_bucket_5m(now).isoformat())
    candidates = []
    with _LOCK:
        if len(_SEEN) > 5000:
            _SEEN.clear()
        for ev in evs:
            action = str(ev.get('action') or '')
            if action not in _ALLOWED_ACTIONS:
                continue
            ind = ev.get('indicators') or {}
            m = ev.get('market') or {}
            bucket = str(ind.get('bucket') or market_bucket)
            key = (str(ev.get('code') or ''), bucket, action)
            if not key[0] or key in _SEEN:
                continue
            _SEEN.add(key)
            price = _num(ind.get('price') or m.get('price'))
            candidates.append((key, ev, price))
    inserted = 0
    try:
        with _conn() as c:
            c.execute('INSERT OR IGNORE INTO market_observation_snapshots(bucket,observed_at,snapshot_json) VALUES(?,?,?)',
                      (market_bucket, now.isoformat(), json.dumps(market, ensure_ascii=False, default=str)))
            for (code, bucket, action), ev, price in candidates:
                snap = _observation_snapshot(ev)
                cur = c.execute('''INSERT OR IGNORE INTO decision_observations(
                    code,name,observed_at,signal_bucket,signal_price,action,score,reasons,blocked_reasons,snapshot_json,market_json)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                    (code, ev.get('name') or code, now.isoformat(), bucket, price, action,
                     _num(ev.get('score')), json.dumps(ev.get('reasons') or [], ensure_ascii=False),
                     json.dumps(ev.get('blockedReasons') or [], ensure_ascii=False),
                     json.dumps(snap, ensure_ascii=False, default=str),
                     json.dumps(market, ensure_ascii=False, default=str)))
                inserted += int(cur.rowcount > 0)
        with _LOCK:
            _STATUS['recorded'] += inserted
            _STATUS['lastRecordAt'] = now.isoformat(timespec='seconds')
            _STATUS['lastError'] = None
        return {'ok': True, 'inserted': inserted}
    except Exception as exc:
        with _LOCK:
            _STATUS['lastError'] = f'{type(exc).__name__}: {exc}'[:500]
        return {'ok': False, 'inserted': inserted, 'error': str(exc)}


def _ret(price, future):
    return round((future / price - 1) * 100, 5) if price > 0 and future > 0 else None


def refresh_outcomes(limit=1000, days=10):
    """Recompute forward labels from current local bars, including official overwrite."""
    init_observer_db()
    limit = max(1, min(int(limit), 5000))
    days = max(1, min(int(days), 120))
    cutoff = (datetime.now(KST) - timedelta(days=days)).isoformat()
    with _conn() as c:
        obs = [dict(r) for r in c.execute('''SELECT id,code,signal_bucket,signal_price FROM decision_observations
                                             WHERE signal_bucket>=? AND signal_price>0
                                             ORDER BY id DESC LIMIT ?''', (cutoff, limit))]
        cache = {}
        now = datetime.now(KST)
        updated = 0
        for o in obs:
            try:
                sig = datetime.fromisoformat(str(o['signal_bucket'])).astimezone(KST)
            except Exception:
                continue
            key = (o['code'], sig.date().isoformat())
            if key not in cache:
                rows = c.execute('''SELECT b.bucket,b.close,p.source FROM bars_5m b
                                    LEFT JOIN bar_5m_provenance p ON p.code=b.code AND p.bucket=b.bucket
                                    WHERE b.code=? AND b.bucket LIKE ? ORDER BY b.bucket''',
                                 (o['code'], key[1] + '%')).fetchall()
                parsed = []
                for r in rows:
                    try:
                        parsed.append({'dt': datetime.fromisoformat(str(r['bucket'])).astimezone(KST),
                                       'close': float(r['close']), 'source': str(r['source'] or '')})
                    except Exception:
                        pass
                cache[key] = parsed
            series = cache[key]
            values = {}
            sources = []
            for h in HORIZONS_MIN:
                target = sig + timedelta(minutes=h)
                hit = next((b for b in series if b['dt'] >= target), None)
                values[h] = _ret(float(o['signal_price']), hit['close']) if hit else None
                if hit:
                    sources.append(hit['source'])
            finished = sig.date() < now.date() or (sig.date() == now.date() and now.hour * 60 + now.minute >= 931)
            eod = None
            if finished:
                eod_rows = [b for b in series if 915 <= b['dt'].hour * 60 + b['dt'].minute <= 930]
                hit = eod_rows[-1] if eod_rows else (series[-1] if series else None)
                eod = _ret(float(o['signal_price']), hit['close']) if hit else None
                if hit:
                    sources.append(hit['source'])
            label_count = sum(v is not None for v in values.values()) + (1 if eod is not None else 0)
            official = sum(1 for s in sources if s == OFFICIAL_5M_SOURCE)
            c.execute('''UPDATE decision_observations SET ret_5m=?,ret_10m=?,ret_30m=?,ret_60m=?,ret_eod=?,
                         label_count=?,official_label_count=?,labels_official=?,labeled_at=? WHERE id=?''',
                      (values[5], values[10], values[30], values[60], eod, label_count, official,
                       1 if label_count and official == label_count else 0, now.isoformat(), o['id']))
            updated += 1
    with _LOCK:
        _STATUS['labelsRefreshed'] += updated
        _STATUS['lastLabelAt'] = datetime.now(KST).isoformat(timespec='seconds')
        _STATUS['lastError'] = None
    return {'ok': True, 'updated': updated}


def observation_report(limit=100):
    init_observer_db()
    capture_universe_snapshot()
    refresh_outcomes(limit=1000, days=10)
    limit = max(1, min(int(limit), 200))
    today = datetime.now(KST).date().isoformat()
    with _conn() as c:
        total = int(c.execute('SELECT COUNT(*) FROM decision_observations').fetchone()[0])
        today_count = int(c.execute('SELECT COUNT(*) FROM decision_observations WHERE signal_bucket LIKE ?', (today + '%',)).fetchone()[0])
        labeled = int(c.execute('SELECT COUNT(*) FROM decision_observations WHERE label_count>0').fetchone()[0])
        official = int(c.execute('SELECT COUNT(*) FROM decision_observations WHERE labels_official=1').fetchone()[0])
        actions = {str(r[0]): int(r[1]) for r in c.execute('''SELECT action,COUNT(*) FROM decision_observations
                    WHERE signal_bucket LIKE ? GROUP BY action ORDER BY action''', (today + '%',)).fetchall()}
        recent = [dict(r) for r in c.execute('''SELECT id,code,name,observed_at,signal_bucket,signal_price,action,score,
                    ret_5m,ret_10m,ret_30m,ret_60m,ret_eod,label_count,official_label_count,labels_official
                    FROM decision_observations ORDER BY id DESC LIMIT ?''', (limit,)).fetchall()]
        market = [dict(r) for r in c.execute('SELECT bucket,observed_at,snapshot_json FROM market_observation_snapshots ORDER BY bucket DESC LIMIT 20').fetchall()]
        universe_days = int(c.execute('SELECT COUNT(DISTINCT snapshot_date) FROM research_universe_snapshots_v01710').fetchone()[0])
        universe_rows = int(c.execute('SELECT COUNT(*) FROM research_universe_snapshots_v01710').fetchone()[0])
    return {
        'ok': True,
        'version': '0.17.10',
        'researchOnly': True,
        'controlStrategy': 'v0.8.0 LOCKED',
        'controlMutation': False,
        'realOrderEnabled': False,
        'horizonsMinutes': list(HORIZONS_MIN),
        'totalObservations': total,
        'todayObservations': today_count,
        'todayActions': actions,
        'labeledObservations': labeled,
        'fullyOfficialLabeledObservations': official,
        'universeSnapshotTable': UNIVERSE_TABLE,
        'universeSnapshotDays': universe_days,
        'universeSnapshotRows': universe_rows,
        'recent': recent,
        'marketSnapshots': market,
        'status': status(),
    }


def _worker():
    with _LOCK:
        _STATUS['running'] = True
    while not _STOP.is_set():
        try:
            capture_universe_snapshot()
            refresh_outcomes(limit=1500, days=15)
        except Exception as exc:
            with _LOCK:
                _STATUS['lastError'] = f'{type(exc).__name__}: {exc}'[:500]
        _STOP.wait(300)
    with _LOCK:
        _STATUS['running'] = False


def start():
    global _THREAD
    init_observer_db()
    if _THREAD and _THREAD.is_alive():
        return status()
    _STOP.clear()
    _THREAD = threading.Thread(target=_worker, name='research-observer-labeler', daemon=True)
    _THREAD.start()
    return status()


def stop():
    _STOP.set()
    if _THREAD and _THREAD.is_alive():
        _THREAD.join(timeout=3)
    return status()


def status():
    with _LOCK:
        return dict(_STATUS)


init_observer_db()
