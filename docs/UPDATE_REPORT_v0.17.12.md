# Stock Trader v0.17.12 — Safety Hardening

## Scope

This release strengthens Android/Termux operations without changing Control v0.8.0 entry, exit, sizing, daily-lock, or real-order behavior.

## 1. Android API network isolation

- All `/api/*` requests, including GET reads, are denied unless the client is loopback or inside the official Tailscale IPv4 CGNAT range `100.64.0.0/10`.
- The previous broad `100.*` assumption is not used by the Android guard.
- Uvicorn may continue listening on `0.0.0.0:8000`; ordinary LAN/WAN clients cannot obtain API data at the application layer.
- Static UI files can still be served, but protected API data remains inaccessible outside the trusted range.

## 2. Remote Health reliability

Remote Health component version: **0.17.12**.

- State-change publication now compares the observed state against the **last successfully published state**.
- A transition that occurs inside the anti-spam interval remains pending and is published as soon as the interval allows; it is no longer swallowed by updating only an in-memory previous-observation value.
- The daemon is launched with an explicit component version marker.
- `ensure_remote_health.sh` safely replaces a positively identified older project daemon after software updates.
- A new independent `remote_health_guardian.sh` checks the beacon process about once per minute and recreates it if the beacon alone dies.
- Remote-health failure remains non-fatal to the Stock Trader server.

## 3. Optional encrypted off-device DB backup

- New `offsite_backup.py` creates a normal WAL-safe local snapshot first.
- The snapshot is encrypted with OpenSSL AES-256-CBC, PBKDF2, SHA-256, and a configurable PBKDF2 iteration count (default 200,000).
- The passphrase is passed to OpenSSL through a child-process environment variable, not the command line.
- Upload requires an `https://` PUT target.
- A bearer token is optional.
- Status output never returns the passphrase, token, or target URL.
- This feature is **OFF by default**. No off-device upload occurs until the local `.env` explicitly sets `OFFSITE_BACKUP_ENABLED=true` and supplies a valid HTTPS target plus passphrase.
- `ensure_offsite_backup.sh` starts the daemon only after this explicit opt-in and shuts down a previously running project daemon if the flag is disabled.

Recommended secret handling:

```text
OFFSITE_BACKUP_ENABLED=true
OFFSITE_BACKUP_PUT_URL=https://private-storage.example/backups/{filename}
OFFSITE_BACKUP_BEARER_TOKEN=...
OFFSITE_BACKUP_PASSPHRASE_FILE=/data/data/com.termux/files/home/.stock-trader-offsite-passphrase
```

The passphrase file should be local to the phone and permission-restricted; never commit it.

## 4. CI and deployment checks

Safety Invariants now perform:

- Python compile checks
- `bash -n` syntax validation for Android startup, watchdog, updater, recovery, remote-health and offsite-backup shell scripts
- all `tests/test_*.py` validation tests

New regression coverage verifies:

- exact Tailscale CIDR boundaries
- GET as well as write API isolation
- pending Remote Health state changes
- versioned/self-supervised remote-health process
- encrypted offsite backup remains opt-in and has no broker/order dependency
- Control v0.8.0 constants remain unchanged

## 5. Branch protection

The repository should require PRs and the `Safety Invariants` check before changes can enter `main`. The connected GitHub integration used for this update does not expose repository-administration/branch-protection mutation, so this repository setting is not changed automatically by v0.17.12.

Recommended GitHub setting:

- protect `main`
- require a pull request before merging
- require `Safety Invariants` to pass
- block force pushes/deletion
- optionally require conversation resolution

## Safety invariants unchanged

- Control strategy: **v0.8.0 LOCKED**
- `ENABLE_TRADING=False`
- Android requires `APP_MODE=paper`
- Android requires `.env` `ENABLE_TRADING=false`
- existing Celltrion `068270` protection remains
- risk per trade remains 0.35%
- stop remains 1.0%
- max concurrent positions remains 2
- two-consecutive-loss Daily Lock remains unchanged
- daily max-loss and max-trade semantics remain unchanged
- research/shadow layers cannot automatically modify Control

## Deferred Control-semantic items

The following were intentionally not changed because they alter Paper/Control behavior and require a separate explicit decision:

- consecutive-loss ordering based on actual exit time
- EOD catch-up after the phone was offline during the normal EOD window
- `MAX_DAILY_TRADES=8` closed-trade vs entry-count semantics
- mark-to-market daily-loss gating

These should be evaluated separately from operational/security hardening.
