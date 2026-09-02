"""Immutable flatten requests + append-only flatten events, against real

PostgreSQL (core critical path item 7, ADR-009). Mirrors
`test_execution_persistence.py`'s claim/conflict-hardening coverage
exactly, one table pair over — `persistence/flatten.py` copies
`persistence/execution.py`'s discipline near-mechanically, so its tests
do too.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import Engine

from crumblr.domain.enums import Environment, FlattenEventType, ReasonCode
from crumblr.persistence.flatten import (
    FlattenClaimResult,
    FlattenEventConflictError,
    FlattenEventStore,
    FlattenRequestConflictError,
    FlattenRequestStore,
    flatten_event_id_for,
)
from crumblr.persistence.journal import AppendResult
from tests.conftest import FIXED_NOW

pytestmark = pytest.mark.integration


def _claim_kwargs(**overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "flatten_request_id": uuid4(),
        "environment": Environment.PAPER,
        "canonical_symbol": "EUR/USD",
        "trading_day": FIXED_NOW.date(),
        "session_close_utc": FIXED_NOW + timedelta(hours=5),
        "flatten_deadline_utc": FIXED_NOW + timedelta(hours=4),
        "fingerprint": "fp-1",
        "claimed_by": "test-worker",
        "now": FIXED_NOW,
    }
    fields.update(overrides)
    return fields


class TestClaim:
    def test_the_first_claim_wins(self, engine: Engine) -> None:
        store = FlattenRequestStore(engine)
        result = store.claim(**_claim_kwargs())
        assert result == FlattenClaimResult(claimed=True)

    def test_a_second_claim_with_matching_content_is_not_an_error(self, engine: Engine) -> None:
        store = FlattenRequestStore(engine)
        kwargs = _claim_kwargs()

        first = store.claim(**kwargs)
        second = store.claim(**kwargs)

        assert first == FlattenClaimResult(claimed=True)
        assert second == FlattenClaimResult(claimed=False)

    def test_a_second_claim_with_different_content_fails_closed(self, engine: Engine) -> None:
        store = FlattenRequestStore(engine)
        flatten_request_id = uuid4()

        store.claim(**_claim_kwargs(flatten_request_id=flatten_request_id, fingerprint="fp-1"))

        with pytest.raises(FlattenRequestConflictError, match=str(flatten_request_id)):
            store.claim(
                **_claim_kwargs(flatten_request_id=flatten_request_id, fingerprint="fp-2-different")
            )

    def test_different_flatten_request_ids_never_collide(self, engine: Engine) -> None:
        store = FlattenRequestStore(engine)

        first = store.claim(**_claim_kwargs(flatten_request_id=uuid4()))
        second = store.claim(**_claim_kwargs(flatten_request_id=uuid4()))

        assert first == FlattenClaimResult(claimed=True)
        assert second == FlattenClaimResult(claimed=True)


class TestFlattenEvents:
    def test_events_read_back_in_order(self, engine: Engine) -> None:
        flatten_request_id = uuid4()
        FlattenRequestStore(engine).claim(**_claim_kwargs(flatten_request_id=flatten_request_id))
        store = FlattenEventStore(engine)

        store.append(
            flatten_request_id=flatten_request_id,
            event_type=FlattenEventType.FLATTEN_REQUEST_CLAIMED,
            occurred_at_utc=FIXED_NOW,
        )
        store.append(
            flatten_request_id=flatten_request_id,
            event_type=FlattenEventType.FLATTEN_GATE_PASSED,
            occurred_at_utc=FIXED_NOW,
            payload={"open": True},
        )

        events = store.events_for(flatten_request_id)

        assert [event.event_type for event in events] == [
            FlattenEventType.FLATTEN_REQUEST_CLAIMED,
            FlattenEventType.FLATTEN_GATE_PASSED,
        ]
        assert events[1].payload == {"open": True}

    def test_reason_codes_round_trip(self, engine: Engine) -> None:
        flatten_request_id = uuid4()
        FlattenRequestStore(engine).claim(**_claim_kwargs(flatten_request_id=flatten_request_id))
        store = FlattenEventStore(engine)

        store.append(
            flatten_request_id=flatten_request_id,
            event_type=FlattenEventType.FLATTEN_GATE_BLOCKED,
            occurred_at_utc=FIXED_NOW,
            reason_codes=(ReasonCode.FLATTEN_SUBMISSION_NOT_ENABLED, ReasonCode.SYSTEM_HALTED),
        )

        events = store.events_for(flatten_request_id)
        assert events[0].reason_codes == (
            ReasonCode.FLATTEN_SUBMISSION_NOT_ENABLED,
            ReasonCode.SYSTEM_HALTED,
        )

    def test_re_appending_the_same_transition_does_not_duplicate(self, engine: Engine) -> None:
        flatten_request_id = uuid4()
        FlattenRequestStore(engine).claim(**_claim_kwargs(flatten_request_id=flatten_request_id))
        store = FlattenEventStore(engine)

        first = store.append(
            flatten_request_id=flatten_request_id,
            event_type=FlattenEventType.FLATTEN_REQUEST_CLAIMED,
            occurred_at_utc=FIXED_NOW,
        )
        second = store.append(
            flatten_request_id=flatten_request_id,
            event_type=FlattenEventType.FLATTEN_REQUEST_CLAIMED,
            occurred_at_utc=FIXED_NOW,
        )

        assert first == AppendResult(event_id=first.event_id, inserted=True)
        assert second == AppendResult(event_id=first.event_id, inserted=False)
        assert second.was_duplicate

        events = store.events_for(flatten_request_id)
        assert len(events) == 1

    def test_a_second_append_with_a_different_payload_fails_closed(self, engine: Engine) -> None:
        flatten_request_id = uuid4()
        FlattenRequestStore(engine).claim(**_claim_kwargs(flatten_request_id=flatten_request_id))
        store = FlattenEventStore(engine)
        event_id = flatten_event_id_for(
            flatten_request_id=flatten_request_id,
            event_type=FlattenEventType.FLATTEN_SUBMISSION_STARTED,
        )

        store.append(
            flatten_request_id=flatten_request_id,
            event_type=FlattenEventType.FLATTEN_SUBMISSION_STARTED,
            occurred_at_utc=FIXED_NOW,
            payload={"target_count": 1},
        )

        with pytest.raises(FlattenEventConflictError, match=str(event_id)):
            store.append(
                flatten_request_id=flatten_request_id,
                event_type=FlattenEventType.FLATTEN_SUBMISSION_STARTED,
                occurred_at_utc=FIXED_NOW,
                payload={"target_count": 2},
            )

    def test_a_second_append_with_different_reason_codes_fails_closed(self, engine: Engine) -> None:
        flatten_request_id = uuid4()
        FlattenRequestStore(engine).claim(**_claim_kwargs(flatten_request_id=flatten_request_id))
        store = FlattenEventStore(engine)

        store.append(
            flatten_request_id=flatten_request_id,
            event_type=FlattenEventType.FLATTEN_GATE_BLOCKED,
            occurred_at_utc=FIXED_NOW,
            reason_codes=(ReasonCode.FLATTEN_SUBMISSION_NOT_ENABLED,),
        )

        with pytest.raises(FlattenEventConflictError):
            store.append(
                flatten_request_id=flatten_request_id,
                event_type=FlattenEventType.FLATTEN_GATE_BLOCKED,
                occurred_at_utc=FIXED_NOW,
                reason_codes=(ReasonCode.SYSTEM_HALTED,),
            )

    def test_a_second_append_with_different_detail_fails_closed(self, engine: Engine) -> None:
        flatten_request_id = uuid4()
        FlattenRequestStore(engine).claim(**_claim_kwargs(flatten_request_id=flatten_request_id))
        store = FlattenEventStore(engine)

        store.append(
            flatten_request_id=flatten_request_id,
            event_type=FlattenEventType.FLATTEN_OUTCOME_RESOLVED,
            occurred_at_utc=FIXED_NOW,
            detail="first observation",
        )

        with pytest.raises(FlattenEventConflictError):
            store.append(
                flatten_request_id=flatten_request_id,
                event_type=FlattenEventType.FLATTEN_OUTCOME_RESOLVED,
                occurred_at_utc=FIXED_NOW,
                detail="a different observation",
            )

    def test_event_id_is_derived_not_random(self) -> None:
        flatten_request_id = uuid4()
        first = flatten_event_id_for(
            flatten_request_id=flatten_request_id,
            event_type=FlattenEventType.FLATTEN_GATE_PASSED,
        )
        second = flatten_event_id_for(
            flatten_request_id=flatten_request_id,
            event_type=FlattenEventType.FLATTEN_GATE_PASSED,
        )
        different = flatten_event_id_for(
            flatten_request_id=flatten_request_id,
            event_type=FlattenEventType.FLATTEN_GATE_BLOCKED,
        )
        assert first == second
        assert first != different
