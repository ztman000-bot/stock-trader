# Stock Day Trader v0.17.11 Remote Health Monitoring

Date: 2026-09-06

## Purpose

Add an independent outbound Android heartbeat so a remote observer can distinguish a Stock Trader server failure from a fully offline phone/network. Control v0.8.0 remains locked and REAL ORDER remains OFF.

## Architecture

- `remote_health_daemon.py` runs as a process separate from FastAPI/uvicorn.
- `start_android.sh` ensures exactly one daemon through `ensure_remote_health.sh`.
- The daemon checks local `/api/health` every 30 seconds.
- During a live market session it also checks `/api/system/runtime-health` for quote freshness.
- It publishes a regular heartbeat at most every 10 minutes and immediately on meaningful state changes, subject to a one-minute anti-flap floor.
- Default transport is `ntfy.sh`; a custom webhook can be configured with `REMOTE_HEALTH_WEBHOOK_URL`.

## Remote states

- `HEALTHY`: phone beacon process and Stock Trader server are alive; Paper/real-order invariants are correct.
- `DATA_STALE`: server is alive during market hours but runtime quotes are stale/unhealthy.
- `SERVER_DOWN`: phone-side beacon is still alive but FastAPI health is unavailable.
- `UPDATING`: the updater flag is present and a short server interruption is expected.
- `SAFETY_ALERT`: Paper/REAL ORDER safety state differs from the locked expectation.
- If no beacon is seen for more than two heartbeat intervals, treat it as device/network offline until proven otherwise.

## Privacy / security

The outbound payload uses a strict allow-list and contains only:

- device label
- health state
- server alive flag
- Paper flag
- REAL ORDER flag
- quote freshness
- market-session flag
- UI version when available
- timestamp and short diagnostic state

It does **not** send account number, NH credentials, symbols, positions, PnL, orders, strategy parameters, or database content.

The default ntfy topic is generated locally with 24 random bytes and stored only at `~/.stock-trader-remote-health-topic` with private file permissions. The topic/status URL acts like a password and must not be committed to GitHub.

## Local status command

```sh
cd ~/stock-trader
python server/remote_health_daemon.py --status
```

Use `statusUrl` from that output for remote checks. Do not publish the URL publicly.

## Control invariants

- Control v0.8.0 LOCKED
- `ENABLE_TRADING=False`
- no broker/order dependency in remote health
- no new NH market-data calls
- Celltrion protection unchanged
- risk sizing / stop / trailing / Daily Lock unchanged
