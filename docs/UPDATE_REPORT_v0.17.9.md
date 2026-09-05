# Stock Day Trader v0.17.9 Update Report

Date: 2026-09-05

## Release scope

v0.17.9 is a reliability, operational-safety, and research-validation hardening release. It does not change Control v0.8.0 entry/exit/sizing rules and does not enable real orders.

## Safety invariants retained

- Control v0.8.0 LOCKED
- REAL ORDER OFF
- `ENABLE_TRADING=False` hard-disabled in server code
- Android requires `APP_MODE=paper` and `ENABLE_TRADING=false`
- protected code `068270`
- risk per trade 0.35%
- 2 consecutive losses -> Daily Lock / new signals Shadow Only
- max open positions 2
- max daily trades constant 8 unchanged
- daily max loss 0.75% unchanged
- Decision Intelligence remains Shadow Only
- research cannot auto-promote itself into Control

## Reliability hardening

- Android watchdog now checks actual live quote freshness through runtime-health after startup grace.
- Safety Invariant GitHub CI compiles all Python sources and runs all `test_*.py` tests.
- Android updater runs the same invariant checks before replacing the server.
- Android dependency changes are checked with `pip check`; failed updates restore previous pinned requirements.
- WAL-safe SQLite backups use `sqlite3.Connection.backup()` and `PRAGMA quick_check(1)`.
- A database snapshot is created before Android server replacement.
- Android performs a daily after-market database snapshot with rotating retention.
- EOD Paper positions without a latest quote are recorded as unresolved instead of receiving a fabricated exit price.
- Android state-changing HTTP methods are restricted to localhost/Tailscale clients.

## Research hardening

- Historical NH period 5-minute bars are tagged in `bar_5m_provenance` as `nh_period_5m`.
- Structural data quality remains separately visible.
- Profitability/Robust research now requires structurally GOOD code-days with at least 76 official NH-provenance 5-minute bars.
- Legacy/live-sampled days without official provenance are re-fetched after market before becoming research-eligible.
- Robust Validation uses expanding, non-overlapping forward test folds.
- One trading-day purge gap is inserted between train and test.
- Minimum selected training sample per fold increased from 8 to 12 trades.
- At least 3 valid Walk-Forward folds are required.
- At least 75% of valid folds must be positive.
- Final Lockbox minimum increased from 10 to 20 trades.
- KR 1-minute Exit Replay remains a separate deployment gate.

## Expected behavior after Android update

Immediately after v0.17.9 deployment, Profitability/Robust research can show fewer eligible days/trades because older code-days have no provenance metadata yet. This is expected. The historical accumulator will re-fetch those days after market and progressively restore only code-days verified from official NH 5-minute period data.

## Deferred items

The following remain separate future work because they need environment-specific runtime validation or wider research design changes:

- cross-host laptop/Android single-primary peer lock configuration
- device-specific thermal throttling based on Android sensor paths
- bootstrap confidence intervals for research metrics

These deferred items do not weaken the current REAL ORDER OFF or Control v0.8.0 isolation.
