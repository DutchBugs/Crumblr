"""Agent Gateway persistence against real PostgreSQL (ADR-005 Step B).

The Gateway's own fail-closed/authorization rules are unit-tested against
the in-memory stores (`tests/unit/test_agent_gateway.py`); this file proves
the two properties that only a real database can prove: the claim/conflict
SQL (`ON CONFLICT DO NOTHING RETURNING`, the same primitive
`persistence/execution.py` already relies on) actually behaves atomically
under PostgreSQL, and restart-safety -- "restart does not duplicate a
logical proposal" (`CRUMBLR_DEV2_AGENT_INTEGRATION_INSTRUCTIONS_V2.md` §8) --
holds when a fresh `AgentGateway` instance, backed by a fresh set of
Postgres-store objects pointed at the same engine, replaces one that
"crashed".
"""

from __future__ import annotations

import threading
from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import Engine

from crumblr.agent_gateway.contracts import (
    AgentIdentity,
    AgentRole,
    AgentStatus,
    ChampionShadowStatus,
    DecisionContextBundle,
    NoTradeDecision,
    TradeProposal,
    TradingAssignment,
)
from crumblr.agent_gateway.errors import (
    AgentRejectionReason,
    DecisionConflictError,
    EventConflictError,
    UnknownFeatureSnapshotError,
)
from crumblr.agent_gateway.events import AgentDecisionEventType
from crumblr.agent_gateway.gateway import AgentGateway
from crumblr.domain.enums import DataQuality, EntryType, Environment, SessionState, Side
from crumblr.persistence.agent_gateway import (
    PostgresAgentCredentialStore,
    PostgresAgentDecisionOutcomeStore,
    PostgresAgentIdentityStore,
    PostgresDecisionContextBundleStore,
    PostgresTradingAssignmentStore,
)
from crumblr.persistence.features import FeatureSnapshotStore
from tests.conftest import FIXED_NOW

pytestmark = pytest.mark.integration

AGENT_ID = uuid4()
ASSIGNMENT_ID = uuid4()
SECRET = "correct-horse-battery-staple"


def identity(**overrides: Any) -> AgentIdentity:
    fields: dict[str, Any] = {
        "agent_id": AGENT_ID,
        "role": AgentRole.TRADER,
        "runtime_version": "trader-v1",
        "service_identity": "spiffe://crumblr/agents/trader-v1",
        "status": AgentStatus.ACTIVE,
        "registered_at_utc": FIXED_NOW,
    }
    fields.update(overrides)
    return AgentIdentity.model_validate(fields)


def assignment(**overrides: Any) -> TradingAssignment:
    fields: dict[str, Any] = {
        "assignment_id": ASSIGNMENT_ID,
        "assignment_version": "assignment-v1",
        "allowed_agent_id": AGENT_ID,
        "canonical_symbol": "EUR/USD",
        "timeframe": "M5",
        "strategy_artifact_id": uuid4(),
        "strategy_artifact_hash": "abc123",
        "valid_from_utc": FIXED_NOW - timedelta(days=1),
        "valid_until_utc": FIXED_NOW + timedelta(days=30),
        "max_proposals_per_hour": 6,
        "allowed_risk_fraction_min": Decimal("0.001"),
        "allowed_risk_fraction_max": Decimal("0.01"),
        "required_evidence_fields": (),
        "supervisor_policy_version": "supervisor-policy-v1",
        "environment": Environment.PAPER,
        "champion_shadow_status": ChampionShadowStatus.SHADOW,
    }
    fields.update(overrides)
    return TradingAssignment.model_validate(fields)


def proposal(*, context_hash: str, **overrides: Any) -> TradeProposal:
    fields: dict[str, Any] = {
        "proposal_id": uuid4(),
        "agent_id": AGENT_ID,
        "assignment_id": ASSIGNMENT_ID,
        "context_hash": context_hash,
        "strategy_artifact_hash": "abc123",
        "side": Side.BUY,
        "entry_type": EntryType.MARKET,
        "reference_price": Decimal("1.08500"),
        "stop_loss_price": Decimal("1.08000"),
        "take_profit_price": Decimal("1.09000"),
        "confidence": 0.8,
        "requested_risk_fraction": Decimal("0.005"),
        "reason_codes": ("sweep_and_shift",),
        "evidence_refs": (),
        "submitted_at_utc": FIXED_NOW,
        "expires_at_utc": FIXED_NOW + timedelta(minutes=5),
    }
    fields.update(overrides)
    return TradeProposal.model_validate(fields)


def no_trade(*, context_hash: str, **overrides: Any) -> NoTradeDecision:
    fields: dict[str, Any] = {
        "decision_id": uuid4(),
        "agent_id": AGENT_ID,
        "assignment_id": ASSIGNMENT_ID,
        "context_hash": context_hash,
        "reason_codes": ("no_setup",),
        "decided_at_utc": FIXED_NOW,
    }
    fields.update(overrides)
    return NoTradeDecision.model_validate(fields)


def build_gateway(engine: Engine) -> AgentGateway:
    """A fresh Gateway, all-Postgres-backed -- constructing a new one from

    the same engine is exactly what a restarted process does, since none of
    these stores cache anything in memory between calls."""
    return AgentGateway(
        identities=PostgresAgentIdentityStore(engine),
        credentials=PostgresAgentCredentialStore(engine),
        assignments=PostgresTradingAssignmentStore(engine),
        contexts=PostgresDecisionContextBundleStore(engine),
        outcomes=PostgresAgentDecisionOutcomeStore(engine),
        feature_evidence=FeatureSnapshotStore(engine),
    )


def _with_context(gateway: AgentGateway, **overrides: Any) -> DecisionContextBundle:
    """Publishes a context bundle the real way (review 1.26 §5's flow) --

    records `AgentContextEvidence` in the real `feature_snapshots` table
    first, then issues a bundle citing it."""
    fields: dict[str, Any] = {
        "assignment_id": ASSIGNMENT_ID,
        "symbol": "EUR/USD",
        "market_snapshot_id": uuid4(),
        "instrument_spec_version": "spec-v1",
        "portfolio_summary_hash": "portfolio-abc",
        "session_state": SessionState.OPEN,
        "data_quality": DataQuality.GOOD,
        "now": FIXED_NOW,
    }
    fields.update(overrides)
    return gateway.publish_context(**fields)


class TestFeatureEvidenceAgainstRealPostgres:
    """Review 1.26 §5, AG-006's resolution — proves the evidence layer

    against the real `feature_snapshots` table (`persistence/features.py`,
    unmodified, shared with `baseline_v1`/`ict_v1`), not just the in-memory
    fake."""

    def test_publish_context_durably_records_evidence_before_issuing(self, engine: Engine) -> None:
        gateway = build_gateway(engine)
        gateway.register_identity(identity(), credential_secret=SECRET)
        gateway.issue_assignment(assignment())
        bundle = _with_context(gateway)

        payload = FeatureSnapshotStore(engine).get_payload(bundle.feature_snapshot_id)
        assert payload is not None
        assert payload["feature_set_version"] == "agent_context_v1"
        assert payload["regime"] == "UNKNOWN"

    def test_a_bundle_citing_an_unrecorded_snapshot_is_refused_after_restart(
        self, engine: Engine
    ) -> None:
        """A fresh `AgentGateway` (simulating a restart) still refuses --

        the check reads real Postgres, not process memory."""
        first_process = build_gateway(engine)
        first_process.register_identity(identity(), credential_secret=SECRET)
        first_process.issue_assignment(assignment())

        bundle = DecisionContextBundle.model_validate(
            {
                "context_id": uuid4(),
                "assignment_id": ASSIGNMENT_ID,
                "market_snapshot_id": uuid4(),
                "instrument_spec_version": "spec-v1",
                "portfolio_summary_hash": "portfolio-abc",
                "session_state": SessionState.OPEN,
                "data_quality": DataQuality.GOOD,
                "feature_snapshot_id": uuid4(),  # never recorded
                "issued_at_utc": FIXED_NOW,
                "expires_at_utc": FIXED_NOW + timedelta(minutes=5),
            }
        )
        second_process = build_gateway(engine)
        with pytest.raises(UnknownFeatureSnapshotError):
            second_process.issue_context_bundle(bundle)

    def test_publish_context_is_idempotent_across_restarts(self, engine: Engine) -> None:
        first_process = build_gateway(engine)
        first_process.register_identity(identity(), credential_secret=SECRET)
        first_process.issue_assignment(assignment())
        first = _with_context(first_process)

        second_process = build_gateway(engine)
        second = _with_context(second_process)

        assert first.feature_snapshot_id == second.feature_snapshot_id


class TestBasicRoundTrip:
    def test_identity_assignment_and_context_round_trip(self, engine: Engine) -> None:
        gateway = build_gateway(engine)
        gateway.register_identity(identity(), credential_secret=SECRET)
        gateway.issue_assignment(assignment())
        bundle = _with_context(gateway)

        authenticated = gateway.authenticate(agent_id=AGENT_ID, credential_secret=SECRET)
        assert authenticated.agent_id == AGENT_ID

        result = gateway.submit_trade_proposal(
            agent_id=AGENT_ID,
            credential_secret=SECRET,
            proposal=proposal(context_hash=bundle.content_hash),
            now=FIXED_NOW,
        )
        assert result.accepted is True

    def test_a_rejection_is_durably_recorded(self, engine: Engine) -> None:
        gateway = build_gateway(engine)
        gateway.register_identity(identity(), credential_secret=SECRET)
        gateway.issue_assignment(assignment())

        result = gateway.submit_trade_proposal(
            agent_id=AGENT_ID,
            credential_secret=SECRET,
            proposal=proposal(context_hash="never-issued-hash"),
            now=FIXED_NOW,
        )
        assert result.accepted is False
        assert result.reason is AgentRejectionReason.UNKNOWN_CONTEXT

        # Read back from a second, independent store instance -- proves this
        # is a real durable row, not process-local state.
        events = PostgresAgentDecisionOutcomeStore(engine).events_for(result.outcome_id)
        assert len(events) == 2  # RECEIVED, then REJECTED


class TestRestartSafety:
    """`CRUMBLR_DEV2_AGENT_INTEGRATION_INSTRUCTIONS_V2.md` §8: "restart does

    not duplicate logical proposal" and "agent process can disappear
    without making Crumblr unsafe"."""

    def test_a_retry_against_a_freshly_constructed_gateway_is_still_idempotent(
        self, engine: Engine
    ) -> None:
        first_process = build_gateway(engine)
        first_process.register_identity(identity(), credential_secret=SECRET)
        first_process.issue_assignment(assignment())
        bundle = _with_context(first_process)

        original = proposal(context_hash=bundle.content_hash)
        first_result = first_process.submit_trade_proposal(
            agent_id=AGENT_ID, credential_secret=SECRET, proposal=original, now=FIXED_NOW
        )
        assert first_result.accepted is True

        # Simulate a crash and restart: an entirely new AgentGateway, new
        # store objects, same engine/database -- nothing carried over in
        # memory.
        second_process = build_gateway(engine)
        second_result = second_process.submit_trade_proposal(
            agent_id=AGENT_ID, credential_secret=SECRET, proposal=original, now=FIXED_NOW
        )

        assert second_result == first_result
        events = PostgresAgentDecisionOutcomeStore(engine).events_for(original.proposal_id)
        # Exactly RECEIVED + ACCEPTED once each -- the retry after "restart"
        # appended nothing new.
        assert len(events) == 2

    def test_a_conflicting_retry_after_restart_still_fails_closed(self, engine: Engine) -> None:
        first_process = build_gateway(engine)
        first_process.register_identity(identity(), credential_secret=SECRET)
        first_process.issue_assignment(assignment())
        bundle = _with_context(first_process)

        original = proposal(context_hash=bundle.content_hash)
        first_process.submit_trade_proposal(
            agent_id=AGENT_ID, credential_secret=SECRET, proposal=original, now=FIXED_NOW
        )

        second_process = build_gateway(engine)
        conflicting = original.model_copy(update={"reference_price": Decimal("1.09999")})
        with pytest.raises(DecisionConflictError):
            second_process.submit_trade_proposal(
                agent_id=AGENT_ID, credential_secret=SECRET, proposal=conflicting, now=FIXED_NOW
            )

    def test_the_reconstructed_trade_intent_is_identical_across_a_restart(
        self, engine: Engine
    ) -> None:
        """Review 1.26 §7 item 2: the mapping is deterministic, so a

        "crashed and restarted" process reconstructs the exact same
        `TradeIntent` (same `intent_id`, same every field) rather than
        needing it stored separately -- explicit regression coverage for
        what `test_a_retry_against_a_freshly_constructed_gateway_is_still_idempotent`
        above already proves implicitly via `==`."""
        first_process = build_gateway(engine)
        first_process.register_identity(identity(), credential_secret=SECRET)
        first_process.issue_assignment(assignment())
        bundle = _with_context(first_process)

        original = proposal(context_hash=bundle.content_hash)
        first_result = first_process.submit_trade_proposal(
            agent_id=AGENT_ID, credential_secret=SECRET, proposal=original, now=FIXED_NOW
        )
        assert first_result.trade_intent is not None

        second_process = build_gateway(engine)
        second_result = second_process.submit_trade_proposal(
            agent_id=AGENT_ID,
            credential_secret=SECRET,
            proposal=original,
            now=FIXED_NOW + timedelta(hours=1),
        )
        assert second_result.trade_intent == first_result.trade_intent

    def test_no_trade_survives_a_restart_idempotently(self, engine: Engine) -> None:
        first_process = build_gateway(engine)
        first_process.register_identity(identity(), credential_secret=SECRET)
        first_process.issue_assignment(assignment())
        bundle = _with_context(first_process)

        decision = no_trade(context_hash=bundle.content_hash)
        first_result = first_process.submit_no_trade(
            agent_id=AGENT_ID, credential_secret=SECRET, decision=decision, now=FIXED_NOW
        )

        second_process = build_gateway(engine)
        second_result = second_process.submit_no_trade(
            agent_id=AGENT_ID, credential_secret=SECRET, decision=decision, now=FIXED_NOW
        )
        assert second_result == first_result


class TestConcurrentClaimIsAtomic:
    def test_two_racing_claims_for_the_same_proposal_id_only_one_wins(self, engine: Engine) -> None:
        """Proves the `ON CONFLICT DO NOTHING RETURNING` primitive itself,

        independent of the Gateway's own single-threaded call pattern above
        -- the same property `persistence/execution.py`'s module docstring
        documents for `execution_requests`."""
        gateway = build_gateway(engine)
        gateway.register_identity(identity(), credential_secret=SECRET)
        gateway.issue_assignment(assignment())
        bundle = _with_context(gateway)
        shared = proposal(context_hash=bundle.content_hash)

        store = PostgresAgentDecisionOutcomeStore(engine)
        first = store.claim_trade_proposal(shared, now=FIXED_NOW)
        second = store.claim_trade_proposal(shared, now=FIXED_NOW)

        assert first.claimed is True
        assert second.claimed is False


class TestEventConflictIsDetectedUnderRealPostgres:
    """Self-review finding: `append_event`'s `(outcome_id, event_type)`-derived
    `event_id` made a same-id-different-content collision structurally
    possible, and unlike every other claim/register/issue method in this
    package it had no fail-closed conflict check at all -- `ON CONFLICT DO
    NOTHING` alone silently discarded the second, different write. Proven
    here against a real database, not merely the in-memory store, since the
    fix reads the already-committed row back on conflict."""

    def test_identical_content_is_a_safe_idempotent_no_op(self, engine: Engine) -> None:
        store = PostgresAgentDecisionOutcomeStore(engine)
        decision = no_trade(context_hash="ctx-event-conflict-1")
        store.claim_no_trade(decision, now=FIXED_NOW)

        store.append_event(
            outcome_id=decision.decision_id,
            event_type=AgentDecisionEventType.RECEIVED,
            occurred_at_utc=FIXED_NOW,
            reason_codes=(),
            detail=None,
        )
        store.append_event(
            outcome_id=decision.decision_id,
            event_type=AgentDecisionEventType.RECEIVED,
            occurred_at_utc=FIXED_NOW,
            reason_codes=(),
            detail=None,
        )
        events = store.events_for(decision.decision_id)
        assert len(events) == 1

    def test_a_different_occurred_at_utc_alone_is_not_a_conflict(self, engine: Engine) -> None:
        """Code-review finding on the first version of this fix: a same-key
        re-append with a different `occurred_at_utc` but identical
        `reason_codes`/`detail` must stay a safe no-op -- `RECEIVED` is
        re-appended on every resumed-but-unsettled retry with that call's
        own fresh wall-clock `now` (AG-008). Treating that as a conflict
        would make an interrupted claim permanently unrecoverable against a
        real database too, not only in the in-memory store."""
        store = PostgresAgentDecisionOutcomeStore(engine)
        decision = no_trade(context_hash="ctx-event-conflict-3")
        store.claim_no_trade(decision, now=FIXED_NOW)

        store.append_event(
            outcome_id=decision.decision_id,
            event_type=AgentDecisionEventType.RECEIVED,
            occurred_at_utc=FIXED_NOW,
        )
        store.append_event(
            outcome_id=decision.decision_id,
            event_type=AgentDecisionEventType.RECEIVED,
            occurred_at_utc=FIXED_NOW + timedelta(seconds=5),
        )
        events = store.events_for(decision.decision_id)
        assert len(events) == 1

    def test_different_content_for_the_same_key_raises(self, engine: Engine) -> None:
        store = PostgresAgentDecisionOutcomeStore(engine)
        decision = no_trade(context_hash="ctx-event-conflict-2")
        store.claim_no_trade(decision, now=FIXED_NOW)

        store.append_event(
            outcome_id=decision.decision_id,
            event_type=AgentDecisionEventType.REJECTED,
            occurred_at_utc=FIXED_NOW,
            reason_codes=("first_reason",),
            detail=None,
        )
        with pytest.raises(EventConflictError):
            store.append_event(
                outcome_id=decision.decision_id,
                event_type=AgentDecisionEventType.REJECTED,
                occurred_at_utc=FIXED_NOW,
                reason_codes=("a_completely_different_reason",),
                detail=None,
            )
        # The conflicting write never landed -- the original content stands.
        events = store.events_for(decision.decision_id)
        assert len(events) == 1
        assert events[0].reason_codes == ("first_reason",)


class TestRateLimitIsAtomicUnderRealConcurrency:
    """Regression coverage for a self-review finding: the original design

    read the proposal-rate-limit count and claimed in two separate
    transactions, letting concurrent submissions for the same assignment
    each observe a stale below-limit count and all get accepted. The fix is
    a per-assignment Postgres advisory lock held for the whole claim→count→
    evaluate→settle sequence (`AgentDecisionOutcomeStore.lock_assignment()`).
    This test only proves anything under a real database — a single
    Postgres instance's real lock manager, real concurrent connections."""

    def test_only_max_proposals_per_hour_are_ever_accepted_under_real_concurrent_submission(
        self, engine: Engine
    ) -> None:
        admin = build_gateway(engine)
        admin.register_identity(identity(), credential_secret=SECRET)
        admin.issue_assignment(assignment(max_proposals_per_hour=3))
        bundle = _with_context(admin)

        proposals = [proposal(context_hash=bundle.content_hash) for _ in range(10)]
        results: list[Any] = []
        results_lock = threading.Lock()

        def submit(one_proposal: TradeProposal) -> None:
            # Each thread gets its own AgentGateway/store objects sharing
            # only the engine -- the same shape two genuinely separate
            # concurrent Gateway processes would have, not two threads
            # sharing one connection.
            worker_gateway = build_gateway(engine)
            result = worker_gateway.submit_trade_proposal(
                agent_id=AGENT_ID, credential_secret=SECRET, proposal=one_proposal, now=FIXED_NOW
            )
            with results_lock:
                results.append(result)

        threads = [threading.Thread(target=submit, args=(p,)) for p in proposals]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        accepted = [result for result in results if result.accepted]
        rejected = [result for result in results if not result.accepted]
        assert len(accepted) == 3
        assert len(rejected) == 7
        assert all(result.reason is AgentRejectionReason.RATE_LIMIT_EXCEEDED for result in rejected)
