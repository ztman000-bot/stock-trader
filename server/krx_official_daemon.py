"""Android supervisor loop for official KRX historical collection.

The loop deliberately runs KRX network work only after the configured KRX EOD hour.
It never runs pre-market after midnight, which prevents a new trading date from being
mistaken for a no-data holiday before that session has occurred.
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime

import krx_official_collector as collector


def run(instance_version: str = "0.17.15-krx-official-1") -> int:
    print(f"[KRX] official research daemon started ({instance_version}); REAL ORDER OFF", flush=True)
    last_state = ""
    while True:
        if not collector._bool_cfg("KRX_OFFICIAL_AUTO_COLLECT", True):
            state = "disabled"
            if state != last_state:
                print("[KRX] auto collection disabled by local config", flush=True)
                last_state = state
            time.sleep(300)
            continue

        if not collector.auth_key_configured():
            state = "waiting_for_auth_key"
            if state != last_state:
                print("[KRX] waiting for locally configured KRX_AUTH_KEY; no network call made", flush=True)
                last_state = state
            time.sleep(300)
            continue

        now = datetime.now(collector.KST)
        start_hour = collector._int_cfg("KRX_EOD_START_HOUR", 19, 16, 23)
        if collector._within_live_session(now) or now.hour < start_hour:
            state = "waiting_for_eod_window"
            if state != last_state:
                print(f"[KRX] waiting for EOD window (start={start_hour}:00 KST)", flush=True)
                last_state = state
            time.sleep(600)
            continue

        state = "running"
        if state != last_state:
            print("[KRX] official EOD/backfill cycle running", flush=True)
            last_state = state
        try:
            result = collector.run_cycle()
            if result.get("accessBlocked"):
                print("[KRX] access blocked; check KRX key/service approvals (key not logged)", flush=True)
                time.sleep(1800)
            else:
                print(f"[KRX] cycle complete: dates={result.get('datesAttempted', 0)}", flush=True)
                time.sleep(collector._int_cfg("KRX_CYCLE_INTERVAL_MIN", 120, 30, 720) * 60)
        except collector.KRXAccessError:
            print("[KRX] access blocked; check local KRX authorization (key not logged)", flush=True)
            time.sleep(1800)
        except Exception as exc:
            print(f"[KRX] daemon error: {type(exc).__name__}: {str(exc)[:200]}", flush=True)
            time.sleep(900)
    return 0


def _cli() -> int:
    parser = argparse.ArgumentParser(description="Official KRX Android research daemon")
    parser.add_argument("--instance-version", default="0.17.15-krx-official-1")
    args = parser.parse_args()
    return run(args.instance_version)


if __name__ == "__main__":
    raise SystemExit(_cli())
