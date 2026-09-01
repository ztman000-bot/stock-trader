"""US market-data collector for Stock Day Trader v0.17.4.

Safety boundary:
- Market/research data only. No overseas order endpoint is called here.
- KR and US databases/strategy statistics remain independent.
- Official NH current endpoint: /gbstock/quote/v1/current.
- Shared overseas REST throttle is exported as us_call() for research modules.
"""
import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from nhplug import call

BASE_DIR = Path(__file__).resolve().parent
US_DB_PATH = Path(os.getenv('US_MARKET_DB_PATH', str(BASE_DIR / 'us_market_data.db')))
US_WATCHLIST = [x.strip().upper() for x in os.getenv(
    'US_WATCHLIST',
    'AAPL,NVDA,MSFT,AMD,AMZN,META,GOOGL,TSLA'
).split(',') if x.strip()]
US_POLL_SEC = max(2.0, float(os.getenv('US_POLL_SEC', '3.0')))
US_CLOSED_POLL_SEC = max(60.0, float(os.getenv('US_CLOSED_POLL_SEC', '300')))
US_REST_MIN_INTERVAL = max(0.20, float(os.getenv('US_REST_MIN_INTERVAL', '0.30')))
US_DATA_ENABLED = os.getenv('US_DATA_ENABLED', 'true').lower() == 'true'
US_PAPER_ENABLED = False
US_REAL_ORDER_ENABLED = False
NY = ZoneInfo('America/New_York')

_API_LOCK = threading.Lock()
_LAST_API_CALL = 0.0


def us_regular_session(now=None):
    """DST-aware US regular session approximation. Exchange holidays safely return empty data."""
    n = (now or datetime.now(timezone.utc)).astimezone(NY)
    hm = n.hour * 60 + n.minute
    return n.weekday() < 5 and 570 <= hm < 960  # 09:30-16:00 ET


def us_call(path, payload):
    """Serialize overseas REST calls so current quotes and research never burst the NH limit."""
    global _LAST_API_CALL
    with _API_LOCK:
        wait = US_REST_MIN_INTERVAL - (time.monotonic() - _LAST_API_CALL)
        if wait > 0:
            time.sleep(wait)
        try:
            return call(path, payload)
        finally:
            _LAST_API_CALL = time.monotonic()


def _conn():
    c = sqlite3.connect(US_DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    return c


def init_us_db():
    with _conn() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS us_quote_samples(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          ticker TEXT NOT NULL,
          sampled_at TEXT NOT NULL,
          price REAL,
          raw_json TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_us_quote_ticker_time
          ON us_quote_samples(ticker, sampled_at DESC);
        ''')


def _num(value):
    try:
        if value is None or value == '':
            return None
        return float(str(value).replace(',', '').strip())
    except (TypeError, ValueError):
        return None


def _output0(data):
    if not isinstance(data, dict):
        return {}
    out = data.get('Output_0') or data.get('output_0') or {}
    if isinstance(out, list):
        return out[0] if out and isinstance(out[0], dict) else {}
    return out if isinstance(out, dict) else {}


def fetch_current(ticker):
    ticker = ticker.strip().upper()
    data = us_call('/gbstock/quote/v1/current', {'iem_cd': ticker})
    out = _output0(data)
    price = _num(out.get('trdprc'))
    if price is None or price <= 0:
        for key in ('ovrs_prpr', 'pf_trdprc'):
            candidate = _num(out.get(key))
            if candidate is not None and candidate > 0:
                price = candidate
                break
    return {
        'ticker': ticker,
        'price': price,
        'name': out.get('kor_name') or out.get('iem_eng_nm') or out.get('iem_nm') or out.get('name'),
        'change': _num(out.get('netchng')),
        'changeRate': _num(out.get('pctchng') or out.get('netchng_rate')),
        'volume': _num(out.get('acvol') or out.get('vol')),
        'businessDate': out.get('bsop_date'),
        'data': data,
    }


class USCollector:
    def __init__(self):
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.RLock()
        self._status = {
            'running': False, 'enabled': US_DATA_ENABLED,
            'lastCycleAt': None, 'lastSuccessAt': None,
            'lastError': None, 'samples': 0, 'pricedSamples': 0,
            'regularSession': False, 'paperEnabled': False, 'realOrderEnabled': False,
            'restMinIntervalSec': US_REST_MIN_INTERVAL,
        }

    def status(self):
        with self._lock:
            return {**self._status, 'regularSession': us_regular_session(),
                    'watchlist': list(US_WATCHLIST), 'db': US_DB_PATH.name}

    def start(self):
        if not US_DATA_ENABLED:
            return self.status()
        with self._lock:
            if self._thread and self._thread.is_alive():
                return self.status()
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name='us-market-collector', daemon=True)
            self._thread.start()
        return self.status()

    def stop(self):
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        return self.status()

    def _run(self):
        with self._lock:
            self._status['running'] = True
        while not self._stop.is_set():
            cycle_ok = False
            live = us_regular_session()
            for ticker in US_WATCHLIST:
                if self._stop.is_set():
                    break
                try:
                    q = fetch_current(ticker)
                    now = datetime.now(timezone.utc).isoformat()
                    with _conn() as c:
                        c.execute(
                            'INSERT INTO us_quote_samples(ticker,sampled_at,price,raw_json) VALUES(?,?,?,?)',
                            (ticker, now, q['price'], json.dumps(q['data'], ensure_ascii=False)[:20000])
                        )
                    with self._lock:
                        self._status['samples'] += 1
                        if q['price'] is not None:
                            self._status['pricedSamples'] += 1
                        self._status['lastSuccessAt'] = now
                        self._status['lastError'] = None if q['price'] is not None else f'{ticker}: current-price field is empty'
                    cycle_ok = True
                except Exception as exc:
                    with self._lock:
                        self._status['lastError'] = f'{type(exc).__name__}: {exc}'[:500]
            with self._lock:
                self._status['lastCycleAt'] = datetime.now(timezone.utc).isoformat()
                self._status['regularSession'] = live
            delay = US_POLL_SEC if live and cycle_ok else US_CLOSED_POLL_SEC
            self._stop.wait(delay)
        with self._lock:
            self._status['running'] = False


def latest_us_quotes():
    init_us_db()
    out = []
    with _conn() as c:
        for ticker in US_WATCHLIST:
            r = c.execute(
                '''SELECT ticker,sampled_at,price FROM us_quote_samples
                   WHERE ticker=? AND price IS NOT NULL AND price>0
                   ORDER BY id DESC LIMIT 1''', (ticker,)
            ).fetchone()
            if not r:
                r = c.execute(
                    'SELECT ticker,sampled_at,price FROM us_quote_samples WHERE ticker=? ORDER BY id DESC LIMIT 1',
                    (ticker,)
                ).fetchone()
            if r:
                out.append(dict(r))
    return out


init_us_db()
us_collector = USCollector()
