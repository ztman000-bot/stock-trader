"""US market-data foundation for Stock Day Trader v0.15.

Safety boundary:
- Market data only. No overseas order endpoint is called here.
- KR collector/database strategy remains independent.
- Uses official nhplug SDK endpoint /gbstock/quote/v1/current.
"""
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from nhplug import call, NhplugError

BASE_DIR = Path(__file__).resolve().parent
US_DB_PATH = Path(os.getenv('US_MARKET_DB_PATH', str(BASE_DIR / 'us_market_data.db')))
US_WATCHLIST = [x.strip().upper() for x in os.getenv(
    'US_WATCHLIST',
    'AAPL,NVDA,MSFT,AMD,AMZN,META,GOOGL,TSLA'
).split(',') if x.strip()]
US_POLL_SEC = max(2.0, float(os.getenv('US_POLL_SEC', '3.0')))
US_DATA_ENABLED = os.getenv('US_DATA_ENABLED', 'true').lower() == 'true'
US_PAPER_ENABLED = False
US_REAL_ORDER_ENABLED = False


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


def _find_number(obj, preferred):
    if isinstance(obj, dict):
        for key in preferred:
            if key in obj:
                try:
                    v = float(str(obj[key]).replace(',', ''))
                    if v > 0:
                        return v
                except (TypeError, ValueError):
                    pass
        for v in obj.values():
            found = _find_number(v, preferred)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_number(v, preferred)
            if found is not None:
                return found
    return None


def fetch_current(ticker):
    ticker = ticker.strip().upper()
    data = call('/gbstock/quote/v1/current', {'iem_cd': ticker})
    # Field names are deliberately tolerant until the user's production response
    # is observed; the raw response is never exposed with credentials/tokens.
    price = _find_number(data, (
        'last', 'last_pr', 'last_price', 'prpr', 'ovrs_nmix_prpr',
        'stck_prpr', 'close', 'price', 'now_pr'
    ))
    return {'ticker': ticker, 'price': price, 'data': data}


class USCollector:
    def __init__(self):
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.RLock()
        self._status = {
            'running': False, 'enabled': US_DATA_ENABLED,
            'lastCycleAt': None, 'lastSuccessAt': None,
            'lastError': None, 'samples': 0,
            'paperEnabled': False, 'realOrderEnabled': False,
        }

    def status(self):
        with self._lock:
            return {**self._status, 'watchlist': list(US_WATCHLIST), 'db': US_DB_PATH.name}

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
            for ticker in US_WATCHLIST:
                if self._stop.is_set():
                    break
                try:
                    q = fetch_current(ticker)
                    now = datetime.now(timezone.utc).isoformat()
                    import json
                    with _conn() as c:
                        c.execute(
                            'INSERT INTO us_quote_samples(ticker,sampled_at,price,raw_json) VALUES(?,?,?,?)',
                            (ticker, now, q['price'], json.dumps(q['data'], ensure_ascii=False)[:20000])
                        )
                    with self._lock:
                        self._status['samples'] += 1
                        self._status['lastSuccessAt'] = now
                        self._status['lastError'] = None
                    cycle_ok = True
                except Exception as exc:
                    with self._lock:
                        self._status['lastError'] = f'{type(exc).__name__}: {exc}'[:500]
                self._stop.wait(0.25)
            with self._lock:
                self._status['lastCycleAt'] = datetime.now(timezone.utc).isoformat()
            self._stop.wait(US_POLL_SEC if cycle_ok else max(US_POLL_SEC, 10))
        with self._lock:
            self._status['running'] = False


def latest_us_quotes():
    init_us_db()
    out = []
    with _conn() as c:
        for ticker in US_WATCHLIST:
            r = c.execute(
                'SELECT ticker,sampled_at,price FROM us_quote_samples WHERE ticker=? ORDER BY id DESC LIMIT 1',
                (ticker,)
            ).fetchone()
            if r:
                out.append(dict(r))
    return out


init_us_db()
us_collector = USCollector()
