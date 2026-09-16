# GitHub Market-Regime Reference

## Purpose

This layer exists only to provide long-horizon Korean-market regime and breadth context.
It is deliberately separate from the NH 1-minute/5-minute forward evidence used for
execution research.

Source priority for historical context:

1. `FinanceData/marcap` public GitHub data -> compact aggregate regime reference
2. Financial Services Commission / `data.go.kr` -> optional official cross-check
3. KRX Data Marketplace -> optional paid/separately-approved cross-check

## Why this is efficient

The Android phone does **not** clone the upstream repository and does **not** download
30+ years of per-stock parquet files. A GitHub Action processes the public yearly files
off-device and proposes only a small market-level aggregate CSV and its provenance
metadata through a pull request. After Safety and review, the phone downloads the
merged compact CSV from `main` and stores it in a separate
`regime_reference.db`.

## Aggregate publication: PR, Safety, then review

The weekly schedule remains Monday 03:30 KST. Manual runs must select `main`.
Publication now follows this sequence:

1. Check out the latest `main`, resolve the upstream commit and build the aggregate.
2. Validate the output and run the complete safety test family before publishing.
3. Refuse unexpected staged files, non-aggregate changes or a changed `main` base.
4. If an open `automation/marcap-regime-*` PR exists, leave it unchanged and wait
   for review. Otherwise, push a new run/attempt-specific branch containing only
   `research/regime/marcap_regime_daily.csv` and `research/regime/marcap_regime_meta.json`.
5. Open a PR targeting `main`. An operator approves workflow execution when GitHub
   requests it, waits for **Safety Invariants on the current PR head** to pass,
   reviews the diff and provenance, and then merges through the PR.
6. Verify post-merge main Safety. Android sync continues to read only merged `main`.

There is no direct push to `main`, CI-skip marker, force-push, automatic approval,
automatic merge, or fallback that bypasses a permission/Safety failure. The builder's
pre-PR tests are not a substitute for the PR's own Safety check. A successful build
with a pending PR does not mean Android has received the proposed data.

The workflow uses the built-in `GITHUB_TOKEN`, with only `contents: write` and
`pull-requests: write` in its publication job; no PAT or broker credentials are added.
The repository must allow GitHub Actions to create pull requests in
**Settings > Actions > General > Workflow permissions**. If GitHub denies creation,
the job stops; the maintainer must resolve the permission explicitly. If creation
fails after a branch push, that branch remains unmerged for inspection. A workflow
rerun uses a new attempt-specific branch and does not overwrite it.

GitHub documents that `GITHUB_TOKEN`-created/updated PRs can have approval-required
workflow runs. Select **Approve workflows to run** when shown; never treat a
missing or pending Safety result as a pass. See
[GitHub's token event rules](https://docs.github.com/en/actions/concepts/security/github_token)
and [repository Actions settings](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/enabling-features-for-your-repository/managing-github-actions-settings-for-a-repository).

This change does not configure repository branch protection or rulesets. For
server-enforced protection, the maintainer should require PRs and the Safety check
for `main`, including approval invalidation/check revalidation after a head update.
The workflow itself never merges, even if repository protection is not configured.

The aggregate contains, per date and market:

- active issue count
- advancers / decliners / unchanged
- advance and decline ratios
- total traded value
- total market capitalization
- top-10 market-cap concentration
- cap-weighted daily change
- median daily change
- rolling 5-day breadth
- rolling 20-day cap-weighted return
- rolling 20-day volatility
- rolling turnover ratio
- observational regime label (`STRESS`, `RISK_OFF`, `NEUTRAL`, `RISK_ON`, `RISK_ON_STRONG`)

### Definition version 2 (v0.17.16)

`schema_version=2` covers all supplied upstream issues in `universe_count`,
`market_cap_total` and `top10_cap_share`, including suspended/no-trade rows.
`active_count` and `active_market_cap_total` use positive-volume issues.
Breadth uses active issues with finite reported returns; `missing_return_count`
tracks the rest instead of calling them unchanged. `return_observation_count` is
the breadth denominator.

`equal_weight_change_pct` is the arithmetic mean of observed active returns;
`median_change_pct` remains the median. The cap-weighted field uses **current-day**
capitalization of observed active issues. It is a descriptive reference, not a
lagged-weight investable index. Its compounded 20-day value must not be presented
as portfolio performance. A missing daily return resets that rolling window;
20 complete observations are required for the 20-day fields.

The first v2 build refreshes every year so incompatible definitions are not mixed.
Old v1 data remain readable while the new aggregate PR is pending. Status reports
the version actually imported. Once v2 is imported, schema downgrades are rejected.
The phone validates a complete snapshot before atomically replacing source rows;
failed validation preserves the prior rows and sync metadata. Only aggregate facts
are downloaded. Publication times from the historical source are not guaranteed,
so date-only references cannot be used as same-day intraday available information.

These labels are research annotations only. They are not Control entry filters, sizing
rules, exit rules or live-order inputs.

## Upstream provenance / licence boundary

Upstream repository: `FinanceData/marcap`.

At implementation time GitHub reported no machine-readable repository licence for the
upstream repository. This project therefore:

- does not copy upstream source code,
- does not commit upstream parquet files,
- does not store raw per-stock upstream rows on the Android phone,
- keeps attribution and the exact upstream commit SHA in generated metadata,
- stores only compact aggregate market-level research facts,
- treats the source as replaceable reference data rather than execution evidence.

If upstream terms change or redistribution becomes inappropriate, disable the GitHub
reference workflow and use the official FSC/KRX collectors instead.

## Safety boundary

Unchanged:

- Control strategy: `v0.8.0 LOCKED`
- `ENABLE_TRADING=False`
- REAL ORDER OFF
- Celltrion `068270` protection
- Paper entry/exit/risk semantics
- NH 1m/5m provenance and forward-validation gates

The regime reference database is not read by the order endpoint and cannot promote a
strategy to live trading.
