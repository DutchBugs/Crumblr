"""agent_gateway/stores.py -- direct store-level tests not already covered
through `AgentGateway` itself (`tests/unit/test_agent_gateway.py`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from crumblr.agent_gateway.contracts import NoTradeDecision
from crumblr.agent_gateway.errors import EventConflictError
from crumblr.agent_gateway.events import AgentDecisionEventType
from crumblr.agent_gateway.stores import InMemoryAgentDecisionOutcomeStore

NOW = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)


def no_trade_decision(**overrides: object) -> NoTradeDecision:
    fields: dict[str, object] = {
        "decision_id": uuid4(),
        "agent_id": uuid4(),
        "assignment_id": uuid4(),
        "context_hash": "ctx-hash",
        "reason_codes": ("no_setup",),
        "decided_at_utc": NOW,
    }
    fields.update(overrides)
    return NoTradeDecision.model_validate(fields)


class TestAppendEventConflictDetection:
    """Self-review finding: `append_event`'s `(outcome_id, event_type)`-only
    idempotency check treated *any* re-append as a safe no-op, never
    comparing content -- unlike every other claim/register/issue method in
    this package (`stores.py`'s own module docstring: "the same id with
    *different* content always raises, never silently overwrites")."""

    def _claimed_store(self) -> tuple[InMemoryAgentDecisionOutcomeStore, UUID]:
        store = InMemoryAgentDecisionOutcomeStore()
        decision = no_trade_decision()
        store.claim_no_trade(decision, now=NOW)
        return store, decision.decision_id

    def test_identical_content_is_a_safe_idempotent_no_op(self) -> None:
        store, outcome_id = self._claimed_store()
        store.append_event(
            outcome_id=outcome_id,
            event_type=AgentDecisionEventType.RECEIVED,
            occurred_at_utc=NOW,
            reason_codes=(),
            detail=None,
        )
        store.append_event(
            outcome_id=outcome_id,
            event_type=AgentDecisionEventType.RECEIVED,
            occurred_at_utc=NOW,
            reason_codes=(),
            detail=None,
        )
        assert len(store.events_for(outcome_id)) == 1

    def test_different_reason_codes_for_the_same_key_raises(self) -> None:
        store, outcome_id = self._claimed_store()
        store.append_event(
            outcome_id=outcome_id,
            event_type=AgentDecisionEventType.REJECTED,
            occurred_at_utc=NOW,
            reason_codes=("first_reason",),
            detail=None,
        )
        with pytest.raises(EventConflictError):
            store.append_event(
                outcome_id=outcome_id,
                event_type=AgentDecisionEventType.REJECTED,
                occurred_at_utc=NOW,
                reason_codes=("a_completely_different_reason",),
                detail=None,
            )
        events = store.events_for(outcome_id)
        assert len(events) == 1
        assert events[0].reason_codes == ("first_reason",)

    def test_different_detail_for_the_same_key_raises(self) -> None:
        store, outcome_id = self._claimed_store()
        store.append_event(
            outcome_id=outcome_id,
            event_type=AgentDecisionEventType.REJECTED,
            occurred_at_utc=NOW,
            reason_codes=("reason",),
            detail="first detail",
        )
        with pytest.raises(EventConflictError):
            store.append_event(
                outcome_id=outcome_id,
                event_type=AgentDecisionEventType.REJECTED,
                occurred_at_utc=NOW,
                reason_codes=("reason",),
                detail="a different detail",
            )

    def test_a_different_occurred_at_utc_alone_is_not_a_conflict(self) -> None:
        """Code-review finding on the first version of this fix: a same-key
        re-append with a different `occurred_at_utc` but identical
        `reason_codes`/`detail` must stay a safe no-op. `RECEIVED` is
        re-appended on every resumed-but-unsettled retry
        (`AgentGateway.submit_trade_proposal`/`submit_no_trade`,
        AG-008/`review/AGENT_FEEDBACK.md`) with that call's own fresh
        wall-clock `now` -- treating that as a conflict would have made an
        interrupted claim permanently unrecoverable, since every future
        retry supplies a new `now` too."""
        store, outcome_id = self._claimed_store()
        store.append_event(
            outcome_id=outcome_id,
            event_type=AgentDecisionEventType.RECEIVED,
            occurred_at_utc=NOW,
        )
        store.append_event(
            outcome_id=outcome_id,
            event_type=AgentDecisionEventType.RECEIVED,
            occurred_at_utc=NOW + timedelta(seconds=1),
        )
        assert len(store.events_for(outcome_id)) == 1

    def test_a_conflict_does_not_change_what_is_already_durably_recorded(self) -> None:
        store, outcome_id = self._claimed_store()
        store.append_event(
            outcome_id=outcome_id,
            event_type=AgentDecisionEventType.RECEIVED,
            occurred_at_utc=NOW,
            reason_codes=("original",),
        )
        with pytest.raises(EventConflictError):
            store.append_event(
                outcome_id=outcome_id,
                event_type=AgentDecisionEventType.RECEIVED,
                occurred_at_utc=NOW,
                reason_codes=("attempted_overwrite",),
            )
        events = store.events_for(outcome_id)
        assert len(events) == 1
        assert events[0].reason_codes == ("original",)
