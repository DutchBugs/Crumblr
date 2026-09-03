"""Derive expected exposure from durable platform execution history (core

critical path item 8, ADR-010). Pure: no I/O, no broker, no clock of its
own — `flatten_plan.py`'s "no second opinion" discipline applied one step
further. This module only classifies already-read event history; deciding
what to do with the result (compare it against the broker, decide whether
to record a `RECONCILED` verdict) is the driver's job
(`application/execution.py::ExecutionOrchestrator.reconcile_once()`).

Review 1.16 §7-8: *"expected state must come from the platform's durable
execution/order history... Otherwise reconciliation becomes: compare MT5
to MT5. That detects nothing."* `application/reconciliation.py::
ExpectedState.expected_position_tickets` has existed since F-047 and has
never been populated by any caller — this module is the producer that was
always missing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from crumblr.domain.enums import EntryType, ExecutionEventType, FlattenEventType
from crumblr.persistence.execution import ExecutionEventRecord
from crumblr.persistence.flatten import FlattenEventRecord


class _Exposure(Enum):
    """What one durable event, as the *last* exposure-relevant event in a

    request's history, says about that request's current exposure.
    """

    NONE = "NONE"
    """Committed to nothing; expects zero."""

    DETERMINED = "DETERMINED"
    """Exposure is durably known; read the tickets from the event's payload."""

    UNDETERMINED = "UNDETERMINED"
    """Cannot honestly say either way; fail closed."""

    NOT_EXPOSURE_RELEVANT = "NOT_EXPOSURE_RELEVANT"
    """Does not itself change exposure; keep looking further back for the

    last event that does. Only `RECONCILED` is this today — treating it
    as exposure-relevant would make a request's own derivation poison
    itself the pass after `reconcile_once()` writes a verdict for it."""


_EXPOSURE_BY_EVENT: Mapping[ExecutionEventType, _Exposure] = {
    ExecutionEventType.REQUEST_CLAIMED: _Exposure.NONE,
    ExecutionEventType.INELIGIBLE: _Exposure.NONE,
    ExecutionEventType.GATE_CLOSED: _Exposure.NONE,
    ExecutionEventType.RECONCILIATION_BLOCKED: _Exposure.NONE,
    ExecutionEventType.FINAL_RISK_PASSED: _Exposure.NONE,
    ExecutionEventType.FINAL_RISK_BLOCKED: _Exposure.NONE,
    ExecutionEventType.ORDER_CHECKED: _Exposure.NONE,
    ExecutionEventType.ORDER_CHECK_REJECTED: _Exposure.NONE,
    ExecutionEventType.SUBMISSION_GATE_PASSED: _Exposure.NONE,
    ExecutionEventType.SUBMISSION_GATE_BLOCKED: _Exposure.NONE,
    ExecutionEventType.SUBMISSION_STARTED: _Exposure.UNDETERMINED,
    ExecutionEventType.AMBIGUOUS_OUTCOME_RESOLVED: _Exposure.DETERMINED,
    ExecutionEventType.RECONCILED: _Exposure.NOT_EXPOSURE_RELEVANT,
    # Reserved for M5, unemittable today — see domain/enums.py. If a
    # future item emits one of these without updating this table, the
    # exhaustiveness test below still passes (they are already mapped),
    # but the UNDETERMINED choice means this module fails closed rather
    # than silently reporting zero exposure for a state it does not yet
    # know how to read. FILLED now has a real (still unwired) producer
    # as of Phase B item B3 — its UNDETERMINED entry is deliberately
    # unchanged: attributing a ticket is still the magic-number search's
    # job (`_recover_ambiguous_submission`), not this event's own
    # payload — see `domain/enums.py::ExecutionEventType.FILLED`'s own
    # docstring.
    ExecutionEventType.SUBMITTED: _Exposure.UNDETERMINED,
    ExecutionEventType.BROKER_ACK: _Exposure.UNDETERMINED,
    ExecutionEventType.FILLED: _Exposure.UNDETERMINED,
    ExecutionEventType.CLOSED: _Exposure.UNDETERMINED,
    ExecutionEventType.REJECTED: _Exposure.DETERMINED,
}
"""Total over `ExecutionEventType` — see

`tests/unit/test_expected_state.py
::test_every_execution_event_type_has_a_declared_exposure_meaning`. A
member added later without an entry here fails that test instead of
silently being read as "no exposure"."""


@dataclass(frozen=True)
class DerivedExposure:
    """What the platform's own durable history says it should currently

    have at the broker — never what the latest MT5 snapshot says (review
    1.16 §8: that would compare MT5 to MT5 and detect nothing).
    """

    expected_position_tickets: frozenset[int] = frozenset()
    expected_pending_order_ids: frozenset[int] = frozenset()
    undetermined_reasons: tuple[str, ...] = ()
    tickets_by_request: Mapping[UUID, frozenset[int]] = field(default_factory=dict)
    determined_request_ids: frozenset[UUID] = frozenset()
    expected_stop_loss_by_request: Mapping[UUID, Decimal] = field(default_factory=dict)
    """Core critical path item 9: the platform's own durably-recorded

    intended stop-loss for each determined, submitted request, read from
    `SUBMISSION_STARTED`'s own persisted `ApprovedOrder` payload. A
    request absent from this mapping (rather than present with some
    fallback value) means its intended stop could not be established
    from durable history — deliberately not folded into
    `undetermined_reasons` (which feeds the whole-book MATCHED/MISMATCHED
    verdict); the absence itself is the fail-closed signal `application
    /reconciliation.py::verify_protective_stops` consumes."""

    @classmethod
    def empty(cls) -> DerivedExposure:
        """No durable history at all — provably equal to `flat()`'s own

        expectation. See `tests/unit/test_expected_state.py
        ::test_an_empty_history_is_exactly_flat`, the mechanical version
        of this item's central honesty claim."""
        return cls()


def _last_exposure_relevant(
    events: Sequence[ExecutionEventRecord],
) -> tuple[ExecutionEventType, dict[str, Any] | None] | None:
    for event in reversed(events):
        exposure = _EXPOSURE_BY_EVENT[event.event_type]
        if exposure is _Exposure.NOT_EXPOSURE_RELEVANT:
            continue
        return event.event_type, event.payload
    return None


def _flatten_removed_and_undetermined(
    flatten_histories: Sequence[tuple[UUID, Sequence[FlattenEventRecord]]],
) -> tuple[frozenset[int], tuple[str, ...]]:
    """Tickets a resolved flatten durably closed (to be removed from

    whatever an execution request still expects), and undetermined
    reasons for tickets an unresolved flatten committed to but has not
    yet confirmed (ADR-009's own ambiguity, one layer up).

    `FLATTEN_SUBMISSION_STARTED` never by itself removes a ticket — D-050
    warns explicitly against reading a commitment as an outcome.
    """
    closed: set[int] = set()
    reasons: list[str] = []
    for flatten_request_id, events in flatten_histories:
        commitment_payload: dict[str, Any] | None = None
        resolved_payload: dict[str, Any] | None = None
        for event in events:
            if event.event_type is FlattenEventType.FLATTEN_SUBMISSION_STARTED:
                commitment_payload = event.payload
            elif event.event_type is FlattenEventType.FLATTEN_OUTCOME_RESOLVED:
                resolved_payload = event.payload
        if commitment_payload is None:
            continue
        if resolved_payload is not None:
            closed.update(int(t) for t in resolved_payload.get("closed_tickets", []))
        else:
            targets = commitment_payload.get("instructions", [])
            tickets = [int(target["ticket"]) for target in targets]
            if tickets:
                reasons.append(
                    f"flatten occurrence {flatten_request_id} committed to closing "
                    f"{tickets} but has not yet confirmed the outcome"
                )
    return frozenset(closed), tuple(reasons)


def derive_expected_exposure(
    request_histories: Sequence[tuple[UUID, Sequence[ExecutionEventRecord]]],
    *,
    flatten_histories: Sequence[tuple[UUID, Sequence[FlattenEventRecord]]] = (),
) -> DerivedExposure:
    """One `DerivedExposure` from every committed request's durable

    history plus every flatten occurrence's — see `_EXPOSURE_BY_EVENT`
    for the per-request classification and this item's ADR (§2.3-§2.6)
    for the full reasoning.
    """
    tickets_by_request: dict[UUID, frozenset[int]] = {}
    determined_request_ids: set[UUID] = set()
    reasons: list[str] = []
    all_tickets: set[int] = set()
    stop_loss_by_request: dict[UUID, Decimal] = {}

    for order_request_id, events in request_histories:
        classification = _last_exposure_relevant(events)
        if classification is None:
            continue
        event_type, payload = classification

        if event_type is ExecutionEventType.SUBMISSION_STARTED:
            reasons.append(
                f"order_request_id {order_request_id} is stuck at SUBMISSION_STARTED "
                "with its outcome not yet determined"
            )
            continue

        if event_type is ExecutionEventType.REJECTED:
            # A definite broker rejection: no position was ever created,
            # no ticket search needed -- mirrors AMBIGUOUS_OUTCOME_RESOLVED
            # {submitted=False}'s own zero-exposure shape exactly (Phase B
            # item B3).
            determined_request_ids.add(order_request_id)
            tickets_by_request[order_request_id] = frozenset()
            continue

        if event_type is ExecutionEventType.AMBIGUOUS_OUTCOME_RESOLVED:
            if payload is not None and payload.get("integrity_ambiguity"):
                reasons.append(
                    f"order_request_id {order_request_id}: "
                    f"{payload.get('matching_position_count')} broker positions share "
                    "its magic number -- cannot safely attribute any of them to this "
                    "request (integrity ambiguity, not a malformed payload)"
                )
                continue
            if payload is None or "submitted" not in payload:
                reasons.append(
                    f"order_request_id {order_request_id}'s AMBIGUOUS_OUTCOME_RESOLVED "
                    "payload is missing or malformed"
                )
                continue
            if not payload["submitted"]:
                determined_request_ids.add(order_request_id)
                tickets_by_request[order_request_id] = frozenset()
                continue
            tickets = frozenset(int(t) for t in payload.get("matching_tickets", []))
            determined_request_ids.add(order_request_id)
            tickets_by_request[order_request_id] = tickets
            all_tickets |= tickets

            stop_loss_price = _stop_loss_price_of(events)
            if stop_loss_price is not None:
                stop_loss_by_request[order_request_id] = stop_loss_price

            entry_type = _entry_type_of(events)
            if entry_type is not None and entry_type is not EntryType.MARKET:
                # D-049: `magic` is not tracked for pending orders at any
                # layer today. A non-MARKET entry could be sitting as a
                # pending order this platform cannot name — an empty
                # `expected_pending_order_ids` would be a false MATCHED,
                # not an honest zero.
                reasons.append(
                    f"order_request_id {order_request_id} is a {entry_type.value} entry; "
                    "a resulting pending order cannot be attributed (D-049)"
                )
        # NONE -> nothing to record.

    closed_by_flatten, flatten_reasons = _flatten_removed_and_undetermined(flatten_histories)
    all_tickets -= closed_by_flatten
    for order_request_id, tickets in tickets_by_request.items():
        remaining = tickets - closed_by_flatten
        if remaining != tickets:
            tickets_by_request[order_request_id] = remaining
    reasons.extend(flatten_reasons)

    return DerivedExposure(
        expected_position_tickets=frozenset(all_tickets),
        expected_pending_order_ids=frozenset(),
        undetermined_reasons=tuple(reasons),
        tickets_by_request=tickets_by_request,
        determined_request_ids=frozenset(determined_request_ids),
        expected_stop_loss_by_request=stop_loss_by_request,
    )


def _entry_type_of(events: Sequence[ExecutionEventRecord]) -> EntryType | None:
    for event in events:
        if event.event_type is ExecutionEventType.SUBMISSION_STARTED and event.payload:
            raw = event.payload.get("entry_type")
            if raw is not None:
                return EntryType(raw)
    return None


def _stop_loss_price_of(events: Sequence[ExecutionEventRecord]) -> Decimal | None:
    """Core critical path item 9: the platform's own intended stop-loss

    for a request, as durably recorded in `SUBMISSION_STARTED`'s full
    `ApprovedOrder` payload (`execution.py::_start_submission`). Mirrors
    `_entry_type_of` above exactly. `stop_loss_price` is a required
    `ApprovedOrder` field, so a `None` return here is a durable-history
    data-integrity gap, not an expected outcome — callers must treat it
    as fail-closed, not as "no stop was intended."
    """
    for event in events:
        if event.event_type is ExecutionEventType.SUBMISSION_STARTED and event.payload:
            raw = event.payload.get("stop_loss_price")
            if raw is not None:
                return Decimal(str(raw))
    return None
