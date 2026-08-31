import math
import os
import re
import sqlite3
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from nhplug import call

try:
    from nhplug.instruments import load_master
except Exception:  # Older SDK / temporary import failure: fail closed for entries.
    load_master = None

KST = ZoneInfo('Asia/Seoul')
DB_PATH = os.getenv('MARKET_DB_PATH', os.path.join(os.path.dirname(__file__), 'market_data.db'))

# REST budget: the official SDK documents 429 around 5 calls/sec. Keep margin.
API_MIN_INTERVAL = max(0.25, float(os.getenv('NH_REST_MIN_INTERVAL', '0.28')))
MASTER_PRESELECT = max(40, min(int(os.getenv('MASTER_PRESELECT', '120')), 240))
FOCUS_SIZE = max(10, min(int(os.getenv('FOCUS_SIZE', '30')), 50))
MIN_MARKET_CAP_EOK = max(0.0, float(os.getenv('MIN_MARKET_CAP_EOK', '500')))
MIN_PRICE = max(100.0, float(os.getenv('MIN_TRADE_PRICE', '1000')))
MAX_SPREAD_PCT = max(0.05, float(os.getenv('MAX_SPREAD_PCT', '0.25')))
MIN_INTRADAY_RANGE_PCT = max(0.0, float(os.getenv('MIN_INTRADAY_RANGE_PCT', '0.50')))
QUOTE_MAX_AGE_SEC = max(60, int(os.getenv('QUOTE_MAX_AGE_SEC', '180')))
PRIORITY_POLL_SEC = max(1.0, float(os.getenv('PRIORITY_POLL_SEC', '2.0')))
UNIVERSE_REFRESH_SEC = max(3600, int(os.getenv('UNIVERSE_REFRESH_SEC', str(3 * 3600))))

PROTECTED_CODES = {'068270'}
PROTECTED_CODES.update(x.strip() for x in os.getenv('PROTECTED_CODES', '').split(',') if x.strip())
MANUAL_EXCLUDED_CODES = {x.strip() for x in os.getenv('EXCLUDED_CODES', '').split(',') if x.strip()}

# Only used while the official master is loading. Entries stay fail-closed until verified.
FALLBACK_UNIVERSE = [
    '005930', '000660', '035420', '035720', '051910', '207940', '005380', '000270',
    '105560', '055550', '005490', '012450', '028260', '066570', '003670', '096770',
    '034020', '329180', '042700', '086790',
]

_API_LOCK = threading.Lock()
_LAST_API_CALL = 0.0


def nh_call(path, payload):
    """One process-wide NH REST rate gate shared by quote and backfill calls."""
    global _LAST_API_CALL
    with _API_LOCK:
        wait = API_MIN_INTERVAL - (time.monotonic() - _LAST_API_CALL)
        if wait > 0:
            time.sleep(wait)
        try:
            return call(path, payload)
        finally:
            _LAST_API_CALL = time.monotonic()


def _conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    return conn


def init_db():
    with _conn() as conn:
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS quote_samples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL,
            name TEXT,
            sampled_at TEXT NOT NULL,
            trade_time TEXT,
            price REAL NOT NULL,
            ask1 REAL,
            bid1 REAL,
            cumulative_volume INTEGER,
            day_open REAL,
            day_high REAL,
            day_low REAL,
            change_rate REAL,
            total_ask_qty INTEGER,
            total_bid_qty INTEGER,
            scoring REAL
        );
        CREATE INDEX IF NOT EXISTS idx_quote_code_time ON quote_samples(code, sampled_at);
        CREATE TABLE IF NOT EXISTS bars_5m (
            code TEXT NOT NULL,
            bucket TEXT NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume INTEGER NOT NULL DEFAULT 0,
            sample_count INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY(code, bucket)
        );
        CREATE INDEX IF NOT EXISTS idx_bars_code_bucket ON bars_5m(code, bucket);
        ''')


def _bucket_5m(dt):
    minute = (dt.minute // 5) * 5
    return dt.replace(minute=minute, second=0, microsecond=0)


def _extract(payload):
    root = payload.get('Output_0') or payload.get('output_0') or payload
    if not isinstance(root, dict):
        raise ValueError('NH currentPrice 응답에서 Output_0을 찾을 수 없습니다.')
    aux = payload.get('Output_2') or payload.get('output_2') or {}
    return root, aux if isinstance(aux, dict) else {}


def _clean_name(value):
    text = str(value or '').strip()
    return text[1:].strip() if text[:1] in ('*', '#') else text


def _txt(row, key):
    return str(row.get(key) or '').strip()


def _num(row, key):
    try:
        return float(str(row.get(key) or '0').replace(',', '').strip() or 0)
    except (TypeError, ValueError):
        return 0.0


def _preferred_or_special_name(name):
    if not name:
        return True
    if '스팩' in name or '우선주' in name:
        return True
    if re.search(r'(?:\d)?우(?:B|C)?$', name):
        return True
    return False


def _risk_reasons(row):
    """Hard exclusions for an unattended day-trading candidate universe."""
    reasons = []
    if _txt(row, 'eUnder').upper() == 'Y': reasons.append('management')
    if _txt(row, 'eStop').upper() == 'Y': reasons.append('trading_stop')
    if _txt(row, 'sltr_yn').upper() == 'Y': reasons.append('liquidation')
    if _txt(row, 'eWarn').upper() == 'Y': reasons.append('investment_caution')
    if _txt(row, 'invt_epmd_issu_yn').upper() == 'Y': reasons.append('investment_attention')
    if _txt(row, 'eGongsi').upper() == 'Y': reasons.append('unfaithful_disclosure')
    if _txt(row, 'short_over_issu_cls_code') not in ('', '0'): reasons.append('short_overheat')
    if _txt(row, 'alert_gb') not in ('', '0'): reasons.append('market_warning')
    # Price discontinuities/new listings are excluded while the flag is active.
    if _txt(row, 'eRights') not in ('', '0'): reasons.append('rights_or_new')
    return reasons


def _master_records():
    if load_master is None:
        raise RuntimeError('현재 nhplug SDK에서 종목마스터 모듈을 불러올 수 없습니다.')
    raw = load_master('m_new_stock')
    if hasattr(raw, 'to_dict'):
        return raw.to_dict('records')
    return list(raw or [])


def _build_safe_universe():
    records = _master_records()
    excluded = {}
    eligible = []
    for row in records:
        code = _txt(row, 'sCode')
        market = _txt(row, 'sMarket')
        name = _clean_name(row.get('sKorName'))
        if len(code) != 6 or not code.isdigit() or market not in ('1', '4'):
            continue
        if code in PROTECTED_CODES:
            excluded['protected'] = excluded.get('protected', 0) + 1
            continue
        if code in MANUAL_EXCLUDED_CODES:
            excluded['manual'] = excluded.get('manual', 0) + 1
            continue
        reasons = _risk_reasons(row)
        if reasons:
            for reason in reasons:
                excluded[reason] = excluded.get(reason, 0) + 1
            continue
        if _preferred_or_special_name(name):
            excluded['preferred_or_spac'] = excluded.get('preferred_or_spac', 0) + 1
            continue
        # KOSPI type codes: 5 investment company, 6 REIT, 8 ETF, 9 ship fund, A infra fund.
        if market == '1' and _txt(row, 'gVenture').upper() in {'5', '6', '8', '9', 'A'}:
            excluded['non_common_product'] = excluded.get('non_common_product', 0) + 1
            continue
        market_cap = _num(row, 'prdy_avls')
        if market_cap < MIN_MARKET_CAP_EOK:
            excluded['small_market_cap'] = excluded.get('small_market_cap', 0) + 1
            continue
        eligible.append({
            'code': code,
            'name': name or code,
            'market': 'KOSPI' if market == '1' else 'KOSDAQ',
            'marketCapEok': market_cap,
        })

    # Keep KOSDAQ meaningfully represented: stock-in-play opportunities are not only KOSPI mega caps.
    kospi = sorted((x for x in eligible if x['market'] == 'KOSPI'), key=lambda x: x['marketCapEok'], reverse=True)
    kosdaq = sorted((x for x in eligible if x['market'] == 'KOSDAQ'), key=lambda x: x['marketCapEok'], reverse=True)
    q_count = round(MASTER_PRESELECT * 0.45)
    p_count = MASTER_PRESELECT - q_count
    selected = kospi[:p_count] + kosdaq[:q_count]
    if len(selected) < MASTER_PRESELECT:
        used = {x['code'] for x in selected}
        remainder = sorted((x for x in eligible if x['code'] not in used), key=lambda x: x['marketCapEok'], reverse=True)
        selected.extend(remainder[:MASTER_PRESELECT - len(selected)])
    selected = selected[:MASTER_PRESELECT]
    return records, eligible, selected, excluded


_MASTER_META = {}
_UNIVERSE_STATUS = {
    'verified': False,
    'masterRows': 0,
    'eligibleRows': 0,
    'selectedRows': len(FALLBACK_UNIVERSE),
    'focusSize': FOCUS_SIZE,
    'loadedAt': None,
    'lastError': None,
    'excluded': {},
}


def instrument_meta(code):
    return dict(_MASTER_META.get(str(code), {}))


def universe_status():
    return dict(_UNIVERSE_STATUS, excluded=dict(_UNIVERSE_STATUS.get('excluded') or {}))


def universe_verified():
    return bool(_UNIVERSE_STATUS.get('verified'))


def is_safe_code(code):
    return universe_verified() and str(code) in _MASTER_META and str(code) not in PROTECTED_CODES and str(code) not in MANUAL_EXCLUDED_CODES


def save_quote(code, payload):
    q, aux = _extract(payload)
    now = datetime.now(KST)
    price = float(q.get('stck_prpr') or 0)
    if price <= 0:
        raise ValueError(f'{code} 현재가가 0 이하입니다.')
    cum_vol = int(q.get('acml_vol') or 0)
    meta_name = instrument_meta(code).get('name')
    with _conn() as conn:
        prev = conn.execute('SELECT cumulative_volume FROM quote_samples WHERE code=? ORDER BY id DESC LIMIT 1', (code,)).fetchone()
        vol_delta = 0
        if prev is not None and prev['cumulative_volume'] is not None:
            prev_vol = int(prev['cumulative_volume'])
            if cum_vol >= prev_vol:
                vol_delta = cum_vol - prev_vol
        conn.execute('''INSERT INTO quote_samples(
                code,name,sampled_at,trade_time,price,ask1,bid1,cumulative_volume,
                day_open,day_high,day_low,change_rate,total_ask_qty,total_bid_qty,scoring
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (
                code, q.get('iem_nm') or meta_name, now.isoformat(), q.get('hoga_bsop_hour'), price,
                float(q.get('askp1') or q.get('askp') or 0), float(q.get('bidp1') or q.get('bidp') or 0),
                cum_vol, float(q.get('stck_oprc') or 0), float(q.get('stck_hgpr') or 0),
                float(q.get('stck_lwpr') or 0), float(q.get('prdy_ctrt') or 0),
                int(q.get('total_askp_rsqn') or 0), int(q.get('total_bidp_rsqn') or 0), float(aux.get('scoring') or 0),
            ))
        bucket = _bucket_5m(now).isoformat()
        row = conn.execute('SELECT * FROM bars_5m WHERE code=? AND bucket=?', (code, bucket)).fetchone()
        if row is None:
            conn.execute('INSERT INTO bars_5m(code,bucket,open,high,low,close,volume,sample_count) VALUES(?,?,?,?,?,?,?,1)',
                         (code, bucket, price, price, price, price, vol_delta))
        else:
            conn.execute('''UPDATE bars_5m SET high=?, low=?, close=?, volume=?, sample_count=sample_count+1
                            WHERE code=? AND bucket=?''',
                         (max(float(row['high']), price), min(float(row['low']), price), price,
                          int(row['volume']) + vol_delta, code, bucket))


def fetch_and_store(code):
    data = nh_call('/krstock/quote/v1/currentPrice', {'iem_cd': code, 'market_cd': 'KRX'})
    save_quote(code, data)
    return data


def _turnover_floor_eok(now=None):
    now = now or datetime.now(KST)
    minutes = max(0, min(390, (now.hour * 60 + now.minute) - 9 * 60))
    return round(max(2.0, min(30.0, 2.0 + minutes * 0.08)), 2)


def activity_metrics(quote):
    price = float(quote.get('price') or 0)
    cum_vol = max(0, int(quote.get('cumulative_volume') or 0))
    op = float(quote.get('day_open') or 0)
    hi = float(quote.get('day_high') or 0)
    lo = float(quote.get('day_low') or 0)
    ask = float(quote.get('ask1') or 0)
    bid = float(quote.get('bid1') or 0)
    change = float(quote.get('change_rate') or 0)
    turnover = price * cum_vol / 100_000_000 if price > 0 else 0.0
    range_pct = ((hi - lo) / op * 100) if op > 0 and hi >= lo > 0 else 0.0
    spread_pct = ((ask - bid) / price * 100) if price > 0 and ask > 0 and bid > 0 and ask >= bid else 0.0
    turnover_score = min(45.0, math.log10(1.0 + max(0.0, turnover)) * 15.0)
    range_score = min(20.0, max(0.0, range_pct) * 3.0)
    change_score = min(20.0, abs(change) * 2.0)
    spread_score = 10.0 if spread_pct <= 0 else max(0.0, 10.0 - spread_pct * 40.0)
    activity_score = round(min(100.0, turnover_score + range_score + change_score + spread_score + 5.0), 2)
    floor = _turnover_floor_eok()
    liquidity_ok = (
        price >= MIN_PRICE
        and turnover >= floor
        and range_pct >= MIN_INTRADAY_RANGE_PCT
        and (spread_pct <= 0 or spread_pct <= MAX_SPREAD_PCT)
    )
    return {
        'activityScore': activity_score,
        'turnoverEok': round(turnover, 2),
        'turnoverFloorEok': floor,
        'rangePct': round(range_pct, 3),
        'spreadPct': round(spread_pct, 4),
        'changeRate': change,
        'liquidityOk': liquidity_ok,
    }


def latest_quotes(codes=None):
    init_db()
    params = []
    where = ''
    if codes:
        codes = list(dict.fromkeys(str(x) for x in codes))
        placeholders = ','.join('?' for _ in codes)
        where = f'WHERE q.code IN ({placeholders})'
        params.extend(codes)
    sql = f'''
        SELECT q.* FROM quote_samples q
        JOIN (SELECT code, MAX(id) AS max_id FROM quote_samples GROUP BY code) x
          ON q.code=x.code AND q.id=x.max_id
        {where}
        ORDER BY q.code
    '''
    with _conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _fresh(quote):
    try:
        dt = datetime.fromisoformat(str(quote.get('sampled_at'))).astimezone(KST)
        return 0 <= (datetime.now(KST) - dt).total_seconds() <= QUOTE_MAX_AGE_SEC
    except Exception:
        return False


def candidate_meta(code):
    rows = latest_quotes([code])
    if not rows:
        return {'code': str(code), 'name': instrument_meta(code).get('name') or str(code), 'liquidityOk': False}
    quote = rows[0]
    return {**quote, **instrument_meta(code), **activity_metrics(quote), 'fresh': _fresh(quote)}


def active_candidates(limit=FOCUS_SIZE):
    limit = max(1, min(int(limit), 100))
    codes = collector.watchlist if 'collector' in globals() else FALLBACK_UNIVERSE
    rows = latest_quotes(codes)
    out = []
    for q in rows:
        if not _fresh(q):
            continue
        code = q['code']
        if universe_verified() and not is_safe_code(code):
            continue
        out.append({**q, **instrument_meta(code), **activity_metrics(q), 'fresh': True})
    out.sort(key=lambda x: (bool(x.get('liquidityOk')), float(x.get('activityScore') or 0), float(x.get('turnoverEok') or 0)), reverse=True)
    return out[:limit]


def bars(code, limit=120):
    init_db()
    limit = max(1, min(int(limit), 1000))
    with _conn() as conn:
        rows = conn.execute('SELECT * FROM bars_5m WHERE code=? ORDER BY bucket DESC LIMIT ?', (code, limit)).fetchall()
    return [dict(r) for r in reversed(rows)]


class MarketCollector:
    def __init__(self):
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._universe_ready = threading.Event()
        self.watchlist = list(FALLBACK_UNIVERSE)
        self.priority_codes = set()
        self.started_at = None
        self.last_cycle_at = None
        self.last_success_at = None
        self.last_error = None
        self.samples = 0
        self.cycles = 0
        self._last_priority_poll = 0.0
        self._last_universe_refresh = 0.0
        init_db()

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def refresh_universe(self):
        global _MASTER_META, _UNIVERSE_STATUS
        try:
            records, eligible, selected, excluded = _build_safe_universe()
            meta = {x['code']: x for x in selected}
            with self._lock:
                self.watchlist = [x['code'] for x in selected]
            _MASTER_META = meta
            _UNIVERSE_STATUS = {
                'verified': True,
                'masterRows': len(records),
                'eligibleRows': len(eligible),
                'selectedRows': len(selected),
                'focusSize': FOCUS_SIZE,
                'loadedAt': datetime.now(KST).isoformat(),
                'lastError': None,
                'excluded': excluded,
            }
            self._last_universe_refresh = time.monotonic()
            self._universe_ready.set()
            return universe_status()
        except Exception as exc:
            # If a previously verified master exists, keep it. Initial failures remain fail-closed.
            _UNIVERSE_STATUS['lastError'] = f'{type(exc).__name__}: {exc}'[:500]
            _UNIVERSE_STATUS['loadedAt'] = datetime.now(KST).isoformat()
            self._last_universe_refresh = time.monotonic()
            self._universe_ready.set()
            return universe_status()

    def wait_for_universe(self, timeout=20):
        self._universe_ready.wait(max(0.0, float(timeout)))
        return universe_status()

    def set_priority_codes(self, codes):
        clean = {str(x).strip() for x in (codes or []) if str(x).strip()}
        with self._lock:
            self.priority_codes = clean.intersection(set(self.watchlist))

    def start(self, codes=None):
        with self._lock:
            if self.running:
                return self.status()
            if codes:
                clean = []
                for code in codes:
                    code = str(code).strip()
                    if len(code) == 6 and code.isdigit() and code not in clean:
                        clean.append(code)
                if clean:
                    self.watchlist = clean[:MASTER_PRESELECT]
            self._stop.clear()
            self.started_at = datetime.now(KST).isoformat()
            self.last_error = None
            self._thread = threading.Thread(target=self._run, name='nh-market-collector', daemon=True)
            self._thread.start()
            return self.status()

    def stop(self):
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        return self.status()

    def _poll_priority(self):
        now_mono = time.monotonic()
        if now_mono - self._last_priority_poll < PRIORITY_POLL_SEC:
            return
        with self._lock:
            priority = list(self.priority_codes)
        for code in priority:
            if self._stop.is_set():
                break
            try:
                fetch_and_store(code)
                self.samples += 1
                self.last_success_at = datetime.now(KST).isoformat()
            except Exception as exc:
                self.last_error = f'{type(exc).__name__}: {exc}'[:500]
        self._last_priority_poll = time.monotonic()

    def _run(self):
        # Master verification is deliberately inside the background thread so the UI boots fast.
        self.refresh_universe()
        while not self._stop.is_set():
            cycle_started = time.monotonic()
            self.last_cycle_at = datetime.now(KST).isoformat()
            if time.monotonic() - self._last_universe_refresh >= UNIVERSE_REFRESH_SEC:
                self.refresh_universe()
            with self._lock:
                codes = list(self.watchlist)
            for code in codes:
                if self._stop.is_set():
                    break
                self._poll_priority()
                try:
                    fetch_and_store(code)
                    self.samples += 1
                    self.last_success_at = datetime.now(KST).isoformat()
                    self.last_error = None
                except Exception as exc:
                    self.last_error = f'{type(exc).__name__}: {exc}'[:500]
            self.cycles += 1
            elapsed = time.monotonic() - cycle_started
            self._stop.wait(max(0.2, 1.0 - elapsed))

    def status(self):
        u = universe_status()
        with self._lock:
            watch = list(self.watchlist)
            priority = sorted(self.priority_codes)
        return {
            'running': self.running,
            'watchlist': watch,
            'universeSize': len(watch),
            'focusSize': FOCUS_SIZE,
            'safetyVerified': bool(u.get('verified')),
            'universe': u,
            'priorityCodes': priority,
            'apiMinInterval': API_MIN_INTERVAL,
            'startedAt': self.started_at,
            'lastCycleAt': self.last_cycle_at,
            'lastSuccessAt': self.last_success_at,
            'lastError': self.last_error,
            'samples': self.samples,
            'cycles': self.cycles,
            'database': os.path.basename(DB_PATH),
            'protectedCodes': sorted(PROTECTED_CODES),
            'manualExcludedCodes': sorted(MANUAL_EXCLUDED_CODES),
        }


collector = MarketCollector()
