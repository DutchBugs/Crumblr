"""Whether the execution preflight chain may run at all (Phase 4).

`review/PHASE4_PLAN_REVIEW_GO_WITH_TWEAKS.md` point 2, non-negotiable: this
gate is deliberately **not** the same thing as the future `SubmissionGate`
(`risk/submission_gate.py`) that will one day govern `order_send`. This one
governs a narrower question — may the chain

    fresh observation → reconciliation → FINAL Risk → ApprovedOrder → order_check

run at all for this decision — and stays scoped to that. It is not where
"is this account/environment/reconciliation state safe enough to submit a
real order" gets decided; that is `SubmissionGate`'s job, later, and
conflating the two behind one flag is exactly what the plan review
forbade.
"""

from __future__ import annotations

from dataclasses import dataclass

from crumblr.domain.enums import Environment, ReasonCode
from crumblr.risk.kill_switch import KillSwitch


@dataclass(frozen=True)
class PreflightGateDecision:
    open: bool
    reason_codes: tuple[ReasonCode, ...]

    def __post_init__(self) -> None:
        if not self.open and not self.reason_codes:
            raise ValueError("a closed gate must carry reason codes")
        if self.open and self.reason_codes:
            raise ValueError("an open gate must not carry reason codes")


def evaluate_preflight_gate(
    *,
    environment: Environment,
    canonical_symbol: str,
    allowed_symbols: frozenset[str],
    kill_switch: KillSwitch,
) -> PreflightGateDecision:
    """Collects every failing reason rather than short-circuiting, matching

    `risk/policies.py::evaluate()`'s own philosophy: an operator seeing one
    of several closed legs should see all of them, not just the first.
    """
    reasons: list[ReasonCode] = []

    if environment is Environment.LIVE:
        # No live trading, structurally, at every layer that could reach one
        # — not only in `config/live.yaml` not existing (CLAUDE.md §4).
        reasons.append(ReasonCode.LIVE_EXECUTION_NOT_PERMITTED)
    if canonical_symbol not in allowed_symbols:
        reasons.append(ReasonCode.SYMBOL_NOT_ALLOWED)
    if kill_switch.is_halted:
        reasons.append(ReasonCode.SYSTEM_HALTED)

    if reasons:
        return PreflightGateDecision(open=False, reason_codes=tuple(reasons))
    return PreflightGateDecision(open=True, reason_codes=())
