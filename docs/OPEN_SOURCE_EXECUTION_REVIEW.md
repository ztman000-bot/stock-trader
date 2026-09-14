# Open-source execution architecture review

This note records the ideas reviewed before the future Micro Live execution layer is built.
It is intentionally about architecture and safety, not copying public trading strategies.

## References reviewed

- **QuantConnect/Lean** (Apache-2.0): mature brokerage abstraction and explicit order-status handling.
- **NautilusTrader** (LGPL-3.0): deterministic event-driven execution, explicit order lifecycle, reconciliation and restart-oriented execution design.
- **OpenAlgo** (AGPL-3.0): broker abstraction, self-hosted operations and risk-management patterns.
- Public KIS Open API trading examples: useful operational ideas for Korean brokerage APIs, but repositories without a clear license are treated as idea-only references.

No third-party implementation code is copied into this repository by this change.

## What is worth adopting now

1. **Explicit order lifecycle contract**
   - INITIALIZED -> SUBMITTED -> ACCEPTED -> PARTIALLY_FILLED -> FILLED
   - cancel/reject/expire paths are modeled explicitly.
   - terminal orders cannot silently return to an active state.

2. **Deterministic client-order identity**
   - retries for the same logical signal must resolve to the same client key.
   - an intentional replacement/new attempt must use a new attempt number.
   - this is the basis for duplicate-submit protection.

3. **Reconciliation before trusting local state**
   - after restart or connection loss, internal order state must be compared with broker order/fill state.
   - mismatches must be surfaced, not silently overwritten.

4. **Broker adapter separation**
   - strategy/Control logic must not know NH request details.
   - a future NH execution adapter should translate broker messages into the internal lifecycle contract.

5. **Append-only execution evidence**
   - before Micro Live, order requests, broker acknowledgements, fills, cancels, rejects and restart reconciliation need durable event records.

## What is deliberately NOT adopted

- Public-repository buy/sell thresholds or claimed profitable strategies.
- Any code that enables real orders.
- Any automatic AI mutation of Control v0.8.0.
- Any relaxation of the existing Paper/Shadow/OOS/Walk-Forward/Lockbox validation path.

## Current implementation boundary

`server/execution_readiness.py` is a **read-only design contract** only. It has no NH credentials, no broker client, no order endpoint, and no database write. It provides:

- guarded state transitions;
- deterministic idempotency/client-order keys;
- pure snapshot reconciliation;
- an explicit report of what is still missing before Micro Live.

## Required before Micro Live

The execution layer remains blocked until all of the following exist and are tested:

- NH broker adapter behind a separate interface;
- persistent append-only order/event journal;
- startup/reconnect reconciliation against NH orders, fills and positions;
- duplicate-submit/retry tests;
- partial-fill/cancel/reject/timeout/restart simulations;
- roughly 100-200 broker order-lifecycle simulation trials;
- the existing strategy-validation gates remain satisfied.

Control v0.8.0, Paper entry/exit/risk semantics, protected Celltrion handling and REAL ORDER OFF are unchanged.
