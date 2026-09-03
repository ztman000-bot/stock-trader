"""Small localhost health/maintenance helper with no PowerShell dependency."""
from __future__ import annotations

import json
import sys
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

BASE = "http://127.0.0.1:8000"
KST = ZoneInfo("Asia/Seoul")


def _json(path: str, timeout: float = 4.0, method: str = "GET"):
    req = urllib.request.Request(BASE + path, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        if r.status != 200:
            raise RuntimeError(f"HTTP {r.status}")
        return json.loads(r.read().decode("utf-8"))


def health() -> bool:
    try:
        return bool(_json("/api/health", 3).get("ok"))
    except Exception:
        return False


def full() -> bool:
    try:
        h = _json("/api/health", 4)
        u = _json("/api/system/ui-health", 4)
        return bool(h.get("ok") and u.get("ok"))
    except Exception:
        return False


def runtime() -> bool:
    try:
        h = _json("/api/health", 4)
        u = _json("/api/system/ui-health", 4)
        r = _json("/api/system/runtime-health", 4)
        return bool(h.get("ok") and u.get("ok") and r.get("ok"))
    except Exception:
        return False


def backfill_if_safe() -> bool:
    now = datetime.now(KST)
    minute = now.hour * 60 + now.minute
    if now.weekday() < 5 and 540 <= minute <= 930:
        return True
    try:
        _json("/api/market/backfill", 300, method="POST")
        return True
    except Exception:
        # Startup backfill is best-effort research work; never fail server startup.
        return True


def main() -> int:
    mode = (sys.argv[1] if len(sys.argv) > 1 else "health").lower()
    if mode == "health":
        return 0 if health() else 1
    if mode == "full":
        return 0 if full() else 1
    if mode == "runtime":
        return 0 if runtime() else 1
    if mode in ("backfill", "backfill-if-safe"):
        return 0 if backfill_if_safe() else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
