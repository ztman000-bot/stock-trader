import os
import sqlite3
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from nhplug import call

KST = ZoneInfo('Asia/Seoul')
DB_PATH = os.getenv('MARKET_DB_PATH', os.path.join(os.path.dirname(__file__), 'market_data.db'))
# Phase-1 dynamic scanner universe: liquid large-cap equities, configurable from .env.
# The UI ranks this live universe and exposes only the best Top 10. Real orders remain locked.
DEFAULT_UNIVERSE = (
    '005930,000660,035420,035720,051910,207940,005380,000270,105560,055550,'
    '005490,012450,028260,066570,003670,096770,034020,329180,042700,086790'
)
DEFAULT_WATCHLIST = [x.strip() for x in os.getenv('WATCHLIST', DEFAULT_UNIVERSE).split(',') if x.strip()]
MAX_WATCHLIST = max(5, min(int(os.getenv('MAX_WATCHLIST', '20')), 40))
POLL_SECONDS = max(1.0, float(os.getenv('POLL_SECONDS', '2.0')))
PROTECTED_CODES = {'068270'}


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


def save_quote(code, payload):
    q, aux = _extract(payload)
    now = datetime.now(KST)
    price = float(q.get('stck_prpr') or 0)
    if price <= 0:
        raise ValueError(f'{code} 현재가가 0 이하입니다.')
    cum_vol = int(q.get('acml_vol') or 0)
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
                code, q.get('iem_nm'), now.isoformat(), q.get('hoga_bsop_hour'), price,
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
    data = call('/krstock/quote/v1/currentPrice', {'iem_cd': code, 'market_cd': 'KRX'})
    save_quote(code, data)
    return data


class MarketCollector:
    def __init__(self):
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.watchlist = list(DEFAULT_WATCHLIST)[:MAX_WATCHLIST]
        self.started_at = None
        self.last_cycle_at = None
        self.last_success_at = None
        self.last_error = None
        self.samples = 0
        self.cycles = 0
        init_db()

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

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
                    self.watchlist = clean[:MAX_WATCHLIST]
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

    def _run(self):
        while not self._stop.is_set():
            cycle_started = time.monotonic()
            self.last_cycle_at = datetime.now(KST).isoformat()
            for code in list(self.watchlist):
                if self._stop.is_set():
                    break
                try:
                    fetch_and_store(code)
                    self.samples += 1
                    self.last_success_at = datetime.now(KST).isoformat()
                    self.last_error = None
                except Exception as exc:
                    self.last_error = f'{type(exc).__name__}: {exc}'[:500]
                # Keep below the documented rough REST call-rate ceiling.
                time.sleep(0.25)
            self.cycles += 1
            elapsed = time.monotonic() - cycle_started
            self._stop.wait(max(0.2, POLL_SECONDS - elapsed))

    def status(self):
        return {
            'running': self.running,
            'watchlist': list(self.watchlist),
            'universeSize': len(self.watchlist),
            'maxWatchlist': MAX_WATCHLIST,
            'pollSeconds': POLL_SECONDS,
            'startedAt': self.started_at,
            'lastCycleAt': self.last_cycle_at,
            'lastSuccessAt': self.last_success_at,
            'lastError': self.last_error,
            'samples': self.samples,
            'cycles': self.cycles,
            'database': os.path.basename(DB_PATH),
            'protectedCodes': sorted(PROTECTED_CODES),
        }


def latest_quotes(codes=None):
    init_db()
    params = []
    where = ''
    if codes:
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


def bars(code, limit=120):
    init_db()
    limit = max(1, min(int(limit), 1000))
    with _conn() as conn:
        rows = conn.execute('SELECT * FROM bars_5m WHERE code=? ORDER BY bucket DESC LIMIT ?', (code, limit)).fetchall()
    return [dict(r) for r in reversed(rows)]


collector = MarketCollector()
