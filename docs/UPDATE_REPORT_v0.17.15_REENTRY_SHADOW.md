# v0.17.15 Research Hotfix — Same-stock Reentry Shadow Continuation

## Goal
Collect enough trade-level evidence to decide whether a losing/STOP trade should block same-stock re-entry for the rest of the day, without changing Control v0.8.0.

## What is collected
The Android phone now runs a separate research-only Shadow continuation daemon. It reads `BUY_CANDIDATE` and `SHADOW_ONLY` rows from the localhost `/api/paper/scan` endpoint and records normalized hypothetical trades in `shadow_continuation_trades`.

The ledger records:
- first same-code entry vs later same-code re-entry
- re-entry after a prior Shadow loss
- re-entry after a prior Shadow win
- whether an actual Control/Paper loss already occurred in that code
- trades that continue after the normal Paper daily lock
- entry score/reasons, entry/exit price, exit reason and normalized PnL%

## Research continuation policy
For data collection only, the Shadow ledger ignores the Control daily-loss lock, two-consecutive-loss lock, max daily trade count and global max-open-position limit. It still allows only one open Shadow position per code and only one entry per code/signal bucket. This gives more counterfactual samples without changing Paper/Control behavior.

## Exit model
The Shadow ledger reuses the locked Control v0.8.0 constants for STOP, trailing stop, cost-cover protection, commission, tax and slippage, and performs EOD exits. It uses local cached quotes and does not add NH REST calls.

## Decision threshold
The proposed rule under study is:

> STOP/loss 후 동일 종목 당일 재진입 금지

No Control change is applied. `readyForControlReview` stays false until at least 50 completed re-entry-after-loss Shadow trades exist. A larger sample should still be preferred before any Control change.

## Android operations
`server/start_android.sh` starts `server/ensure_shadow_continuation.sh`, which keeps one version-aware daemon instance running. The daemon writes a current report to `server/shadow_continuation_report.json` and the full trade ledger stays in the existing SQLite DB.

Disable only if needed by setting `SHADOW_CONTINUATION_ENABLED=false`; default is enabled on the dedicated Android server.

## Safety
- Control v0.8.0 unchanged
- `ENABLE_TRADING=False` unchanged
- REAL ORDER OFF
- no order endpoint calls
- no writes to `paper_trades`
- no automatic strategy promotion or Control mutation
