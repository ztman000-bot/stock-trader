"""Free official Korean-market daily/reference collector via data.go.kr.

Research-only. This collector uses Financial Services Commission Open APIs that relay
KRX-origin data through Korea's Public Data Portal. It is deliberately isolated from
Control/Paper execution: it never sends broker orders, never reads NH credentials,
and never mutates Control v0.8.0.

The operator must obtain a free data.go.kr service key and apply for the relevant
Open APIs. The key stays in ``server/.env`` and is never logged or committed.

Because usage/licence terms can differ by dataset, this integration is intentionally
restricted to the user's private research database. It must not be used to redistribute
raw KRX-linked public data. Check the current data.go.kr licence before any commercial
or third-party use.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlencode
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
SOURCE = "FSC_DATA_GO_KR"
CONTROL_STRATEGY = "v0.8.0 LOCKED"
REAL_ORDER_ENABLED = False
RESEARCH_ONLY = True

STOCK_URL = "https://apis.data.go.kr/1160100/service/GetStockSecuritiesInfoService/getStockPriceInfo"
INDEX_URL = "https://apis.data.go.kr/1160100/service/GetMarketIndexInfoService/getStockMarketIndex"
LISTED_URL = "https://apis.data.go.kr/1160100/service/GetKrxListedInfoService/getItemInfo"

STOCK_SOURCE_REF = "GetStockSecuritiesInfoService/getStockPriceInfo"
INDEX_SOURCE_REF = "GetMarketIndexInfoService/getStockMarketIndex"
LISTED_SOURCE_REF = "GetKrxListedInfoService/getItemInfo"
DEFAULT_BACKFILL_START = date(2020, 1, 2)
ACCESS_CODES = {"20", "30", "31"}
RATE_LIMIT_CODES = {"22", "23"}


class PublicDataAccessError(RuntimeError):
    """Missing/invalid key or service permission. Never includes the actual key."""


class PublicDataResponseError(RuntimeError):
    """Network, gateway or malformed-response failure."""


def _local_env() -> dict[str, str]:
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


def service_key_configured() -> bool:
    key = _cfg("DATA_GO_KR_SERVICE_KEY") or _cfg("FSC_PUBLIC_SERVICE_KEY")
    return bool(key and key not in {"YOUR_DATA_GO_KR_SERVICE_KEY", "YOUR_SERVICE_KEY", "CHANGE_ME"})


def _service_key() -> str:
    key = _cfg("DATA_GO_KR_SERVICE_KEY") or _cfg("FSC_PUBLIC_SERVICE_KEY")
    if not key or key in {"YOUR_DATA_GO_KR_SERVICE_KEY", "YOUR_SERVICE_KEY", "CHANGE_ME"}:
        raise PublicDataAccessError("data.go.kr service key is not configured locally")
    # data.go.kr exposes both encoded and decoded key forms. Normalise once so
    # urlencode() cannot accidentally double-encode an already encoded key.
    return unquote(key)


def _safe_message(value: object) -> str:
    text = str(value or "").strip()
    # Never allow query-string looking material into logs/errors.
    text = re.sub(r"serviceKey=[^&\s]+", "serviceKey=[REDACTED]", text, flags=re.I)
    return text[:240]


def _parse_response(payload: object) -> tuple[list[dict[str, object]], int, int]:
    if not isinstance(payload, Mapping):
        raise PublicDataResponseError("public-data response is not a JSON object")
    response = payload.get("response")
    if not isinstance(response, Mapping):
        raise PublicDataResponseError("public-data response envelope is missing")
    header = response.get("header") or {}
    if not isinstance(header, Mapping):
        header = {}
    code = str(header.get("resultCode") or "").strip()
    msg = _safe_message(header.get("resultMsg"))
    if code and code not in {"00", "0"}:
        if code in ACCESS_CODES:
            raise PublicDataAccessError(f"data.go.kr access denied (code {code}); check service key/application")
        if code in RATE_LIMIT_CODES:
            raise PublicDataResponseError(f"data.go.kr rate limit reached (code {code})")
        raise PublicDataResponseError(f"data.go.kr error code {code}: {msg or 'request failed'}")

    body = response.get("body") or {}
    if not isinstance(body, Mapping):
        raise PublicDataResponseError("public-data response body is missing")
    items_box = body.get("items")
    item_value: object = []
    if isinstance(items_box, Mapping):
        item_value = items_box.get("item") or []
    elif isinstance(items_box, list):
        item_value = items_box
    if isinstance(item_value, Mapping):
        rows = [dict(item_value)]
    elif isinstance(item_value, list):
        rows = [dict(x) for x in item_value if isinstance(x, Mapping)]
    else:
        rows = []
    try:
        total = int(body.get("totalCount") or len(rows))
    except (TypeError, ValueError):
        total = len(rows)
    try:
        page_size = int(body.get("numOfRows") or max(1, len(rows)))
    except (TypeError, ValueError):
        page_size = max(1, len(rows))
    return rows, max(0, total), max(1, page_size)


def _request_page(
    url: str,
    params: Mapping[str, object],
    *,
    service_key: str | None = None,
    opener: Callable[..., object] = urlopen,
    timeout: float = 20.0,
) -> tuple[list[dict[str, object]], int, int]:
    key = service_key or _service_key()
    query = dict(params)
    query.update({"serviceKey": key, "resultType": "json"})
    request = Request(
        f"{url}?{urlencode(query)}",
        headers={"Accept": "application/json", "User-Agent": "stock-trader-research/0.17.15"},
        method="GET",
    )
    try:
        response = opener(request, timeout=timeout)
        raw = response.read()
    except HTTPError as exc:
        if exc.code in {401, 403}:
            raise PublicDataAccessError(
                f"data.go.kr access denied (HTTP {exc.code}); check service key/application"
            ) from None
        raise PublicDataResponseError(f"data.go.kr HTTP error: {exc.code}") from None
    except URLError as exc:
        raise PublicDataResponseError(f"data.go.kr network error: {type(exc.reason).__name__}") from None
    except TimeoutError:
        raise PublicDataResponseError("data.go.kr request timed out") from None

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        text = raw.decode("utf-8", errors="ignore")
        upper = text.upper()
        if any(token in upper for token in ("SERVICE_KEY", "PERMISSION_DENIED", "ACCESS_DENIED")):
            raise PublicDataAccessError("data.go.kr authentication/application error") from None
        raise PublicDataResponseError("data.go.kr response is not valid JSON") from None
    return _parse_response(payload)


def request_all_rows(
    url: str,
    filters: Mapping[str, object],
    *,
    service_key: str | None = None,
    opener: Callable[..., object] = urlopen,
    page_size: int = 1000,
    max_pages: int = 20,
    interval_sec: float | None = None,
) -> list[dict[str, object]]:
    """Fetch all pages for one filtered public-data query with a hard page cap."""
    key = service_key or _service_key()
    rows: list[dict[str, object]] = []
    interval = (
        _float_cfg("FSC_PUBLIC_REQUEST_INTERVAL_SEC", 0.5, 0.1, 10.0)
        if interval_sec is None
        else max(0.0, interval_sec)
    )
    size = max(1, min(int(page_size), 1000))
    for page in range(1, max(1, max_pages) + 1):
        page_rows, total, returned_size = _request_page(
            url,
            {**dict(filters), "numOfRows": size, "pageNo": page},
            service_key=key,
            opener=opener,
        )
        rows.extend(page_rows)
        if len(rows) >= total or not page_rows:
            break
        # Defensive against a gateway ignoring numOfRows.
        if page * max(size, returned_size) >= total:
            break
        if interval:
            time.sleep(interval)
    return rows


def _canonical_stock_row(row: Mapping[str, object]) -> dict[str, object]:
    return {
        "BAS_DD": row.get("basDt"),
        "ISU_SRT_CD": row.get("srtnCd"),
        "ISU_ABBRV": row.get("itmsNm"),
        "TDD_OPNPRC": row.get("mkp"),
        "TDD_HGPRC": row.get("hipr"),
        "TDD_LWPRC": row.get("lopr"),
        "TDD_CLSPRC": row.get("clpr"),
        "ACC_TRDVOL": row.get("trqu"),
        "ACC_TRDVAL": row.get("trPrc"),
        "MKTCAP": row.get("mrktTotAmt"),
        "LIST_SHRS": row.get("lstgStCnt"),
    }


def _index_market(row: Mapping[str, object]) -> str | None:
    name = str(row.get("idxNm") or "").strip().lower().replace(" ", "")
    cls = str(row.get("idxCsf") or "").strip().lower().replace(" ", "")
    if name in {"코스피", "kospi"}:
        return "KOSPI"
    if name in {"코스닥", "kosdaq"}:
        return "KOSDAQ"
    # Keep the fallback conservative: family names alone can contain many
    # sub-indices, so only use them when the row itself is the family headline.
    if cls in {"코스피", "kospi"} and name in {"", "코스피", "kospi"}:
        return "KOSPI"
    if cls in {"코스닥", "kosdaq"} and name in {"", "코스닥", "kosdaq"}:
        return "KOSDAQ"
    return None


def _canonical_index_row(row: Mapping[str, object], market: str) -> dict[str, object]:
    name = str(row.get("idxNm") or market).strip()
    family = str(row.get("idxCsf") or market).strip()
    return {
        "BAS_DD": row.get("basDt"),
        "IDX_CLSS": f"{market}:{family}:{name}",
        "IDX_NM": name,
        "OPNPRC_IDX": row.get("mkp"),
        "HGPRC_IDX": row.get("hipr"),
        "LWPRC_IDX": row.get("lopr"),
        "CLSPRC_IDX": row.get("clpr"),
        "ACC_TRDVOL": row.get("trqu"),
        "ACC_TRDVAL": row.get("trPrc"),
    }


def _ensure_tables(path: str | os.PathLike[str] | None = None) -> None:
    init_db(path)
    with connect(path) as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS fsc_public_collection_log(
                trade_date TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                attempted_at TEXT NOT NULL,
                detail_json TEXT,
                error TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_fsc_public_collection_status
              ON fsc_public_collection_log(status, trade_date);
            """
        )


def _set_meta(key: str, value: object, path: str | os.PathLike[str] | None = None) -> None:
    _ensure_tables(path)
    with connect(path) as c:
        c.execute(
            "INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )


def _get_meta(key: str, path: str | os.PathLike[str] | None = None) -> str | None:
    _ensure_tables(path)
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
    _ensure_tables(path)
    with connect(path) as c:
        c.execute(
            """INSERT INTO fsc_public_collection_log(trade_date,status,attempted_at,detail_json,error)
               VALUES(?,?,?,?,?)
               ON CONFLICT(trade_date) DO UPDATE SET
                 status=excluded.status,attempted_at=excluded.attempted_at,
                 detail_json=excluded.detail_json,error=excluded.error""",
            (
                day.isoformat(),
                status,
                datetime.now(KST).isoformat(timespec="seconds"),
                json.dumps(dict(detail), ensure_ascii=False, sort_keys=True),
                _safe_message(error) or None,
            ),
        )


def _daily_present(day: date, market: str, path: str | os.PathLike[str] | None = None) -> bool:
    _ensure_tables(path)
    with connect(path) as c:
        return bool(
            c.execute(
                "SELECT 1 FROM daily_bars WHERE trade_date=? AND market=? AND source=? LIMIT 1",
                (day.isoformat(), market, SOURCE),
            ).fetchone()
        )


def _index_present(day: date, market: str, path: str | os.PathLike[str] | None = None) -> bool:
    _ensure_tables(path)
    with connect(path) as c:
        return bool(
            c.execute(
                "SELECT 1 FROM index_daily WHERE trade_date=? AND market=? AND source=? LIMIT 1",
                (day.isoformat(), market, SOURCE),
            ).fetchone()
        )


def _month_symbol_snapshot_present(
    day: date, path: str | os.PathLike[str] | None = None
) -> bool:
    _ensure_tables(path)
    month = day.strftime("%Y-%m")
    with connect(path) as c:
        return bool(
            c.execute(
                "SELECT 1 FROM symbol_history WHERE substr(effective_from,1,7)=? AND source=? LIMIT 1",
                (month, SOURCE),
            ).fetchone()
        )


def _import_symbol_snapshot(
    rows: list[dict[str, object]],
    *,
    day: date,
    path: str | os.PathLike[str] | None = None,
) -> dict[str, int]:
    _ensure_tables(path)
    written = rejected = 0
    now = datetime.now(KST).isoformat(timespec="seconds")
    with connect(path) as c:
        for row in rows:
            code = str(row.get("srtnCd") or "").strip()
            market = str(row.get("mrktCtg") or "").strip().upper()
            if len(code) != 6 or not code.isdigit() or market not in {"KOSPI", "KOSDAQ"}:
                rejected += 1
                continue
            c.execute(
                """INSERT INTO symbol_history(
                    code,effective_from,effective_to,name,market,security_type,
                    listed_date,delisted_date,source,source_ref,imported_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(code,effective_from,source) DO UPDATE SET
                    effective_to=excluded.effective_to,name=excluded.name,market=excluded.market,
                    security_type=excluded.security_type,source_ref=excluded.source_ref,
                    imported_at=excluded.imported_at""",
                (
                    code,
                    day.isoformat(),
                    day.isoformat(),
                    str(row.get("itmsNm") or "").strip(),
                    market,
                    str(row.get("isinCd") or "").strip(),
                    None,
                    None,
                    SOURCE,
                    LISTED_SOURCE_REF,
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


def latest_published_candidate(now: datetime | None = None) -> date:
    """Newest date safe to request given the portal's T+1 afternoon publication."""
    now = now or datetime.now(KST)
    lag = 1 if now.hour >= 14 else 2
    return now.date() - timedelta(days=lag)


def collect_date(
    day: date,
    *,
    service_key: str | None = None,
    opener: Callable[..., object] = urlopen,
    path: str | os.PathLike[str] | None = None,
    include_symbols: bool | None = None,
    interval_sec: float | None = None,
) -> dict[str, object]:
    """Collect one published date from free FSC/data.go.kr official APIs."""
    _ensure_tables(path)
    if day.weekday() >= 5:
        result = {"ok": True, "date": day.isoformat(), "status": "WEEKEND", "rows": 0}
        _record_collection(day, "NO_DATA", result, None, path)
        return result
    key = service_key or _service_key()
    interval = (
        _float_cfg("FSC_PUBLIC_REQUEST_INTERVAL_SEC", 0.5, 0.1, 10.0)
        if interval_sec is None
        else max(0.0, interval_sec)
    )
    include_symbols = (
        _bool_cfg("FSC_PUBLIC_SYMBOL_SNAPSHOTS", True)
        if include_symbols is None
        else bool(include_symbols)
    )
    detail: dict[str, object] = {}
    total_rows = 0
    access_error: str | None = None
    response_error: str | None = None

    daily_complete_before = all(_daily_present(day, market, path) for market in ("KOSPI", "KOSDAQ"))
    if daily_complete_before:
        detail["stock_price"] = {"cached": True}
    else:
        try:
            stock_rows = request_all_rows(
                STOCK_URL,
                {"basDt": day.strftime("%Y%m%d")},
                service_key=key,
                opener=opener,
                interval_sec=interval,
            )
            market_counts: dict[str, int] = {}
            rejected = 0
            for market in ("KOSPI", "KOSDAQ"):
                subset = [
                    _canonical_stock_row(row)
                    for row in stock_rows
                    if str(row.get("mrktCtg") or "").strip().upper() == market
                ]
                if not subset:
                    market_counts[market] = 0
                    continue
                imported = import_daily_rows(
                    subset,
                    market=market,
                    source=SOURCE,
                    source_ref=STOCK_SOURCE_REF,
                    adjusted=False,
                    path=path,
                )
                count = int(imported.get("writtenRows") or 0)
                rejected += int(imported.get("rejectedRows") or 0)
                market_counts[market] = count
                total_rows += count
            detail["stock_price"] = {
                "cached": False,
                "receivedRows": len(stock_rows),
                "writtenByMarket": market_counts,
                "rejectedRows": rejected,
            }
        except PublicDataAccessError as exc:
            access_error = str(exc)
            detail["stock_price"] = {"error": access_error}
        except PublicDataResponseError as exc:
            response_error = str(exc)
            detail["stock_price"] = {"error": response_error}

    index_complete_before = all(_index_present(day, market, path) for market in ("KOSPI", "KOSDAQ"))
    if not access_error and not response_error:
        if index_complete_before:
            detail["market_index"] = {"cached": True}
        else:
            try:
                index_rows = request_all_rows(
                    INDEX_URL,
                    {"basDt": day.strftime("%Y%m%d")},
                    service_key=key,
                    opener=opener,
                    interval_sec=interval,
                )
                selected: dict[str, list[dict[str, object]]] = {"KOSPI": [], "KOSDAQ": []}
                for row in index_rows:
                    market = _index_market(row)
                    if market:
                        selected[market].append(_canonical_index_row(row, market))
                written: dict[str, int] = {}
                rejected = 0
                for market, rows in selected.items():
                    if not rows:
                        written[market] = 0
                        continue
                    imported = import_index_rows(
                        rows,
                        market=market,
                        source=SOURCE,
                        source_ref=INDEX_SOURCE_REF,
                        path=path,
                    )
                    count = int(imported.get("writtenRows") or 0)
                    rejected += int(imported.get("rejectedRows") or 0)
                    written[market] = count
                    total_rows += count
                detail["market_index"] = {
                    "cached": False,
                    "receivedRows": len(index_rows),
                    "writtenByMarket": written,
                    "rejectedRows": rejected,
                }
            except PublicDataAccessError as exc:
                access_error = str(exc)
                detail["market_index"] = {"error": access_error}
            except PublicDataResponseError as exc:
                response_error = str(exc)
                detail["market_index"] = {"error": response_error}

    symbol_warnings: list[str] = []
    core_any = any(_daily_present(day, market, path) for market in ("KOSPI", "KOSDAQ")) or any(
        _index_present(day, market, path) for market in ("KOSPI", "KOSDAQ")
    )
    if include_symbols and core_any and not access_error and not response_error:
        if _month_symbol_snapshot_present(day, path):
            detail["listed_symbols"] = {"cachedMonth": True}
        else:
            try:
                symbol_rows = request_all_rows(
                    LISTED_URL,
                    {"basDt": day.strftime("%Y%m%d")},
                    service_key=key,
                    opener=opener,
                    interval_sec=interval,
                )
                imported = _import_symbol_snapshot(symbol_rows, day=day, path=path)
                total_rows += int(imported["writtenRows"])
                detail["listed_symbols"] = {"receivedRows": len(symbol_rows), **imported}
            except (PublicDataAccessError, PublicDataResponseError) as exc:
                # Listing snapshots are useful for survivorship/PIT research but are
                # not allowed to discard already acquired price/index history.
                warning = f"listed_symbols: {exc}"
                symbol_warnings.append(warning)
                detail["listed_symbols"] = {"error": str(exc)}

    daily_complete = all(_daily_present(day, market, path) for market in ("KOSPI", "KOSDAQ"))
    index_complete = all(_index_present(day, market, path) for market in ("KOSPI", "KOSDAQ"))
    core_any = daily_complete or index_complete or core_any

    if access_error:
        status = "ACCESS_BLOCKED"
        error = access_error
    elif response_error:
        status = "ERROR"
        error = response_error
    elif daily_complete and index_complete:
        status = "COMPLETE"
        error = "; ".join(symbol_warnings) or None
    elif not core_any:
        status = "NO_DATA"
        error = None
    else:
        status = "PARTIAL"
        error = "one or more public official datasets are missing"

    result = {
        "ok": status in {"COMPLETE", "NO_DATA"},
        "date": day.isoformat(),
        "status": status,
        "officialRowsWritten": total_rows,
        "detail": detail,
        "symbolSnapshotWarnings": symbol_warnings,
        "source": SOURCE,
        "researchOnly": True,
        "realOrderEnabled": False,
    }
    _record_collection(day, status, detail, error, path)
    if access_error:
        _set_meta("fsc_public_last_access_error", access_error, path)
    elif status in {"COMPLETE", "NO_DATA"}:
        _set_meta("fsc_public_last_success", day.isoformat(), path)
    return result


def _parse_start_date() -> date:
    raw = _cfg("FSC_PUBLIC_BACKFILL_START", "20200102").replace("-", "")
    try:
        parsed = datetime.strptime(raw, "%Y%m%d").date()
    except ValueError:
        parsed = DEFAULT_BACKFILL_START
    return max(DEFAULT_BACKFILL_START, parsed)


def _completed_dates(path: str | os.PathLike[str] | None = None) -> set[str]:
    _ensure_tables(path)
    with connect(path) as c:
        return {
            str(row[0])
            for row in c.execute(
                "SELECT trade_date FROM fsc_public_collection_log WHERE status IN ('COMPLETE','NO_DATA')"
            )
        }


def _backfill_candidates(
    limit: int,
    *,
    end: date | None = None,
    path: str | os.PathLike[str] | None = None,
) -> list[date]:
    start = _parse_start_date()
    cursor = end or latest_published_candidate()
    done = _completed_dates(path)
    out: list[date] = []
    while cursor >= start and len(out) < limit:
        if cursor.weekday() < 5 and cursor.isoformat() not in done:
            out.append(cursor)
        cursor -= timedelta(days=1)
    return out


def run_cycle(
    *,
    service_key: str | None = None,
    opener: Callable[..., object] = urlopen,
    path: str | os.PathLike[str] | None = None,
    max_days: int | None = None,
    interval_sec: float | None = None,
) -> dict[str, object]:
    """Sync recently published dates, then backfill 2020+ newest-to-oldest."""
    if _within_live_session():
        return {"ok": True, "deferred": True, "reason": "live_session_priority", "researchOnly": True}
    key = service_key or _service_key()
    limit = max_days or _int_cfg("FSC_PUBLIC_BACKFILL_DAYS_PER_CYCLE", 20, 1, 120)
    newest = latest_published_candidate()
    candidates: list[date] = []
    for offset in range(0, 10):
        d = newest - timedelta(days=offset)
        if d.weekday() < 5:
            candidates.append(d)
    for d in _backfill_candidates(limit, end=newest, path=path):
        if d not in candidates:
            candidates.append(d)
        if len(candidates) >= limit + 10:
            break

    results: list[dict[str, object]] = []
    access_blocked = False
    for day in candidates:
        result = collect_date(
            day,
            service_key=key,
            opener=opener,
            path=path,
            include_symbols=True,
            interval_sec=interval_sec,
        )
        results.append(result)
        if result.get("status") == "ACCESS_BLOCKED":
            access_blocked = True
            break
    _set_meta("fsc_public_last_cycle_at", datetime.now(KST).isoformat(timespec="seconds"), path)
    return {
        "ok": not access_blocked,
        "researchOnly": True,
        "realOrderEnabled": False,
        "source": SOURCE,
        "datesAttempted": len(results),
        "accessBlocked": access_blocked,
        "results": results,
    }


def collector_status(path: str | os.PathLike[str] | None = None) -> dict[str, object]:
    _ensure_tables(path)
    hist = historical_status(path)
    with connect(path) as c:
        daily = int(c.execute("SELECT COUNT(*) FROM daily_bars WHERE source=?", (SOURCE,)).fetchone()[0])
        indices = int(c.execute("SELECT COUNT(*) FROM index_daily WHERE source=?", (SOURCE,)).fetchone()[0])
        symbols = int(c.execute("SELECT COUNT(*) FROM symbol_history WHERE source=?", (SOURCE,)).fetchone()[0])
        completed = int(c.execute("SELECT COUNT(*) FROM fsc_public_collection_log WHERE status='COMPLETE'").fetchone()[0])
        no_data = int(c.execute("SELECT COUNT(*) FROM fsc_public_collection_log WHERE status='NO_DATA'").fetchone()[0])
        blocked = int(c.execute("SELECT COUNT(*) FROM fsc_public_collection_log WHERE status='ACCESS_BLOCKED'").fetchone()[0])
        latest = c.execute(
            "SELECT trade_date,status,attempted_at,error FROM fsc_public_collection_log ORDER BY attempted_at DESC LIMIT 1"
        ).fetchone()
    return {
        "ok": True,
        "researchOnly": True,
        "controlStrategy": CONTROL_STRATEGY,
        "realOrderEnabled": REAL_ORDER_ENABLED,
        "autoCollectEnabled": _bool_cfg("FSC_PUBLIC_AUTO_COLLECT", True),
        "serviceKeyConfigured": service_key_configured(),
        "waitingForServiceKey": not service_key_configured(),
        "officialSource": SOURCE,
        "officialRows": {"dailyBars": daily, "indexDaily": indices, "symbolSnapshots": symbols},
        "collectionDates": {"complete": completed, "noData": no_data, "accessBlocked": blocked},
        "latestAttempt": dict(latest) if latest else None,
        "lastSuccess": _get_meta("fsc_public_last_success", path),
        "lastCycleAt": _get_meta("fsc_public_last_cycle_at", path),
        "lastAccessError": _get_meta("fsc_public_last_access_error", path),
        "historicalDb": hist,
        "publicApiApplications": [
            "금융위원회_주식시세정보",
            "금융위원회_지수시세정보",
            "금융위원회_KRX상장종목정보 (recommended for PIT snapshots)",
        ],
        "usageBoundary": "private research only; verify each data.go.kr dataset licence before redistribution/commercial use",
        "publicationLag": "daily, typically available on the next business day after 13:00 KST",
    }


def _cli() -> int:
    parser = argparse.ArgumentParser(description="FSC/data.go.kr Korean market collector (research only)")
    parser.add_argument("command", choices=("status", "once"))
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--days", type=int, default=None)
    args = parser.parse_args()
    if args.command == "status":
        result = collector_status(args.db)
    else:
        result = run_cycle(path=args.db, max_days=args.days)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
