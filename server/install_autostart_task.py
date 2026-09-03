"""Install exactly one quiet Stock Trader autostart entry.

Deep-clean legacy Stock Trader scheduled tasks / Startup items / Run entries first,
then create one hidden Task Scheduler action: pythonw.exe autostart.py.
No PowerShell is used. Run once as Administrator for the most complete cleanup.
"""
from __future__ import annotations

import html
import os
import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent.resolve()
PYTHONW = BASE / ".venv" / "Scripts" / "pythonw.exe"
AUTOSTART = BASE / "autostart.py"
TASK = "StockTraderAutoStart"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
LOG = Path(tempfile.gettempdir()) / "stock_trader_startup_cleanup.log"

ROOT_TEXT = str(ROOT).replace("/", "\\").lower()
MARKERS = (
    ROOT_TEXT,
    "start_stock_trader_background",
    "stocktradersupervisor",
    "stocktraderwatchdog",
    "stocktraderautostart",
    "stock_trader_supervisor",
    "stock_trader_watchdog",
)


def log(msg: str) -> None:
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().isoformat(timespec='seconds')}] {msg}\n")
    except Exception:
        pass


def run(args, timeout=60, capture=False):
    return subprocess.run(
        [str(x) for x in args], timeout=timeout,
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
        stderr=subprocess.STDOUT if capture else subprocess.DEVNULL,
        text=True,
        creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


def current_user():
    r = run(["whoami"], timeout=10, capture=True)
    return (r.stdout or "").strip() if r.returncode == 0 else ""


def _matches(text: str) -> bool:
    s = str(text or "").replace("/", "\\").lower()
    return any(m and m in s for m in MARKERS)


def _delete_task(name: str) -> None:
    if not name:
        return
    run(["schtasks", "/End", "/TN", name], timeout=15)
    rc = run(["schtasks", "/Delete", "/TN", name, "/F"], timeout=20).returncode
    log(f"task delete {name}: rc={rc}")


def cleanup_scheduled_tasks() -> None:
    # Known historical names first.
    for name in (
        "StockTraderSupervisor", "StockTraderWatchdog", "StockTraderAutoStart",
        "\\StockTraderSupervisor", "\\StockTraderWatchdog", "\\StockTraderAutoStart",
    ):
        _delete_task(name)

    # Deep scan task XML files so renamed/older Stock Trader tasks are also removed.
    tasks_root = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "Tasks"
    try:
        files = list(tasks_root.rglob("*"))
    except Exception as exc:
        log(f"task scan unavailable: {type(exc).__name__}: {exc}")
        return
    for p in files:
        if not p.is_file():
            continue
        try:
            raw = p.read_bytes()
        except Exception:
            continue
        texts = []
        for enc in ("utf-16", "utf-8", "cp949"):
            try:
                texts.append(raw.decode(enc, errors="ignore"))
            except Exception:
                pass
        if not any(_matches(t) for t in texts):
            continue
        try:
            rel = p.relative_to(tasks_root)
            task_name = "\\" + str(rel).replace("/", "\\")
            _delete_task(task_name)
        except Exception as exc:
            log(f"task delete resolve failed {p}: {type(exc).__name__}: {exc}")


def _file_matches(p: Path) -> bool:
    if _matches(p.name):
        return True
    try:
        raw = p.read_bytes()
    except Exception:
        return False
    lower = raw.lower()
    ascii_markers = [m.encode("utf-8", "ignore") for m in MARKERS if m]
    utf16_markers = [m.encode("utf-16le", "ignore") for m in MARKERS if m]
    return any(m in lower for m in ascii_markers) or any(m in lower for m in utf16_markers)


def cleanup_startup_folders() -> None:
    folders = [
        Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs/Startup",
        Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Microsoft/Windows/Start Menu/Programs/StartUp",
    ]
    for folder in folders:
        try:
            items = list(folder.iterdir()) if folder.exists() else []
        except Exception:
            items = []
        for p in items:
            if not p.is_file() or not _file_matches(p):
                continue
            try:
                p.unlink()
                log(f"startup file removed: {p}")
            except Exception as exc:
                log(f"startup file remove failed {p}: {type(exc).__name__}: {exc}")


def cleanup_run_keys() -> None:
    keys = (
        r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run",
        r"HKCU\Software\Microsoft\Windows\CurrentVersion\RunOnce",
        r"HKLM\Software\Microsoft\Windows\CurrentVersion\Run",
        r"HKLM\Software\Microsoft\Windows\CurrentVersion\RunOnce",
    )
    line_re = re.compile(r"^\s*(.*?)\s{2,}(REG_[A-Z0-9_]+)\s{2,}(.*)$", re.I)
    for key in keys:
        q = run(["reg", "query", key], timeout=20, capture=True)
        if q.returncode:
            continue
        for line in (q.stdout or "").splitlines():
            m = line_re.match(line)
            if not m:
                continue
            value_name, _typ, data = m.groups()
            if not _matches(value_name + " " + data):
                continue
            rc = run(["reg", "delete", key, "/v", value_name, "/f"], timeout=20).returncode
            log(f"run value delete {key}::{value_name}: rc={rc}")


def create_single_task() -> int:
    user = current_user()
    if not user:
        return 3
    xml = f'''<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Stock Day Trader silent autostart</Description></RegistrationInfo>
  <Triggers><LogonTrigger><Enabled>true</Enabled></LogonTrigger></Triggers>
  <Principals><Principal id="Author"><UserId>{html.escape(user)}</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>true</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author"><Exec>
    <Command>{html.escape(str(PYTHONW))}</Command>
    <Arguments>"{html.escape(str(AUTOSTART))}"</Arguments>
    <WorkingDirectory>{html.escape(str(ROOT))}</WorkingDirectory>
  </Exec></Actions>
</Task>'''
    tmp = Path(tempfile.gettempdir()) / "stock_trader_autostart_task.xml"
    tmp.write_text(xml, encoding="utf-16")
    try:
        rc = run(["schtasks", "/Create", "/TN", TASK, "/XML", tmp, "/F"], timeout=30).returncode
        log(f"single task create: rc={rc}")
        if rc:
            return 4
        run(["schtasks", "/Run", "/TN", TASK], timeout=20)
        return 0
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


def main():
    if os.name != "nt" or not PYTHONW.exists() or not AUTOSTART.exists():
        return 2
    log("=== deep quiet-start repair begin ===")
    cleanup_scheduled_tasks()
    cleanup_startup_folders()
    cleanup_run_keys()
    rc = create_single_task()
    log(f"=== deep quiet-start repair end rc={rc} ===")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
