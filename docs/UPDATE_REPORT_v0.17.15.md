# Stock Trader v0.17.15 — Official 5m Auto Repair & Data Health Follow-up

## Scope

This release improves research-data repair and monitoring only. It does **not** change Control v0.8.0, entries, exits, sizing, daily locks, protected holdings, or order behavior.

## Main improvement: official NH 5-minute GOOD recovery

The v0.17.14 Data Health panel exposed a low official 5-minute GOOD ratio. v0.17.15 adds an automatic after-market repair loop that works toward a **95% official GOOD target**.

Repair behavior:
- no repair REST calls during the regular KRX session;
- normal 5-minute history jobs have priority;
- normal 1-minute backfill has priority;
- all calls use the existing shared NH REST throttle;
- structurally GOOD-but-unverified code-days are repaired first;
- PARTIAL and BAD code-days are attempted afterward;
- recent dates are prioritized;
- failed/no-progress targets receive a retry cooldown to avoid API hammering;
- repair stops automatically once the 95% target is reached.

## Additional improvements included after review

### 1. Exit Replay coverage repair
When the official 5-minute target has been reached, Data Health can prioritize missing official 1-minute data for already-closed Paper trades. This is intended to improve replay coverage without creating synthetic bars.

- after market only;
- normal history/backfill still has priority;
- per-code/day retry cooldown;
- no entry/exit mutation;
- no order access.

### 2. Forward snapshot cohort
Legacy Scanner/Decision snapshot gaps cannot be reconstructed safely. v0.17.15 stores a forward-monitoring baseline and separates new forward coverage from old irreversible gaps.

- historical incomplete days remain visible;
- future data are never used to reconstruct missing point-in-time snapshots;
- once forward days exist, Data Health scoring uses forward coverage for Scanner/Decision operational health.

### 3. Backup freshness visibility
Data Health now reports whether the latest local backup is fresh in addition to DB/WAL size and weekday `PRAGMA quick_check(1)`.

### 4. UI visibility
Classic Data Health now shows:
- official 5m repair target and last repair progress;
- forward Scanner/Decision coverage and baseline date;
- Exit Replay coverage percentage;
- replay/partial 1m repair activity;
- backup freshness.

## Release versions
- Release: **v0.17.15**
- UI component: **v0.17.10**
- Android Reliability: **v0.17.13**
- Control: **v0.8.0 LOCKED**

## Safety invariants
Unchanged:
- `APP_MODE=paper`
- `ENABLE_TRADING=False`
- REAL ORDER OFF
- Celltrion `068270` auto-trading protection
- 2 consecutive losses => daily new-entry lock
- Control risk constants unchanged
- no AI/research auto-promotion

## Operational note
The 95% official 5-minute target is not expected to become true instantly after update. The repair loop is deliberately bounded and yields to normal history collection. Data Health should show progressive improvement over after-market cycles rather than burst API traffic.
