"""Evaluate an external Supervisor Agent's review (AG-003,
`review/AGENT_FEEDBACK.md`; `ADR-005-external-agent-trust-boundary.md`
section 5's `SupervisorReview` contract; `feedback.1.28.md` section 6's
"Supervisor / Policy" direction).

**What AG-003 names.** `SupervisorReview`/`ExternalSupervisorVerdict` have
existed since Step A -- `ExternalSupervisorVerdict.UNKNOWN`'s own
docstring already says "timeout, error, or an invalid response -- never
approval" -- but nothing evaluated a review against that rule. A response
that never arrived, arrived for the wrong decision, arrived expired, or
was already self-reported `UNKNOWN` by the external Supervisor all had to
resolve the same way: `UNKNOWN`, never a silent approval. This module is
that missing enforcement.

**Veto-only, never authoritative, matches feedback.1.28.md section 6
exactly:** "The Supervisor does not replace Risk, does not size, does not
modify the intent, and cannot waive a broker/safety rule." Nothing here
constructs, sizes, or seals anything -- `evaluate_supervisor_review()`
only ever answers one question: does this specific, genuinely-bound
review veto this specific decision, and if not, why not (`UNKNOWN`'s
`reason_codes` name the exact reason -- no response, a binding mismatch,
an expired review, or a self-reported `UNKNOWN`).

**Never trusted blindly -- binding checked field for field.** A
`SupervisorReview` is accepted only when it is provably about *this*
decision: `proposal_id`, `trade_intent_id` and
`trade_intent_decision_hash` must all match exactly, and the review must
not have expired as of `now`. Any mismatch resolves to `UNKNOWN` with a
reason naming which binding failed, the same "verify, never assume"
discipline `static_agent_translate.py::translate_no_trade_response()`
already applies to the Static Agent bridge.

**Known gap: `risk_decision_id`/`policy_gate_decision_id` are not
checked here** (`review/AGENT_FEEDBACK.md` AG-003), even though
`contracts.py::SupervisorReview` documents them as completing "the audit
chain from the external review back to the exact internal decisions it
was reviewing." Both are optional on the contract ("a review may be
requested before either has run"), so a strict equality check needs an
explicit answer for the unset case this module does not yet have --
tracked, not silently assumed safe. A review's own two binding-relevant
fields (`proposal_id`/`trade_intent_id`/`trade_intent_decision_hash`)
are the only ones enforced today.

**No HTTP client here, deliberately.** Unlike the Static Agent fork
(`static_agent_client.py`), there is no real, existing external Supervisor
service to build and prove a transport client against yet -- writing one
against an unspecified target would be exactly the kind of speculative
code this codebase's "narrow, real, proven" discipline avoids. This module
is the transport-agnostic evaluation core; a caller that already has (or
does not have) a `SupervisorReview` -- however it got one, or timed out
trying -- uses this to turn that into a safe verdict.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from crumblr.agent_gateway.contracts import ExternalSupervisorVerdict, SupervisorReview
from crumblr.domain.timeutils import UtcDatetime

NO_SUPERVISOR_RESPONSE = "NO_SUPERVISOR_RESPONSE"
"""No `SupervisorReview` arrived at all -- a timeout, a transport failure,
or a caller that chose not to request one. Not a distinct failure mode
from any other kind of missing review; all of them are simply
`review=None` to this module, deliberately -- the reason a review never
arrived is a transport-layer concern, not something this evaluation logic
should have to know about."""

PROPOSAL_ID_MISMATCH = "SUPERVISOR_REVIEW_PROPOSAL_ID_MISMATCH"
TRADE_INTENT_ID_MISMATCH = "SUPERVISOR_REVIEW_TRADE_INTENT_ID_MISMATCH"
TRADE_INTENT_HASH_MISMATCH = "SUPERVISOR_REVIEW_TRADE_INTENT_HASH_MISMATCH"
REVIEW_EXPIRED = "SUPERVISOR_REVIEW_EXPIRED"
REVIEW_SELF_REPORTED_UNKNOWN = "SUPERVISOR_REVIEW_SELF_REPORTED_UNKNOWN"


@dataclass(frozen=True)
class SupervisorReviewOutcome:
    """What asking an external Supervisor actually resolved to.

    `verdict` is never anything but `APPROVE`/`VETO` when `review` is
    populated with a genuinely bound, unexpired, non-`UNKNOWN` review --
    every other path is `UNKNOWN`, `review=None`, and a `reason_codes`
    tuple naming why.
    """

    verdict: ExternalSupervisorVerdict
    reason_codes: tuple[str, ...]
    review: SupervisorReview | None

    @property
    def is_veto(self) -> bool:
        return self.verdict is ExternalSupervisorVerdict.VETO


def _unknown(*reason_codes: str) -> SupervisorReviewOutcome:
    return SupervisorReviewOutcome(
        verdict=ExternalSupervisorVerdict.UNKNOWN, reason_codes=reason_codes, review=None
    )


def evaluate_supervisor_review(
    review: SupervisorReview | None,
    *,
    proposal_id: UUID,
    trade_intent_id: UUID,
    trade_intent_decision_hash: str,
    now: UtcDatetime,
) -> SupervisorReviewOutcome:
    """Turn a (possibly absent, possibly stale, possibly mismatched)
    `SupervisorReview` into a safe verdict for exactly the decision named
    by `proposal_id`/`trade_intent_id`/`trade_intent_decision_hash`.

    `review=None` covers every reason a review might not be in hand:
    timeout, transport error, malformed response already rejected before
    it became a `SupervisorReview`, or no review was ever requested.
    """
    if review is None:
        return _unknown(NO_SUPERVISOR_RESPONSE)
    if review.proposal_id != proposal_id:
        return _unknown(PROPOSAL_ID_MISMATCH)
    if review.trade_intent_id != trade_intent_id:
        return _unknown(TRADE_INTENT_ID_MISMATCH)
    if review.trade_intent_decision_hash != trade_intent_decision_hash:
        return _unknown(TRADE_INTENT_HASH_MISMATCH)
    if now >= review.expires_at_utc:
        return _unknown(REVIEW_EXPIRED)
    if review.verdict is ExternalSupervisorVerdict.UNKNOWN:
        return _unknown(REVIEW_SELF_REPORTED_UNKNOWN)

    return SupervisorReviewOutcome(
        verdict=review.verdict, reason_codes=review.reason_codes, review=review
    )
