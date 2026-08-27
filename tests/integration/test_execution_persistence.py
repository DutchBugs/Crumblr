"""Immutable execution requests + append-only execution events, against real

PostgreSQL (Phase 4). `review/PHASE4_PLAN_REVIEW_GO_WITH_TWEAKS.md` points 4
and 5: the claim is the winning insert, a fingerprint mismatch on the same
`order_request_id` fails closed, and the event log is genuinely append-only.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import Engine

from crumblr.domain.enums import Environment, ExecutionEventType, ReasonCode
from crumblr.domain.models import DecisionCapsule
from crumblr.persistence.execution import (
    ClaimResult,
    ExecutionEventStore,
    ExecutionRequestConflictError,
    ExecutionRequestStore,
    event_id_for,
)
from crumblr.persistence.journal import CapsuleStore
from tests.conftest import FIXED_NOW, make_intent, make_risk_decision, make_supervisor_decision

pytestmark = pytest.mark.integration


def sealed_capsule(engine: Engine, **overrides: Any) -> DecisionCapsule:
    intent = overrides.pop("trade_intent", None) or make_intent()
    fields: dict[str, Any] = {
        "capsule_id": uuid4(),
        "occurred_at_utc": FIXED_NOW,
        "correlation_id": uuid4(),
        "canonical_symbol": "EUR/USD",
        "broker_symbol": "EURUSD",
        "market_snapshot_id": uuid4(),
        "feature_set_version": "features-v1",
        "feature_values_hash": "abc123",
        "strategy_version": "0.1.0",
        "model_version": None,
        "trade_intent": intent,
        "risk_config_version": "cfg-v1",
        "risk_decision": make_risk_decision(intent.intent_id),
        "supervisor_decision": make_supervisor_decision(intent.intent_id),
        "code_commit": "deadbeef",
        "environment": Environment.PAPER,
    }
    fields.update(overrides)
    capsule = DecisionCapsule(**fields)
    CapsuleStore(engine).seal(capsule)
    return capsule


class TestClaim:
    def test_the_first_claim_wins(self, engine: Engine) -> None:
        capsule = sealed_capsule(engine)
        order_request_id = uuid4()
        store = ExecutionRequestStore(engine)

        result = store.claim(
            order_request_id=order_request_id,
            capsule_id=capsule.capsule_id,
            intent_id=capsule.trade_intent.intent_id,  # type: ignore[union-attr]
            fingerprint="fp-1",
            claimed_by="test-worker",
            now=FIXED_NOW,
        )

        assert result == ClaimResult(claimed=True)

    def test_a_second_claim_with_matching_content_is_not_an_error(self, engine: Engine) -> None:
        capsule = sealed_capsule(engine)
        order_request_id = uuid4()
        store = ExecutionRequestStore(engine)
        kwargs: dict[str, Any] = {
            "order_request_id": order_request_id,
            "capsule_id": capsule.capsule_id,
            "intent_id": capsule.trade_intent.intent_id,  # type: ignore[union-attr]
            "fingerprint": "fp-1",
            "claimed_by": "test-worker",
            "now": FIXED_NOW,
        }

        first = store.claim(**kwargs)
        second = store.claim(**kwargs)

        assert first == ClaimResult(claimed=True)
        assert second == ClaimResult(claimed=False)

    def test_a_second_claim_with_different_content_fails_closed(self, engine: Engine) -> None:
        capsule = sealed_capsule(engine)
        order_request_id = uuid4()
        store = ExecutionRequestStore(engine)

        store.claim(
            order_request_id=order_request_id,
            capsule_id=capsule.capsule_id,
            intent_id=capsule.trade_intent.intent_id,  # type: ignore[union-attr]
            fingerprint="fp-1",
            claimed_by="test-worker",
            now=FIXED_NOW,
        )

        with pytest.raises(ExecutionRequestConflictError, match=str(order_request_id)):
            store.claim(
                order_request_id=order_request_id,
                capsule_id=capsule.capsule_id,
                intent_id=capsule.trade_intent.intent_id,  # type: ignore[union-attr]
                fingerprint="fp-2-different",
                claimed_by="test-worker",
                now=FIXED_NOW,
            )

    def test_different_order_request_ids_never_collide(self, engine: Engine) -> None:
        capsule = sealed_capsule(engine)
        store = ExecutionRequestStore(engine)

        first = store.claim(
            order_request_id=uuid4(),
            capsule_id=capsule.capsule_id,
            intent_id=capsule.trade_intent.intent_id,  # type: ignore[union-attr]
            fingerprint="fp-1",
            claimed_by="test-worker",
            now=FIXED_NOW,
        )
        second = store.claim(
            order_request_id=uuid4(),
            capsule_id=capsule.capsule_id,
            intent_id=capsule.trade_intent.intent_id,  # type: ignore[union-attr]
            fingerprint="fp-1",
            claimed_by="test-worker",
            now=FIXED_NOW,
        )

        assert first == ClaimResult(claimed=True)
        assert second == ClaimResult(claimed=True)


class TestCountClaimedSince:
    """Review 1.22 F-060: the durable, real order-frequency count FINAL Risk

    needs — platform execution history, not a placeholder.
    """

    def test_zero_when_nothing_has_been_claimed(self, engine: Engine) -> None:
        store = ExecutionRequestStore(engine)
        assert store.count_claimed_since(FIXED_NOW) == 0

    def test_counts_claims_at_or_after_the_cutoff(self, engine: Engine) -> None:
        capsule = sealed_capsule(engine)
        store = ExecutionRequestStore(engine)
        store.claim(
            order_request_id=uuid4(),
            capsule_id=capsule.capsule_id,
            intent_id=capsule.trade_intent.intent_id,  # type: ignore[union-attr]
            fingerprint="fp-a",
            claimed_by="test-worker",
            now=FIXED_NOW,
        )
        store.claim(
            order_request_id=uuid4(),
            capsule_id=capsule.capsule_id,
            intent_id=capsule.trade_intent.intent_id,  # type: ignore[union-attr]
            fingerprint="fp-b",
            claimed_by="test-worker",
            now=FIXED_NOW,
        )

        assert store.count_claimed_since(FIXED_NOW) == 2

    def test_excludes_claims_before_the_cutoff(self, engine: Engine) -> None:
        from datetime import timedelta

        capsule = sealed_capsule(engine)
        store = ExecutionRequestStore(engine)
        store.claim(
            order_request_id=uuid4(),
            capsule_id=capsule.capsule_id,
            intent_id=capsule.trade_intent.intent_id,  # type: ignore[union-attr]
            fingerprint="fp-old",
            claimed_by="test-worker",
            now=FIXED_NOW - timedelta(hours=2),
        )
        store.claim(
            order_request_id=uuid4(),
            capsule_id=capsule.capsule_id,
            intent_id=capsule.trade_intent.intent_id,  # type: ignore[union-attr]
            fingerprint="fp-new",
            claimed_by="test-worker",
            now=FIXED_NOW,
        )

        assert store.count_claimed_since(FIXED_NOW - timedelta(hours=1)) == 1


class TestExecutionEvents:
    def test_events_read_back_in_order(self, engine: Engine) -> None:
        capsule = sealed_capsule(engine)
        order_request_id = uuid4()
        ExecutionRequestStore(engine).claim(
            order_request_id=order_request_id,
            capsule_id=capsule.capsule_id,
            intent_id=capsule.trade_intent.intent_id,  # type: ignore[union-attr]
            fingerprint="fp-1",
            claimed_by="test-worker",
            now=FIXED_NOW,
        )
        store = ExecutionEventStore(engine)

        store.append(
            order_request_id=order_request_id,
            event_type=ExecutionEventType.REQUEST_CLAIMED,
            occurred_at_utc=FIXED_NOW,
        )
        store.append(
            order_request_id=order_request_id,
            event_type=ExecutionEventType.ORDER_CHECKED,
            occurred_at_utc=FIXED_NOW,
            payload={"accepted": True},
        )

        events = store.events_for(order_request_id)

        assert [event.event_type for event in events] == [
            ExecutionEventType.REQUEST_CLAIMED,
            ExecutionEventType.ORDER_CHECKED,
        ]
        assert events[1].payload == {"accepted": True}

    def test_reason_codes_round_trip(self, engine: Engine) -> None:
        capsule = sealed_capsule(engine)
        order_request_id = uuid4()
        ExecutionRequestStore(engine).claim(
            order_request_id=order_request_id,
            capsule_id=capsule.capsule_id,
            intent_id=capsule.trade_intent.intent_id,  # type: ignore[union-attr]
            fingerprint="fp-1",
            claimed_by="test-worker",
            now=FIXED_NOW,
        )
        store = ExecutionEventStore(engine)

        store.append(
            order_request_id=order_request_id,
            event_type=ExecutionEventType.FINAL_RISK_BLOCKED,
            occurred_at_utc=FIXED_NOW,
            reason_codes=(ReasonCode.RISK_PER_TRADE_LIMIT, ReasonCode.EXECUTION_TIME_RISK_BLOCK),
        )

        events = store.events_for(order_request_id)
        assert events[0].reason_codes == (
            ReasonCode.RISK_PER_TRADE_LIMIT,
            ReasonCode.EXECUTION_TIME_RISK_BLOCK,
        )

    def test_re_appending_the_same_transition_does_not_duplicate(self, engine: Engine) -> None:
        """A retry after a crash re-logs the same logical event rather than

        appending a second row for it — the same idempotence discipline the
        main journal uses.
        """
        capsule = sealed_capsule(engine)
        order_request_id = uuid4()
        ExecutionRequestStore(engine).claim(
            order_request_id=order_request_id,
            capsule_id=capsule.capsule_id,
            intent_id=capsule.trade_intent.intent_id,  # type: ignore[union-attr]
            fingerprint="fp-1",
            claimed_by="test-worker",
            now=FIXED_NOW,
        )
        store = ExecutionEventStore(engine)

        store.append(
            order_request_id=order_request_id,
            event_type=ExecutionEventType.REQUEST_CLAIMED,
            occurred_at_utc=FIXED_NOW,
        )
        store.append(
            order_request_id=order_request_id,
            event_type=ExecutionEventType.REQUEST_CLAIMED,
            occurred_at_utc=FIXED_NOW,
        )

        events = store.events_for(order_request_id)
        assert len(events) == 1

    def test_event_id_is_derived_not_random(self) -> None:
        order_request_id = uuid4()
        first = event_id_for(
            order_request_id=order_request_id, event_type=ExecutionEventType.ORDER_CHECKED
        )
        second = event_id_for(
            order_request_id=order_request_id, event_type=ExecutionEventType.ORDER_CHECKED
        )
        different = event_id_for(
            order_request_id=order_request_id, event_type=ExecutionEventType.ORDER_CHECK_REJECTED
        )
        assert first == second
        assert first != different
