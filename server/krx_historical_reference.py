"""Provenance-aware Korean-market historical reference database.

Research-only. This module does not send broker orders, does not read NH credentials,
and does not mutate Control v0.8.0 or Paper state. It stores lawfully obtained
historical KRX-style daily/reference data in a separate SQLite database so long-run
regime, survivorship-bias and universe research can be performed without mixing the
reference dataset into the live NH intraday database.

The importer is intentionally offline-first: CSV/JSON exports may be loaded locally.
Direct KRX Open API downloading is NOT performed here because KRX requires an
approved authentication key/service application and the exact approved API contract
belongs to the operator, not to the trading engine.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("HISTORICAL_MARKET_DB_PATH", str(BASE_DIR / "historical_market.db")))
SCHEMA_VERSION = 1
RESEARCH_ONLY = True
CONTROL_STRATEGY = "v0.8.0 LOCKED"
REAL_ORDER_ENABLED = False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _text(row: Mapping[str, object], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip() != "":
            return str(value).strip()
    return ""


def _number(row: Mapping[str, object], *keys: str, integer: bool = False):
    raw = _text(row, *keys).replace(",", "")
    if raw in {"", "-", "--", "N/A", "null", "None"}:
        return None
    try:
        return int(float(raw)) if integer else float(raw)
    except (TypeError, ValueError):
        return None


def _date_text(value: object) -> str:
    text = str(value or "").strip().replace("-", "").replace("/", "")
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return str(value or "").strip()


def connect(path: str | os.PathLike[str] | None = None) -> sqlite3.Connection:
    db = Path(path or DB_PATH)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(path: str | os.PathLike[str] | None = None) -> dict[str, object]:
    db = Path(path or DB_PATH)
    with connect(db) as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata(
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS import_runs(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dataset_type TEXT NOT NULL,
                source TEXT NOT NULL,
                source_ref TEXT,
                adjusted INTEGER NOT NULL DEFAULT 0,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                input_rows INTEGER NOT NULL DEFAULT 0,
                written_rows INTEGER NOT NULL DEFAULT 0,
                rejected_rows INTEGER NOT NULL DEFAULT 0,
                note TEXT
            );
            CREATE TABLE IF NOT EXISTS daily_bars(
                trade_date TEXT NOT NULL,
                code TEXT NOT NULL,
                name TEXT,
                market TEXT NOT NULL,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume INTEGER,
                value REAL,
                market_cap REAL,
                listed_shares INTEGER,
                adjusted INTEGER NOT NULL DEFAULT 0,
                source TEXT NOT NULL,
                source_ref TEXT,
                imported_at TEXT NOT NULL,
                PRIMARY KEY(trade_date, code, market, adjusted, source)
            );
            CREATE INDEX IF NOT EXISTS idx_daily_bars_code_date ON daily_bars(code, trade_date);
            CREATE INDEX IF NOT EXISTS idx_daily_bars_market_date ON daily_bars(market, trade_date);
            CREATE TABLE IF NOT EXISTS index_daily(
                trade_date TEXT NOT NULL,
                index_code TEXT NOT NULL,
                index_name TEXT,
                market TEXT,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume INTEGER,
                value REAL,
                source TEXT NOT NULL,
                source_ref TEXT,
                imported_at TEXT NOT NULL,
                PRIMARY KEY(trade_date, index_code, source)
            );
            CREATE INDEX IF NOT EXISTS idx_index_daily_code_date ON index_daily(index_code, trade_date);
            CREATE TABLE IF NOT EXISTS symbol_history(
                code TEXT NOT NULL,
                effective_from TEXT NOT NULL,
                effective_to TEXT,
                name TEXT,
                market TEXT,
                security_type TEXT,
                listed_date TEXT,
                delisted_date TEXT,
                source TEXT NOT NULL,
                source_ref TEXT,
                imported_at TEXT NOT NULL,
                PRIMARY KEY(code, effective_from, source)
            );
            CREATE INDEX IF NOT EXISTS idx_symbol_history_code ON symbol_history(code, effective_from, effective_to);
            CREATE TABLE IF NOT EXISTS delisted_symbols(
                code TEXT NOT NULL,
                name TEXT,
                market TEXT,
                listed_date TEXT,
                delisted_date TEXT NOT NULL,
                delist_reason TEXT,
                source TEXT NOT NULL,
                source_ref TEXT,
                imported_at TEXT NOT NULL,
                PRIMARY KEY(code, delisted_date, source)
            );
            CREATE INDEX IF NOT EXISTS idx_delisted_date ON delisted_symbols(delisted_date, market);
            CREATE TABLE IF NOT EXISTS corporate_actions(
                code TEXT NOT NULL,
                action_date TEXT NOT NULL,
                action_type TEXT NOT NULL,
                ratio REAL,
                cash_amount REAL,
                note TEXT,
                source TEXT NOT NULL,
                source_ref TEXT,
                imported_at TEXT NOT NULL,
                PRIMARY KEY(code, action_date, action_type, source)
            );
            CREATE TABLE IF NOT EXISTS data_provenance(
                dataset_type TEXT NOT NULL,
                source TEXT NOT NULL,
                source_ref TEXT NOT NULL DEFAULT '',
                adjusted INTEGER NOT NULL DEFAULT 0,
                first_date TEXT,
                last_date TEXT,
                row_count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(dataset_type, source, source_ref, adjusted)
            );
            """
        )
        c.execute(
            "INSERT INTO metadata(key,value) VALUES('schema_version',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(SCHEMA_VERSION),),
        )
        c.execute(
            "INSERT INTO metadata(key,value) VALUES('research_only','true') "
            "ON CONFLICT(key) DO UPDATE SET value='true'"
        )
    return {"ok": True, "path": str(db), "schemaVersion": SCHEMA_VERSION, "researchOnly": True}


def _begin_run(c: sqlite3.Connection, dataset_type: str, source: str, source_ref: str, adjusted: bool, note: str = "") -> int:
    cur = c.execute(
        "INSERT INTO import_runs(dataset_type,source,source_ref,adjusted,started_at,note) VALUES(?,?,?,?,?,?)",
        (dataset_type, source, source_ref, int(bool(adjusted)), _utc_now(), note),
    )
    return int(cur.lastrowid)


def _finish_run(c: sqlite3.Connection, run_id: int, input_rows: int, written_rows: int, rejected_rows: int) -> None:
    c.execute(
        "UPDATE import_runs SET finished_at=?,input_rows=?,written_rows=?,rejected_rows=? WHERE id=?",
        (_utc_now(), input_rows, written_rows, rejected_rows, run_id),
    )


def _refresh_provenance(c: sqlite3.Connection, dataset_type: str, source: str, source_ref: str, adjusted: bool) -> None:
    if dataset_type == "daily_bars":
        first, last, count = c.execute(
            "SELECT MIN(trade_date),MAX(trade_date),COUNT(*) FROM daily_bars WHERE source=? AND COALESCE(source_ref,'')=? AND adjusted=?",
            (source, source_ref, int(bool(adjusted))),
        ).fetchone()
    elif dataset_type == "index_daily":
        first, last, count = c.execute(
            "SELECT MIN(trade_date),MAX(trade_date),COUNT(*) FROM index_daily WHERE source=? AND COALESCE(source_ref,'')=?",
            (source, source_ref),
        ).fetchone()
    elif dataset_type == "delisted_symbols":
        first, last, count = c.execute(
            "SELECT MIN(delisted_date),MAX(delisted_date),COUNT(*) FROM delisted_symbols WHERE source=? AND COALESCE(source_ref,'')=?",
            (source, source_ref),
        ).fetchone()
    elif dataset_type == "symbol_history":
        first, last, count = c.execute(
            "SELECT MIN(effective_from),MAX(COALESCE(effective_to,effective_from)),COUNT(*) FROM symbol_history WHERE source=? AND COALESCE(source_ref,'')=?",
            (source, source_ref),
        ).fetchone()
    elif dataset_type == "corporate_actions":
        first, last, count = c.execute(
            "SELECT MIN(action_date),MAX(action_date),COUNT(*) FROM corporate_actions WHERE source=? AND COALESCE(source_ref,'')=?",
            (source, source_ref),
        ).fetchone()
    else:
        return
    c.execute(
        """INSERT INTO data_provenance(dataset_type,source,source_ref,adjusted,first_date,last_date,row_count,updated_at)
           VALUES(?,?,?,?,?,?,?,?)
           ON CONFLICT(dataset_type,source,source_ref,adjusted) DO UPDATE SET
             first_date=excluded.first_date,last_date=excluded.last_date,
             row_count=excluded.row_count,updated_at=excluded.updated_at""",
        (dataset_type, source, source_ref, int(bool(adjusted)), first, last, int(count or 0), _utc_now()),
    )


def import_daily_rows(
    rows: Iterable[Mapping[str, object]],
    *,
    market: str,
    source: str = "KRX_EXPORT",
    source_ref: str = "",
    adjusted: bool = False,
    path: str | os.PathLike[str] | None = None,
) -> dict[str, object]:
    """Import KRX-style equity daily rows with idempotent upsert semantics."""

    init_db(path)
    market = str(market or "").strip().upper()
    if not market:
        raise ValueError("market is required")
    source = str(source or "").strip().upper()
    source_ref = str(source_ref or "").strip()
    input_rows = written = rejected = 0
    with connect(path) as c:
        run_id = _begin_run(c, "daily_bars", source, source_ref, adjusted)
        for row in rows:
            input_rows += 1
            trade_date = _date_text(_text(row, "BAS_DD", "basDd", "date", "trade_date", "일자"))
            code = _text(row, "ISU_SRT_CD", "isuSrtCd", "code", "종목코드")
            if len(trade_date) != 10 or len(code) != 6 or not code.isdigit():
                rejected += 1
                continue
            name = _text(row, "ISU_ABBRV", "isuAbbrv", "name", "종목명")
            values = (
                _number(row, "TDD_OPNPRC", "tddOpnprc", "open", "시가"),
                _number(row, "TDD_HGPRC", "tddHgprc", "high", "고가"),
                _number(row, "TDD_LWPRC", "tddLwprc", "low", "저가"),
                _number(row, "TDD_CLSPRC", "tddClsprc", "close", "종가"),
            )
            if values[3] is None:
                rejected += 1
                continue
            c.execute(
                """INSERT INTO daily_bars(
                    trade_date,code,name,market,open,high,low,close,volume,value,market_cap,listed_shares,
                    adjusted,source,source_ref,imported_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(trade_date,code,market,adjusted,source) DO UPDATE SET
                    name=excluded.name,open=excluded.open,high=excluded.high,low=excluded.low,
                    close=excluded.close,volume=excluded.volume,value=excluded.value,
                    market_cap=excluded.market_cap,listed_shares=excluded.listed_shares,
                    source_ref=excluded.source_ref,imported_at=excluded.imported_at""",
                (
                    trade_date,
                    code,
                    name,
                    market,
                    values[0],
                    values[1],
                    values[2],
                    values[3],
                    _number(row, "ACC_TRDVOL", "accTrdvol", "volume", "거래량", integer=True),
                    _number(row, "ACC_TRDVAL", "accTrdval", "value", "거래대금"),
                    _number(row, "MKTCAP", "mrktTotAmt", "market_cap", "시가총액"),
                    _number(row, "LIST_SHRS", "listShrs", "listed_shares", "상장주식수", integer=True),
                    int(bool(adjusted)),
                    source,
                    source_ref,
                    _utc_now(),
                ),
            )
            written += 1
        _finish_run(c, run_id, input_rows, written, rejected)
        _refresh_provenance(c, "daily_bars", source, source_ref, adjusted)
    return {"ok": True, "datasetType": "daily_bars", "inputRows": input_rows, "writtenRows": written, "rejectedRows": rejected}


def import_index_rows(
    rows: Iterable[Mapping[str, object]],
    *,
    source: str = "KRX_EXPORT",
    source_ref: str = "",
    market: str = "KRX",
    path: str | os.PathLike[str] | None = None,
) -> dict[str, object]:
    init_db(path)
    source = str(source or "").strip().upper()
    source_ref = str(source_ref or "").strip()
    input_rows = written = rejected = 0
    with connect(path) as c:
        run_id = _begin_run(c, "index_daily", source, source_ref, False)
        for row in rows:
            input_rows += 1
            trade_date = _date_text(_text(row, "BAS_DD", "basDd", "date", "trade_date", "일자"))
            index_code = _text(row, "IDX_CLSS", "IDX_NM", "index_code", "지수코드", "지수명")
            index_name = _text(row, "IDX_NM", "index_name", "지수명")
            close = _number(row, "CLSPRC_IDX", "tddClsprc", "close", "종가")
            if len(trade_date) != 10 or not index_code or close is None:
                rejected += 1
                continue
            c.execute(
                """INSERT INTO index_daily(trade_date,index_code,index_name,market,open,high,low,close,volume,value,source,source_ref,imported_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(trade_date,index_code,source) DO UPDATE SET
                    index_name=excluded.index_name,market=excluded.market,open=excluded.open,
                    high=excluded.high,low=excluded.low,close=excluded.close,volume=excluded.volume,
                    value=excluded.value,source_ref=excluded.source_ref,imported_at=excluded.imported_at""",
                (
                    trade_date,
                    index_code,
                    index_name,
                    market,
                    _number(row, "OPNPRC_IDX", "open", "시가"),
                    _number(row, "HGPRC_IDX", "high", "고가"),
                    _number(row, "LWPRC_IDX", "low", "저가"),
                    close,
                    _number(row, "ACC_TRDVOL", "volume", "거래량", integer=True),
                    _number(row, "ACC_TRDVAL", "value", "거래대금"),
                    source,
                    source_ref,
                    _utc_now(),
                ),
            )
            written += 1
        _finish_run(c, run_id, input_rows, written, rejected)
        _refresh_provenance(c, "index_daily", source, source_ref, False)
    return {"ok": True, "datasetType": "index_daily", "inputRows": input_rows, "writtenRows": written, "rejectedRows": rejected}


def import_delisted_rows(
    rows: Iterable[Mapping[str, object]],
    *,
    source: str = "KRX_EXPORT",
    source_ref: str = "",
    path: str | os.PathLike[str] | None = None,
) -> dict[str, object]:
    init_db(path)
    source = str(source or "").strip().upper()
    source_ref = str(source_ref or "").strip()
    input_rows = written = rejected = 0
    with connect(path) as c:
        run_id = _begin_run(c, "delisted_symbols", source, source_ref, False)
        for row in rows:
            input_rows += 1
            code = _text(row, "code", "종목코드", "ISU_SRT_CD", "isuSrtCd")
            delisted = _date_text(_text(row, "delisted_date", "폐지일", "DELIST_DD"))
            if len(code) != 6 or not code.isdigit() or len(delisted) != 10:
                rejected += 1
                continue
            c.execute(
                """INSERT INTO delisted_symbols(code,name,market,listed_date,delisted_date,delist_reason,source,source_ref,imported_at)
                   VALUES(?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(code,delisted_date,source) DO UPDATE SET
                    name=excluded.name,market=excluded.market,listed_date=excluded.listed_date,
                    delist_reason=excluded.delist_reason,source_ref=excluded.source_ref,imported_at=excluded.imported_at""",
                (
                    code,
                    _text(row, "name", "종목명", "ISU_ABBRV"),
                    _text(row, "market", "시장구분", "MKT_NM").upper(),
                    _date_text(_text(row, "listed_date", "상장일", "LIST_DD")),
                    delisted,
                    _text(row, "delist_reason", "폐지사유", "DELIST_RSN"),
                    source,
                    source_ref,
                    _utc_now(),
                ),
            )
            written += 1
        _finish_run(c, run_id, input_rows, written, rejected)
        _refresh_provenance(c, "delisted_symbols", source, source_ref, False)
    return {"ok": True, "datasetType": "delisted_symbols", "inputRows": input_rows, "writtenRows": written, "rejectedRows": rejected}


def _read_input(file_path: str | os.PathLike[str]) -> list[dict[str, object]]:
    path = Path(file_path)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        last_error: Exception | None = None
        for encoding in ("utf-8-sig", "cp949", "euc-kr"):
            try:
                with path.open("r", encoding=encoding, newline="") as fh:
                    return [dict(row) for row in csv.DictReader(fh)]
            except UnicodeDecodeError as exc:
                last_error = exc
        if last_error:
            raise last_error
    if suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [dict(x) for x in data if isinstance(x, Mapping)]
        if isinstance(data, Mapping):
            for key in ("OutBlock_1", "output", "data", "rows"):
                value = data.get(key)
                if isinstance(value, list):
                    return [dict(x) for x in value if isinstance(x, Mapping)]
        raise ValueError("JSON must contain a row list")
    raise ValueError("only .csv and .json imports are supported")


def import_file(
    file_path: str | os.PathLike[str],
    *,
    dataset_type: str,
    market: str = "KRX",
    source: str = "KRX_EXPORT",
    source_ref: str = "",
    adjusted: bool = False,
    path: str | os.PathLike[str] | None = None,
) -> dict[str, object]:
    rows = _read_input(file_path)
    source_ref = source_ref or Path(file_path).name
    if dataset_type == "daily_bars":
        return import_daily_rows(rows, market=market, source=source, source_ref=source_ref, adjusted=adjusted, path=path)
    if dataset_type == "index_daily":
        return import_index_rows(rows, market=market, source=source, source_ref=source_ref, path=path)
    if dataset_type == "delisted_symbols":
        return import_delisted_rows(rows, source=source, source_ref=source_ref, path=path)
    raise ValueError("dataset_type must be daily_bars, index_daily, or delisted_symbols")


def status(path: str | os.PathLike[str] | None = None) -> dict[str, object]:
    db = Path(path or DB_PATH)
    if not db.exists():
        return {
            "ok": True,
            "researchOnly": True,
            "controlStrategy": CONTROL_STRATEGY,
            "realOrderEnabled": False,
            "path": str(db),
            "initialized": False,
            "readyForResearch": False,
        }
    init_db(db)
    with connect(db) as c:
        counts = {}
        for table in ("daily_bars", "index_daily", "symbol_history", "delisted_symbols", "corporate_actions"):
            counts[table] = int(c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        first_daily, last_daily = c.execute("SELECT MIN(trade_date),MAX(trade_date) FROM daily_bars").fetchone()
        markets = [dict(row) for row in c.execute("SELECT market,COUNT(*) rows FROM daily_bars GROUP BY market ORDER BY market")]
        provenance = [dict(row) for row in c.execute(
            "SELECT dataset_type,source,source_ref,adjusted,first_date,last_date,row_count,updated_at FROM data_provenance ORDER BY dataset_type,source,source_ref"
        )]
        recent_imports = [dict(row) for row in c.execute(
            "SELECT id,dataset_type,source,source_ref,adjusted,started_at,finished_at,input_rows,written_rows,rejected_rows,note FROM import_runs ORDER BY id DESC LIMIT 10"
        )]
    return {
        "ok": True,
        "researchOnly": True,
        "controlStrategy": CONTROL_STRATEGY,
        "realOrderEnabled": False,
        "path": str(db),
        "initialized": True,
        "schemaVersion": SCHEMA_VERSION,
        "readyForResearch": counts["daily_bars"] > 0 or counts["index_daily"] > 0,
        "counts": counts,
        "dailyRange": {"first": first_daily, "last": last_daily},
        "markets": markets,
        "provenance": provenance,
        "recentImports": recent_imports,
        "survivorshipSupport": counts["delisted_symbols"] > 0 or counts["symbol_history"] > 0,
        "limitations": [
            "daily/reference data does not replace NH 5m/1m forward evidence",
            "adjusted and unadjusted prices are kept distinct",
            "point-in-time universe reconstruction is incomplete until symbol/delisting history is populated",
        ],
    }


def _cli() -> int:
    parser = argparse.ArgumentParser(description="KRX historical reference DB (research only)")
    parser.add_argument("command", choices=("init", "status", "import"))
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--file")
    parser.add_argument("--type", dest="dataset_type", choices=("daily_bars", "index_daily", "delisted_symbols"))
    parser.add_argument("--market", default="KRX")
    parser.add_argument("--source", default="KRX_EXPORT")
    parser.add_argument("--source-ref", default="")
    parser.add_argument("--adjusted", action="store_true")
    args = parser.parse_args()
    if args.command == "init":
        result = init_db(args.db)
    elif args.command == "status":
        result = status(args.db)
    else:
        if not args.file or not args.dataset_type:
            parser.error("import requires --file and --type")
        result = import_file(
            args.file,
            dataset_type=args.dataset_type,
            market=args.market,
            source=args.source,
            source_ref=args.source_ref,
            adjusted=args.adjusted,
            path=args.db,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
