# v0.17.13 Android Watchdog Stability Fix

## Scope

This release hardens Android/Termux process supervision only.

It does **not** change Control v0.8.0, entry/exit logic, sizing, daily locks,
Celltrion protection, Profit allocation, or the real-order hard lock.

## Incident findings

Runtime evidence from the dedicated Android phone showed:

- `/api/health` returned HTTP 200 during manual checks.
- Paper loop and collector loop were running and fresh.
- `/api/system/runtime-health` returned HTTP 200.
- Historical watchdog logs contained many `health failure` records while
  `pidAlive=1` and `heartbeatFresh=1`.
- The failure spacing was consistent with the watchdog's 30-second interval
  plus the old 8-second `/api/health` timeout.
- Historical log ordering also showed evidence consistent with multiple
  watchdog loops having been active at the same time.

The old watchdog used the comparatively expensive `/api/health` endpoint as
both liveness and service-quality evidence. Under Android load, transient
latency from research/SQLite work could therefore look like a dead server.

## Changes

### 1. Minimal liveness endpoint

Android now exposes:

`GET /api/system/liveness`

The endpoint deliberately avoids collector, research, SQLite, backup, and NH
requests. It reports only lightweight process/safety metadata.

Watchdog restart decisions first use this endpoint with a short timeout.

### 2. Liveness and service quality are separated

`android_watchdog_v2.sh` now distinguishes:

- API process liveness failures, such as `LIVENESS_HTTP_TIMEOUT`
- Paper-loop failures, such as `PAPER_STOPPED` / `PAPER_STALE`
- Collector failures, such as `COLLECTOR_STOPPED` / `COLLECTOR_STALE`
- Runtime data-quality failures, such as `RUNTIME_STALE`
- Expensive health-probe timeouts, such as `HEALTH_HTTP_TIMEOUT`

Heavy health/runtime timeouts do not by themselves trigger an immediate server
restart when the lightweight liveness probe is healthy.

Persistent core Paper/collector failures receive a long grace period before a
safe recovery attempt. Default: 20 consecutive watchdog cycles.

### 3. Atomic watchdog singleton

The watchdog owns an atomic lock directory:

`~/.stock-trader-watchdog-v2.lock`

Concurrent launchers cannot create multiple active watchdog loops. When the
new watchdog obtains the canonical lock, it terminates only positively
identified duplicate `android_watchdog_v2.sh` processes.

Launchers no longer write the watchdog PID file. The canonical watchdog process
self-registers its own PID.

### 4. Independent watchdog supervision

`remote_health_guardian.sh`, which is independent of FastAPI, now also verifies
the Android watchdog every ~60 seconds and relaunches it when missing.

This supplements the in-process FastAPI watchdog guardian. If uvicorn dies but
the independent remote-health guardian remains alive, it can restore the
watchdog, which can in turn recover the server.

### 5. Better diagnostics

Watchdog logs now include explicit failure reasons and distinguish:

- liveness failure
- service degradation with restart suppressed
- persistent service recovery
- cooldown suppression
- duplicate watchdog termination

This makes future failures attributable without first running recovery.

## Safety invariants

Unchanged:

- Control strategy: v0.8.0 LOCKED
- `APP_MODE=paper`
- `ENABLE_TRADING=False`
- REAL ORDER OFF
- Celltrion `068270` protected
- 2 consecutive losses daily new-entry stop
- no AI/research auto-mutation of Control

## Validation

GitHub Safety Invariants continues to run:

- Python compile checks
- `bash -n` on Android shell scripts
- complete `unittest` discovery

New regression coverage is in:

`tests/test_watchdog_stability_v01713.py`
