"""Create a credential-free, consistent SQLite snapshot for Android migration.

Uses SQLite backup API so the Windows server may still be running while the snapshot
is created. The export never includes server/.env or NH credentials.
"""
from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent
SRC_DB = BASE / "market_data.db"
SRC_RESEARCH = BASE / "research_latest.json"
OUT = ROOT / "phone_transfer"
OUT_DB = OUT / "market_data.db"
OUT_RESEARCH = OUT / "research_latest.json"


def main() -> int:
    if not SRC_DB.exists():
        print(f"[ERROR] database not found: {SRC_DB}")
        return 2
    OUT.mkdir(parents=True, exist_ok=True)
    if OUT_DB.exists():
        OUT_DB.unlink()
    print("[1/3] Creating consistent SQLite snapshot...")
    with sqlite3.connect(str(SRC_DB), timeout=30) as src, sqlite3.connect(str(OUT_DB), timeout=30) as dst:
        src.backup(dst)
        dst.execute("PRAGMA integrity_check")
    print(f"[OK] {OUT_DB}")

    print("[2/3] Copying latest research summary (no credentials)...")
    if SRC_RESEARCH.exists():
        shutil.copy2(SRC_RESEARCH, OUT_RESEARCH)
        print(f"[OK] {OUT_RESEARCH}")
    else:
        print("[INFO] research_latest.json not present; it will regenerate on Android.")

    note = OUT / "README_PHONE_TRANSFER.txt"
    note.write_text(
        "Stock Trader phone migration snapshot\n"
        f"Created: {datetime.now().isoformat(timespec='seconds')}\n\n"
        "Copy market_data.db to: ~/stock-trader/server/market_data.db\n"
        "research_latest.json is optional.\n"
        "IMPORTANT: .env / App Key / App Secret are intentionally NOT included.\n"
        "Create server/.env locally on the phone and keep ENABLE_TRADING=false.\n",
        encoding="utf-8",
    )
    print("[3/3] Export complete. No .env or credentials were included.")
    print(f"Folder: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
