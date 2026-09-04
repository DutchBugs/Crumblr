"""agent_gateway/reference_supervisor.py -- the deterministic reference
external Supervisor (owner/reviewer work order,
`review/OWNER_WORK_ORDERS_DEMO_CANARY_2026-09-03.md` Phase C).
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from crumblr.agent_gateway.contracts import ExternalSupervisorVerdict, TradeProposal
from crumblr.agent_gateway.reference_supervisor import (
    LOW_CONFIDENCE,
    MISSING_REASON_CODES,
    REFERENCE_SUPERVISOR_POLICY_VERSION,
    REFERENCE_SUPERVISOR_RUNTIME_VERSION,
    ReferenceSupervisor,
    ReferenceSupervisorConfig,
)
from crumblr.domain.enums import EntryType, Side
from tests.conftest import FIXED_NOW, make_intent

CONTEXT_HASH = "context-hash-abc"


def proposal(**overrides: Any) -> TradeProposal:
    fields: dict[str, Any] = {
        "proposal_id": uuid4(),
        "agent_id": uuid4(),
        "assignment_id": uuid4(),
        "context_hash": CONTEXT_HASH,
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


def config(**overrides: Any) -> ReferenceSupervisorConfig:
    fields: dict[str, Any] = {
        "supervisor_agent_id": uuid4(),
        "min_confidence": 0.5,
        "review_validity_seconds": 300,
    }
    fields.update(overrides)
    return ReferenceSupervisorConfig(**fields)


class TestReferenceSupervisorConfig:
    def test_rejects_a_confidence_floor_outside_zero_to_one(self) -> None:
        with pytest.raises(ValueError, match="min_confidence"):
            config(min_confidence=1.5)
        with pytest.raises(ValueError, match="min_confidence"):
            config(min_confidence=-0.1)

    def test_rejects_a_non_positive_validity_window(self) -> None:
        with pytest.raises(ValueError, match="review_validity_seconds"):
            config(review_validity_seconds=0)
        with pytest.raises(ValueError, match="review_validity_seconds"):
            config(review_validity_seconds=-5)

    def test_boundary_confidence_values_are_accepted(self) -> None:
        config(min_confidence=0.0)
        config(min_confidence=1.0)


class TestReferenceSupervisorReview:
    def test_a_well_formed_proposal_above_the_confidence_floor_is_approved(self) -> None:
        supervisor = ReferenceSupervisor(config(min_confidence=0.5))
        review = supervisor.review(
            proposal=proposal(confidence=0.8),
            intent=make_intent(),
            risk_decision_id=uuid4(),
            policy_gate_decision_id=uuid4(),
            now=FIXED_NOW,
        )
        assert review.verdict is ExternalSupervisorVerdict.APPROVE
        assert review.reason_codes == ()

    def test_confidence_below_the_floor_is_vetoed(self) -> None:
        supervisor = ReferenceSupervisor(config(min_confidence=0.5))
        review = supervisor.review(
            proposal=proposal(confidence=0.4),
            intent=make_intent(),
            risk_decision_id=uuid4(),
            policy_gate_decision_id=uuid4(),
            now=FIXED_NOW,
        )
        assert review.verdict is ExternalSupervisorVerdict.VETO
        assert LOW_CONFIDENCE in review.reason_codes

    def test_confidence_exactly_at_the_floor_is_approved_not_vetoed(self) -> None:
        """`< min_confidence` is the veto condition -- the boundary itself
        passes, mirroring `TradeProposal.confidence`'s own inclusive
        [0, 1] range."""
        supervisor = ReferenceSupervisor(config(min_confidence=0.5))
        review = supervisor.review(
            proposal=proposal(confidence=0.5),
            intent=make_intent(),
            risk_decision_id=uuid4(),
            policy_gate_decision_id=uuid4(),
            now=FIXED_NOW,
        )
        assert review.verdict is ExternalSupervisorVerdict.APPROVE

    def test_never_returns_none(self) -> None:
        """Unlike a real transport-backed Supervisor, an in-process
        deterministic call cannot time out or lose a response -- there is
        no honest `None` case for this implementation."""
        supervisor = ReferenceSupervisor(config())
        review = supervisor.review(
            proposal=proposal(),
            intent=make_intent(),
            risk_decision_id=uuid4(),
            policy_gate_decision_id=uuid4(),
            now=FIXED_NOW,
        )
        assert review is not None


class TestReferenceSupervisorBinding:
    def test_review_binds_to_the_exact_proposal_and_intent(self) -> None:
        the_proposal = proposal()
        the_intent = make_intent()
        risk_decision_id = uuid4()
        policy_gate_decision_id = uuid4()
        supervisor = ReferenceSupervisor(config())
        review = supervisor.review(
            proposal=the_proposal,
            intent=the_intent,
            risk_decision_id=risk_decision_id,
            policy_gate_decision_id=policy_gate_decision_id,
            now=FIXED_NOW,
        )
        assert review.proposal_id == the_proposal.proposal_id
        assert review.proposal_fingerprint == the_proposal.proposal_fingerprint
        assert review.trade_intent_id == the_intent.intent_id
        assert review.trade_intent_decision_hash == the_intent.decision_hash
        assert review.risk_decision_id == risk_decision_id
        assert review.policy_gate_decision_id == policy_gate_decision_id
        assert review.evidence_claims == the_proposal.evidence_refs
        assert review.confidence == the_proposal.confidence

    def test_review_carries_this_implementations_own_identity(self) -> None:
        supervisor_agent_id = uuid4()
        supervisor = ReferenceSupervisor(config(supervisor_agent_id=supervisor_agent_id))
        review = supervisor.review(
            proposal=proposal(),
            intent=make_intent(),
            risk_decision_id=uuid4(),
            policy_gate_decision_id=uuid4(),
            now=FIXED_NOW,
        )
        assert review.supervisor_agent_id == supervisor_agent_id
        assert review.supervisor_runtime_version == REFERENCE_SUPERVISOR_RUNTIME_VERSION
        assert review.policy_version == REFERENCE_SUPERVISOR_POLICY_VERSION

    def test_expiry_is_exactly_the_configured_validity_window_from_now(self) -> None:
        supervisor = ReferenceSupervisor(config(review_validity_seconds=60))
        review = supervisor.review(
            proposal=proposal(),
            intent=make_intent(),
            risk_decision_id=uuid4(),
            policy_gate_decision_id=uuid4(),
            now=FIXED_NOW,
        )
        assert review.reviewed_at_utc == FIXED_NOW
        assert review.expires_at_utc == FIXED_NOW + timedelta(seconds=60)


class TestMissingReasonCodes:
    def test_a_proposal_with_no_reason_codes_is_vetoed(self) -> None:
        """`TradeProposal` itself places no non-empty constraint on
        `reason_codes` (mirrors `AgentGateway._evaluate_proposal`'s own
        `MISSING_REASON_CODES` check) -- the reference Supervisor applies
        its own, independent evidence-presence check."""
        supervisor = ReferenceSupervisor(config(min_confidence=0.0))
        review = supervisor.review(
            proposal=proposal(reason_codes=()),
            intent=make_intent(),
            risk_decision_id=uuid4(),
            policy_gate_decision_id=uuid4(),
            now=FIXED_NOW,
        )
        assert review.verdict is ExternalSupervisorVerdict.VETO
        assert MISSING_REASON_CODES in review.reason_codes

    def test_both_failures_are_named_together_not_only_the_first(self) -> None:
        supervisor = ReferenceSupervisor(config(min_confidence=0.9))
        review = supervisor.review(
            proposal=proposal(reason_codes=(), confidence=0.1),
            intent=make_intent(),
            risk_decision_id=uuid4(),
            policy_gate_decision_id=uuid4(),
            now=FIXED_NOW,
        )
        assert review.verdict is ExternalSupervisorVerdict.VETO
        assert MISSING_REASON_CODES in review.reason_codes
        assert LOW_CONFIDENCE in review.reason_codes
