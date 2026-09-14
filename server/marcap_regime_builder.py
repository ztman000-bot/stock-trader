"""Build a compact market-regime reference from FinanceData/marcap.

This module is used by GitHub Actions, not by the Android trading runtime. It downloads
only yearly parquet data files from the public FinanceData/marcap repository, computes
market-level aggregates, and writes a small CSV summary. Raw per-stock rows are never
committed to this repository.

The upstream repository currently exposes no machine-readable repository licence via
GitHub. Therefore this integration is deliberately limited to attribution-preserving,
research-only aggregate facts. It does not copy upstream source code or redistribute
raw parquet files. If the upstream terms or repository availability change, disable the
workflow and fall back to the official FSC/KRX sources.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import tempfile
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping
from urllib.request import Request, urlopen

UPSTREAM_REPO = "FinanceData/marcap"
UPSTREAM_BRANCH = "master"
UPSTREAM_RAW = "https://raw.githubusercontent.com/FinanceData/marcap/{ref}/data/marcap-{year}.parquet"
SOURCE = "GITHUB_FINANCEDATA_MARCAP_AGG"
RESEARCH_ONLY = True
REAL_ORDER_ENABLED = False
CONTROL_STRATEGY = "v0.8.0 LOCKED"
MARKETS = ("KOSPI", "KOSDAQ")
FIRST_YEAR = 1995

SUMMARY_FIELDS = (
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
)


def _finite(value: object, default: float = 0.0) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def classify_regime(weighted_return_20d: float, breadth_5d: float, volatility_20d: float) -> tuple[int, str]:
    """Heuristic research label. It is never a Control/Paper trading gate."""
    r20 = _finite(weighted_return_20d)
    b5 = _finite(breadth_5d, 0.5)
    v20 = max(0.0, _finite(volatility_20d))
    if r20 <= -8.0 and (b5 < 0.38 or v20 >= 2.0):
        return -2, "STRESS"
    if r20 <= -3.0 or b5 < 0.43:
        return -1, "RISK_OFF"
    if r20 >= 5.0 and b5 >= 0.60:
        return 2, "RISK_ON_STRONG"
    if r20 >= 1.5 and b5 >= 0.53:
        return 1, "RISK_ON"
    return 0, "NEUTRAL"


def add_rolling_regime(rows: Iterable[Mapping[str, object]]) -> list[dict[str, object]]:
    """Add rolling breadth/return/volatility fields using only market-level aggregates."""
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        item = dict(row)
        market = str(item.get("market") or "").upper()
        if market in MARKETS:
            grouped[market].append(item)

    output: list[dict[str, object]] = []
    for market, items in grouped.items():
        items.sort(key=lambda r: str(r.get("trade_date") or ""))
        breadth_window: deque[float] = deque(maxlen=5)
        return_window: deque[float] = deque(maxlen=20)
        amount_window: deque[float] = deque(maxlen=20)
        for item in items:
            breadth_window.append(_finite(item.get("advance_ratio"), 0.5))
            daily_pct = _finite(item.get("cap_weighted_change_pct"))
            return_window.append(daily_pct)
            amount = max(0.0, _finite(item.get("turnover_amount")))
            amount_window.append(amount)

            breadth_5d = statistics.fmean(breadth_window) if breadth_window else 0.5
            growth = 1.0
            for pct in return_window:
                growth *= max(0.01, 1.0 + pct / 100.0)
            weighted_return_20d = (growth - 1.0) * 100.0
            volatility_20d = statistics.pstdev(return_window) if len(return_window) >= 2 else 0.0
            amount_median = statistics.median(amount_window) if amount_window else 0.0
            turnover_ratio = (amount / amount_median) if amount_median > 0 else 0.0

            if len(return_window) < 10 or len(breadth_window) < 5:
                score, regime = 0, "WARMUP"
            else:
                score, regime = classify_regime(weighted_return_20d, breadth_5d, volatility_20d)

            item.update(
                breadth_5d=round(breadth_5d, 6),
                weighted_return_20d=round(weighted_return_20d, 6),
                volatility_20d=round(volatility_20d, 6),
                turnover_ratio_20d=round(turnover_ratio, 6),
                regime_score=score,
                regime=regime,
                source=SOURCE,
            )
            output.append(item)
    output.sort(key=lambda r: (str(r.get("trade_date") or ""), str(r.get("market") or "")))
    return output


def _download(url: str, target: Path, timeout: float = 90.0) -> None:
    request = Request(url, headers={"User-Agent": "stock-trader-regime-builder/0.17.15"})
    with urlopen(request, timeout=timeout) as response, target.open("wb") as fh:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            fh.write(chunk)


def aggregate_parquet(path: Path, source_ref: str) -> list[dict[str, object]]:
    """Aggregate one yearly parquet file. pandas/pyarrow are GitHub-Action-only deps."""
    try:
        import pandas as pd  # type: ignore
    except ImportError as exc:  # pragma: no cover - Android runtime must never need this
        raise RuntimeError("pandas+pyarrow are required only for the GitHub regime build job") from exc

    columns = ["Date", "Market", "Volume", "Amount", "ChangesRatio", "Marcap"]
    df = pd.read_parquet(path, columns=columns)
    if df.empty:
        return []
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df["Market"] = df["Market"].astype(str).str.upper()
    for col in ("Volume", "Amount", "ChangesRatio", "Marcap"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df[df["Market"].isin(MARKETS) & df["Date"].notna()]
    # Suspended/no-trade rows are excluded from breadth denominators but remain a
    # known upstream limitation; this summary is for regime context, not fills.
    df = df[df["Volume"].fillna(0) > 0]

    out: list[dict[str, object]] = []
    for (trade_date, market), group in df.groupby([df["Date"].dt.date, "Market"], sort=True):
        changes = group["ChangesRatio"].dropna()
        caps = group["Marcap"].fillna(0).clip(lower=0)
        cap_total = float(caps.sum())
        if cap_total > 0:
            weighted = float((group["ChangesRatio"].fillna(0) * caps).sum() / cap_total)
            top10_share = float(caps.nlargest(10).sum() / cap_total)
        else:
            weighted = 0.0
            top10_share = 0.0
        active = int(len(group))
        advancers = int((changes > 0).sum())
        decliners = int((changes < 0).sum())
        unchanged = max(0, active - advancers - decliners)
        denom = max(1, advancers + decliners + unchanged)
        out.append(
            {
                "trade_date": trade_date.isoformat(),
                "market": str(market),
                "active_count": active,
                "advancers": advancers,
                "decliners": decliners,
                "unchanged": unchanged,
                "advance_ratio": round(advancers / denom, 6),
                "decline_ratio": round(decliners / denom, 6),
                "turnover_amount": round(float(group["Amount"].fillna(0).sum()), 2),
                "market_cap_total": round(cap_total, 2),
                "top10_cap_share": round(top10_share, 6),
                "cap_weighted_change_pct": round(weighted, 6),
                "median_change_pct": round(float(changes.median()) if not changes.empty else 0.0, 6),
                "source_ref": source_ref,
            }
        )
    return out


def _read_existing(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def write_summary(path: Path, rows: Iterable[Mapping[str, object]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = add_rolling_regime(rows)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=SUMMARY_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in normalized:
            writer.writerow({field: row.get(field, "") for field in SUMMARY_FIELDS})
    return len(normalized)


def validate_summary(path: Path) -> dict[str, object]:
    rows = _read_existing(path)
    if not rows:
        raise ValueError("summary contains no rows")
    required = {"trade_date", "market", "advance_ratio", "regime", "source", "source_ref"}
    if not required.issubset(rows[0].keys()):
        raise ValueError("summary header is incomplete")
    bad_market = [r for r in rows if str(r.get("market")) not in MARKETS]
    bad_source = [r for r in rows if str(r.get("source")) != SOURCE]
    if bad_market or bad_source:
        raise ValueError("summary provenance/market validation failed")
    return {
        "ok": True,
        "rows": len(rows),
        "first": min(str(r["trade_date"]) for r in rows),
        "last": max(str(r["trade_date"]) for r in rows),
        "markets": sorted({str(r["market"]) for r in rows}),
        "researchOnly": True,
        "realOrderEnabled": False,
    }


def build(output: Path, meta: Path, upstream_ref: str, mode: str = "auto") -> dict[str, object]:
    current_year = datetime.now(timezone.utc).year
    existing = _read_existing(output)
    if mode == "recent" or (mode == "auto" and existing):
        years = [max(FIRST_YEAR, current_year - 1), current_year]
        refresh_years = set(years)
        kept = [r for r in existing if int(str(r.get("trade_date", "0000"))[:4] or 0) not in refresh_years]
    else:
        years = list(range(FIRST_YEAR, current_year + 1))
        kept = []

    fresh: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="marcap-regime-") as temp_dir:
        temp = Path(temp_dir)
        for year in years:
            url = UPSTREAM_RAW.format(ref=upstream_ref, year=year)
            target = temp / f"marcap-{year}.parquet"
            _download(url, target)
            fresh.extend(aggregate_parquet(target, source_ref=f"{UPSTREAM_REPO}@{upstream_ref}:data/marcap-{year}.parquet"))

    row_count = write_summary(output, [*kept, *fresh])
    validation = validate_summary(output)
    meta.parent.mkdir(parents=True, exist_ok=True)
    meta.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "source": SOURCE,
                "upstreamRepo": UPSTREAM_REPO,
                "upstreamRef": upstream_ref,
                "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "rawRowsRedistributed": False,
                "rawUpstreamCodeCopied": False,
                "purpose": "market-regime-and-breadth-reference-only",
                "controlStrategy": CONTROL_STRATEGY,
                "realOrderEnabled": False,
                "rowCount": row_count,
                "firstDate": validation["first"],
                "lastDate": validation["last"],
                "markets": validation["markets"],
                "upstreamLicenseNote": "FinanceData/marcap reported no machine-readable repository licence when this adapter was created; retain attribution and use only compact aggregate research facts.",
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    return {"ok": True, "mode": mode, "years": years, **validation}


def _cli() -> int:
    parser = argparse.ArgumentParser(description="Build compact GitHub marcap market-regime reference")
    sub = parser.add_subparsers(dest="command", required=True)
    build_p = sub.add_parser("build")
    build_p.add_argument("--output", default="research/regime/marcap_regime_daily.csv")
    build_p.add_argument("--meta", default="research/regime/marcap_regime_meta.json")
    build_p.add_argument("--upstream-ref", required=True)
    build_p.add_argument("--mode", choices=("auto", "full", "recent"), default="auto")
    val_p = sub.add_parser("validate")
    val_p.add_argument("--output", default="research/regime/marcap_regime_daily.csv")
    args = parser.parse_args()
    if args.command == "build":
        result = build(Path(args.output), Path(args.meta), args.upstream_ref, args.mode)
    else:
        result = validate_summary(Path(args.output))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
