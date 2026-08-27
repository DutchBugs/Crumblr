"""Whether a sealed, approved decision may still be acted on (Phase 4).

`review/PHASE4_PLAN_REVIEW_GO_WITH_TWEAKS.md` point 6, non-negotiable: **old
shadow-mode approvals must never become retroactively executable.** A
`DecisionCapsule` sealed while execution was disabled — which is every
capsule that exists today — must not suddenly become eligible the moment a
human later turns execution on. This module is the cheap, first gate the
Execution Service runs, before any of the expensive fresh-observation,
reconciliation or FINAL Risk work: a capsule that fails here is never worth
that work in the first place.

Nothing here duplicates `risk/policies.py::evaluate()`'s intent-expiry check
— it is re-run there too, deliberately, as the authoritative enforcement.
This module exists to filter early, not to be the only place that checks.
"""

from __future__ import annotations

from dataclasses import dataclass

from crumblr.domain.enums import ReasonCode
from crumblr.domain.models import DecisionCapsule
from crumblr.domain.timeutils import UtcDatetime
from crumblr.risk.trading_window import IntradayPolicy, permits_new_entry


@dataclass(frozen=True)
class EligibilityDecision:
    eligible: bool
    reason_codes: tuple[ReasonCode, ...]

    def __post_init__(self) -> None:
        if not self.eligible and not self.reason_codes:
            raise ValueError("an ineligible decision must carry reason codes")
        if self.eligible and self.reason_codes:
            raise ValueError("an eligible decision must not carry reason codes")


def evaluate_execution_eligibility(
    capsule: DecisionCapsule,
    *,
    activation_watermark: UtcDatetime | None,
    now: UtcDatetime,
    current_strategy_version: str,
    current_risk_config_version: str,
    intraday: IntradayPolicy,
) -> EligibilityDecision:
    """Whether `capsule` may enter the execution preflight chain at all.

    `activation_watermark` is a human-set, durably persisted value naming
    when execution eligibility began — never inferred, never defaulted to
    "now" (that would silently make every past capsule eligible the instant
    it is set). Passing `None` means no watermark has ever been set, which
    makes every capsule ineligible; there is no config path that makes this
    check pass on its own.
    """
    if capsule.trade_intent is None or capsule.risk_decision is None:
        raise ValueError(
            f"capsule {capsule.capsule_id} has no trade_intent/risk_decision to "
            "evaluate execution eligibility for — only a capsule with an "
            "intent-time PASS should ever reach this check"
        )

    reasons: list[ReasonCode] = []

    if activation_watermark is None or capsule.occurred_at_utc < activation_watermark:
        reasons.append(ReasonCode.DECISION_PREDATES_EXECUTION_ACTIVATION)

    if (
        capsule.strategy_version != current_strategy_version
        or capsule.risk_config_version != current_risk_config_version
    ):
        reasons.append(ReasonCode.STRATEGY_VERSION_NOT_CURRENT)

    if capsule.trade_intent.is_expired(at=now):
        reasons.append(ReasonCode.INTENT_EXPIRED)

    if not permits_new_entry(now, intraday):
        reasons.append(ReasonCode.SESSION_BLACKOUT)

    if reasons:
        return EligibilityDecision(eligible=False, reason_codes=tuple(reasons))
    return EligibilityDecision(eligible=True, reason_codes=())
