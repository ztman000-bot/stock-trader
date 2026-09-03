"""Windowless Stock Day Trader bootstrap.

This is the canonical automatic launcher for Windows. It starts the watchdog and
localhost uvicorn directly from Python, so Task Scheduler never needs to launch a
visible cmd.exe or PowerShell window. Trading strategy and order settings are not
changed here.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
PYTHON = BASE / ".venv" / "Scripts" / "python.exe"
PYTHONW = BASE / ".venv" / "Scripts" / "pythonw.exe"
WATCHDOG = BASE / "watchdog.py"
HEALTH_PROBE = BASE / "health_probe.py"
LOG = Path(tempfile.gettempdir()) / "stock_trader_bootstrap.log"
UPDATE_FLAG = Path(tempfile.gettempdir()) / "stock_trader_update_in_progress.flag"
ROLE_DIR = Path(os.getenv("LOCALAPPDATA") or tempfile.gettempdir()) / "StockTrader"
PAUSE_FLAG = ROLE_DIR / "laptop_server_paused.flag"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


def log(msg: str) -> None:
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().isoformat(timespec='seconds')}] {msg}\n")
    except Exception:
        pass


def single_bootstrap() -> bool:
    if os.name != "nt":
        return True
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, "Global\\StockTraderBootstrap_v0178")
    if not handle:
        return False
    return kernel32.GetLastError() != 183


def healthy(timeout: float = 3.0) -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=timeout) as r:
            body = r.read(512).decode("utf-8", "ignore").replace(" ", "").lower()
            return r.status == 200 and '"ok":true' in body
    except Exception:
        return False


def _popen_hidden(args, *, env=None, stdout=None, stderr=None):
    flags = (CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP) if os.name == "nt" else 0
    return subprocess.Popen(
        [str(x) for x in args], cwd=str(BASE), env=env,
        stdin=subprocess.DEVNULL,
        stdout=stdout if stdout is not None else subprocess.DEVNULL,
        stderr=stderr if stderr is not None else subprocess.DEVNULL,
        creationflags=flags, close_fds=True,
    )


def ensure_watchdog() -> None:
    if os.getenv("STOCK_TRADER_SKIP_WATCHDOG") == "1" or PAUSE_FLAG.exists():
        return
    if not PYTHONW.exists() or not WATCHDOG.exists():
        return
    try:
        _popen_hidden([PYTHONW, WATCHDOG])
    except Exception as exc:
        log(f"watchdog launch failed: {type(exc).__name__}: {exc}")


def start_server() -> bool:
    if PAUSE_FLAG.exists():
        log("laptop server role paused; bootstrap skipped")
        return True
    if healthy(1.5):
        log("server already healthy")
        return True
    if not PYTHONW.exists():
        log("ERROR pythonw.exe missing")
        return False

    env = os.environ.copy()
    env.setdefault("AUTO_BACKFILL", "false")
    env.setdefault("MASTER_PRESELECT", "180")
    env.setdefault("FOCUS_SIZE", "40")
    env.setdefault("MIN_MARKET_CAP_EOK", "500")
    env.setdefault("MIN_TRADE_PRICE", "1000")
    env.setdefault("MAX_SPREAD_PCT", "0.25")
    env.setdefault("MIN_INTRADAY_RANGE_PCT", "0.50")

    runlog = Path(tempfile.gettempdir()) / f"stock_trader_server_{os.getpid()}_{int(time.time())}.log"
    try:
        with runlog.open("a", encoding="utf-8") as out:
            _popen_hidden(
                [PYTHONW, "-m", "uvicorn", "unified_app:app", "--host", "127.0.0.1", "--port", "8000"],
                env=env, stdout=out, stderr=subprocess.STDOUT,
            )
        log(f"uvicorn launched windowless log={runlog}")
    except Exception as exc:
        log(f"ERROR uvicorn launch: {type(exc).__name__}: {exc}")
        return False

    for _ in range(45):
        if healthy(1.5):
            log("server ONLINE windowless")
            return True
        time.sleep(1)
    log(f"ERROR health timeout log={runlog}")
    return False


def best_effort_backfill() -> None:
    if PAUSE_FLAG.exists() or not PYTHON.exists() or not HEALTH_PROBE.exists():
        return
    try:
        subprocess.run(
            [str(PYTHON), str(HEALTH_PROBE), "backfill-if-safe"], cwd=str(BASE),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=305,
            creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except Exception:
        pass


def main() -> int:
    if os.name != "nt":
        return 0
    if PAUSE_FLAG.exists():
        log("laptop server role paused; autostart ignored")
        return 0
    if not single_bootstrap():
        return 0
    ensure_watchdog()
    ok = start_server()
    if ok and not UPDATE_FLAG.exists():
        best_effort_backfill()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
