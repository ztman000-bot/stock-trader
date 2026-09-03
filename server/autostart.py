"""Silent Windows autostart entrypoint.

Task Scheduler runs this file with pythonw.exe. The server is bootstrapped directly
in Python, so no cmd.exe or PowerShell window is needed at logon.
"""
from __future__ import annotations

import sys

from silent_boot import main as silent_boot_main


def main() -> int:
    return int(silent_boot_main())


if __name__ == "__main__":
    sys.exit(main())
