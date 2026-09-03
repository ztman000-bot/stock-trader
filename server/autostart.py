"""Silent Windows autostart entrypoint.

Runs under pythonw.exe from Task Scheduler and launches the canonical background
starter with CREATE_NO_WINDOW, so no console/PowerShell window is shown.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
START_CMD = BASE / "start_stock_trader_background.cmd"


def main():
    if os.name != "nt" or not START_CMD.exists():
        return 0
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    subprocess.Popen(
        ["cmd.exe", "/d", "/c", str(START_CMD)], cwd=str(BASE),
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=flags, close_fds=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
