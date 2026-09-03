"""A deterministic reference external Supervisor (owner/reviewer work
order, `review/OWNER_WORK_ORDERS_DEMO_CANARY_2026-09-03.md` Phase C):
"If no Supervisor service exists yet, a deterministic reference
Supervisor in a separate process is acceptable for the first DEMO
canary, provided it has zero MT5/DB credentials and the exact same
APPROVE/VETO/UNKNOWN authority limits."

**What this is not.** Not a judgment layer -- no ML, no qualitative
"does this look right" review a real AI Supervisor might someday
provide. A deterministic mechanical stand-in can only honestly check
what it is structurally able to check without domain judgment: that the
proposal carries the auditable evidence build.md/ADR-005 already
require of it (non-empty `reason_codes`) and clears a minimum
`confidence` floor the operator configures. Both are already-defined,
strategy-neutral `TradeProposal` fields -- nothing here reads Risk/
Policy state, sizes, mutates, waives Risk, resets HALT or executes (the
same "never" list `decision_path.py`'s module docstring and
`supervisor_review.py` already hold to).

**In-process today, not yet a separate process.** The work order's
"in a separate process" framing matters for the real DEMO canary trust
boundary; this slice delivers the transport-agnostic evaluation core and
wires it into `decision_path.py` via `ExternalSupervisorProvider`,
proving the wiring genuinely works end-to-end against a real (if
minimal) implementation. An HTTP transport that lets this run as an
actual separate process (mirroring `static_agent_client.py`'s pattern)
is deliberately deferred -- writing one now, with nothing yet requiring
out-of-process deployment, would be exactly the kind of speculative code
this codebase's "narrow, real, proven" discipline avoids building ahead
of an actual need.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

from crumblr.agent_gateway.contracts import (
    ExternalSupervisorVerdict,
    SupervisorReview,
    TradeProposal,
)
from crumblr.domain.models import TradeIntent
from crumblr.domain.timeutils import UtcDatetime

LOW_CONFIDENCE = "REFERENCE_SUPERVISOR_LOW_CONFIDENCE"
MISSING_REASON_CODES = "REFERENCE_SUPERVISOR_MISSING_REASON_CODES"

REFERENCE_SUPERVISOR_RUNTIME_VERSION = "reference-supervisor-v1"
REFERENCE_SUPERVISOR_POLICY_VERSION = "reference-supervisor-policy-v1"


class ReferenceSupervisorConfig:
    """Operator-set parameters for the deterministic reference Supervisor.

    `supervisor_agent_id` identifies this reference implementation the
    same way any other Supervisor Agent would be identified -- never
    fabricated as the reviewed Trading Agent's own identity.
    """

    __slots__ = ("min_confidence", "review_validity_seconds", "supervisor_agent_id")

    def __init__(
        self,
        *,
        supervisor_agent_id: UUID,
        min_confidence: float,
        review_validity_seconds: int = 300,
    ) -> None:
        if not 0.0 <= min_confidence <= 1.0:
            raise ValueError(f"min_confidence must be in [0, 1], got {min_confidence}")
        if review_validity_seconds <= 0:
            raise ValueError(
                f"review_validity_seconds must be positive, got {review_validity_seconds}"
            )
        self.supervisor_agent_id = supervisor_agent_id
        self.min_confidence = min_confidence
        self.review_validity_seconds = review_validity_seconds


class ReferenceSupervisor:
    """Implements `decision_path.py::ExternalSupervisorProvider`,
    deterministically and in-process. See the module docstring for scope
    and limits -- always returns a genuine `SupervisorReview`, never
    `None`: an in-process call cannot time out or lose a response the way
    a real transport could, so there is no honest `None` case for this
    particular implementation to report.
    """

    __slots__ = ("_config",)

    def __init__(self, config: ReferenceSupervisorConfig) -> None:
        self._config = config

    def review(
        self,
        *,
        proposal: TradeProposal,
        intent: TradeIntent,
        risk_decision_id: UUID,
        policy_gate_decision_id: UUID,
        now: UtcDatetime,
    ) -> SupervisorReview:
        reasons: list[str] = []
        if not proposal.reason_codes:
            reasons.append(MISSING_REASON_CODES)
        if proposal.confidence < self._config.min_confidence:
            reasons.append(LOW_CONFIDENCE)
        verdict = ExternalSupervisorVerdict.VETO if reasons else ExternalSupervisorVerdict.APPROVE

        return SupervisorReview(
            review_id=uuid5(
                NAMESPACE_URL,
                f"crumblr:reference-supervisor:{proposal.proposal_id}:{intent.decision_hash}",
            ),
            proposal_id=proposal.proposal_id,
            proposal_fingerprint=proposal.proposal_fingerprint,
            trade_intent_id=intent.intent_id,
            trade_intent_decision_hash=intent.decision_hash,
            risk_decision_id=risk_decision_id,
            policy_gate_decision_id=policy_gate_decision_id,
            supervisor_agent_id=self._config.supervisor_agent_id,
            supervisor_runtime_version=REFERENCE_SUPERVISOR_RUNTIME_VERSION,
            policy_version=REFERENCE_SUPERVISOR_POLICY_VERSION,
            verdict=verdict,
            reason_codes=tuple(reasons),
            evidence_claims=proposal.evidence_refs,
            confidence=proposal.confidence,
            reviewed_at_utc=now,
            expires_at_utc=now + timedelta(seconds=self._config.review_validity_seconds),
        )
