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
    ExecutionEventConflictError,
    ExecutionEventStore,
    ExecutionRequestConflictError,
    ExecutionRequestStore,
    event_id_for,
)
from crumblr.persistence.journal import AppendResult, CapsuleStore
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


class TestCountEventsSince:
    """Review 1.23 F-060 (reopened): the durable order-frequency authority

    is a count of `SUBMISSION_STARTED` events — "the platform committed to
    attempting one broker submission" — never a count of claimed requests,
    which includes every refusal outcome along the way.
    """

    def test_zero_when_nothing_has_happened(self, engine: Engine) -> None:
        store = ExecutionEventStore(engine)
        assert store.count_events_since(ExecutionEventType.SUBMISSION_STARTED, FIXED_NOW) == 0

    def test_other_event_types_are_never_counted(self, engine: Engine) -> None:
        """Phase 4 never emits `SUBMISSION_STARTED` — everything it does

        emit (claims, refusals, order_check outcomes) must not be
        mistaken for a submission attempt.
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
            event_type=ExecutionEventType.ORDER_CHECKED,
            occurred_at_utc=FIXED_NOW,
        )

        assert store.count_events_since(ExecutionEventType.SUBMISSION_STARTED, FIXED_NOW) == 0

    def test_counts_the_requested_event_type_at_or_after_the_cutoff(self, engine: Engine) -> None:
        from datetime import timedelta

        capsule = sealed_capsule(engine)
        requests = ExecutionRequestStore(engine)
        events = ExecutionEventStore(engine)
        for label, when in (("old", FIXED_NOW - timedelta(hours=2)), ("new", FIXED_NOW)):
            order_request_id = uuid4()
            requests.claim(
                order_request_id=order_request_id,
                capsule_id=capsule.capsule_id,
                intent_id=capsule.trade_intent.intent_id,  # type: ignore[union-attr]
                fingerprint=f"fp-{label}",
                claimed_by="test-worker",
                now=when,
            )
            events.append(
                order_request_id=order_request_id,
                event_type=ExecutionEventType.SUBMISSION_STARTED,
                occurred_at_utc=when,
            )

        assert (
            events.count_events_since(
                ExecutionEventType.SUBMISSION_STARTED, FIXED_NOW - timedelta(hours=1)
            )
            == 1
        )


class TestRequestIdsWithEvent:
    """Core critical path item 8 (ADR-010): the read seam

    `reconcile_once()` needs — "which requests could possibly imply live
    exposure" — that `events_for()` (per-request) and
    `count_events_since()` (a scalar count) do not provide.
    """

    def test_empty_on_a_fresh_database(self, engine: Engine) -> None:
        store = ExecutionEventStore(engine)
        assert store.request_ids_with_event(ExecutionEventType.SUBMISSION_STARTED) == ()

    def test_returns_only_requests_that_reached_the_event(self, engine: Engine) -> None:
        capsule = sealed_capsule(engine)
        requests = ExecutionRequestStore(engine)
        events = ExecutionEventStore(engine)

        with_it = uuid4()
        requests.claim(
            order_request_id=with_it,
            capsule_id=capsule.capsule_id,
            intent_id=capsule.trade_intent.intent_id,  # type: ignore[union-attr]
            fingerprint="fp-with",
            claimed_by="test-worker",
            now=FIXED_NOW,
        )
        events.append(
            order_request_id=with_it,
            event_type=ExecutionEventType.SUBMISSION_STARTED,
            occurred_at_utc=FIXED_NOW,
        )

        without_it = uuid4()
        requests.claim(
            order_request_id=without_it,
            capsule_id=capsule.capsule_id,
            intent_id=capsule.trade_intent.intent_id,  # type: ignore[union-attr]
            fingerprint="fp-without",
            claimed_by="test-worker",
            now=FIXED_NOW,
        )
        events.append(
            order_request_id=without_it,
            event_type=ExecutionEventType.INELIGIBLE,
            occurred_at_utc=FIXED_NOW,
        )

        assert events.request_ids_with_event(ExecutionEventType.SUBMISSION_STARTED) == (with_it,)

    def test_a_request_is_returned_once_however_many_times_the_event_appears(
        self, engine: Engine
    ) -> None:
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
        events = ExecutionEventStore(engine)
        events.append(
            order_request_id=order_request_id,
            event_type=ExecutionEventType.SUBMISSION_STARTED,
            occurred_at_utc=FIXED_NOW,
        )
        # A retried append with identical content converges (item 4) —
        # confirm the seam still returns exactly one row.
        events.append(
            order_request_id=order_request_id,
            event_type=ExecutionEventType.SUBMISSION_STARTED,
            occurred_at_utc=FIXED_NOW,
        )

        assert events.request_ids_with_event(ExecutionEventType.SUBMISSION_STARTED) == (
            order_request_id,
        )

    def test_ordered_by_first_occurrence(self, engine: Engine) -> None:
        capsule = sealed_capsule(engine)
        requests = ExecutionRequestStore(engine)
        events = ExecutionEventStore(engine)
        ids = [uuid4(), uuid4(), uuid4()]
        for i, order_request_id in enumerate(ids):
            requests.claim(
                order_request_id=order_request_id,
                capsule_id=capsule.capsule_id,
                intent_id=capsule.trade_intent.intent_id,  # type: ignore[union-attr]
                fingerprint=f"fp-{i}",
                claimed_by="test-worker",
                now=FIXED_NOW,
            )
            events.append(
                order_request_id=order_request_id,
                event_type=ExecutionEventType.SUBMISSION_STARTED,
                occurred_at_utc=FIXED_NOW,
            )

        assert events.request_ids_with_event(ExecutionEventType.SUBMISSION_STARTED) == tuple(ids)


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
        main journal uses. Review 1.23 §7 (core critical path item 4): the
        return value now reports which happened, mirroring
        `ExecutionRequestStore.claim()`'s `ClaimResult`/journal's own
        `AppendResult` — a matching-content retry is a harmless no-op, not
        an error.
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

        first = store.append(
            order_request_id=order_request_id,
            event_type=ExecutionEventType.REQUEST_CLAIMED,
            occurred_at_utc=FIXED_NOW,
        )
        second = store.append(
            order_request_id=order_request_id,
            event_type=ExecutionEventType.REQUEST_CLAIMED,
            occurred_at_utc=FIXED_NOW,
        )

        assert first == AppendResult(event_id=first.event_id, inserted=True)
        assert second == AppendResult(event_id=first.event_id, inserted=False)
        assert second.was_duplicate

        events = store.events_for(order_request_id)
        assert len(events) == 1

    def test_a_second_append_with_a_different_payload_fails_closed(self, engine: Engine) -> None:
        """Review 1.23 §7 / core critical path item 4: the same discipline

        `ExecutionRequestStore._claim()` already enforces for requests, now
        for events — a retried event identity with genuinely different
        content is a conflict, never a silently dropped duplicate.
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
        event_id = event_id_for(
            order_request_id=order_request_id, event_type=ExecutionEventType.ORDER_CHECKED
        )

        store.append(
            order_request_id=order_request_id,
            event_type=ExecutionEventType.ORDER_CHECKED,
            occurred_at_utc=FIXED_NOW,
            payload={"accepted": True},
        )

        with pytest.raises(ExecutionEventConflictError, match=str(event_id)):
            store.append(
                order_request_id=order_request_id,
                event_type=ExecutionEventType.ORDER_CHECKED,
                occurred_at_utc=FIXED_NOW,
                payload={"accepted": False},
            )

    def test_a_second_append_with_different_reason_codes_fails_closed(self, engine: Engine) -> None:
        """Review 1.23 §7 names `reason_codes` explicitly, not only payload."""
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
            reason_codes=(ReasonCode.RISK_PER_TRADE_LIMIT,),
        )

        with pytest.raises(ExecutionEventConflictError):
            store.append(
                order_request_id=order_request_id,
                event_type=ExecutionEventType.FINAL_RISK_BLOCKED,
                occurred_at_utc=FIXED_NOW,
                reason_codes=(ReasonCode.EXECUTION_TIME_RISK_BLOCK,),
            )

    def test_a_second_append_with_different_detail_fails_closed(self, engine: Engine) -> None:
        """Review 1.23 §7 names `detail` explicitly too."""
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
            event_type=ExecutionEventType.RECONCILIATION_BLOCKED,
            occurred_at_utc=FIXED_NOW,
            detail="first observation",
        )

        with pytest.raises(ExecutionEventConflictError):
            store.append(
                order_request_id=order_request_id,
                event_type=ExecutionEventType.RECONCILIATION_BLOCKED,
                occurred_at_utc=FIXED_NOW,
                detail="a different observation",
            )

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
