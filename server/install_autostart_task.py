"""Install one quiet Task Scheduler autostart entry without PowerShell.

The scheduled action is pythonw.exe autostart.py. Legacy every-minute supervisor
and old watchdog tasks are removed to prevent duplicate recovery/process storms.
"""
from __future__ import annotations

import html
import os
import subprocess
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent
PYTHONW = BASE / ".venv" / "Scripts" / "pythonw.exe"
AUTOSTART = BASE / "autostart.py"
TASK = "StockTraderAutoStart"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run(args, timeout=60):
    return subprocess.run([str(x) for x in args], timeout=timeout,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0)


def current_user():
    r = subprocess.run(["whoami"], capture_output=True, text=True, timeout=10,
                       creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0)
    return (r.stdout or "").strip()


def main():
    if os.name != "nt" or not PYTHONW.exists() or not AUTOSTART.exists():
        return 2

    for name in ("StockTraderSupervisor", "StockTraderWatchdog", "StockTraderAutoStart"):
        run(["schtasks", "/Delete", "/TN", name, "/F"], timeout=20)

    startup = Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs/Startup"
    for name in ("StockTraderWatchdog.vbs", "StockTraderAutoStart.vbs"):
        try:
            (startup / name).unlink(missing_ok=True)
        except Exception:
            pass

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
        if rc:
            return 4
        run(["schtasks", "/Run", "/TN", TASK], timeout=20)
        return 0
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
