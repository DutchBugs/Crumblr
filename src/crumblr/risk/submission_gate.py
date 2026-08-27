"""The future gate on `order_send` itself — design-only in Phase 4.

`review/PHASE4_PLAN_REVIEW_GO_WITH_TWEAKS.md` point 2: this is deliberately
**not** built as working logic yet, and deliberately kept separate from
`risk/execution_preflight_gate.py`. `ExecutionPreflightGate` governs whether
the non-sending chain (fresh observation → reconciliation → FINAL Risk →
`ApprovedOrder` → `order_check`) may run. `SubmissionGate` is the different,
later question — whether a real `order_send` may run — and conflating the
two behind one flag was explicitly rejected.

`order_send` stays technically impossible in Phase 4 regardless of this
module: `mt5_gateway/execution.py::OrderCheckMt5Gateway.order_send` always
raises, unconditionally, with no config read anywhere in that method. This
module exists only to name the shape the real gate will need later — F-049's
full multi-gate checklist — so that work has a landing spot instead of being
invented from scratch when M5 arrives. Calling `evaluate_submission_gate`
today always returns closed; there is no config value anywhere that opens
it.

Required before this stops being a stub (`review/DEVIATIONS.md`, and the
plan review's "Later, vóór eerste DEMO-order" list):

    automatic flatten submission
    submission idempotence / ambiguous-result recovery
    post-execution reconciliation
    owner-approved risk policy
    last-entry cutoff, mandatory flatten deadline
    HALT-reset authority
    terminal AlgoTrading gate
    explicit execution enablement
    feedback.2.0 GO
"""

from __future__ import annotations

from dataclasses import dataclass

from crumblr.domain.enums import ReasonCode


@dataclass(frozen=True)
class SubmissionGateDecision:
    open: bool
    reason_codes: tuple[ReasonCode, ...]

    def __post_init__(self) -> None:
        if not self.open and not self.reason_codes:
            raise ValueError("a closed gate must carry reason codes")
        if self.open and self.reason_codes:
            raise ValueError("an open gate must not carry reason codes")


def evaluate_submission_gate() -> SubmissionGateDecision:
    """Always closed. Not yet implemented — see the module docstring.

    Deliberately takes no arguments: there is nothing yet that could open
    it, so there is nothing yet worth threading through a signature that
    would only be discarded.
    """
    return SubmissionGateDecision(
        open=False,
        reason_codes=(ReasonCode.SUBMISSION_GATE_NOT_IMPLEMENTED,),
    )
