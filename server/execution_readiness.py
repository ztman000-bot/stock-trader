"""Read-only execution architecture contract for future Micro Live work.

This module deliberately contains no broker client, credential access, order endpoint,
or database mutation.  It captures the execution-safety ideas we want to validate
before any real-order implementation exists: explicit order states, guarded state
transitions, deterministic client-order keys, and broker/internal reconciliation.

Control v0.8.0 and Paper semantics are intentionally outside this module.
"""

from __future__ import annotations

from hashlib import sha256
from typing import Iterable, Mapping


ARCHITECTURE_VERSION = "execution-readiness-0.1"
CONTROL_STRATEGY = "v0.8.0 LOCKED"
REAL_ORDER_ENABLED = False
RESEARCH_ONLY = True

ORDER_STATES = (
    "INITIALIZED",
    "SUBMITTED",
    "ACCEPTED",
    "PARTIALLY_FILLED",
    "CANCEL_PENDING",
    "FILLED",
    "CANCELED",
    "REJECTED",
    "EXPIRED",
)
TERMINAL_STATES = frozenset({"FILLED", "CANCELED", "REJECTED", "EXPIRED"})

# Conservative lifecycle contract.  A future broker adapter may translate NH-specific
# messages into these states, but it must not bypass this transition guard.
_ALLOWED_TRANSITIONS = {
    "INITIALIZED": frozenset({"SUBMITTED", "REJECTED"}),
    "SUBMITTED": frozenset({"ACCEPTED", "PARTIALLY_FILLED", "FILLED", "CANCELED", "REJECTED", "EXPIRED"}),
    "ACCEPTED": frozenset({"PARTIALLY_FILLED", "FILLED", "CANCEL_PENDING", "CANCELED", "REJECTED", "EXPIRED"}),
    "PARTIALLY_FILLED": frozenset({"PARTIALLY_FILLED", "FILLED", "CANCEL_PENDING", "CANCELED", "EXPIRED"}),
    "CANCEL_PENDING": frozenset({"PARTIALLY_FILLED", "FILLED", "CANCELED", "REJECTED", "EXPIRED"}),
    "FILLED": frozenset(),
    "CANCELED": frozenset(),
    "REJECTED": frozenset(),
    "EXPIRED": frozenset(),
}


def _state(value: object) -> str:
    state = str(value or "").strip().upper()
    if state not in _ALLOWED_TRANSITIONS:
        raise ValueError(f"unknown order state: {value!r}")
    return state


def transition_allowed(current: object, new: object) -> bool:
    """Return whether an order may move from *current* to *new*.

    Terminal states are intentionally immutable.  PARTIALLY_FILLED ->
    PARTIALLY_FILLED is allowed because multiple fills can arrive before completion.
    """

    current_state = _state(current)
    new_state = _state(new)
    return new_state in _ALLOWED_TRANSITIONS[current_state]


def apply_transition(current: object, new: object) -> str:
    """Validate and return the normalized target state, or fail closed."""

    current_state = _state(current)
    new_state = _state(new)
    if not transition_allowed(current_state, new_state):
        raise ValueError(f"invalid order transition: {current_state} -> {new_state}")
    return new_state


def client_order_key(
    *,
    trading_date: object,
    code: object,
    side: object,
    signal_bucket: object,
    attempt: int = 0,
    strategy: str = "control-v0.8.0",
) -> str:
    """Create a deterministic idempotency key for a future broker adapter.

    The same logical signal and attempt produce the same key, so a retry can be
    recognized instead of accidentally becoming a second order.  Incrementing
    *attempt* represents an intentional new/replacement order.
    """

    code_text = str(code or "").strip()
    side_text = str(side or "").strip().upper()
    if len(code_text) != 6 or not code_text.isdigit():
        raise ValueError("code must be a 6-digit Korean stock code")
    if side_text not in {"BUY", "SELL"}:
        raise ValueError("side must be BUY or SELL")
    if int(attempt) < 0:
        raise ValueError("attempt must be >= 0")
    raw = "|".join(
        (
            str(strategy).strip(),
            str(trading_date).strip(),
            code_text,
            side_text,
            str(signal_bucket).strip(),
            str(int(attempt)),
        )
    )
    digest = sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"std-{digest}"


def _index(rows: Iterable[Mapping[str, object]]) -> dict[str, Mapping[str, object]]:
    indexed: dict[str, Mapping[str, object]] = {}
    for row in rows:
        key = str(row.get("clientOrderKey") or "").strip()
        if not key:
            raise ValueError("every order snapshot requires clientOrderKey")
        if key in indexed:
            raise ValueError(f"duplicate clientOrderKey: {key}")
        indexed[key] = row
    return indexed


def reconcile_orders(
    internal_orders: Iterable[Mapping[str, object]],
    broker_orders: Iterable[Mapping[str, object]],
) -> dict[str, object]:
    """Pure comparison of internal and broker snapshots.

    No broker call is made here.  The future NH adapter will supply snapshots.  The
    comparison intentionally treats any mismatch as something requiring operator or
    recovery logic rather than silently repairing state.
    """

    internal = _index(internal_orders)
    broker = _index(broker_orders)
    missing_in_broker = sorted(set(internal) - set(broker))
    external_only = sorted(set(broker) - set(internal))
    status_mismatch: list[dict[str, object]] = []
    quantity_mismatch: list[dict[str, object]] = []
    fill_mismatch: list[dict[str, object]] = []

    for key in sorted(set(internal) & set(broker)):
        left = internal[key]
        right = broker[key]
        left_status = str(left.get("status") or "").upper()
        right_status = str(right.get("status") or "").upper()
        if left_status != right_status:
            status_mismatch.append({"clientOrderKey": key, "internal": left_status, "broker": right_status})
        left_qty = int(left.get("qty") or 0)
        right_qty = int(right.get("qty") or 0)
        if left_qty != right_qty:
            quantity_mismatch.append({"clientOrderKey": key, "internal": left_qty, "broker": right_qty})
        left_filled = int(left.get("filledQty") or 0)
        right_filled = int(right.get("filledQty") or 0)
        if left_filled != right_filled:
            fill_mismatch.append({"clientOrderKey": key, "internal": left_filled, "broker": right_filled})

    clean = not (missing_in_broker or external_only or status_mismatch or quantity_mismatch or fill_mismatch)
    return {
        "ok": clean,
        "readOnly": True,
        "missingInBroker": missing_in_broker,
        "externalOnly": external_only,
        "statusMismatch": status_mismatch,
        "quantityMismatch": quantity_mismatch,
        "fillMismatch": fill_mismatch,
    }


def readiness_report() -> dict[str, object]:
    """Expose what is designed now and what must still exist before Micro Live."""

    return {
        "ok": True,
        "architectureVersion": ARCHITECTURE_VERSION,
        "researchOnly": RESEARCH_ONLY,
        "controlStrategy": CONTROL_STRATEGY,
        "realOrderEnabled": REAL_ORDER_ENABLED,
        "microLiveReady": False,
        "stateModel": {
            "states": list(ORDER_STATES),
            "terminalStates": sorted(TERMINAL_STATES),
            "partialFillModeled": True,
            "invalidTransitionFailClosed": True,
        },
        "capabilities": {
            "deterministicClientOrderKey": True,
            "snapshotReconciliationSpec": True,
            "brokerAdapterImplemented": False,
            "persistentOrderJournalImplemented": False,
            "restartReconciliationImplemented": False,
            "nhOrderLifecycleSimulationCompleted": False,
        },
        "requiredBeforeMicroLive": [
            "NH broker adapter behind a separate execution interface",
            "persistent append-only order/event journal",
            "startup reconciliation against broker orders/fills/positions",
            "duplicate-submit and retry/idempotency tests",
            "partial-fill, cancel, reject, timeout and restart simulations",
            "100-200 broker order-lifecycle simulation trials before real orders",
        ],
        "safety": {
            "sendsOrders": False,
            "readsCredentials": False,
            "mutatesControl": False,
            "mutatesPaper": False,
        },
    }
