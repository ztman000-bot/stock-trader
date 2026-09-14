# GitHub Market-Regime Reference

## Purpose

This layer exists only to provide long-horizon Korean-market regime and breadth context.
It is deliberately separate from the NH 1-minute/5-minute forward evidence used for
execution research.

Source priority for historical context:

1. `FinanceData/marcap` public GitHub data -> compact aggregate regime reference
2. Financial Services Commission / `data.go.kr` -> optional official cross-check
3. KRX Data Marketplace -> optional paid/separately-approved cross-check

## Why this is efficient

The Android phone does **not** clone the upstream repository and does **not** download
30+ years of per-stock parquet files. A GitHub Action processes the public yearly files
off-device and commits only a small market-level aggregate CSV to this repository.
The phone then downloads that compact CSV and stores it in a separate
`regime_reference.db`.

The aggregate contains, per date and market:

- active issue count
- advancers / decliners / unchanged
- advance and decline ratios
- total traded value
- total market capitalization
- top-10 market-cap concentration
- cap-weighted daily change
- median daily change
- rolling 5-day breadth
- rolling 20-day cap-weighted return
- rolling 20-day volatility
- rolling turnover ratio
- observational regime label (`STRESS`, `RISK_OFF`, `NEUTRAL`, `RISK_ON`, `RISK_ON_STRONG`)

These labels are research annotations only. They are not Control entry filters, sizing
rules, exit rules or live-order inputs.

## Upstream provenance / licence boundary

Upstream repository: `FinanceData/marcap`.

At implementation time GitHub reported no machine-readable repository licence for the
upstream repository. This project therefore:

- does not copy upstream source code,
- does not commit upstream parquet files,
- does not store raw per-stock upstream rows on the Android phone,
- keeps attribution and the exact upstream commit SHA in generated metadata,
- stores only compact aggregate market-level research facts,
- treats the source as replaceable reference data rather than execution evidence.

If upstream terms change or redistribution becomes inappropriate, disable the GitHub
reference workflow and use the official FSC/KRX collectors instead.

## Safety boundary

Unchanged:

- Control strategy: `v0.8.0 LOCKED`
- `ENABLE_TRADING=False`
- REAL ORDER OFF
- Celltrion `068270` protection
- Paper entry/exit/risk semantics
- NH 1m/5m provenance and forward-validation gates

The regime reference database is not read by the order endpoint and cannot promote a
strategy to live trading.
