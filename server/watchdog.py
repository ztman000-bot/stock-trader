"""Windows watchdog for Stock Day Trader v0.17.8.

Runs outside FastAPI, checks localhost health, and restarts the server after
repeated local health failures. It pauses while the atomic update flag exists.
No trading logic lives here.
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
START_CMD = BASE / 'start_stock_trader_background.cmd'
LOG = Path(tempfile.gettempdir()) / 'stock_trader_watchdog.log'
UPDATE_FLAG = Path(tempfile.gettempdir()) / 'stock_trader_update_in_progress.flag'
CHECK_SEC = max(10, int(os.getenv('STOCK_TRADER_WATCHDOG_CHECK_SEC', '20')))
FAILURES_BEFORE_RESTART = max(2, int(os.getenv('STOCK_TRADER_WATCHDOG_FAILURES', '3')))
COOLDOWN_SEC = max(30, int(os.getenv('STOCK_TRADER_WATCHDOG_COOLDOWN_SEC', '45')))


def log(msg):
    try:
        with LOG.open('a', encoding='utf-8') as f:
            f.write(f'[{datetime.now().isoformat(timespec="seconds")}] {msg}\n')
    except Exception:
        pass


def single_instance():
    if os.name != 'nt':
        return True
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, 'Global\\StockTraderWatchdog_v0178')
    if not handle:
        return False
    if kernel32.GetLastError() == 183:
        return False
    return True


def healthy():
    try:
        with urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3) as r:
            if r.status != 200:
                return False
            body = r.read(512).decode('utf-8', 'ignore')
            return '"ok":true' in body.replace(' ', '').lower()
    except Exception:
        return False


def kill_port_8000():
    if os.name != 'nt':
        return
    try:
        out = subprocess.check_output(['netstat', '-ano'], text=True, errors='ignore')
        pids = set()
        for line in out.splitlines():
            if ':8000' in line and 'LISTENING' in line.upper():
                parts = line.split()
                if parts and parts[-1].isdigit():
                    pids.add(parts[-1])
        for pid in pids:
            subprocess.run(['taskkill', '/PID', pid, '/F'], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=10)
    except Exception as exc:
        log(f'kill port failed: {type(exc).__name__}: {exc}')


def restart_server():
    if UPDATE_FLAG.exists():
        log('restart deferred: update in progress')
        return False
    log('health failed repeatedly; restarting localhost server')
    kill_port_8000()
    env = os.environ.copy()
    env['STOCK_TRADER_SKIP_WATCHDOG'] = '1'
    try:
        subprocess.run(['cmd.exe', '/d', '/c', 'call', str(START_CMD)], cwd=str(BASE), env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=50)
    except Exception as exc:
        log(f'restart command error: {type(exc).__name__}: {exc}')
    ok = False
    for _ in range(15):
        if healthy():
            ok = True
            break
        time.sleep(2)
    log('restart result: ' + ('HEALTHY' if ok else 'FAILED'))
    return ok


def main():
    if os.name != 'nt':
        return 0
    if not single_instance():
        return 0
    log(f'watchdog started check={CHECK_SEC}s threshold={FAILURES_BEFORE_RESTART}')
    failures = 0
    last_restart = 0.0
    while True:
        if UPDATE_FLAG.exists():
            failures = 0
            time.sleep(CHECK_SEC)
            continue
        if healthy():
            failures = 0
        else:
            failures += 1
            log(f'health miss {failures}/{FAILURES_BEFORE_RESTART}')
            if failures >= FAILURES_BEFORE_RESTART and time.time() - last_restart >= COOLDOWN_SEC:
                restart_server()
                last_restart = time.time()
                failures = 0
        time.sleep(CHECK_SEC)


if __name__ == '__main__':
    sys.exit(main())
