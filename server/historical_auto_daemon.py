"""Public-first historical market research daemon.

Source priority:
1. Financial Services Commission / data.go.kr free official APIs (default)
2. KRX Data Marketplace API only when the operator explicitly enables the paid/
   separately-approved supplement and provides a local KRX key.

This daemon is research-only and never touches broker orders, Paper execution or
Control v0.8.0 semantics.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime

import fsc_public_collector as public
import krx_official_collector as krx


def source_status() -> dict[str, object]:
    public_enabled = public._bool_cfg("FSC_PUBLIC_AUTO_COLLECT", True)
    public_ready = public.service_key_configured()
    paid_enabled = krx._bool_cfg("KRX_OFFICIAL_AUTO_COLLECT", False)
    paid_ready = krx.auth_key_configured()
    primary = None
    if public_enabled and public_ready:
        primary = public.SOURCE
    elif paid_enabled and paid_ready:
        primary = krx.SOURCE
    return {
        "ok": True,
        "researchOnly": True,
        "realOrderEnabled": False,
        "controlStrategy": "v0.8.0 LOCKED",
        "primarySource": primary,
        "publicOfficial": {
            "source": public.SOURCE,
            "enabled": public_enabled,
            "serviceKeyConfigured": public_ready,
            "priority": 1,
        },
        "paidKrxSupplement": {
            "source": krx.SOURCE,
            "enabled": paid_enabled,
            "authKeyConfigured": paid_ready,
            "priority": 2,
        },
        "waitingForAnySource": not (
            (public_enabled and public_ready) or (paid_enabled and paid_ready)
        ),
    }


def run_once() -> dict[str, object]:
    status = source_status()
    results: dict[str, object] = {}
    if status["publicOfficial"]["enabled"] and status["publicOfficial"]["serviceKeyConfigured"]:
        results["publicOfficial"] = public.run_cycle()
    if status["paidKrxSupplement"]["enabled"] and status["paidKrxSupplement"]["authKeyConfigured"]:
        # Optional supplement/cross-check only. The free public official source remains
        # first when both are configured.
        results["paidKrxSupplement"] = krx.run_cycle()
    return {
        "ok": all(bool(v.get("ok", False)) for v in results.values()) if results else True,
        "researchOnly": True,
        "realOrderEnabled": False,
        "sourceStatus": status,
        "results": results,
    }


def run(instance_version: str = "0.17.15-historical-auto-2") -> int:
    print(
        f"[HIST] public-first historical research daemon started ({instance_version}); REAL ORDER OFF",
        flush=True,
    )
    last_state = ""
    while True:
        status = source_status()
        public_enabled = bool(status["publicOfficial"]["enabled"])
        public_ready = bool(status["publicOfficial"]["serviceKeyConfigured"])
        paid_enabled = bool(status["paidKrxSupplement"]["enabled"])
        paid_ready = bool(status["paidKrxSupplement"]["authKeyConfigured"])

        if not public_enabled and not paid_enabled:
            state = "disabled"
            if state != last_state:
                print("[HIST] historical auto collection disabled by local config", flush=True)
                last_state = state
            time.sleep(300)
            continue

        if not ((public_enabled and public_ready) or (paid_enabled and paid_ready)):
            state = "waiting_for_source_key"
            if state != last_state:
                print(
                    "[HIST] waiting for local data.go.kr service key (free primary) or optional KRX key; no network call made",
                    flush=True,
                )
                last_state = state
            time.sleep(300)
            continue

        now = datetime.now(public.KST)
        public_start = public._int_cfg("FSC_PUBLIC_EOD_START_HOUR", 19, 16, 23)
        krx_start = krx._int_cfg("KRX_EOD_START_HOUR", 19, 16, 23)
        starts = []
        if public_enabled and public_ready:
            starts.append(public_start)
        if paid_enabled and paid_ready:
            starts.append(krx_start)
        start_hour = min(starts) if starts else 19
        if public._within_live_session(now) or now.hour < start_hour:
            state = "waiting_for_eod_window"
            if state != last_state:
                print(f"[HIST] waiting for EOD research window (start={start_hour}:00 KST)", flush=True)
                last_state = state
            time.sleep(600)
            continue

        state = "running"
        if state != last_state:
            print("[HIST] official historical EOD/backfill cycle running", flush=True)
            last_state = state
        try:
            result = run_once()
            public_result = result.get("results", {}).get("publicOfficial") or {}
            paid_result = result.get("results", {}).get("paidKrxSupplement") or {}
            if public_result.get("accessBlocked"):
                print(
                    "[HIST] public-data access blocked; check data.go.kr key/application (key not logged)",
                    flush=True,
                )
            if paid_result.get("accessBlocked"):
                print(
                    "[HIST] optional KRX supplement access blocked; check KRX key/approvals (key not logged)",
                    flush=True,
                )
            dates = int(public_result.get("datesAttempted") or 0) + int(
                paid_result.get("datesAttempted") or 0
            )
            print(f"[HIST] cycle complete: date-attempts={dates}", flush=True)
            interval_min = public._int_cfg("FSC_PUBLIC_CYCLE_INTERVAL_MIN", 120, 30, 720)
            time.sleep(interval_min * 60)
        except public.PublicDataAccessError:
            print("[HIST] data.go.kr access blocked; local key not logged", flush=True)
            time.sleep(1800)
        except krx.KRXAccessError:
            print("[HIST] optional KRX access blocked; local key not logged", flush=True)
            time.sleep(1800)
        except Exception as exc:
            # Fail isolated from the trading server.
            print(f"[HIST] daemon error: {type(exc).__name__}: {str(exc)[:200]}", flush=True)
            time.sleep(900)
    return 0


def _cli() -> int:
    parser = argparse.ArgumentParser(description="Public-first historical research daemon")
    parser.add_argument("command", nargs="?", choices=("status", "once", "daemon"), default="daemon")
    parser.add_argument("--instance-version", default="0.17.15-historical-auto-2")
    args = parser.parse_args()
    if args.command == "status":
        print(json.dumps(source_status(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "once":
        print(json.dumps(run_once(), ensure_ascii=False, indent=2))
        return 0
    return run(args.instance_version)


if __name__ == "__main__":
    raise SystemExit(_cli())
