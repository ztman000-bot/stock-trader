"""Safe SQLite snapshots for Stock Trader.

Uses sqlite3.Connection.backup() so WAL-mode databases are copied consistently.
Backups are operational safety artifacts only; they never affect trading logic.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from collector import DB_PATH

KST = ZoneInfo("Asia/Seoul")
BACKUP_DIR = Path(os.getenv("DB_BACKUP_DIR", str(Path(DB_PATH).resolve().parent / "backups")))
RETENTION = max(3, min(int(os.getenv("DB_BACKUP_RETENTION", "7")), 30))


def _quick_check(path: Path) -> str:
    with sqlite3.connect(str(path), timeout=15) as conn:
        row = conn.execute("PRAGMA quick_check(1)").fetchone()
    return str(row[0] if row else "missing-result")


def _rotate() -> list[str]:
    files = sorted(BACKUP_DIR.glob("market_data-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    removed = []
    for path in files[RETENTION:]:
        try:
            path.unlink()
            removed.append(path.name)
        except OSError:
            pass
    return removed


def snapshot(reason: str = "manual") -> dict:
    src_path = Path(DB_PATH).resolve()
    if not src_path.exists():
        return {"ok": False, "error": f"database not found: {src_path}", "reason": reason}

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(KST)
    stamp = now.strftime("%Y%m%d-%H%M%S")
    final = BACKUP_DIR / f"market_data-{stamp}.db"
    temp = final.with_suffix(".db.tmp")
    temp.unlink(missing_ok=True)

    try:
        with sqlite3.connect(str(src_path), timeout=30) as src, sqlite3.connect(str(temp), timeout=30) as dst:
            src.backup(dst, pages=1024)
            dst.commit()
        check = _quick_check(temp)
        if check != "ok":
            temp.unlink(missing_ok=True)
            return {"ok": False, "error": f"backup quick_check={check}", "reason": reason}
        temp.replace(final)
        removed = _rotate()
        return {
            "ok": True,
            "reason": reason,
            "createdAt": now.isoformat(timespec="seconds"),
            "path": str(final),
            "bytes": final.stat().st_size,
            "quickCheck": check,
            "retention": RETENTION,
            "removed": removed,
        }
    except Exception as exc:
        temp.unlink(missing_ok=True)
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "reason": reason}


def status() -> dict:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(BACKUP_DIR.glob("market_data-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    latest = files[0] if files else None
    return {
        "ok": True,
        "backupDir": str(BACKUP_DIR),
        "retention": RETENTION,
        "count": len(files),
        "latest": None if latest is None else {
            "name": latest.name,
            "bytes": latest.stat().st_size,
            "modifiedAt": datetime.fromtimestamp(latest.stat().st_mtime, KST).isoformat(timespec="seconds"),
        },
        "orderAccess": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--reason", default="manual")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()
    result = status() if args.status else snapshot(args.reason)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
