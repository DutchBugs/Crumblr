"""The event journal vocabulary (build.md §23).

Components talk to each other in typed events, not free-form agent chat. Each
envelope carries `correlation_id` (which decision window this belongs to) and
`causation_id` (which event directly triggered it), so a trade can be walked
back to the tick that started it.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Self, cast
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from crumblr.domain.enums import (
    Environment,
    KillSwitchState,
    OrderState,
    ReasonCode,
    Regime,
    Side,
)
from crumblr.domain.models import (
    AccountState,
    Contract,
    DecisionCapsule,
    ExecutionResult,
    Incident,
    MarketSnapshot,
    PositionState,
    RiskDecision,
    SupervisorDecision,
    Symbol,
    TradeIntent,
    VersionTag,
)
from crumblr.domain.money import ZERO, ExactDecimal
from crumblr.domain.timeutils import UtcDatetime, utc_now


class EventType(StrEnum):
    """Event names as persisted in the journal. Renaming one is a migration."""

    MARKET_SNAPSHOT_READY = "MarketSnapshotReady"
    SIGNAL_GENERATED = "SignalGenerated"
    TRADE_INTENT_CREATED = "TradeIntentCreated"
    RISK_DECISION_MADE = "RiskDecisionMade"
    SUPERVISOR_DECISION_MADE = "SupervisorDecisionMade"
    ORDER_CHECK_COMPLETED = "OrderCheckCompleted"
    ORDER_SUBMITTED = "OrderSubmitted"
    ORDER_RESULT_RECEIVED = "OrderResultReceived"
    POSITION_CHANGED = "PositionChanged"
    RECONCILIATION_COMPLETED = "ReconciliationCompleted"
    EVALUATION_COMPLETED = "EvaluationCompleted"
    INCIDENT_RAISED = "IncidentRaised"
    SYSTEM_HALTED = "SystemHalted"
    DECISION_CAPSULE_SEALED = "DecisionCapsuleSealed"


# --------------------------------------------------------------------------- #
# Payloads that are not already domain objects
# --------------------------------------------------------------------------- #


class SignalGenerated(Contract):
    """A strategy evaluated a window. NO_TRADE is a first-class outcome."""

    signal_id: UUID
    snapshot_id: UUID
    symbol: Symbol
    strategy_id: VersionTag
    strategy_version: VersionTag
    model_version: VersionTag | None = None
    proposed_side: Side
    confidence: float = Field(ge=0.0, le=1.0)
    regime: Regime = Regime.UNKNOWN
    feature_snapshot_id: UUID
    feature_set_version: VersionTag
    reason_codes: tuple[str, ...] = ()


class OrderCheckCompleted(Contract):
    """Result of the broker's pre-flight `order_check`."""

    order_request_id: UUID
    intent_id: UUID
    accepted: bool
    retcode: int | None = None
    comment: str | None = Field(default=None, max_length=512)
    margin_required: ExactDecimal | None = Field(default=None, ge=ZERO)
    payload: dict[str, Any] | None = None


class OrderSubmitted(Contract):
    order_request_id: UUID
    intent_id: UUID
    broker_symbol: Symbol
    side: Side
    submitted_at_utc: UtcDatetime
    idempotency_key: UUID


class OrderResultReceived(Contract):
    result: ExecutionResult
    state: OrderState


class PositionChanged(Contract):
    before: tuple[PositionState, ...] = ()
    after: tuple[PositionState, ...] = ()
    trigger: Annotated[str, Field(min_length=1, max_length=64)]


class ReconciliationCompleted(Contract):
    """build.md §7 invariant 6. A mismatch is a HALT condition, not a retry."""

    reconciliation_id: UUID
    account: AccountState
    broker_positions: tuple[PositionState, ...] = ()
    local_positions: tuple[PositionState, ...] = ()
    matched: bool
    mismatch_detail: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _check_mismatch_is_explained(self) -> Self:
        if not self.matched and not self.mismatch_detail:
            raise ValueError("a failed reconciliation must describe the mismatch")
        return self


class EvaluationCompleted(Contract):
    """Post-trade scorecard produced by the Evaluator (build.md §10.1)."""

    evaluation_id: UUID
    intent_id: UUID | None = None
    window_start_utc: UtcDatetime
    window_end_utc: UtcDatetime
    policy_version: VersionTag
    metrics: dict[str, Any]
    anomalies: tuple[str, ...] = ()


class SystemHalted(Contract):
    """Kill switch tripped. Agents may trip it; only an operator may reset it."""

    state_before: KillSwitchState
    state_after: KillSwitchState
    reason_codes: tuple[ReasonCode, ...]
    tripped_by: Annotated[str, Field(min_length=1, max_length=64)]
    detail: str | None = Field(default=None, max_length=2000)
    incident_id: UUID | None = None

    @model_validator(mode="after")
    def _check_reason(self) -> Self:
        if not self.reason_codes:
            raise ValueError("a halt must record why it happened")
        return self


# --------------------------------------------------------------------------- #
# Envelope
# --------------------------------------------------------------------------- #


class Event[PayloadT: Contract](Contract):
    """Journal envelope. Every event carries its own provenance."""

    event_id: UUID
    event_type: EventType
    occurred_at_utc: UtcDatetime
    correlation_id: UUID
    causation_id: UUID | None = None
    schema_version: int = Field(default=1, ge=1)
    environment: Environment
    source: Annotated[str, Field(min_length=1, max_length=64)]
    payload: PayloadT

    @model_validator(mode="after")
    def _check_payload_matches_type(self) -> Self:
        expected = EVENT_PAYLOAD_TYPES.get(self.event_type)
        if expected is not None and not isinstance(self.payload, expected):
            raise ValueError(
                f"{self.event_type} requires a {expected.__name__} payload, "
                f"got {type(self.payload).__name__}"
            )
        return self


EVENT_PAYLOAD_TYPES: dict[EventType, type[Contract]] = {
    EventType.MARKET_SNAPSHOT_READY: MarketSnapshot,
    EventType.SIGNAL_GENERATED: SignalGenerated,
    EventType.TRADE_INTENT_CREATED: TradeIntent,
    EventType.RISK_DECISION_MADE: RiskDecision,
    EventType.SUPERVISOR_DECISION_MADE: SupervisorDecision,
    EventType.ORDER_CHECK_COMPLETED: OrderCheckCompleted,
    EventType.ORDER_SUBMITTED: OrderSubmitted,
    EventType.ORDER_RESULT_RECEIVED: OrderResultReceived,
    EventType.POSITION_CHANGED: PositionChanged,
    EventType.RECONCILIATION_COMPLETED: ReconciliationCompleted,
    EventType.EVALUATION_COMPLETED: EvaluationCompleted,
    EventType.INCIDENT_RAISED: Incident,
    EventType.SYSTEM_HALTED: SystemHalted,
    EventType.DECISION_CAPSULE_SEALED: DecisionCapsule,
}
"""Which payload class belongs to which event type."""

_PAYLOAD_TO_EVENT_TYPE: dict[type[Contract], EventType] = {
    payload_type: event_type for event_type, payload_type in EVENT_PAYLOAD_TYPES.items()
}


def event_type_for(payload: Contract) -> EventType:
    """Which event type a payload class belongs to.

    Exposed so a caller can derive an event's identity *before* building it —
    a deterministic `event_id` has to be computed from the type and the
    payload, and the type is only known through this mapping.
    """
    event_type = _PAYLOAD_TO_EVENT_TYPE.get(type(payload))
    if event_type is None:
        raise ValueError(f"{type(payload).__name__} is not a registered event payload")
    return event_type


def build_event[PayloadT: Contract](
    payload: PayloadT,
    *,
    correlation_id: UUID,
    environment: Environment,
    source: str,
    causation_id: UUID | None = None,
    schema_version: int = 1,
    event_id: UUID | None = None,
    occurred_at_utc: UtcDatetime | None = None,
) -> Event[PayloadT]:
    """Wrap `payload` in an envelope, deriving the event type from its class.

    The envelope is parametrised with the concrete payload class so that
    serialisation to the journal writes the payload's own fields rather than
    the base-class projection of them.

    Both `event_id` and `occurred_at_utc` may be supplied by the producer, and
    a producer that writes to the journal should supply them:

    - The journal's append is idempotent on `event_id` (ADR-003 invariant 3),
      which only converges if re-emitting the *same logical event* yields the
      same id. A fresh `uuid4` per call turns a retried run into duplicate
      history rather than a no-op.
    - `occurred_at_utc` is market time and is what replay orders by (invariant
      4). The wall clock below is a fallback for events that genuinely happen
      now — an operator action, a heartbeat — not for anything replayed.
    """
    event_type = event_type_for(payload)
    payload_type = type(payload)
    envelope_cls = Event[payload_type]  # type: ignore[valid-type]
    return cast(
        "Event[PayloadT]",
        envelope_cls(
            event_id=event_id if event_id is not None else uuid4(),
            event_type=event_type,
            occurred_at_utc=occurred_at_utc if occurred_at_utc is not None else utc_now(),
            correlation_id=correlation_id,
            causation_id=causation_id,
            schema_version=schema_version,
            environment=environment,
            source=source,
            payload=payload,
        ),
    )


def decode_event(raw: dict[str, Any]) -> Event[Contract]:
    """Rebuild an event from journal storage with its declared payload type.

    An unknown `event_type` raises rather than degrading to a dict: replay must
    fail loudly when it meets an event it cannot interpret.
    """
    event_type = raw.get("event_type")
    if not isinstance(event_type, str):
        raise ValueError("event is missing a string event_type")
    try:
        known_type = EventType(event_type)
    except ValueError as exc:
        raise ValueError(f"unknown event_type {event_type!r}") from exc
    payload_type = EVENT_PAYLOAD_TYPES[known_type]
    envelope_cls = Event[payload_type]  # type: ignore[valid-type]
    return cast("Event[Contract]", envelope_cls.model_validate(raw))
