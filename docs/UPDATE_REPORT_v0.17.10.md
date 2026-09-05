# Stock Day Trader v0.17.10 Update Report

Date: 2026-09-05

## Purpose

v0.17.10 expands research observation and point-in-time data collection without changing Control v0.8.0 trading rules. REAL ORDER remains OFF.

## Control invariants retained

- Control v0.8.0 LOCKED
- `ENABLE_TRADING=False`
- Celltrion `068270` remains protected
- risk per trade 0.35%
- 2 consecutive losses -> Daily Lock / Shadow Only
- max open positions 2
- `MAX_DAILY_TRADES=8` remains unchanged
- current daily trade lock semantics remain based on CLOSED trades
- daily max loss 0.75%
- research cannot mutate or auto-promote Control

## New observation data

### Point-in-time scanner decisions

`decision_observations` records scanner states once per code/action/5-minute signal bucket:

- BUY_CANDIDATE
- SETUP
- WATCH
- SHADOW_ONLY
- BLOCKED
- PROTECTED / SAFETY_WAIT / WAIT_DATA when observed
- score, reasons and blocked reasons
- indicator snapshot
- candidate market metadata
- daily state and market breadth

An in-memory dedupe plus SQLite unique key prevents repeated UI/Paper polling from creating duplicate records for the same state.

### Forward outcome labels

Each observation is relabeled from the current local 5-minute database for:

- +5 minutes
- +10 minutes
- +30 minutes
- +60 minutes
- end of day

Labels are recomputed, not treated as immutable. When historical NH data later overwrites live sampled bars, the observer can recompute the outcomes from the newer bars. Official provenance counts are stored using source `nh_period_5m`.

### Daily Universe Snapshot

The first verified collector watchlist seen each KST date is stored in `universe_snapshots` with:

- date
- code and selected rank
- name / KOSPI-KOSDAQ market
- market cap metadata
- capture time

This builds point-in-time history for future survivorship-bias reduction.

### Market context snapshot

Every observed 5-minute bucket stores a market-context summary without extra NH API calls:

- active candidate count
- liquidity-passing count
- total candidate turnover
- market breadth
- KOSPI/KOSDAQ candidate advancer percentage
- KOSPI/KOSDAQ average change rate
- daily Paper state

## Daily trade-count telemetry

`paper_trades` now has observational `entry_sequence` metadata and `daily_stats()` exposes `entriesToday` alongside `closedTrades`.

This deliberately does **not** change the Control gate. The current `MAX_DAILY_TRADES=8` lock continues to use CLOSED trades while data are accumulated to compare entry-count versus closed-count semantics later.

## Existing selective 1-minute collection

No extra 1-minute API expansion was added because the current KR 1-minute research collector already prioritizes open Paper positions (`priority_codes`), then active candidates, then the watchlist, capped by `KR_1M_LIVE_FOCUS` (maximum 10).

## API / health visibility

New endpoint:

- `GET /api/research/observations`

UI health now reports point-in-time decision logging, forward outcome labels, Universe Snapshot, and entry-sequence telemetry.

## Operational effect

The observer uses the existing local SQLite/WAL database and adds no NH REST/WebSocket traffic. A lightweight daemon refreshes outcome labels every five minutes. Trading decisions remain independent: observer failures are caught and must not block `scan()` or alter Control behavior.
