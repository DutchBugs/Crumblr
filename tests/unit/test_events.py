"""Event journal envelope (build.md §23).

The journal is the system's memory. If an envelope can lose payload fields on
the way to storage, or silently accept an event it cannot interpret, replay
stops being evidence.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from crumblr.domain.enums import Environment, KillSwitchState, ReasonCode
from crumblr.domain.events import (
    EVENT_PAYLOAD_TYPES,
    Event,
    EventType,
    ReconciliationCompleted,
    SystemHalted,
    build_event,
    decode_event,
)
from crumblr.domain.models import MarketSnapshot, TradeIntent
from tests.conftest import FIXED_NOW, make_account_state, make_intent, make_snapshot


class TestEnvelopeConstruction:
    def test_event_type_is_derived_from_the_payload(self) -> None:
        event = build_event(
            make_intent(),
            correlation_id=uuid4(),
            environment=Environment.PAPER,
            source="trading_agent",
        )
        assert event.event_type is EventType.TRADE_INTENT_CREATED

    def test_every_event_type_has_a_registered_payload(self) -> None:
        assert set(EVENT_PAYLOAD_TYPES) == set(EventType)

    def test_unregistered_payload_is_refused(self) -> None:
        from crumblr.domain.models import Contract

        class Rogue(Contract):
            value: int

        with pytest.raises(ValueError, match="not a registered event payload"):
            build_event(
                Rogue(value=1),
                correlation_id=uuid4(),
                environment=Environment.PAPER,
                source="test",
            )

    def test_payload_must_match_the_declared_event_type(self) -> None:
        """A mislabelled envelope would corrupt replay, so it is refused up front."""
        with pytest.raises(ValidationError, match="requires a TradeIntent payload"):
            Event[MarketSnapshot](
                event_id=uuid4(),
                event_type=EventType.TRADE_INTENT_CREATED,
                occurred_at_utc=FIXED_NOW,
                correlation_id=uuid4(),
                environment=Environment.PAPER,
                source="test",
                payload=make_snapshot(),
            )


class TestJournalRoundTrip:
    """A payload must survive storage without losing subclass fields."""

    def test_intent_payload_survives_serialisation(self) -> None:
        intent = make_intent()
        event = build_event(
            intent,
            correlation_id=uuid4(),
            environment=Environment.PAPER,
            source="trading_agent",
        )
        restored = decode_event(event.model_dump(mode="json"))
        assert isinstance(restored.payload, TradeIntent)
        assert restored.payload == intent
        assert restored.payload.decision_hash == intent.decision_hash

    def test_snapshot_payload_survives_serialisation(self) -> None:
        snapshot = make_snapshot()
        event = build_event(
            snapshot,
            correlation_id=uuid4(),
            environment=Environment.PAPER,
            source="market_data",
        )
        restored = decode_event(event.model_dump(mode="json"))
        assert restored.payload == snapshot

    def test_correlation_and_causation_are_preserved(self) -> None:
        correlation_id, causation_id = uuid4(), uuid4()
        event = build_event(
            make_intent(),
            correlation_id=correlation_id,
            causation_id=causation_id,
            environment=Environment.PAPER,
            source="trading_agent",
        )
        restored = decode_event(event.model_dump(mode="json"))
        assert restored.correlation_id == correlation_id
        assert restored.causation_id == causation_id

    def test_unknown_event_type_fails_loudly(self) -> None:
        event = build_event(
            make_intent(),
            correlation_id=uuid4(),
            environment=Environment.PAPER,
            source="trading_agent",
        )
        raw = event.model_dump(mode="json")
        raw["event_type"] = "SomethingFromTheFuture"
        with pytest.raises(ValueError, match="unknown event_type"):
            decode_event(raw)

    def test_missing_event_type_fails_loudly(self) -> None:
        with pytest.raises(ValueError, match="missing a string event_type"):
            decode_event({"payload": {}})


class TestHaltEvents:
    def test_a_halt_must_record_why(self) -> None:
        with pytest.raises(ValidationError, match="must record why it happened"):
            SystemHalted(
                state_before=KillSwitchState.RUNNING,
                state_after=KillSwitchState.HALTED,
                reason_codes=(),
                tripped_by="evaluator",
            )

    def test_a_halt_with_a_reason_is_valid(self) -> None:
        halt = SystemHalted(
            state_before=KillSwitchState.RUNNING,
            state_after=KillSwitchState.HALTED,
            reason_codes=(ReasonCode.RECONCILIATION_MISMATCH,),
            tripped_by="evaluator",
        )
        assert halt.state_after is KillSwitchState.HALTED


class TestReconciliationEvents:
    def test_a_mismatch_must_be_described(self) -> None:
        with pytest.raises(ValidationError, match="must describe the mismatch"):
            ReconciliationCompleted(
                reconciliation_id=uuid4(),
                account=make_account_state(),
                matched=False,
            )

    def test_a_match_needs_no_detail(self) -> None:
        event = ReconciliationCompleted(
            reconciliation_id=uuid4(),
            account=make_account_state(),
            matched=True,
        )
        assert event.matched

    def test_nested_account_state_survives_the_journal(self) -> None:
        payload = ReconciliationCompleted(
            reconciliation_id=uuid4(),
            account=make_account_state(observed_at_utc=FIXED_NOW + timedelta(seconds=5)),
            matched=True,
        )
        event = build_event(
            payload,
            correlation_id=uuid4(),
            environment=Environment.PAPER,
            source="mt5_gateway",
        )
        restored = decode_event(event.model_dump(mode="json"))
        assert restored.payload == payload
