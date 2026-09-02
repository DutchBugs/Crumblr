"""agent_gateway/supervisor_review.py -- AG-003's missing enforcement:
timeout/error/mismatch/expiry all resolve to `UNKNOWN`, never approval.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from crumblr.agent_gateway.contracts import ExternalSupervisorVerdict, SupervisorReview
from crumblr.agent_gateway.supervisor_review import (
    NO_SUPERVISOR_RESPONSE,
    PROPOSAL_ID_MISMATCH,
    REVIEW_EXPIRED,
    REVIEW_SELF_REPORTED_UNKNOWN,
    TRADE_INTENT_HASH_MISMATCH,
    TRADE_INTENT_ID_MISMATCH,
    evaluate_supervisor_review,
)

NOW = datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC)
PROPOSAL_ID = uuid4()
TRADE_INTENT_ID = uuid4()
TRADE_INTENT_HASH = "decision-hash-abc"


def review(**overrides: Any) -> SupervisorReview:
    fields: dict[str, Any] = {
        "review_id": uuid4(),
        "proposal_id": PROPOSAL_ID,
        "proposal_fingerprint": "proposal-fp-abc",
        "trade_intent_id": TRADE_INTENT_ID,
        "trade_intent_decision_hash": TRADE_INTENT_HASH,
        "supervisor_agent_id": uuid4(),
        "supervisor_runtime_version": "supervisor-v1",
        "policy_version": "policy-v1",
        "verdict": ExternalSupervisorVerdict.APPROVE,
        "reason_codes": (),
        "evidence_claims": (),
        "reviewed_at_utc": NOW,
        "expires_at_utc": NOW + timedelta(minutes=5),
    }
    fields.update(overrides)
    return SupervisorReview.model_validate(fields)


def evaluate(review_obj: SupervisorReview | None, **overrides: Any) -> Any:
    fields: dict[str, Any] = {
        "proposal_id": PROPOSAL_ID,
        "trade_intent_id": TRADE_INTENT_ID,
        "trade_intent_decision_hash": TRADE_INTENT_HASH,
        "now": NOW,
    }
    fields.update(overrides)
    return evaluate_supervisor_review(review_obj, **fields)


class TestNoResponse:
    def test_none_resolves_to_unknown(self) -> None:
        outcome = evaluate(None)
        assert outcome.verdict is ExternalSupervisorVerdict.UNKNOWN
        assert outcome.reason_codes == (NO_SUPERVISOR_RESPONSE,)
        assert outcome.review is None
        assert outcome.is_veto is False


class TestGenuineApproveAndVeto:
    def test_a_genuinely_bound_approve_passes_through(self) -> None:
        approve_review = review(verdict=ExternalSupervisorVerdict.APPROVE)
        outcome = evaluate(approve_review)
        assert outcome.verdict is ExternalSupervisorVerdict.APPROVE
        assert outcome.review == approve_review
        assert outcome.is_veto is False

    def test_a_genuinely_bound_veto_passes_through_with_its_reason_codes(self) -> None:
        veto_review = review(
            verdict=ExternalSupervisorVerdict.VETO, reason_codes=("insufficient_evidence",)
        )
        outcome = evaluate(veto_review)
        assert outcome.verdict is ExternalSupervisorVerdict.VETO
        assert outcome.reason_codes == ("insufficient_evidence",)
        assert outcome.review == veto_review
        assert outcome.is_veto is True


class TestBindingMismatches:
    """A review for a different decision must never gate this one --
    each mismatch is refused independently, never assumed close enough."""

    def test_a_different_proposal_id_is_refused(self) -> None:
        mismatched = review(proposal_id=uuid4())
        outcome = evaluate(mismatched)
        assert outcome.verdict is ExternalSupervisorVerdict.UNKNOWN
        assert outcome.reason_codes == (PROPOSAL_ID_MISMATCH,)
        assert outcome.review is None

    def test_a_different_trade_intent_id_is_refused(self) -> None:
        mismatched = review(trade_intent_id=uuid4())
        outcome = evaluate(mismatched)
        assert outcome.reason_codes == (TRADE_INTENT_ID_MISMATCH,)

    def test_a_different_trade_intent_decision_hash_is_refused(self) -> None:
        mismatched = review(trade_intent_decision_hash="a-different-hash")
        outcome = evaluate(mismatched)
        assert outcome.reason_codes == (TRADE_INTENT_HASH_MISMATCH,)

    def test_an_approve_verdict_does_not_survive_a_binding_mismatch(self) -> None:
        """The strongest proof: even an outright APPROVE from the
        external Supervisor is discarded, not merely downgraded, when it
        does not genuinely bind to this decision."""
        mismatched = review(proposal_id=uuid4(), verdict=ExternalSupervisorVerdict.APPROVE)
        outcome = evaluate(mismatched)
        assert outcome.verdict is ExternalSupervisorVerdict.UNKNOWN
        assert outcome.review is None


class TestExpiry:
    def test_an_expired_review_is_refused(self) -> None:
        expired = review(
            reviewed_at_utc=NOW - timedelta(minutes=10),
            expires_at_utc=NOW - timedelta(minutes=5),
        )
        outcome = evaluate(expired, now=NOW)
        assert outcome.verdict is ExternalSupervisorVerdict.UNKNOWN
        assert outcome.reason_codes == (REVIEW_EXPIRED,)

    def test_exactly_at_expiry_is_refused_not_a_boundary_pass(self) -> None:
        boundary = review(reviewed_at_utc=NOW - timedelta(seconds=1), expires_at_utc=NOW)
        outcome = evaluate(boundary, now=NOW)
        assert outcome.verdict is ExternalSupervisorVerdict.UNKNOWN
        assert outcome.reason_codes == (REVIEW_EXPIRED,)

    def test_one_second_before_expiry_still_passes(self) -> None:
        fresh = review(expires_at_utc=NOW + timedelta(seconds=1))
        outcome = evaluate(fresh, now=NOW)
        assert outcome.verdict is ExternalSupervisorVerdict.APPROVE


class TestSelfReportedUnknown:
    def test_a_review_that_already_says_unknown_stays_unknown(self) -> None:
        self_unknown = review(verdict=ExternalSupervisorVerdict.UNKNOWN)
        outcome = evaluate(self_unknown)
        assert outcome.verdict is ExternalSupervisorVerdict.UNKNOWN
        assert outcome.reason_codes == (REVIEW_SELF_REPORTED_UNKNOWN,)
        assert outcome.review is None
