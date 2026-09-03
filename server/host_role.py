"""One-click Windows host role switch for temporary Android server operation.

pause:
- creates a persistent local pause flag under LOCALAPPDATA
- disables the StockTraderAutoStart scheduled task
- stops only the local port-8000 process after verifying it is Stock Trader

resume:
- removes the pause flag
- re-enables/recreates the hidden autostart task if needed
- starts the laptop Stock Trader server and verifies localhost health

No trading strategy, database, NH credentials, or phone server is modified.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent
PYTHON = BASE / ".venv" / "Scripts" / "python.exe"
INSTALLER = BASE / "install_autostart_task.py"
TASK = "StockTraderAutoStart"
ROLE_DIR = Path(os.getenv("LOCALAPPDATA") or tempfile.gettempdir()) / "StockTrader"
PAUSE_FLAG = ROLE_DIR / "laptop_server_paused.flag"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run(args, timeout=30, capture=False):
    return subprocess.run(
        [str(x) for x in args], cwd=str(ROOT), timeout=timeout,
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
        stderr=subprocess.STDOUT if capture else subprocess.DEVNULL,
        text=True,
        creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


def healthy(timeout=2.5):
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=timeout) as r:
            body = r.read(768).decode("utf-8", "ignore").replace(" ", "").lower()
            return r.status == 200 and '"ok":true' in body
    except Exception:
        return False


def listeners_8000():
    if os.name != "nt":
        return []
    r = run(["netstat", "-ano"], timeout=15, capture=True)
    if r.returncode:
        return []
    pids = set()
    for line in (r.stdout or "").splitlines():
        if ":8000" in line and "LISTENING" in line.upper():
            parts = line.split()
            if parts and parts[-1].isdigit():
                pids.add(parts[-1])
    return sorted(pids)


def stop_verified_server():
    pids = listeners_8000()
    if not pids:
        return True, "이미 로컬 8000 서버가 중지되어 있습니다."
    if not healthy():
        return False, "8000번 포트를 다른 프로그램이 사용할 가능성이 있어 강제 종료하지 않았습니다."
    for pid in pids:
        run(["taskkill", "/PID", pid, "/F"], timeout=15)
    for _ in range(30):
        if not listeners_8000():
            return True, "Stock Trader 로컬 서버를 중지했습니다."
        time.sleep(0.5)
    return False, "8000번 포트가 해제되지 않았습니다."


def pause_laptop():
    ROLE_DIR.mkdir(parents=True, exist_ok=True)
    PAUSE_FLAG.write_text("temporary-phone-server\n", encoding="utf-8")
    run(["schtasks", "/End", "/TN", TASK], timeout=15)
    task_rc = run(["schtasks", "/Change", "/TN", TASK, "/Disable"], timeout=20).returncode
    ok, msg = stop_verified_server()
    print("[PAUSE] Laptop Stock Trader server role")
    print(f"- pause flag: {PAUSE_FLAG}")
    print("- autostart: " + ("DISABLED" if task_rc == 0 else "task not found/access denied (pause flag still blocks startup)"))
    print(f"- local server: {msg}")
    print("- laptop can now be used normally while the Android phone is the only server.")
    if not ok:
        print("[WARN] " + msg)
        return 3
    return 0


def ensure_task_enabled():
    rc = run(["schtasks", "/Change", "/TN", TASK, "/Enable"], timeout=20).returncode
    if rc == 0:
        return True
    if PYTHON.exists() and INSTALLER.exists():
        rc = run([PYTHON, INSTALLER], timeout=60).returncode
        if rc == 0:
            return True
    return False


def resume_laptop():
    try:
        PAUSE_FLAG.unlink(missing_ok=True)
    except Exception as exc:
        print(f"[ERROR] pause flag removal failed: {exc}")
        return 4
    if not ensure_task_enabled():
        print("[ERROR] StockTraderAutoStart task could not be enabled/recreated.")
        print("Run this file once as Administrator if Windows blocked Task Scheduler changes.")
        return 5
    run(["schtasks", "/Run", "/TN", TASK], timeout=20)
    print("[RESUME] Laptop Stock Trader server role")
    print("- autostart: ENABLED")
    print("- waiting for localhost health...")
    for _ in range(60):
        if healthy(1.5):
            print("[OK] Laptop Stock Trader server is ONLINE.")
            print("IMPORTANT: stop the Android phone server before using laptop server mode.")
            return 0
        time.sleep(1)
    print("[ERROR] Laptop server did not become healthy within 60 seconds.")
    return 6


def status():
    print("Laptop role: " + ("PAUSED / PHONE SERVER MODE" if PAUSE_FLAG.exists() else "ENABLED"))
    print("Local Stock Trader health: " + ("ONLINE" if healthy() else "OFFLINE"))
    print(f"Pause flag: {PAUSE_FLAG}")
    return 0


def main():
    if os.name != "nt":
        print("This helper is Windows-only.")
        return 2
    cmd = (sys.argv[1] if len(sys.argv) > 1 else "status").strip().lower()
    if cmd == "pause":
        return pause_laptop()
    if cmd == "resume":
        return resume_laptop()
    return status()


if __name__ == "__main__":
    raise SystemExit(main())
