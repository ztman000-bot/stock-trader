"""Historical market research supervisor.

Source roles:
1. GitHub-hosted compact FinanceData/marcap market-regime aggregate (default, keyless)
2. Financial Services Commission / data.go.kr official APIs (optional cross-check/raw daily)
3. KRX Data Marketplace API (optional paid/separately-approved supplement)

The GitHub source is used only for long-horizon market regime/breadth context. It is
kept in a separate database and never replaces NH 1m/5m forward evidence. This daemon
is research-only and never touches broker orders, Paper execution or Control v0.8.0.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime

import fsc_public_collector as public
import github_regime_reference as github_regime
import krx_official_collector as krx


def source_status() -> dict[str, object]:
    github_enabled = github_regime.reference_enabled()
    github_status = github_regime.collector_status()
    public_enabled = public._bool_cfg("FSC_PUBLIC_AUTO_COLLECT", False)
    public_ready = public.service_key_configured()
    paid_enabled = krx._bool_cfg("KRX_OFFICIAL_AUTO_COLLECT", False)
    paid_ready = krx.auth_key_configured()

    # Keep the historical/raw-reference primarySource semantics backward compatible:
    # when an operator explicitly enables an approved official source, that source
    # remains the primary raw reference. The keyless GitHub aggregate is separately
    # identified as regimePrimarySource because its role is market context only.
    primary = None
    if public_enabled and public_ready:
        primary = public.SOURCE
    elif paid_enabled and paid_ready:
        primary = krx.SOURCE
    elif github_enabled:
        primary = github_regime.SOURCE

    regime_primary = github_regime.SOURCE if github_enabled else None

    return {
        "ok": True,
        "researchOnly": True,
        "realOrderEnabled": False,
        "controlStrategy": "v0.8.0 LOCKED",
        "primarySource": primary,
        "regimePrimarySource": regime_primary,
        "githubRegimeReference": {
            "source": github_regime.SOURCE,
            "enabled": github_enabled,
            "keyRequired": False,
            "priority": 1,
            "localReady": int(github_status.get("rows") or 0) > 0,
            "rows": int(github_status.get("rows") or 0),
            "last": github_status.get("last"),
            "latest": github_status.get("latest") or {},
            "rawPerStockStoredOnPhone": False,
        },
        "publicOfficial": {
            "source": public.SOURCE,
            "enabled": public_enabled,
            "serviceKeyConfigured": public_ready,
            "priority": 2,
            "role": "optional-official-cross-check",
        },
        "paidKrxSupplement": {
            "source": krx.SOURCE,
            "enabled": paid_enabled,
            "authKeyConfigured": paid_ready,
            "priority": 3,
            "role": "optional-paid-cross-check",
        },
        "waitingForAnySource": not (
            github_enabled
            or (public_enabled and public_ready)
            or (paid_enabled and paid_ready)
        ),
    }


def run_once() -> dict[str, object]:
    status = source_status()
    results: dict[str, object] = {}

    if status["githubRegimeReference"]["enabled"]:
        results["githubRegimeReference"] = github_regime.sync_summary()

    if status["publicOfficial"]["enabled"] and status["publicOfficial"]["serviceKeyConfigured"]:
        results["publicOfficial"] = public.run_cycle()

    if status["paidKrxSupplement"]["enabled"] and status["paidKrxSupplement"]["authKeyConfigured"]:
        results["paidKrxSupplement"] = krx.run_cycle()

    # The GitHub summary may legitimately be unavailable before the first scheduled
    # build. Treat that as an isolated research-source issue, never a trading-server
    # failure. Return per-source status without mutating Paper/Control.
    overall = all(bool(v.get("ok", False)) for v in results.values()) if results else True
    return {
        "ok": overall,
        "researchOnly": True,
        "realOrderEnabled": False,
        "sourceStatus": source_status(),
        "results": results,
    }


def run(instance_version: str = "0.17.17-historical-auto-4") -> int:
    print(
        f"[HIST] GitHub-regime-first historical research daemon started ({instance_version}); REAL ORDER OFF",
        flush=True,
    )
    last_state = ""
    while True:
        status = source_status()
        github_enabled = bool(status["githubRegimeReference"]["enabled"])
        public_enabled = bool(status["publicOfficial"]["enabled"])
        public_ready = bool(status["publicOfficial"]["serviceKeyConfigured"])
        paid_enabled = bool(status["paidKrxSupplement"]["enabled"])
        paid_ready = bool(status["paidKrxSupplement"]["authKeyConfigured"])

        if not github_enabled and not public_enabled and not paid_enabled:
            state = "disabled"
            if state != last_state:
                print("[HIST] historical research sources disabled by local config", flush=True)
                last_state = state
            time.sleep(300)
            continue

        if not github_enabled and not ((public_enabled and public_ready) or (paid_enabled and paid_ready)):
            state = "waiting_for_source_key"
            if state != last_state:
                print(
                    "[HIST] GitHub regime reference disabled; waiting for optional official-source key",
                    flush=True,
                )
                last_state = state
            time.sleep(300)
            continue

        now = datetime.now(public.KST)
        starts = []
        if github_enabled:
            starts.append(github_regime.sync_hour())
        if public_enabled and public_ready:
            starts.append(public._int_cfg("FSC_PUBLIC_EOD_START_HOUR", 19, 16, 23))
        if paid_enabled and paid_ready:
            starts.append(krx._int_cfg("KRX_EOD_START_HOUR", 19, 16, 23))
        start_hour = min(starts) if starts else 20

        if public._within_live_session(now) or now.hour < start_hour:
            state = "waiting_for_eod_window"
            if state != last_state:
                print(f"[HIST] waiting for EOD research window (start={start_hour}:00 KST)", flush=True)
                last_state = state
            time.sleep(600)
            continue

        state = "running"
        if state != last_state:
            print("[HIST] regime/reference EOD sync running", flush=True)
            last_state = state
        try:
            result = run_once()
            github_result = result.get("results", {}).get("githubRegimeReference") or {}
            public_result = result.get("results", {}).get("publicOfficial") or {}
            paid_result = result.get("results", {}).get("paidKrxSupplement") or {}

            if github_enabled and not github_result.get("ok", False):
                print(
                    f"[HIST] GitHub regime summary unavailable: {str(github_result.get('error') or 'unknown')[:160]}",
                    flush=True,
                )
            if public_result.get("accessBlocked"):
                print("[HIST] optional data.go.kr cross-check blocked; key not logged", flush=True)
            if paid_result.get("accessBlocked"):
                print("[HIST] optional KRX cross-check blocked; key not logged", flush=True)

            dates = int(public_result.get("datesAttempted") or 0) + int(paid_result.get("datesAttempted") or 0)
            github_rows = int(github_result.get("writtenRows") or 0)
            print(f"[HIST] cycle complete: github-rows={github_rows}, official-date-attempts={dates}", flush=True)
            interval_min = github_regime._int_cfg("MARCAP_GITHUB_CYCLE_INTERVAL_MIN", 360, 60, 1440)
            time.sleep(interval_min * 60)
        except public.PublicDataAccessError:
            print("[HIST] optional data.go.kr access blocked; local key not logged", flush=True)
            time.sleep(1800)
        except krx.KRXAccessError:
            print("[HIST] optional KRX access blocked; local key not logged", flush=True)
            time.sleep(1800)
        except Exception as exc:
            print(f"[HIST] daemon error: {type(exc).__name__}: {str(exc)[:200]}", flush=True)
            time.sleep(900)
    return 0


def _cli() -> int:
    parser = argparse.ArgumentParser(description="GitHub-regime-first historical research daemon")
    parser.add_argument("command", nargs="?", choices=("status", "once", "daemon"), default="daemon")
    parser.add_argument("--instance-version", default="0.17.17-historical-auto-4")
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
