"""Compact GitHub-hosted market-regime reference for Android research runtime.

The Android phone downloads only the small aggregate CSV produced by this repository's
GitHub Action. It never downloads FinanceData/marcap raw parquet files and never needs
pandas/pyarrow. This database is separate from NH 1m/5m forward evidence and cannot
change Control/Paper behavior.
"""

from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("REGIME_REFERENCE_DB_PATH", str(BASE_DIR / "regime_reference.db")))
SOURCE = "GITHUB_FINANCEDATA_MARCAP_AGG"
CONTROL_STRATEGY = "v0.8.0 LOCKED"
REAL_ORDER_ENABLED = False
RESEARCH_ONLY = True
DEFAULT_SUMMARY_URL = "https://raw.githubusercontent.com/ztman000-bot/stock-trader/main/research/regime/marcap_regime_daily.csv"
MAX_SUMMARY_BYTES = 12 * 1024 * 1024
REQUIRED_FIELDS = {
    "trade_date",
    "market",
    "active_count",
    "advancers",
    "decliners",
    "unchanged",
    "advance_ratio",
    "decline_ratio",
    "turnover_amount",
    "market_cap_total",
    "top10_cap_share",
    "cap_weighted_change_pct",
    "median_change_pct",
    "breadth_5d",
    "weighted_return_20d",
    "volatility_20d",
    "turnover_ratio_20d",
    "regime_score",
    "regime",
    "source",
    "source_ref",
}


def _local_env() -> dict[str, str]:
    env_path = BASE_DIR / ".env"
    out: dict[str, str] = {}
    if not env_path.exists():
        return out
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            if key:
                out[key] = value
    except (OSError, UnicodeError):
        return {}
    return out


def _cfg(name: str, default: str = "") -> str:
    value = os.getenv(name)
    if value is not None:
        return value.strip()
    return _local_env().get(name, default).strip()


def _bool_cfg(name: str, default: bool) -> bool:
    return _cfg(name, "true" if default else "false").lower() in {"1", "true", "yes", "on"}


def _int_cfg(name: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(_cfg(name, str(default)))
    except ValueError:
        value = default
    return max(lo, min(value, hi))


def reference_enabled() -> bool:
    return _bool_cfg("MARCAP_GITHUB_REGIME_ENABLED", True)


def sync_hour() -> int:
    return _int_cfg("MARCAP_GITHUB_SYNC_HOUR", 20, 16, 23)


def summary_url() -> str:
    url = _cfg("MARCAP_GITHUB_SUMMARY_URL", DEFAULT_SUMMARY_URL) or DEFAULT_SUMMARY_URL
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "raw.githubusercontent.com":
        raise ValueError("MARCAP_GITHUB_SUMMARY_URL must use https://raw.githubusercontent.com")
    return url


def connect(path: str | os.PathLike[str] | None = None) -> sqlite3.Connection:
    db = Path(path or DB_PATH)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(path: str | os.PathLike[str] | None = None) -> None:
    with connect(path) as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS regime_daily(
                trade_date TEXT NOT NULL,
                market TEXT NOT NULL,
                active_count INTEGER,
                advancers INTEGER,
                decliners INTEGER,
                unchanged INTEGER,
                advance_ratio REAL,
                decline_ratio REAL,
                turnover_amount REAL,
                market_cap_total REAL,
                top10_cap_share REAL,
                cap_weighted_change_pct REAL,
                median_change_pct REAL,
                breadth_5d REAL,
                weighted_return_20d REAL,
                volatility_20d REAL,
                turnover_ratio_20d REAL,
                regime_score INTEGER,
                regime TEXT NOT NULL,
                source TEXT NOT NULL,
                source_ref TEXT,
                synced_at TEXT NOT NULL,
                PRIMARY KEY(trade_date, market, source)
            );
            CREATE INDEX IF NOT EXISTS idx_regime_daily_market_date
              ON regime_daily(market, trade_date);
            CREATE TABLE IF NOT EXISTS regime_meta(
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        c.execute("INSERT INTO regime_meta(key,value) VALUES('research_only','true') ON CONFLICT(key) DO UPDATE SET value='true'")


def _num(row: Mapping[str, str], key: str, integer: bool = False):
    raw = str(row.get(key) or "").strip().replace(",", "")
    if not raw:
        return None
    try:
        value = float(raw)
        return int(value) if integer else value
    except ValueError:
        return None


def import_summary_text(text: str, path: str | os.PathLike[str] | None = None, *, etag: str | None = None, url: str | None = None) -> dict[str, object]:
    init_db(path)
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or not REQUIRED_FIELDS.issubset(set(reader.fieldnames)):
        raise ValueError("regime summary header is incomplete")
    now = datetime.now(KST).isoformat(timespec="seconds")
    written = rejected = 0
    first = last = None
    markets: set[str] = set()
    with connect(path) as c:
        for row in reader:
            trade_date = str(row.get("trade_date") or "").strip()
            market = str(row.get("market") or "").strip().upper()
            source = str(row.get("source") or "").strip()
            regime = str(row.get("regime") or "").strip().upper()
            if len(trade_date) != 10 or market not in {"KOSPI", "KOSDAQ"} or source != SOURCE or not regime:
                rejected += 1
                continue
            c.execute(
                """INSERT INTO regime_daily(
                    trade_date,market,active_count,advancers,decliners,unchanged,
                    advance_ratio,decline_ratio,turnover_amount,market_cap_total,
                    top10_cap_share,cap_weighted_change_pct,median_change_pct,breadth_5d,
                    weighted_return_20d,volatility_20d,turnover_ratio_20d,
                    regime_score,regime,source,source_ref,synced_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(trade_date,market,source) DO UPDATE SET
                    active_count=excluded.active_count,advancers=excluded.advancers,
                    decliners=excluded.decliners,unchanged=excluded.unchanged,
                    advance_ratio=excluded.advance_ratio,decline_ratio=excluded.decline_ratio,
                    turnover_amount=excluded.turnover_amount,market_cap_total=excluded.market_cap_total,
                    top10_cap_share=excluded.top10_cap_share,
                    cap_weighted_change_pct=excluded.cap_weighted_change_pct,
                    median_change_pct=excluded.median_change_pct,breadth_5d=excluded.breadth_5d,
                    weighted_return_20d=excluded.weighted_return_20d,
                    volatility_20d=excluded.volatility_20d,
                    turnover_ratio_20d=excluded.turnover_ratio_20d,
                    regime_score=excluded.regime_score,regime=excluded.regime,
                    source_ref=excluded.source_ref,synced_at=excluded.synced_at""",
                (
                    trade_date, market,
                    _num(row, "active_count", True), _num(row, "advancers", True),
                    _num(row, "decliners", True), _num(row, "unchanged", True),
                    _num(row, "advance_ratio"), _num(row, "decline_ratio"),
                    _num(row, "turnover_amount"), _num(row, "market_cap_total"),
                    _num(row, "top10_cap_share"), _num(row, "cap_weighted_change_pct"),
                    _num(row, "median_change_pct"), _num(row, "breadth_5d"),
                    _num(row, "weighted_return_20d"), _num(row, "volatility_20d"),
                    _num(row, "turnover_ratio_20d"), _num(row, "regime_score", True),
                    regime, SOURCE, str(row.get("source_ref") or "").strip(), now,
                ),
            )
            written += 1
            markets.add(market)
            first = trade_date if first is None or trade_date < first else first
            last = trade_date if last is None or trade_date > last else last
        meta = {
            "last_sync_at": now,
            "summary_url": url or "local-test",
            "etag": etag or "",
            "first_date": first or "",
            "last_date": last or "",
            "last_import_rows": str(written),
        }
        for key, value in meta.items():
            c.execute(
                "INSERT INTO regime_meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)),
            )
    return {
        "ok": written > 0,
        "writtenRows": written,
        "rejectedRows": rejected,
        "first": first,
        "last": last,
        "markets": sorted(markets),
        "researchOnly": True,
        "realOrderEnabled": False,
    }


def _meta(key: str, path: str | os.PathLike[str] | None = None) -> str | None:
    init_db(path)
    with connect(path) as c:
        row = c.execute("SELECT value FROM regime_meta WHERE key=?", (key,)).fetchone()
        return str(row[0]) if row else None


def sync_summary(path: str | os.PathLike[str] | None = None, *, opener=urlopen, timeout: float = 30.0) -> dict[str, object]:
    if not reference_enabled():
        return {"ok": True, "disabled": True, "researchOnly": True, "realOrderEnabled": False}
    url = summary_url()
    headers = {"Accept": "text/csv,text/plain;q=0.9,*/*;q=0.1", "User-Agent": "stock-trader-regime-reference/0.17.15"}
    etag = _meta("etag", path)
    if etag:
        headers["If-None-Match"] = etag
    request = Request(url, headers=headers, method="GET")
    try:
        response = opener(request, timeout=timeout)
        raw = response.read(MAX_SUMMARY_BYTES + 1)
        if len(raw) > MAX_SUMMARY_BYTES:
            raise ValueError("regime summary exceeds safety size limit")
        response_etag = str(response.headers.get("ETag") or "").strip() if getattr(response, "headers", None) else ""
    except HTTPError as exc:
        if exc.code == 304:
            return {"ok": True, "notModified": True, "researchOnly": True, "realOrderEnabled": False}
        return {"ok": False, "error": f"github summary HTTP {exc.code}", "researchOnly": True, "realOrderEnabled": False}
    except (URLError, TimeoutError) as exc:
        return {"ok": False, "error": f"github summary network {type(exc).__name__}", "researchOnly": True, "realOrderEnabled": False}
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return {"ok": False, "error": "github summary is not UTF-8", "researchOnly": True, "realOrderEnabled": False}
    try:
        result = import_summary_text(text, path, etag=response_etag, url=url)
    except (ValueError, sqlite3.Error) as exc:
        return {"ok": False, "error": f"github summary validation {type(exc).__name__}: {str(exc)[:160]}", "researchOnly": True, "realOrderEnabled": False}
    return {**result, "notModified": False}


def collector_status(path: str | os.PathLike[str] | None = None) -> dict[str, object]:
    init_db(path)
    with connect(path) as c:
        count = int(c.execute("SELECT COUNT(*) FROM regime_daily WHERE source=?", (SOURCE,)).fetchone()[0])
        rng = c.execute("SELECT MIN(trade_date),MAX(trade_date) FROM regime_daily WHERE source=?", (SOURCE,)).fetchone()
        latest_rows = c.execute(
            """SELECT r.* FROM regime_daily r
               JOIN (SELECT market,MAX(trade_date) d FROM regime_daily WHERE source=? GROUP BY market) x
                 ON x.market=r.market AND x.d=r.trade_date
               WHERE r.source=? ORDER BY r.market""",
            (SOURCE, SOURCE),
        ).fetchall()
    latest = {
        str(row["market"]): {
            "tradeDate": row["trade_date"],
            "regime": row["regime"],
            "regimeScore": row["regime_score"],
            "advanceRatio": row["advance_ratio"],
            "weightedReturn20d": row["weighted_return_20d"],
            "volatility20d": row["volatility_20d"],
        }
        for row in latest_rows
    }
    return {
        "ok": True,
        "researchOnly": True,
        "controlStrategy": CONTROL_STRATEGY,
        "realOrderEnabled": REAL_ORDER_ENABLED,
        "enabled": reference_enabled(),
        "keyRequired": False,
        "source": SOURCE,
        "rows": count,
        "first": rng[0] if rng else None,
        "last": rng[1] if rng else None,
        "latest": latest,
        "lastSyncAt": _meta("last_sync_at", path),
        "summaryUrlHost": urlparse(summary_url()).hostname,
        "rawPerStockStoredOnPhone": False,
    }


def _cli() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="GitHub market-regime aggregate reference")
    parser.add_argument("command", nargs="?", choices=("status", "sync"), default="status")
    parser.add_argument("--db", default=str(DB_PATH))
    args = parser.parse_args()
    result = collector_status(args.db) if args.command == "status" else sync_summary(args.db)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
