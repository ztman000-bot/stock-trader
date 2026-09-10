"""Android daemon for research-only Shadow continuation.

Runs outside uvicorn so it cannot block API responsiveness. Entry signals are
read only from the local Stock Trader API; exits use the shared SQLite/local
quote cache through shadow_continuation. No broker order endpoint is called.
"""
from __future__ import annotations

import json
import signal
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

from collector import KST
from shadow_continuation import (
    SCAN_SEC,
    _entry_hours,
    _eod_due,
    force_close_all,
    mark_positions,
    observe_scan,
    report,
)

LOCAL_SCAN_URL = 'http://127.0.0.1:8000/api/paper/scan'
OUT = Path(__file__).resolve().parent / 'shadow_continuation_report.json'
_STOP = threading.Event()


def _stop(*_):
    _STOP.set()


def _scan_rows():
    req = Request(LOCAL_SCAN_URL, headers={'Accept': 'application/json', 'User-Agent': 'stock-trader-shadow-research'})
    with urlopen(req, timeout=6) as r:
        payload = json.loads(r.read().decode('utf-8'))
    rows = payload.get('rows') if isinstance(payload, dict) else None
    return rows if isinstance(rows, list) else []


def _publish_report():
    data = report(limit=50)
    data['publishedAt'] = datetime.now(KST).isoformat(timespec='seconds')
    tmp = OUT.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    tmp.replace(OUT)


def main():
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    last_scan = 0.0
    last_publish = 0.0
    while not _STOP.is_set():
        try:
            now = datetime.now(KST)
            mark_positions(now)
            if _eod_due(now):
                force_close_all('EOD_EXIT', now)
            elif _entry_hours(now) and time.monotonic() - last_scan >= SCAN_SEC:
                rows = _scan_rows()
                observe_scan(rows, now)
                last_scan = time.monotonic()
            if time.monotonic() - last_publish >= 60:
                _publish_report()
                last_publish = time.monotonic()
        except Exception as exc:
            # Server may be restarting during an atomic update. Keep the research
            # process alive and retry; never fall back to broker calls.
            try:
                err = {
                    'ok': False,
                    'researchOnly': True,
                    'controlStrategy': 'v0.8.0 LOCKED',
                    'realOrderEnabled': False,
                    'lastError': f'{type(exc).__name__}: {exc}'[:500],
                    'publishedAt': datetime.now(KST).isoformat(timespec='seconds'),
                }
                tmp = OUT.with_suffix('.tmp')
                tmp.write_text(json.dumps(err, ensure_ascii=False, indent=2), encoding='utf-8')
                tmp.replace(OUT)
            except Exception:
                pass
        _STOP.wait(2.0)
    try:
        _publish_report()
    except Exception:
        pass
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
