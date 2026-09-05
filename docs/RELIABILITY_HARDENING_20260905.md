# Stock Trader Reliability Hardening — 2026-09-05

This change set hardens the existing v0.17.8 Paper/Research platform without changing Control v0.8.0 trading rules.

## Locked safety state

- Control v0.8.0 remains LOCKED.
- REAL ORDER remains OFF.
- `ENABLE_TRADING=False` remains hard-disabled in server code.
- Android startup still requires `APP_MODE=paper` and `ENABLE_TRADING=false` in local `.env`.
- Protected code `068270` remains excluded.
- 2 consecutive losses still trigger Daily Lock / Shadow-only new signals.
- Decision Intelligence remains Shadow Only and cannot change entry, exit, sizing, or order behavior.

## Reliability changes

### 1. Safety invariant CI

`.github/workflows/safety-invariants.yml` now compiles all Python source and runs static safety invariants on every PR to `main` and every push to `main`.

The invariant suite locks the highest-risk assumptions, including REAL ORDER OFF, protected Celltrion, Control risk constants, Daily Lock behavior, Shadow-only intelligence, update blocking with open Paper positions, and deployment isolation.

### 2. Android runtime freshness watchdog

The Android watchdog still verifies process identity, heartbeat, Paper-loop liveness, and collector-loop liveness. During live market sessions, after a short startup grace, it additionally requires `/api/system/runtime-health` to confirm that market quotes are actually fresh.

This prevents a collector thread that is still cycling while all NH data calls are failing from being treated as healthy indefinitely.

### 3. Safer Android update transaction

Before the running server is replaced, the updater now:

1. fast-forwards from `origin/main`,
2. installs changed Android requirements when needed,
3. runs `pip check`,
4. compiles critical Python modules,
5. runs `preflight.py`,
6. runs the Safety Invariant test suite,
7. creates a WAL-safe SQLite snapshot,
8. stops the old server,
9. starts the new server and verifies health.

If the update fails, code is reset to the previous Git commit. If Android requirements changed, the updater also reinstalls the previous pinned requirements snapshot before restarting the old server.

### 4. WAL-safe database snapshots

`server/db_backup.py` uses `sqlite3.Connection.backup()` and validates the snapshot with `PRAGMA quick_check(1)` before finalizing it. Backups are stored under `server/backups/` and are excluded from Git.

The default retention is 7 snapshots and can be adjusted with `DB_BACKUP_RETENTION` from 3 to 30.

### 5. EOD unresolved Paper positions

If an EOD Paper close has no latest quote, the system no longer silently clears collector priority for the still-open position. It records the unresolved position in `paper_state` under `eod_unresolved` and keeps remaining open codes prioritized for data collection.

No synthetic exit price is invented and no Control exit threshold is changed.

## Deliberately not changed in this patch

The following items remain separate follow-up hardening tasks because they require wider operational or research changes and should not be bundled into the same safety patch:

- automatic laptop ↔ Android peer-primary lock configuration,
- admin-token/Tailscale-only enforcement for every maintenance POST endpoint,
- continuous thermal/RAM adaptive research throttling,
- 5-minute bar provenance migration and official-bar-only research gating,
- purged/non-overlapping Walk-Forward and bootstrap confidence intervals.

These should be introduced independently so each can be validated without changing Control v0.8.0.
