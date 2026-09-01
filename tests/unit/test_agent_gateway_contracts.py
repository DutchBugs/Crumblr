"""First-draft external-agent contracts (ADR-005, Step A).

Structural tests only — there is no Agent Gateway yet to test against, and
these contracts are not imported by anything outside this test file and
`src/crumblr/agent_gateway/`. What is asserted here is exactly what makes
these contracts reviewable as a design: immutability, closed vocabularies,
and the specific invariants ADR-005's six owner tweaks require.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from crumblr.agent_gateway.contracts import (
    AgentIdentity,
    AgentRole,
    AgentStatus,
    ChampionShadowStatus,
    DecisionContextBundle,
    ExternalSupervisorVerdict,
    NoTradeDecision,
    PolicyHints,
    ProposalWithdrawal,
    SupervisorReview,
    TradeProposal,
    TradingAssignment,
)
from crumblr.domain.enums import DataQuality, EntryType, Environment, SessionState, Side
from tests.conftest import FIXED_NOW


def agent_identity(**overrides: Any) -> AgentIdentity:
    fields: dict[str, Any] = {
        "agent_id": uuid4(),
        "role": AgentRole.TRADER,
        "runtime_version": "trader-v1",
        "service_identity": "spiffe://crumblr/agents/trader-v1",
        "status": AgentStatus.ACTIVE,
        "registered_at_utc": FIXED_NOW,
    }
    fields.update(overrides)
    return AgentIdentity.model_validate(fields)


def trading_assignment(**overrides: Any) -> TradingAssignment:
    fields: dict[str, Any] = {
        "assignment_id": uuid4(),
        "assignment_version": "assignment-v1",
        "allowed_agent_id": uuid4(),
        "canonical_symbol": "EUR/USD",
        "timeframe": "M5",
        "strategy_artifact_id": uuid4(),
        "strategy_artifact_hash": "abc123",
        "valid_from_utc": FIXED_NOW,
        "valid_until_utc": FIXED_NOW + timedelta(days=30),
        "max_proposals_per_hour": 6,
        "allowed_risk_fraction_min": Decimal("0.001"),
        "allowed_risk_fraction_max": Decimal("0.01"),
        "required_evidence_fields": ("regime", "confidence_basis"),
        "supervisor_policy_version": "supervisor-policy-v1",
        "environment": Environment.PAPER,
        "champion_shadow_status": ChampionShadowStatus.SHADOW,
    }
    fields.update(overrides)
    return TradingAssignment.model_validate(fields)


def decision_context_bundle(**overrides: Any) -> DecisionContextBundle:
    fields: dict[str, Any] = {
        "context_id": uuid4(),
        "assignment_id": uuid4(),
        "market_snapshot_id": uuid4(),
        "instrument_spec_version": "spec-v1",
        "portfolio_summary_hash": "portfolio-abc",
        "session_state": SessionState.OPEN,
        "data_quality": DataQuality.GOOD,
        "feature_snapshot_id": uuid4(),
        "issued_at_utc": FIXED_NOW,
        "expires_at_utc": FIXED_NOW + timedelta(minutes=5),
    }
    fields.update(overrides)
    return DecisionContextBundle.model_validate(fields)


def trade_proposal(**overrides: Any) -> TradeProposal:
    fields: dict[str, Any] = {
        "proposal_id": uuid4(),
        "agent_id": uuid4(),
        "assignment_id": uuid4(),
        "context_hash": "context-hash-abc",
        "strategy_artifact_hash": "abc123",
        "side": Side.BUY,
        "entry_type": EntryType.MARKET,
        "reference_price": Decimal("1.08500"),
        "stop_loss_price": Decimal("1.08000"),
        "take_profit_price": Decimal("1.09000"),
        "confidence": 0.8,
        "requested_risk_fraction": Decimal("0.005"),
        "reason_codes": ("sweep_and_shift",),
        "evidence_refs": (uuid4(),),
        "submitted_at_utc": FIXED_NOW,
        "expires_at_utc": FIXED_NOW + timedelta(minutes=5),
    }
    fields.update(overrides)
    return TradeProposal.model_validate(fields)


def no_trade_decision(**overrides: Any) -> NoTradeDecision:
    fields: dict[str, Any] = {
        "decision_id": uuid4(),
        "agent_id": uuid4(),
        "assignment_id": uuid4(),
        "context_hash": "context-hash-abc",
        "reason_codes": ("no_setup",),
        "decided_at_utc": FIXED_NOW,
    }
    fields.update(overrides)
    return NoTradeDecision.model_validate(fields)


def proposal_withdrawal(**overrides: Any) -> ProposalWithdrawal:
    fields: dict[str, Any] = {
        "withdrawal_id": uuid4(),
        "proposal_id": uuid4(),
        "proposal_fingerprint": "fingerprint-abc",
        "agent_id": uuid4(),
        "requested_at_utc": FIXED_NOW,
        "reason": "regime changed before submission",
        "honoured": True,
    }
    fields.update(overrides)
    return ProposalWithdrawal.model_validate(fields)


def supervisor_review(**overrides: Any) -> SupervisorReview:
    fields: dict[str, Any] = {
        "review_id": uuid4(),
        "proposal_id": uuid4(),
        "proposal_fingerprint": "fingerprint-abc",
        "trade_intent_id": uuid4(),
        "trade_intent_decision_hash": "intent-hash-abc",
        "supervisor_agent_id": uuid4(),
        "supervisor_runtime_version": "supervisor-v1",
        "policy_version": "supervisor-policy-v1",
        "verdict": ExternalSupervisorVerdict.APPROVE,
        "reason_codes": (),
        "evidence_claims": (uuid4(),),
        "reviewed_at_utc": FIXED_NOW,
        "expires_at_utc": FIXED_NOW + timedelta(minutes=5),
    }
    fields.update(overrides)
    return SupervisorReview.model_validate(fields)


class TestImmutabilityAndClosedVocabulary:
    def test_agent_identity_is_frozen(self) -> None:
        identity = agent_identity()
        with pytest.raises(ValidationError):
            identity.status = AgentStatus.SUSPENDED

    def test_trade_proposal_rejects_an_unknown_field(self) -> None:
        with pytest.raises(ValidationError):
            trade_proposal(unexpected_field="not allowed")

    def test_policy_hints_rejects_an_unknown_key(self) -> None:
        """Proves PolicyHints is genuinely closed, not `Any` in disguise

        (owner tweak 4)."""
        with pytest.raises(ValidationError):
            PolicyHints.model_validate({"made_up_hint": True})


class TestTradingAssignment:
    def test_valid_until_must_be_after_valid_from(self) -> None:
        with pytest.raises(ValidationError, match="valid_until_utc must be after"):
            trading_assignment(valid_from_utc=FIXED_NOW, valid_until_utc=FIXED_NOW)

    def test_risk_fraction_min_must_not_exceed_max(self) -> None:
        with pytest.raises(ValidationError, match="must not exceed"):
            trading_assignment(
                allowed_risk_fraction_min=Decimal("0.02"),
                allowed_risk_fraction_max=Decimal("0.01"),
            )

    def test_a_well_formed_assignment_is_valid(self) -> None:
        assignment = trading_assignment()
        assert assignment.champion_shadow_status is ChampionShadowStatus.SHADOW


class TestDecisionContextBundle:
    def test_expires_must_be_after_issued(self) -> None:
        with pytest.raises(ValidationError, match="expires_at_utc must be after"):
            decision_context_bundle(issued_at_utc=FIXED_NOW, expires_at_utc=FIXED_NOW)

    def test_content_hash_changes_when_content_changes(self) -> None:
        first = decision_context_bundle(portfolio_summary_hash="a")
        second = decision_context_bundle(portfolio_summary_hash="b")
        assert first.content_hash != second.content_hash

    def test_content_hash_is_stable_for_identical_content(self) -> None:
        shared: dict[str, Any] = {
            "assignment_id": uuid4(),
            "market_snapshot_id": uuid4(),
            "portfolio_summary_hash": "same",
            "feature_snapshot_id": uuid4(),
        }
        first = decision_context_bundle(**shared)
        second = decision_context_bundle(**shared)
        assert first.content_hash == second.content_hash

    def test_content_hash_changes_when_feature_snapshot_id_changes(self) -> None:
        """Review 1.26 §5: "bundle content_hash includes the feature

        snapshot reference" -- a bundle's evidence reference cannot be
        swapped without changing the hash a proposal binds to."""
        first = decision_context_bundle(feature_snapshot_id=uuid4())
        second = decision_context_bundle(feature_snapshot_id=uuid4())
        assert first.content_hash != second.content_hash

    def test_content_hash_reacts_to_policy_hints(self) -> None:
        base = decision_context_bundle(policy_hints=PolicyHints(session_blackout_active=False))
        changed = decision_context_bundle(policy_hints=PolicyHints(session_blackout_active=True))
        assert base.content_hash != changed.content_hash

    def test_computed_content_hash_is_rejected_as_input_on_reload(self) -> None:
        """The same `_discard_computed_fields` mechanism every other

        computed field in this codebase relies on — a serialised bundle
        can be loaded back without `extra="forbid"` rejecting its own
        computed field."""
        bundle = decision_context_bundle()
        reloaded = DecisionContextBundle.model_validate(bundle.model_dump(mode="json"))
        assert reloaded.content_hash == bundle.content_hash


class TestTradeProposal:
    def test_missing_stop_loss_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            trade_proposal(stop_loss_price=None)

    def test_missing_take_profit_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            trade_proposal(take_profit_price=None)

    def test_flat_side_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must be directional"):
            trade_proposal(side=Side.FLAT)

    def test_buy_stop_must_be_below_reference_price(self) -> None:
        with pytest.raises(ValidationError, match="stop_loss_price must be below"):
            trade_proposal(
                side=Side.BUY,
                reference_price=Decimal("1.08500"),
                stop_loss_price=Decimal("1.09000"),
                take_profit_price=Decimal("1.09500"),
            )

    def test_sell_stop_must_be_above_reference_price(self) -> None:
        with pytest.raises(ValidationError, match="stop_loss_price must be above"):
            trade_proposal(
                side=Side.SELL,
                reference_price=Decimal("1.08500"),
                stop_loss_price=Decimal("1.08000"),
                take_profit_price=Decimal("1.08000"),
            )

    def test_expiry_must_be_after_submission(self) -> None:
        with pytest.raises(ValidationError, match="expires_at_utc must be after"):
            trade_proposal(submitted_at_utc=FIXED_NOW, expires_at_utc=FIXED_NOW)

    def test_proposal_fingerprint_changes_when_content_changes(self) -> None:
        first = trade_proposal(requested_risk_fraction=Decimal("0.005"))
        second = trade_proposal(requested_risk_fraction=Decimal("0.006"))
        assert first.proposal_fingerprint != second.proposal_fingerprint

    def test_proposal_fingerprint_excludes_proposal_id(self) -> None:
        """The exact property a future Agent Gateway's idempotent claim

        logic needs (owner tweak 3, mirrors
        `ExecutionRequestStore.claim()`'s fingerprint check): two proposals
        differing only in `proposal_id` fingerprint identically."""
        shared: dict[str, Any] = {
            "agent_id": uuid4(),
            "assignment_id": uuid4(),
            "context_hash": "same-context",
            "evidence_refs": (uuid4(),),
        }
        first = trade_proposal(**shared)
        second = trade_proposal(**shared)
        assert first.proposal_id != second.proposal_id
        assert first.proposal_fingerprint == second.proposal_fingerprint


class TestNoTradeDecisionIsIndependentOfProposal:
    def test_no_trade_decision_is_constructible_on_its_own(self) -> None:
        decision = no_trade_decision()
        assert decision.reason_codes == ("no_setup",)

    def test_no_trade_decision_and_trade_proposal_are_unrelated_types(self) -> None:
        """Proves NO_TRADE is never derived from a TradeProposal's

        absence (owner tweak 1) — both are independently constructible,
        neither implies or requires the other."""
        decision = no_trade_decision()
        proposal = trade_proposal()
        assert not isinstance(decision, TradeProposal)
        assert not isinstance(proposal, NoTradeDecision)

    def test_decision_fingerprint_changes_when_content_changes(self) -> None:
        first = no_trade_decision(reason_codes=("no_setup",))
        second = no_trade_decision(reason_codes=("regime_unclear",))
        assert first.decision_fingerprint != second.decision_fingerprint

    def test_decision_fingerprint_excludes_decision_id(self) -> None:
        """Same idempotent-claim property as `TradeProposal.proposal_fingerprint`

        (Step B needs this for NO_TRADE too): two decisions differing only
        in `decision_id` fingerprint identically."""
        shared: dict[str, Any] = {
            "agent_id": uuid4(),
            "assignment_id": uuid4(),
            "context_hash": "same-context",
        }
        first = no_trade_decision(**shared)
        second = no_trade_decision(**shared)
        assert first.decision_id != second.decision_id
        assert first.decision_fingerprint == second.decision_fingerprint


class TestProposalWithdrawal:
    def test_an_honoured_withdrawal_is_valid(self) -> None:
        withdrawal = proposal_withdrawal(honoured=True)
        assert withdrawal.honoured is True

    def test_a_refused_too_late_withdrawal_is_still_a_valid_record(self) -> None:
        """A withdrawal attempt refused as too late is durably auditable,

        not silently dropped (owner tweak 6)."""
        withdrawal = proposal_withdrawal(honoured=False, reason="submission already started")
        assert withdrawal.honoured is False


class TestSupervisorReview:
    def test_expiry_must_be_after_review(self) -> None:
        with pytest.raises(ValidationError, match="expires_at_utc must be after"):
            supervisor_review(reviewed_at_utc=FIXED_NOW, expires_at_utc=FIXED_NOW)

    def test_binds_the_platform_owned_trade_intent(self) -> None:
        """Owner tweak 2: a review is provably about one specific,

        fully-identified internal decision chain, not merely a proposal
        id."""
        intent_id = uuid4()
        review = supervisor_review(trade_intent_id=intent_id)
        assert review.trade_intent_id == intent_id
        assert review.risk_decision_id is None
        assert review.policy_gate_decision_id is None

    def test_can_carry_risk_and_policy_gate_references_once_available(self) -> None:
        risk_decision_id = uuid4()
        policy_gate_decision_id = uuid4()
        review = supervisor_review(
            risk_decision_id=risk_decision_id,
            policy_gate_decision_id=policy_gate_decision_id,
        )
        assert review.risk_decision_id == risk_decision_id
        assert review.policy_gate_decision_id == policy_gate_decision_id

    def test_unknown_verdict_is_constructible_and_distinct_from_internal_verdict(self) -> None:
        """Timeout/error/invalid response reads as UNKNOWN, never as

        approval (guide §2.7, O-007) — and this is a different enum from
        the internal SupervisorVerdict entirely, not a shared value."""
        review = supervisor_review(verdict=ExternalSupervisorVerdict.UNKNOWN)
        assert review.verdict is ExternalSupervisorVerdict.UNKNOWN
        assert not hasattr(ExternalSupervisorVerdict, "HALT")
