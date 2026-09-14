"""Official KRX Open API collector for the research-only historical database.

This module is intentionally isolated from Control/Paper execution. It never sends
broker orders, never reads NH credentials, and never mutates Control v0.8.0. It uses
only a locally supplied KRX Data Marketplace AUTH_KEY and stores official daily/reference
data in ``historical_market.db``.

The KRX key and per-service approvals must be obtained by the operator from KRX.
No key is embedded in code, logs, GitHub, or API responses. When no approved key is
present the daemon stays idle and reports ``waitingForAuthKey``.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from krx_historical_reference import (
    DB_PATH,
    connect,
    import_daily_rows,
    import_index_rows,
    init_db,
    status as historical_status,
)

KST = ZoneInfo("Asia/Seoul")
SOURCE = "KRX_OPEN_API"
CONTROL_STRATEGY = "v0.8.0 LOCKED"
REAL_ORDER_ENABLED = False
RESEARCH_ONLY = True
BASE_URL = "https://data-dbg.krx.co.kr/svc/apis"
DEFAULT_BACKFILL_START = date(2010, 1, 4)

ENDPOINTS = {
    "kospi_daily": ("sto", "stk_bydd_trd", "daily_bars", "KOSPI"),
    "kosdaq_daily": ("sto", "ksq_bydd_trd", "daily_bars", "KOSDAQ"),
    "kospi_index": ("idx", "kospi_dd_trd", "index_daily", "KOSPI"),
    "kosdaq_index": ("idx", "kosdaq_dd_trd", "index_daily", "KOSDAQ"),
    "kospi_symbols": ("sto", "stk_isu_base_info", "symbol_history", "KOSPI"),
    "kosdaq_symbols": ("sto", "ksq_isu_base_info", "symbol_history", "KOSDAQ"),
}
CORE_ENDPOINTS = ("kospi_daily", "kosdaq_daily", "kospi_index", "kosdaq_index")
SYMBOL_ENDPOINTS = ("kospi_symbols", "kosdaq_symbols")


class KRXAccessError(RuntimeError):
    """Authentication/service-approval failure. Never includes the actual key."""


class KRXResponseError(RuntimeError):
    """Malformed/unexpected KRX response."""


def _local_env() -> dict[str, str]:
    """Read server/.env without evaluating shell syntax or exposing secret values."""
    env_path = Path(__file__).resolve().parent / ".env"
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


def auth_key_configured() -> bool:
    key = _cfg("KRX_AUTH_KEY")
    return bool(key and key not in {"YOUR_KRX_AUTH_KEY", "YOUR_AUTH_KEY", "CHANGE_ME"})


def _auth_key() -> str:
    key = _cfg("KRX_AUTH_KEY")
    if not key or key in {"YOUR_KRX_AUTH_KEY", "YOUR_AUTH_KEY", "CHANGE_ME"}:
        raise KRXAccessError("KRX AUTH_KEY is not configured locally")
    return key


def _bool_cfg(name: str, default: bool) -> bool:
    raw = _cfg(name, "true" if default else "false").lower()
    return raw in {"1", "true", "yes", "on"}


def _int_cfg(name: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(_cfg(name, str(default)))
    except ValueError:
        value = default
    return max(lo, min(value, hi))


def _float_cfg(name: str, default: float, lo: float, hi: float) -> float:
    try:
        value = float(_cfg(name, str(default)))
    except ValueError:
        value = default
    return max(lo, min(value, hi))


def _endpoint_url(name: str) -> str:
    category, api_id, _, _ = ENDPOINTS[name]
    return f"{BASE_URL}/{category}/{api_id}"


def _extract_rows(payload: object) -> list[dict[str, object]]:
    if not isinstance(payload, Mapping):
        raise KRXResponseError("KRX response is not a JSON object")
    for key in ("OutBlock_1", "output", "data", "rows"):
        value = payload.get(key)
        if isinstance(value, list):
            return [dict(row) for row in value if isinstance(row, Mapping)]
    # An approved API may legitimately return no rows for a holiday. KRX normally
    # returns OutBlock_1, so an object without any known row block is treated as an error.
    msg = str(payload.get("message") or payload.get("msg") or payload.get("error") or "").strip()
    if msg:
        raise KRXResponseError(f"KRX response error: {msg[:200]}")
    raise KRXResponseError("KRX response does not contain OutBlock_1")


def request_rows(
    endpoint_name: str,
    bas_dd: str,
    *,
    auth_key: str | None = None,
    opener: Callable[..., object] = urlopen,
    timeout: float = 20.0,
) -> list[dict[str, object]]:
    """Fetch one official KRX endpoint/day. The key is never included in exceptions."""
    if endpoint_name not in ENDPOINTS:
        raise ValueError(f"unknown KRX endpoint: {endpoint_name}")
    key = auth_key or _auth_key()
    if not key:
        raise KRXAccessError("KRX AUTH_KEY is not configured locally")
    url = f"{_endpoint_url(endpoint_name)}?{urlencode({'basDd': bas_dd})}"
    request = Request(
        url,
        headers={
            "AUTH_KEY": key,
            "Accept": "application/json",
            "User-Agent": "stock-trader-research/0.17.15",
        },
        method="GET",
    )
    try:
        response = opener(request, timeout=timeout)
        raw = response.read()
    except HTTPError as exc:
        if exc.code in {401, 403}:
            raise KRXAccessError(
                f"KRX API access denied (HTTP {exc.code}); check AUTH_KEY and per-service approval"
            ) from None
        raise KRXResponseError(f"KRX HTTP error: {exc.code}") from None
    except URLError as exc:
        raise KRXResponseError(f"KRX network error: {type(exc.reason).__name__}") from None
    except TimeoutError:
        raise KRXResponseError("KRX request timed out") from None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise KRXResponseError("KRX response is not valid UTF-8 JSON") from None
    return _extract_rows(payload)


def _normalize_daily_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    normalized: list[dict[str, object]] = []
    for source_row in rows:
        row = dict(source_row)
        # Official daily trade APIs expose ISU_CD. Historical importer also supports
        # ISU_SRT_CD, so normalize a six-digit ISU_CD into that field locally.
        code = str(row.get("ISU_SRT_CD") or row.get("ISU_CD") or "").strip()
        if len(code) == 6 and code.isdigit():
            row["ISU_SRT_CD"] = code
        if not row.get("ISU_ABBRV") and row.get("ISU_NM"):
            row["ISU_ABBRV"] = row.get("ISU_NM")
        normalized.append(row)
    return normalized


def _normalize_index_rows(rows: list[dict[str, object]], market: str) -> list[dict[str, object]]:
    normalized: list[dict[str, object]] = []
    for source_row in rows:
        row = dict(source_row)
        name = str(row.get("IDX_NM") or "").strip()
        cls = str(row.get("IDX_CLSS") or "").strip()
        # The historical DB primary key needs a stable unique identifier. IDX_CLSS
        # alone can represent a family, so combine family + index name when possible.
        if name:
            row["IDX_CLSS"] = f"{market}:{cls}:{name}" if cls else f"{market}:{name}"
        normalized.append(row)
    return normalized


def _ensure_collector_tables(path: str | os.PathLike[str] | None = None) -> None:
    init_db(path)
    with connect(path) as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS krx_official_collection_log(
                trade_date TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                attempted_at TEXT NOT NULL,
                detail_json TEXT,
                error TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_krx_collection_status
              ON krx_official_collection_log(status, trade_date);
            """
        )


def _set_meta(key: str, value: object, path: str | os.PathLike[str] | None = None) -> None:
    _ensure_collector_tables(path)
    with connect(path) as c:
        c.execute(
            "INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )


def _get_meta(key: str, path: str | os.PathLike[str] | None = None) -> str | None:
    _ensure_collector_tables(path)
    with connect(path) as c:
        row = c.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return str(row[0]) if row else None


def _record_collection(
    day: date,
    status: str,
    detail: Mapping[str, object],
    error: str | None,
    path: str | os.PathLike[str] | None = None,
) -> None:
    _ensure_collector_tables(path)
    with connect(path) as c:
        c.execute(
            """INSERT INTO krx_official_collection_log(trade_date,status,attempted_at,detail_json,error)
               VALUES(?,?,?,?,?)
               ON CONFLICT(trade_date) DO UPDATE SET
                 status=excluded.status,attempted_at=excluded.attempted_at,
                 detail_json=excluded.detail_json,error=excluded.error""",
            (
                day.isoformat(),
                status,
                datetime.now(KST).isoformat(timespec="seconds"),
                json.dumps(dict(detail), ensure_ascii=False, sort_keys=True),
                (error or "")[:500] or None,
            ),
        )


def _dataset_present(
    endpoint_name: str,
    day: date,
    path: str | os.PathLike[str] | None = None,
) -> bool:
    _, _, dataset_type, market = ENDPOINTS[endpoint_name]
    day_text = day.isoformat()
    _ensure_collector_tables(path)
    with connect(path) as c:
        if dataset_type == "daily_bars":
            row = c.execute(
                "SELECT 1 FROM daily_bars WHERE trade_date=? AND market=? AND source=? LIMIT 1",
                (day_text, market, SOURCE),
            ).fetchone()
        elif dataset_type == "index_daily":
            row = c.execute(
                "SELECT 1 FROM index_daily WHERE trade_date=? AND market=? AND source=? LIMIT 1",
                (day_text, market, SOURCE),
            ).fetchone()
        else:
            row = c.execute(
                "SELECT 1 FROM symbol_history WHERE effective_from=? AND market=? AND source=? LIMIT 1",
                (day_text, market, SOURCE),
            ).fetchone()
        return bool(row)


def _month_symbol_snapshot_present(
    day: date,
    market: str,
    path: str | os.PathLike[str] | None = None,
) -> bool:
    _ensure_collector_tables(path)
    month = day.strftime("%Y-%m")
    with connect(path) as c:
        return bool(
            c.execute(
                "SELECT 1 FROM symbol_history WHERE substr(effective_from,1,7)=? AND market=? AND source=? LIMIT 1",
                (month, market, SOURCE),
            ).fetchone()
        )


def _import_symbol_snapshot(
    rows: list[dict[str, object]],
    *,
    day: date,
    market: str,
    source_ref: str,
    path: str | os.PathLike[str] | None = None,
) -> dict[str, int]:
    """Store an immutable point-in-time listing snapshot without inferring delisting."""
    _ensure_collector_tables(path)
    written = rejected = 0
    day_text = day.isoformat()
    now = datetime.now(KST).isoformat(timespec="seconds")
    with connect(path) as c:
        for row in rows:
            code = str(row.get("ISU_SRT_CD") or "").strip()
            if len(code) != 6 or not code.isdigit():
                rejected += 1
                continue
            listed_raw = str(row.get("LIST_DD") or "").strip().replace("-", "")
            listed = (
                f"{listed_raw[:4]}-{listed_raw[4:6]}-{listed_raw[6:]}"
                if len(listed_raw) == 8 and listed_raw.isdigit()
                else None
            )
            c.execute(
                """INSERT INTO symbol_history(
                    code,effective_from,effective_to,name,market,security_type,
                    listed_date,delisted_date,source,source_ref,imported_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(code,effective_from,source) DO UPDATE SET
                    effective_to=excluded.effective_to,name=excluded.name,market=excluded.market,
                    security_type=excluded.security_type,listed_date=excluded.listed_date,
                    source_ref=excluded.source_ref,imported_at=excluded.imported_at""",
                (
                    code,
                    day_text,
                    day_text,
                    str(row.get("ISU_ABBRV") or row.get("ISU_NM") or "").strip(),
                    market,
                    str(row.get("SECUGRP_NM") or row.get("KIND_STKCERT_TP_NM") or "").strip(),
                    listed,
                    None,
                    SOURCE,
                    source_ref,
                    now,
                ),
            )
            written += 1
    return {"writtenRows": written, "rejectedRows": rejected}


def _within_live_session(now: datetime | None = None) -> bool:
    now = now or datetime.now(KST)
    if now.weekday() >= 5:
        return False
    hm = now.hour * 60 + now.minute
    return 8 * 60 + 50 <= hm <= 15 * 60 + 40


def collect_date(
    day: date,
    *,
    auth_key: str | None = None,
    opener: Callable[..., object] = urlopen,
    path: str | os.PathLike[str] | None = None,
    include_symbols: bool = True,
    interval_sec: float | None = None,
) -> dict[str, object]:
    """Collect one date from approved official KRX APIs and persist idempotently."""
    _ensure_collector_tables(path)
    if day.weekday() >= 5:
        result = {"ok": True, "date": day.isoformat(), "status": "WEEKEND", "rows": 0}
        _record_collection(day, "NO_DATA", result, None, path)
        return result
    key = auth_key or _auth_key()
    interval = _float_cfg("KRX_REQUEST_INTERVAL_SEC", 1.0, 0.2, 10.0) if interval_sec is None else max(0.0, interval_sec)
    detail: dict[str, object] = {}
    total_rows = 0
    access_error: str | None = None
    response_error: str | None = None

    for endpoint_name in CORE_ENDPOINTS:
        if _dataset_present(endpoint_name, day, path):
            detail[endpoint_name] = {"cached": True}
            continue
        _, api_id, dataset_type, market = ENDPOINTS[endpoint_name]
        try:
            rows = request_rows(
                endpoint_name,
                day.strftime("%Y%m%d"),
                auth_key=key,
                opener=opener,
            )
            if dataset_type == "daily_bars":
                normalized = _normalize_daily_rows(rows)
                imported = import_daily_rows(
                    normalized,
                    market=market,
                    source=SOURCE,
                    source_ref=api_id,
                    adjusted=False,
                    path=path,
                )
            else:
                normalized = _normalize_index_rows(rows, market)
                imported = import_index_rows(
                    normalized,
                    market=market,
                    source=SOURCE,
                    source_ref=api_id,
                    path=path,
                )
            count = int(imported.get("writtenRows") or 0)
            total_rows += count
            detail[endpoint_name] = {
                "cached": False,
                "receivedRows": len(rows),
                "writtenRows": count,
                "rejectedRows": int(imported.get("rejectedRows") or 0),
            }
        except KRXAccessError as exc:
            access_error = str(exc)
            detail[endpoint_name] = {"error": access_error}
            break
        except KRXResponseError as exc:
            response_error = str(exc)
            detail[endpoint_name] = {"error": response_error}
            break
        if interval:
            time.sleep(interval)

    core_complete = all(_dataset_present(name, day, path) for name in CORE_ENDPOINTS)
    core_any = any(_dataset_present(name, day, path) for name in CORE_ENDPOINTS)

    # Listing snapshots are deliberately monthly, not daily, to keep the Android DB
    # compact. Absence from one snapshot is NOT automatically labeled as delisted.
    symbol_errors: list[str] = []
    if include_symbols and core_any and not access_error and not response_error:
        for endpoint_name in SYMBOL_ENDPOINTS:
            _, api_id, _, market = ENDPOINTS[endpoint_name]
            if _month_symbol_snapshot_present(day, market, path):
                detail[endpoint_name] = {"cachedMonth": True}
                continue
            try:
                rows = request_rows(
                    endpoint_name,
                    day.strftime("%Y%m%d"),
                    auth_key=key,
                    opener=opener,
                )
                imported = _import_symbol_snapshot(
                    rows,
                    day=day,
                    market=market,
                    source_ref=api_id,
                    path=path,
                )
                total_rows += int(imported["writtenRows"])
                detail[endpoint_name] = {"receivedRows": len(rows), **imported}
            except (KRXAccessError, KRXResponseError) as exc:
                # Symbol/basic-info approval is useful but must not discard already
                # collected official prices and indices.
                symbol_errors.append(f"{endpoint_name}: {exc}")
                detail[endpoint_name] = {"error": str(exc)}
            if interval:
                time.sleep(interval)

    if access_error:
        status = "ACCESS_BLOCKED"
        error = access_error
    elif response_error:
        status = "ERROR"
        error = response_error
    elif core_complete:
        status = "COMPLETE"
        error = "; ".join(symbol_errors) or None
    elif not core_any:
        # A weekday with zero rows across every core endpoint is normally a holiday.
        status = "NO_DATA"
        error = None
    else:
        status = "PARTIAL"
        error = "one or more core KRX datasets are missing"

    result = {
        "ok": status in {"COMPLETE", "NO_DATA"},
        "date": day.isoformat(),
        "status": status,
        "officialRowsWritten": total_rows,
        "detail": detail,
        "symbolSnapshotWarnings": symbol_errors,
        "researchOnly": True,
        "realOrderEnabled": False,
    }
    _record_collection(day, status, detail, error, path)
    if access_error:
        _set_meta("krx_official_last_access_error", access_error, path)
    elif status in {"COMPLETE", "NO_DATA"}:
        _set_meta("krx_official_last_success", day.isoformat(), path)
    return result


def _parse_start_date() -> date:
    raw = _cfg("KRX_BACKFILL_START", "20100104").replace("-", "")
    try:
        parsed = datetime.strptime(raw, "%Y%m%d").date()
    except ValueError:
        parsed = DEFAULT_BACKFILL_START
    return max(DEFAULT_BACKFILL_START, parsed)


def _completed_dates(path: str | os.PathLike[str] | None = None) -> set[str]:
    _ensure_collector_tables(path)
    with connect(path) as c:
        return {
            str(row[0])
            for row in c.execute(
                "SELECT trade_date FROM krx_official_collection_log WHERE status IN ('COMPLETE','NO_DATA')"
            )
        }


def _backfill_candidates(
    limit: int,
    *,
    end: date | None = None,
    path: str | os.PathLike[str] | None = None,
) -> list[date]:
    start = _parse_start_date()
    cursor = end or datetime.now(KST).date()
    done = _completed_dates(path)
    out: list[date] = []
    while cursor >= start and len(out) < limit:
        if cursor.weekday() < 5 and cursor.isoformat() not in done:
            out.append(cursor)
        cursor -= timedelta(days=1)
    return out


def run_cycle(
    *,
    auth_key: str | None = None,
    opener: Callable[..., object] = urlopen,
    path: str | os.PathLike[str] | None = None,
    max_days: int | None = None,
    interval_sec: float | None = None,
) -> dict[str, object]:
    """Sync recent dates first, then fill older official history newest-to-oldest."""
    if _within_live_session():
        return {"ok": True, "deferred": True, "reason": "live_session_priority", "researchOnly": True}
    key = auth_key or _auth_key()
    limit = max_days or _int_cfg("KRX_BACKFILL_DAYS_PER_CYCLE", 60, 1, 250)
    today = datetime.now(KST).date()
    candidates: list[date] = []
    # Recent dates first, including today after the configured EOD hour.
    for offset in range(0, 8):
        d = today - timedelta(days=offset)
        if d.weekday() < 5:
            candidates.append(d)
    for d in _backfill_candidates(limit, end=today, path=path):
        if d not in candidates:
            candidates.append(d)
        if len(candidates) >= limit + 8:
            break

    results: list[dict[str, object]] = []
    access_blocked = False
    for day in candidates:
        result = collect_date(
            day,
            auth_key=key,
            opener=opener,
            path=path,
            include_symbols=True,
            interval_sec=interval_sec,
        )
        results.append(result)
        if result.get("status") == "ACCESS_BLOCKED":
            access_blocked = True
            break
    _set_meta("krx_official_last_cycle_at", datetime.now(KST).isoformat(timespec="seconds"), path)
    return {
        "ok": not access_blocked,
        "researchOnly": True,
        "realOrderEnabled": False,
        "datesAttempted": len(results),
        "accessBlocked": access_blocked,
        "results": results,
    }


def collector_status(path: str | os.PathLike[str] | None = None) -> dict[str, object]:
    _ensure_collector_tables(path)
    hist = historical_status(path)
    with connect(path) as c:
        official_daily = int(c.execute("SELECT COUNT(*) FROM daily_bars WHERE source=?", (SOURCE,)).fetchone()[0])
        official_index = int(c.execute("SELECT COUNT(*) FROM index_daily WHERE source=?", (SOURCE,)).fetchone()[0])
        official_symbols = int(c.execute("SELECT COUNT(*) FROM symbol_history WHERE source=?", (SOURCE,)).fetchone()[0])
        completed = int(c.execute("SELECT COUNT(*) FROM krx_official_collection_log WHERE status='COMPLETE'").fetchone()[0])
        no_data = int(c.execute("SELECT COUNT(*) FROM krx_official_collection_log WHERE status='NO_DATA'").fetchone()[0])
        blocked = int(c.execute("SELECT COUNT(*) FROM krx_official_collection_log WHERE status='ACCESS_BLOCKED'").fetchone()[0])
        latest = c.execute(
            "SELECT trade_date,status,attempted_at,error FROM krx_official_collection_log ORDER BY attempted_at DESC LIMIT 1"
        ).fetchone()
    return {
        "ok": True,
        "researchOnly": True,
        "controlStrategy": CONTROL_STRATEGY,
        "realOrderEnabled": REAL_ORDER_ENABLED,
        "autoCollectEnabled": _bool_cfg("KRX_OFFICIAL_AUTO_COLLECT", True),
        "authKeyConfigured": auth_key_configured(),
        "waitingForAuthKey": not auth_key_configured(),
        "officialSource": SOURCE,
        "officialRows": {
            "dailyBars": official_daily,
            "indexDaily": official_index,
            "symbolSnapshots": official_symbols,
        },
        "collectionDates": {"complete": completed, "noData": no_data, "accessBlocked": blocked},
        "latestAttempt": dict(latest) if latest else None,
        "lastSuccess": _get_meta("krx_official_last_success", path),
        "lastCycleAt": _get_meta("krx_official_last_cycle_at", path),
        "lastAccessError": _get_meta("krx_official_last_access_error", path),
        "historicalDb": hist,
        "requiredApprovals": [
            "유가증권 일별매매정보",
            "코스닥 일별매매정보",
            "KOSPI 시리즈 일별시세정보",
            "KOSDAQ 시리즈 일별시세정보",
            "유가증권 종목기본정보 (recommended)",
            "코스닥 종목기본정보 (recommended)",
        ],
    }


def daemon(instance_version: str = "krx-official-1") -> int:
    """Low-priority Android daemon: EOD sync + gradual 2010+ official backfill."""
    print(f"[KRX] official research collector started ({instance_version}); REAL ORDER OFF", flush=True)
    last_state = ""
    while True:
        enabled = _bool_cfg("KRX_OFFICIAL_AUTO_COLLECT", True)
        if not enabled:
            state = "disabled"
            if state != last_state:
                print("[KRX] auto collection disabled by local config", flush=True)
                last_state = state
            time.sleep(300)
            continue
        if not auth_key_configured():
            state = "waiting_for_auth_key"
            if state != last_state:
                print("[KRX] waiting for locally configured KRX_AUTH_KEY; no network call made", flush=True)
                last_state = state
            time.sleep(300)
            continue
        now = datetime.now(KST)
        start_hour = _int_cfg("KRX_EOD_START_HOUR", 19, 16, 23)
        allowed = now.hour >= start_hour or now.hour < 8
        if _within_live_session(now) or not allowed:
            state = "waiting_for_eod_window"
            if state != last_state:
                print("[KRX] waiting for EOD research window", flush=True)
                last_state = state
            time.sleep(600)
            continue
        state = "running"
        if state != last_state:
            print("[KRX] official EOD/backfill cycle running", flush=True)
            last_state = state
        try:
            result = run_cycle()
            if result.get("accessBlocked"):
                print("[KRX] access blocked; check KRX key and service approvals (key not logged)", flush=True)
                time.sleep(1800)
            else:
                print(f"[KRX] cycle complete: dates={result.get('datesAttempted', 0)}", flush=True)
                time.sleep(_int_cfg("KRX_CYCLE_INTERVAL_MIN", 120, 30, 720) * 60)
        except KRXAccessError:
            print("[KRX] access blocked; check local KRX authorization (key not logged)", flush=True)
            time.sleep(1800)
        except Exception as exc:  # fail isolated; never stop Paper/Control
            print(f"[KRX] collector error: {type(exc).__name__}: {str(exc)[:200]}", flush=True)
            time.sleep(900)
    return 0


def _cli() -> int:
    parser = argparse.ArgumentParser(description="Official KRX historical collector (research only)")
    parser.add_argument("command", choices=("status", "once", "daemon"))
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--instance-version", default="krx-official-1")
    args = parser.parse_args()
    if args.command == "status":
        result = collector_status(args.db)
    elif args.command == "once":
        result = run_cycle(path=args.db, max_days=args.days)
    else:
        return daemon(args.instance_version)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
