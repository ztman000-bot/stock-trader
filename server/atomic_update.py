"""Atomic/quiet updater for Stock Day Trader.

Keeps the known-good server alive until the new checkout passes preflight. After
replacement it verifies localhost health and rolls back to the previous Git commit
if startup fails. Automatic startup prefers the windowless Python bootstrap; the
legacy cmd starter is retained only as a rollback compatibility fallback.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime
from pathlib import Path

SERVER = Path(__file__).resolve().parent
ROOT = SERVER.parent
PYTHON = SERVER / ".venv" / "Scripts" / "python.exe"
PYTHONW = SERVER / ".venv" / "Scripts" / "pythonw.exe"
SILENT_BOOT = SERVER / "silent_boot.py"
START_CMD = SERVER / "start_stock_trader_background.cmd"
PREFLIGHT = SERVER / "preflight.py"
REQ = SERVER / "requirements.txt"
FLAG = Path(tempfile.gettempdir()) / "stock_trader_update_in_progress.flag"
LOG = Path(tempfile.gettempdir()) / "stock_trader_remote_update.log"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


def log(msg: str):
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().isoformat(timespec='seconds')}] {msg}\n")
    except Exception:
        pass


def run(args, *, timeout=120, capture=False, env=None):
    return subprocess.run(
        [str(x) for x in args], cwd=str(ROOT), timeout=timeout,
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
        stderr=subprocess.STDOUT if capture else subprocess.DEVNULL,
        text=True, env=env,
        creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


def text(args, timeout=30):
    r = run(args, timeout=timeout, capture=True)
    if r.returncode:
        raise RuntimeError((r.stdout or "command failed").strip())
    return (r.stdout or "").strip()


def file_hash(path: Path):
    if not path.exists():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def tracked_clean():
    return run(["git", "diff", "--quiet", "--"], timeout=20).returncode == 0 and \
        run(["git", "diff", "--cached", "--quiet", "--"], timeout=20).returncode == 0


def healthy(timeout=3):
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=timeout) as r:
            body = r.read(512).decode("utf-8", "ignore").replace(" ", "").lower()
            return r.status == 200 and '"ok":true' in body
    except Exception:
        return False


def listeners():
    if os.name != "nt":
        return []
    try:
        out = subprocess.check_output(
            ["netstat", "-ano"], text=True, errors="ignore",
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception:
        return []
    pids = set()
    for line in out.splitlines():
        if ":8000" in line and "LISTENING" in line.upper():
            parts = line.split()
            if parts and parts[-1].isdigit():
                pids.add(parts[-1])
    return sorted(pids)


def stop_server():
    for pid in listeners():
        run(["taskkill", "/PID", pid, "/F"], timeout=15)
    for _ in range(30):
        if not listeners():
            return True
        time.sleep(0.5)
    return False


def start_server():
    env = os.environ.copy()
    flags = (CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP) if os.name == "nt" else 0
    if SILENT_BOOT.exists() and PYTHONW.exists():
        env["STOCK_TRADER_SKIP_WATCHDOG"] = "1"
        subprocess.Popen(
            [str(PYTHONW), str(SILENT_BOOT)], cwd=str(SERVER), env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=flags, close_fds=True,
        )
    else:
        subprocess.Popen(
            ["cmd.exe", "/d", "/c", str(START_CMD)], cwd=str(SERVER), env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0, close_fds=True,
        )
    for _ in range(50):
        if healthy(1.5):
            return True
        time.sleep(1)
    return False


def rollback(old_head: str):
    log(f"rollback -> {old_head}")
    stop_server()
    run(["git", "reset", "--hard", old_head], timeout=90)
    ok = start_server()
    log("ROLLBACK " + ("HEALTHY" if ok else "FAILED"))
    return ok


def main():
    if not PYTHON.exists():
        log("ERROR: venv python missing")
        return 10
    try:
        FLAG.write_text("update", encoding="utf-8")
    except Exception:
        pass
    old_head = ""
    try:
        log("atomic update requested")
        if not tracked_clean():
            log("STOP: tracked/staged local changes exist")
            return 2
        old_head = text(["git", "rev-parse", "HEAD"])
        req_before = file_hash(REQ)
        pull = run(["git", "pull", "--ff-only"], timeout=120, capture=True)
        if pull.returncode:
            log("ERROR git pull: " + (pull.stdout or "").strip()[-1000:])
            return 3
        new_head = text(["git", "rev-parse", "HEAD"])
        log(f"checkout {old_head[:10]} -> {new_head[:10]}")

        if req_before != file_hash(REQ):
            pip = run([PYTHON, "-m", "pip", "install", "-r", REQ,
                       "--disable-pip-version-check"], timeout=300, capture=True)
            if pip.returncode:
                log("ERROR dependency install: " + (pip.stdout or "").strip()[-1200:])
                run(["git", "reset", "--hard", old_head], timeout=90)
                return 4

        pre = run([PYTHON, PREFLIGHT], timeout=120, capture=True)
        if pre.returncode:
            log("ERROR preflight: " + (pre.stdout or "").strip()[-1500:])
            run(["git", "reset", "--hard", old_head], timeout=90)
            return 5
        log("preflight OK; replacing server")

        if not stop_server():
            log("ERROR: port 8000 did not release")
            rollback(old_head)
            return 6
        if start_server():
            log("UPDATE OK: new server healthy")
            return 0
        log("ERROR: new server health timeout")
        rollback(old_head)
        return 7
    except Exception as exc:
        log(f"CRITICAL {type(exc).__name__}: {exc}")
        if old_head:
            try:
                rollback(old_head)
            except Exception as rb:
                log(f"rollback exception {type(rb).__name__}: {rb}")
        return 9
    finally:
        try:
            FLAG.unlink(missing_ok=True)
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
