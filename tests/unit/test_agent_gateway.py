"""Agent Gateway (ADR-005 Step B) against the in-memory stores.

Tests every scenario `review/adr/ADR-005-external-agent-trust-boundary.md`
§7's planning-level test matrix names, plus the nine required proofs in
`CRUMBLR_DEV2_AGENT_INTEGRATION_INSTRUCTIONS_V2.md` §8 that this module can
actually exercise without a real database (restart-safety needs a real
PostgreSQL and lives in `tests/integration/test_agent_gateway_store.py`
instead; malformed-input rejection at the contract level is already proven
by `tests/unit/test_agent_gateway_contracts.py`'s 29 tests, since this
Gateway only ever receives already-validated contract objects — there is no
raw-payload ingestion endpoint yet, see `agent_gateway/gateway.py`'s module
docstring).
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

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
    AgentNotActiveError,
    AgentRejectionReason,
    AuthenticationError,
    DecisionConflictError,
    ImpersonationError,
    UnknownAgentError,
)
from crumblr.agent_gateway.events import AgentDecisionEventType, AgentOutcomeType
from crumblr.agent_gateway.gateway import AgentGateway
from crumblr.agent_gateway.stores import (
    InMemoryAgentCredentialStore,
    InMemoryAgentDecisionOutcomeStore,
    InMemoryAgentIdentityStore,
    InMemoryDecisionContextBundleStore,
    InMemoryTradingAssignmentStore,
)
from crumblr.domain.enums import DataQuality, EntryType, Environment, SessionState, Side
from tests.conftest import FIXED_NOW

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
        "max_proposals_per_hour": 2,
        "allowed_risk_fraction_min": Decimal("0.001"),
        "allowed_risk_fraction_max": Decimal("0.01"),
        "required_evidence_fields": (),
        "supervisor_policy_version": "supervisor-policy-v1",
        "environment": Environment.PAPER,
        "champion_shadow_status": ChampionShadowStatus.SHADOW,
    }
    fields.update(overrides)
    return TradingAssignment.model_validate(fields)


def context_bundle(**overrides: Any) -> DecisionContextBundle:
    fields: dict[str, Any] = {
        "context_id": uuid4(),
        "assignment_id": ASSIGNMENT_ID,
        "market_snapshot_id": uuid4(),
        "instrument_spec_version": "spec-v1",
        "portfolio_summary_hash": "portfolio-abc",
        "session_state": SessionState.OPEN,
        "data_quality": DataQuality.GOOD,
        "issued_at_utc": FIXED_NOW,
        "expires_at_utc": FIXED_NOW + timedelta(minutes=5),
    }
    fields.update(overrides)
    return DecisionContextBundle.model_validate(fields)


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


@pytest.fixture
def outcomes() -> InMemoryAgentDecisionOutcomeStore:
    return InMemoryAgentDecisionOutcomeStore()


@pytest.fixture
def gateway(outcomes: InMemoryAgentDecisionOutcomeStore) -> AgentGateway:
    return AgentGateway(
        identities=InMemoryAgentIdentityStore(),
        credentials=InMemoryAgentCredentialStore(),
        assignments=InMemoryTradingAssignmentStore(),
        contexts=InMemoryDecisionContextBundleStore(),
        outcomes=outcomes,
    )


def _registered(gateway: AgentGateway, *, status: AgentStatus = AgentStatus.ACTIVE) -> None:
    gateway.register_identity(identity(status=status), credential_secret=SECRET)
    gateway.issue_assignment(assignment())


def _with_context(gateway: AgentGateway) -> DecisionContextBundle:
    bundle = gateway.issue_context_bundle(context_bundle())
    return bundle


class TestIdentity:
    """ADR-005 §7 "Identity" row."""

    def test_an_unregistered_agent_is_refused(self, gateway: AgentGateway) -> None:
        with pytest.raises(UnknownAgentError):
            gateway.authenticate(agent_id=uuid4(), credential_secret=SECRET)

    def test_a_wrong_credential_is_refused(self, gateway: AgentGateway) -> None:
        _registered(gateway)
        with pytest.raises(AuthenticationError):
            gateway.authenticate(agent_id=AGENT_ID, credential_secret="wrong-secret")

    def test_a_suspended_agent_is_refused(self, gateway: AgentGateway) -> None:
        _registered(gateway, status=AgentStatus.SUSPENDED)
        with pytest.raises(AgentNotActiveError):
            gateway.authenticate(agent_id=AGENT_ID, credential_secret=SECRET)

    def test_a_retired_agent_is_refused(self, gateway: AgentGateway) -> None:
        _registered(gateway, status=AgentStatus.RETIRED)
        with pytest.raises(AgentNotActiveError):
            gateway.authenticate(agent_id=AGENT_ID, credential_secret=SECRET)

    def test_an_active_agent_with_the_right_credential_authenticates(
        self, gateway: AgentGateway
    ) -> None:
        _registered(gateway)
        result = gateway.authenticate(agent_id=AGENT_ID, credential_secret=SECRET)
        assert result.agent_id == AGENT_ID

    def test_reactivating_a_suspended_agent_restores_authentication(
        self, gateway: AgentGateway
    ) -> None:
        """`register_identity` is append-only (a fresh snapshot), not a

        mutation -- proves the latest snapshot wins."""
        _registered(gateway, status=AgentStatus.SUSPENDED)
        gateway.register_identity(identity(status=AgentStatus.ACTIVE), credential_secret=SECRET)
        result = gateway.authenticate(agent_id=AGENT_ID, credential_secret=SECRET)
        assert result.status is AgentStatus.ACTIVE


class TestImpersonation:
    def test_a_proposal_for_a_different_agent_id_is_refused(self, gateway: AgentGateway) -> None:
        _registered(gateway)
        bundle = _with_context(gateway)
        other_agent_proposal = proposal(context_hash=bundle.content_hash, agent_id=uuid4())
        with pytest.raises(ImpersonationError):
            gateway.submit_trade_proposal(
                agent_id=AGENT_ID,
                credential_secret=SECRET,
                proposal=other_agent_proposal,
                now=FIXED_NOW,
            )


class TestAssignmentScope:
    """ADR-005 §7 "Authorization / assignment scope" row."""

    def test_an_unknown_assignment_is_rejected(self, gateway: AgentGateway) -> None:
        _registered(gateway)
        bundle = _with_context(gateway)
        bad_proposal = proposal(context_hash=bundle.content_hash, assignment_id=uuid4())
        result = gateway.submit_trade_proposal(
            agent_id=AGENT_ID, credential_secret=SECRET, proposal=bad_proposal, now=FIXED_NOW
        )
        assert result.accepted is False
        assert result.reason is AgentRejectionReason.UNKNOWN_ASSIGNMENT

    def test_an_assignment_belonging_to_another_agent_is_rejected(
        self, gateway: AgentGateway
    ) -> None:
        gateway.register_identity(identity(), credential_secret=SECRET)
        gateway.issue_assignment(assignment(allowed_agent_id=uuid4()))
        bundle = _with_context(gateway)
        result = gateway.submit_trade_proposal(
            agent_id=AGENT_ID,
            credential_secret=SECRET,
            proposal=proposal(context_hash=bundle.content_hash),
            now=FIXED_NOW,
        )
        assert result.reason is AgentRejectionReason.ASSIGNMENT_NOT_OWNED

    def test_a_proposal_outside_the_validity_window_is_rejected(
        self, gateway: AgentGateway
    ) -> None:
        _registered(gateway)
        bundle = _with_context(gateway)
        result = gateway.submit_trade_proposal(
            agent_id=AGENT_ID,
            credential_secret=SECRET,
            proposal=proposal(context_hash=bundle.content_hash),
            now=FIXED_NOW + timedelta(days=365),
        )
        assert result.reason is AgentRejectionReason.ASSIGNMENT_NOT_VALID_AT_TIME

    def test_a_requested_risk_fraction_above_the_allowed_band_is_rejected(
        self, gateway: AgentGateway
    ) -> None:
        _registered(gateway)
        bundle = _with_context(gateway)
        result = gateway.submit_trade_proposal(
            agent_id=AGENT_ID,
            credential_secret=SECRET,
            proposal=proposal(
                context_hash=bundle.content_hash, requested_risk_fraction=Decimal("0.05")
            ),
            now=FIXED_NOW,
        )
        assert result.reason is AgentRejectionReason.RISK_FRACTION_OUT_OF_BAND

    def test_exceeding_the_hourly_proposal_rate_limit_is_rejected(
        self, gateway: AgentGateway
    ) -> None:
        """assignment() fixes max_proposals_per_hour=2."""
        _registered(gateway)
        bundle = _with_context(gateway)
        for _ in range(2):
            result = gateway.submit_trade_proposal(
                agent_id=AGENT_ID,
                credential_secret=SECRET,
                proposal=proposal(context_hash=bundle.content_hash),
                now=FIXED_NOW,
            )
            assert result.accepted is True

        third = gateway.submit_trade_proposal(
            agent_id=AGENT_ID,
            credential_secret=SECRET,
            proposal=proposal(context_hash=bundle.content_hash),
            now=FIXED_NOW,
        )
        assert third.reason is AgentRejectionReason.RATE_LIMIT_EXCEEDED

    def test_a_rate_limited_rejection_is_still_durably_audited(
        self, gateway: AgentGateway, outcomes: InMemoryAgentDecisionOutcomeStore
    ) -> None:
        """Guide §9: every rejection is auditable, not only accepted proposals."""
        _registered(gateway)
        bundle = _with_context(gateway)
        results = [
            gateway.submit_trade_proposal(
                agent_id=AGENT_ID,
                credential_secret=SECRET,
                proposal=proposal(context_hash=bundle.content_hash),
                now=FIXED_NOW,
            )
            for _ in range(3)
        ]
        rejected = next(result for result in results if not result.accepted)
        events = outcomes.events_for(rejected.outcome_id)
        assert any(event.event_type is AgentDecisionEventType.REJECTED for event in events)
        assert any(event.event_type is AgentDecisionEventType.RECEIVED for event in events)


class TestContext:
    """ADR-005 §7 "Expiry" row, context half."""

    def test_a_proposal_citing_an_unissued_context_hash_is_rejected(
        self, gateway: AgentGateway
    ) -> None:
        _registered(gateway)
        result = gateway.submit_trade_proposal(
            agent_id=AGENT_ID,
            credential_secret=SECRET,
            proposal=proposal(context_hash="never-issued-hash"),
            now=FIXED_NOW,
        )
        assert result.reason is AgentRejectionReason.UNKNOWN_CONTEXT

    def test_a_context_bundle_for_a_different_assignment_is_rejected(
        self, gateway: AgentGateway
    ) -> None:
        _registered(gateway)
        bundle = gateway.issue_context_bundle(context_bundle(assignment_id=uuid4()))
        result = gateway.submit_trade_proposal(
            agent_id=AGENT_ID,
            credential_secret=SECRET,
            proposal=proposal(context_hash=bundle.content_hash),
            now=FIXED_NOW,
        )
        assert result.reason is AgentRejectionReason.CONTEXT_ASSIGNMENT_MISMATCH

    def test_an_expired_context_bundle_is_rejected(self, gateway: AgentGateway) -> None:
        _registered(gateway)
        bundle = _with_context(gateway)
        # The proposal's own lifetime deliberately outlives the context
        # bundle's, so this isolates CONTEXT_EXPIRED from PROPOSAL_EXPIRED
        # (both fields default to the same FIXED_NOW+5min window otherwise).
        long_lived_proposal = proposal(
            context_hash=bundle.content_hash,
            expires_at_utc=bundle.expires_at_utc + timedelta(hours=1),
        )
        result = gateway.submit_trade_proposal(
            agent_id=AGENT_ID,
            credential_secret=SECRET,
            proposal=long_lived_proposal,
            now=bundle.expires_at_utc + timedelta(seconds=1),
        )
        assert result.reason is AgentRejectionReason.CONTEXT_EXPIRED

    def test_an_expired_proposal_is_rejected(self, gateway: AgentGateway) -> None:
        _registered(gateway)
        bundle = _with_context(gateway)
        stale = proposal(
            context_hash=bundle.content_hash,
            submitted_at_utc=FIXED_NOW,
            expires_at_utc=FIXED_NOW + timedelta(seconds=1),
        )
        result = gateway.submit_trade_proposal(
            agent_id=AGENT_ID,
            credential_secret=SECRET,
            proposal=stale,
            now=FIXED_NOW + timedelta(seconds=2),
        )
        assert result.reason is AgentRejectionReason.PROPOSAL_EXPIRED


class TestIdempotency:
    """ADR-005 §7 "Idempotency" row."""

    def test_an_identical_retry_is_a_safe_no_op(self, gateway: AgentGateway) -> None:
        _registered(gateway)
        bundle = _with_context(gateway)
        original = proposal(context_hash=bundle.content_hash)

        first = gateway.submit_trade_proposal(
            agent_id=AGENT_ID, credential_secret=SECRET, proposal=original, now=FIXED_NOW
        )
        second = gateway.submit_trade_proposal(
            agent_id=AGENT_ID, credential_secret=SECRET, proposal=original, now=FIXED_NOW
        )
        assert first == second
        assert first.accepted is True

    def test_a_retry_does_not_double_count_toward_the_rate_limit(
        self, gateway: AgentGateway
    ) -> None:
        """max_proposals_per_hour=2 -- one genuine proposal retried five times

        must still leave room for a second genuine proposal."""
        _registered(gateway)
        bundle = _with_context(gateway)
        original = proposal(context_hash=bundle.content_hash)
        for _ in range(5):
            gateway.submit_trade_proposal(
                agent_id=AGENT_ID, credential_secret=SECRET, proposal=original, now=FIXED_NOW
            )

        second = gateway.submit_trade_proposal(
            agent_id=AGENT_ID,
            credential_secret=SECRET,
            proposal=proposal(context_hash=bundle.content_hash),
            now=FIXED_NOW,
        )
        assert second.accepted is True

    def test_a_conflicting_retry_fails_closed(self, gateway: AgentGateway) -> None:
        _registered(gateway)
        bundle = _with_context(gateway)
        original = proposal(context_hash=bundle.content_hash)
        gateway.submit_trade_proposal(
            agent_id=AGENT_ID, credential_secret=SECRET, proposal=original, now=FIXED_NOW
        )

        conflicting = original.model_copy(update={"reference_price": Decimal("1.09999")})
        with pytest.raises(DecisionConflictError):
            gateway.submit_trade_proposal(
                agent_id=AGENT_ID, credential_secret=SECRET, proposal=conflicting, now=FIXED_NOW
            )

    def test_a_rejected_proposal_replays_the_same_rejection_on_retry(
        self, gateway: AgentGateway
    ) -> None:
        _registered(gateway)
        bad_proposal = proposal(context_hash="never-issued-hash")
        first = gateway.submit_trade_proposal(
            agent_id=AGENT_ID, credential_secret=SECRET, proposal=bad_proposal, now=FIXED_NOW
        )
        second = gateway.submit_trade_proposal(
            agent_id=AGENT_ID, credential_secret=SECRET, proposal=bad_proposal, now=FIXED_NOW
        )
        assert first == second
        assert second.reason is AgentRejectionReason.UNKNOWN_CONTEXT


class TestNoTradeIsDistinctFromNoResponse:
    """ADR-005 §8's "NO_TRADE distinct from no response" / "agent process can

    disappear without making Crumblr unsafe" proofs."""

    def test_no_trade_is_accepted_and_durably_recorded(self, gateway: AgentGateway) -> None:
        _registered(gateway)
        bundle = _with_context(gateway)
        decision = no_trade(context_hash=bundle.content_hash)
        result = gateway.submit_no_trade(
            agent_id=AGENT_ID, credential_secret=SECRET, decision=decision, now=FIXED_NOW
        )
        assert result.accepted is True
        assert result.outcome_type is AgentOutcomeType.NO_TRADE

    def test_nothing_is_recorded_for_an_agent_that_never_responds(
        self, gateway: AgentGateway, outcomes: InMemoryAgentDecisionOutcomeStore
    ) -> None:
        """Silence produces no outcome row at all -- never collapsed into an

        implicit NO_TRADE. Verified by construction: nothing is ever
        submitted, so `outcomes.count_claimed_since` for the assignment
        stays zero, proving the Gateway has no code path that fabricates a
        decision for a silent agent."""
        _registered(gateway)
        count = outcomes.count_claimed_since(assignment_id=ASSIGNMENT_ID, since=FIXED_NOW)
        assert count == 0

    def test_a_conflicting_no_trade_retry_fails_closed(self, gateway: AgentGateway) -> None:
        _registered(gateway)
        bundle = _with_context(gateway)
        original = no_trade(context_hash=bundle.content_hash)
        gateway.submit_no_trade(
            agent_id=AGENT_ID, credential_secret=SECRET, decision=original, now=FIXED_NOW
        )
        conflicting = original.model_copy(update={"reason_codes": ("different_reason",)})
        with pytest.raises(DecisionConflictError):
            gateway.submit_no_trade(
                agent_id=AGENT_ID, credential_secret=SECRET, decision=conflicting, now=FIXED_NOW
            )


class TestInterruptedClaimIsResumedNotAssumedAccepted:
    """Regression coverage for a self-review finding: the original `_replay`

    treated "no REJECTED event found" as proof of acceptance. A claim can be
    durably recorded with no settling event at all if the process that made
    it crashed between the claim and the verdict -- a retry must resume
    evaluation with fresh inputs, never default to `accepted=True`."""

    def test_a_claimed_but_unsettled_proposal_is_evaluated_fresh_not_assumed_accepted(
        self, gateway: AgentGateway, outcomes: InMemoryAgentDecisionOutcomeStore
    ) -> None:
        _registered(gateway)
        bundle = _with_context(gateway)
        # A longer proposal lifetime than the bundle's isolates
        # CONTEXT_EXPIRED from PROPOSAL_EXPIRED (both default to the same
        # FIXED_NOW+5min window otherwise).
        original = proposal(
            context_hash=bundle.content_hash,
            expires_at_utc=bundle.expires_at_utc + timedelta(hours=1),
        )
        # Simulate an interrupted first attempt: claim directly via the
        # store, bypassing the Gateway's evaluate+settle steps entirely --
        # exactly the state a crash between claim and verdict would leave.
        outcomes.claim_trade_proposal(original, now=FIXED_NOW)
        assert outcomes.settlement_for(original.proposal_id) is None

        # If the bug were still present, this would incorrectly return
        # accepted=True regardless of "now" -- the fix must actually run
        # the context-expiry check fresh, using this call's own inputs.
        result = gateway.submit_trade_proposal(
            agent_id=AGENT_ID,
            credential_secret=SECRET,
            proposal=original,
            now=bundle.expires_at_utc + timedelta(seconds=1),
        )
        assert result.accepted is False
        assert result.reason is AgentRejectionReason.CONTEXT_EXPIRED

    def test_resuming_an_unsettled_claim_does_not_duplicate_the_received_event(
        self, gateway: AgentGateway, outcomes: InMemoryAgentDecisionOutcomeStore
    ) -> None:
        _registered(gateway)
        bundle = _with_context(gateway)
        original = proposal(context_hash=bundle.content_hash)
        outcomes.claim_trade_proposal(original, now=FIXED_NOW)

        gateway.submit_trade_proposal(
            agent_id=AGENT_ID, credential_secret=SECRET, proposal=original, now=FIXED_NOW
        )

        received = [
            event
            for event in outcomes.events_for(original.proposal_id)
            if event.event_type is AgentDecisionEventType.RECEIVED
        ]
        assert len(received) == 1


class TestRequiredEvidence:
    """Regression coverage for a self-review finding: `TradingAssignment.required_evidence_fields`

    was defined but never checked -- a proposal with no evidence at all was
    accepted even against an assignment that demands some."""

    def test_no_evidence_when_the_assignment_requires_it_is_rejected(
        self, gateway: AgentGateway
    ) -> None:
        gateway.register_identity(identity(), credential_secret=SECRET)
        gateway.issue_assignment(assignment(required_evidence_fields=("regime",)))
        bundle = _with_context(gateway)
        result = gateway.submit_trade_proposal(
            agent_id=AGENT_ID,
            credential_secret=SECRET,
            proposal=proposal(context_hash=bundle.content_hash, evidence_refs=()),
            now=FIXED_NOW,
        )
        assert result.reason is AgentRejectionReason.MISSING_REQUIRED_EVIDENCE

    def test_some_evidence_when_the_assignment_requires_it_is_not_rejected_on_that_ground(
        self, gateway: AgentGateway
    ) -> None:
        """Conservative check, documented as such in gateway.py: proves

        *some* evidence was cited, does not verify it covers each named
        field (that needs evidence-content inspection, out of scope until
        AG-005's ingestion path exists)."""
        gateway.register_identity(identity(), credential_secret=SECRET)
        gateway.issue_assignment(assignment(required_evidence_fields=("regime",)))
        bundle = _with_context(gateway)
        result = gateway.submit_trade_proposal(
            agent_id=AGENT_ID,
            credential_secret=SECRET,
            proposal=proposal(context_hash=bundle.content_hash, evidence_refs=(uuid4(),)),
            now=FIXED_NOW,
        )
        assert result.accepted is True

    def test_no_evidence_is_fine_when_the_assignment_does_not_require_any(
        self, gateway: AgentGateway
    ) -> None:
        """`assignment()`'s default `required_evidence_fields=()` -- proves

        the check is opt-in per assignment, not a blanket requirement."""
        _registered(gateway)
        bundle = _with_context(gateway)
        result = gateway.submit_trade_proposal(
            agent_id=AGENT_ID,
            credential_secret=SECRET,
            proposal=proposal(context_hash=bundle.content_hash, evidence_refs=()),
            now=FIXED_NOW,
        )
        assert result.accepted is True
