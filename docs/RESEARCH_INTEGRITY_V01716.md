# Research integrity update v0.17.16

This release corrects research calculations and adds evidence controls. It never
changes Control v0.8.0, Paper entries/exits/risk, protected Celltrion 068270, or
REAL ORDER OFF. There is no NH execution adapter or automatic promotion.

## Calculation changes and comparability

- ATR adaptive stops use completed bars strictly before entry. Future high/low of
  the entry candle cannot change the stop chosen at its open.
- Five-minute research evaluates both O-H-L-C and O-L-H-C paths and uses the lower
  net result. Gaps through active levels use the observed opening price, followed
  by slippage/fees. One-minute Exit Replay also uses observed gap prices while
  preserving the locked Control reason priority in its read-only replay.
- These remain bar models: neither path proves the actual tick sequence, liquidity,
  queue position or attainable execution. Intrabar research exits are scheduled at
  bar end in account evaluation. Real Paper logic is not modified.
- Five-minute net returns use entry and exit transaction cash flows. The existing
  research cost assumptions remain 0.01% commission each side, 0.15% sell tax and
  0.05% slippage each side. These are configurable-model assumptions, not a claim
  that every instrument/date/account has those costs.
- Legacy research caches and UI comparison baselines are invalidated. The next full
  research run produces the new results. Previous scores must not be interpreted
  as a before/after strategy improvement: the measurement method changed.

## Separate account evaluation

Profitability and final holdout reports include `account`: integer share quantities,
cash constraints, simultaneous positions, commission/tax/slippage, equity at 5m
closes, monetary profit factor, return and account maximum drawdown. Same-timestamp
marks are valued together to avoid phantom drawdowns from event iteration order.

Defaults are KRW 10,000,000, 25% equity allocation per position and two simultaneous
positions. These are **independent research assumptions**, not Control sizing rules.
Account MDD is based on available bar-close marks and can miss intrabar losses.
Capacity/cash/duplicate-instrument/protected-code rejections are reported.
`maxDrawdownPct` in the legacy trade summary remains a compatibility field; its
`drawdownBasis` explicitly identifies cumulative trade-return percentage points.
Use `account.maxDrawdownPct` for this research account's measured risk.

## Precommitted future experiment

The first eligible Robust run freezes a candidate selected from the latest fold's
training segment (not the fold with the most favorable test return). It records:

- immutable experiment ID, candidate configuration, code and settings hashes;
- historical candidate/bar data hash and development cutoff;
- a fixed stock universe and historical start date, so changing bar counts or a
  rolling quality-map limit cannot silently change the candidate population;
- a fixed future calendar window, beginning tomorrow in Korea and ending 42 days
  after registration; the last day must close before evaluation;
- an append-only record of registration, consumption, evaluation and retirement.

These records live in separate `server/research_experiments.db`. Profitability and
public-benchmark selection stay within that experiment's development cutoff.
Rolling historical benchmark holdouts are labelled development comparisons, not
untouched final evidence. Other exploratory research views may still show recent
market data; this protocol freezes a candidate rather than claiming users are blind
to the market. This remains bar-based research and cannot replace Paper/Shadow
forward execution evidence.

Code, configuration or historical-data revisions block final evaluation. The final
window is claimed durably before computation and evaluated once; repeated requests
return the saved result. A crash after consumption does not silently retry. Changed
final data invalidates the cached evidence claim. Missing/insufficient trades produce
a failed result, never an automatic window extension or a more favorable reselection.

The 42-day window and minimum 20 final trades are research protocol parameters,
not statistical proof of profitability. `researchReplayReady` describes only the
research/replay gate. `deploymentReady` remains false even if every research gate
passes. Live execution still lacks separate broker validation.

Inspect from the repository root:

```bash
python server/research_experiments.py status
python server/execution_simulation.py selftest
python server/github_regime_reference.py status
```

If an experiment is invalidated, inspect the reason and preserve the database. An
operator can retire it explicitly, retaining the old manifest/result and reason:

```bash
python server/research_experiments.py retire --id EXPERIMENT_ID --reason "reviewed reason"
```

The next eligible run registers a new **future** window. It cannot relabel the old
period as unseen. Do not delete registries to erase unsuccessful attempts.

## Offline execution recovery

`execution_simulation.py` supplies a separate, append-only SQLite simulation
journal with deterministic intent identity, duplicate-event validation, cumulative
fill bounds, guarded transitions and restart recovery against supplied fake-broker
snapshots. Missing orders/unknown outcomes block recovery; there is no blind retry.
Unrelated databases and protected instruments are rejected.

The full research report caches 140 synthetic exercises per server process: seven
scenarios, each repeated 20 times (lost acknowledgement, partial fill/cancel,
duplicate fill, cancel/fill race, uncertain timeout, restart and stale/out-of-order
fill). Repetitions are regression exercises, not 140 independent market observations.

The report never claims NH validation. Production broker adapters, durable live
journaling/reconciliation, fill corrections, network timing and broker-specific
order lifecycle testing remain future work. No credentials, network client or
real-order endpoint is added.

## Aggregate reference

Schema v2 separates the full supplied market universe from traded issues, adds
arithmetic equal-weight returns alongside the median, and reports missing returns.
The builder forces a full historical rebuild when old definitions are present.
The phone accepts the old schema while waiting for the generated data PR; a successful
code update alone does not mean schema v2 data has been published.

The generated aggregate still follows branch → PR → Safety → review → merge.
Invalid dates, non-finite values, impossible ratios/counts, duplicate date/market rows
or mixed schemas reject the entire download and preserve the prior valid dataset
and sync metadata. No raw yearly data is stored on the phone.
