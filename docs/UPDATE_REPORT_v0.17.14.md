# Stock Trader v0.17.14 — Data Health & Research Readiness

## Scope

This release improves research-data completeness and operational visibility only. It does **not** change Control v0.8.0, entry/exit rules, sizing, daily locks, protected holdings, or order behavior.

## Changes

### 1. Point-in-time snapshot coverage audit
- Audits Scanner Intelligence and Decision Intelligence against the expected 5-minute session cadence.
- Default completeness threshold: **95%**.
- Missing point-in-time history is marked `INCOMPLETE_DAY`.
- Missing snapshots are never reconstructed from future data.

### 2. Strict 1-minute Exit Replay readiness
The Data Health layer applies the project monitoring thresholds:
- replay coverage >= **95%**
- OHLC/OLHC path agreement >= **85%**
- actual exit-reason agreement >= **90%**
- minimum replayable Paper trades: **30**

This is a research gate only. Passing it does not promote a strategy or enable live orders.

### 3. PARTIAL 1-minute priority repair
- Runs after market only.
- Never adds repair REST traffic during the live session.
- Defers while official 5-minute history or the normal 1-minute backfill is busy.
- Prioritizes recent research-universe code-days with 1–359 complete 1-minute bars.
- Uses the existing shared NH REST throttle.

### 4. Integrated Data Health
Classic UI now shows a read-only Data Health panel covering:
- official NH 5-minute GOOD ratio
- 1-minute completion ratio
- Scanner snapshot coverage
- Decision snapshot coverage
- strict Exit Replay readiness
- SQLite/WAL status

The score is an **operational/research-data health score**, not a profitability score or live-readiness certification.

### 5. Long-run SQLite monitoring
- Monitors DB/WAL/SHM size and backup status.
- Runs one `PRAGMA quick_check(1)` after market each weekday.
- Does not VACUUM, truncate WAL, delete research data, or alter trading state.

## Release/version display
- Release: **v0.17.14**
- UI component: **v0.17.10**
- Android Reliability: **v0.17.13**
- Control: **v0.8.0 LOCKED**

## Safety invariants
Unchanged:
- `APP_MODE=paper`
- `ENABLE_TRADING=False`
- REAL ORDER OFF
- Celltrion `068270` protected
- 2 consecutive losses => daily new-entry lock
- Control risk constants unchanged
- no AI/research auto-promotion
