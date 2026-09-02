"""The real gate on a flatten submission (core critical path item 7, ADR-009).

`risk/submission_gate.py`'s own reasoning, applied to the different later
question of whether an automatic *close* may be committed to. `close_all_
positions`/`order_send` stay technically impossible regardless of what
this module returns: `mt5_gateway/execution.py::OrderCheckMt5Gateway
.close_all_positions` always raises, unconditionally, and nothing in this
codebase calls `evaluate_flatten_gate` yet — there is no caller that acts
on a `True` result. This module makes the gate itself real and tested,
ahead of the engine that will one day call it, the same "build the
approved shape before the schedule pressure" reasoning `submission_gate.py`
was built under.

Eleven required conditions, **all** required simultaneously — any one
false or unknown closes the gate. Eight are `submission_gate.py`'s own
legs, reused because the same environment/account/AlgoTrading/risk-config/
`feedback_2_0` questions apply identically to a flatten; three are new:

    1. environment is DEMO-only (never LIVE)
    2. the observed account is genuinely a demo account
    3. the account is connected
    4. the terminal reports AlgoTrading enabled
    5. the position book was observed COMPLETE this pass
    6. reconciliation is not UNKNOWN (see §"not MATCHED" below)
    7. not halted for any reason *other than* OVERNIGHT_EXPOSURE
    8. a flatten is actually required right now
    9. the owner has approved this exact risk-config version
    10. flatten submission is explicitly enabled
    11. `feedback.2.0` has given its GO

**Condition 6 is "not UNKNOWN", deliberately never "MATCHED" — and this

holds regardless of which expectation reconciliation is given.** A
flatten is *triggered by* an open position; under *any* expectation the
platform can form, that position is either one the platform attributed
to itself (`MATCHED`) or one it did not (`MISMATCHED`). Requiring
`MATCHED` would refuse to flatten precisely the positions the platform
did *not* put there — opened by hand, by another EA, by an earlier
deployment — and an unattributable position past the deadline is
strictly *more* alarming than an attributed one, not less. Gating the
flatten on attribution would therefore be inverted, not merely
inconvenient. ADR-004 §5.3's real safety property is *observability* —
"flattening what you cannot see is how a hedge becomes a naked
position" — not agreement, and `UNKNOWN` is precisely the codified
"cannot see" (a missing, stale, or incomplete snapshot).
`MISMATCHED`-because-a-position-exists is the expected, informative
state and must not close this gate.

`flatten_once()` (`application/execution.py`) deliberately still passes
`ExpectedState.flat()` here rather than the derived expectation core
critical path item 8 makes available
(`ExpectedState.from_durable_exposure()`,
`review/adr/ADR-010-post-fill-reconciliation.md` §2.3) — switching would
be all-cost, no-benefit at the one moment (the deadline) this platform
cares most about not stalling: the derived expectation is provably
either identical to `flat()` or `flat()` plus an undetermined reason (an
unrelated request stuck at `SUBMISSION_STARTED`), which can only *newly
close* this gate, never newly open it. ADR-004 §5.3's "reconcile before
flattening" requirement is already fully satisfied by the *observed*-side
legs above (5, plus `reconcile()`'s own completeness checks) — identical
under either expectation — so keeping `flat()` here does not weaken §5.3
by even one leg. See ADR-010 §2.3 for the full reasoning and D-051 for
the trigger condition to revisit this.

**Condition 7 tolerates an `OVERNIGHT_EXPOSURE`-only halt, deliberately.**
The existing detection path (`risk/policies.py::_overnight_breach`,
`application/orchestration.py`/`application/live_decision.py
::_check_session_boundary`) already trips `OVERNIGHT_EXPOSURE` on the
identical condition this gate exists to resolve. A plain "not halted" leg
would make the gate permanently closed by the very condition it is meant
to answer — becoming flat is the *safe resolution* of an
overnight-exposure halt, not a further risk to refuse. A halt for any
*other* reason (drawdown, reconciliation mismatch, manual, MT5 connection
failure) still closes the gate: those say the platform's picture of the
world is untrustworthy, which is exactly ADR-004 §5.3's warning, and
applies to a flatten exactly as it does to any other action.

Conditions 9, 10 and 11 read `config.RiskConfig.approved_config_version`
/ `config.ExecutionConfig.flatten_submission_enabled` /
`config.ExecutionConfig.feedback_2_0_approved` — the same durable fields
`submission_gate.py` reads (`flatten_submission_enabled` is a fourth,
separate flag from `submission_enabled`, deliberately — ADR-004 §5.1's
decoupling requirement), all defaulting to the closed/unapproved state,
none set by any shipped config file. The gate is therefore proven closed
against `config/base.yaml`/`config/paper.yaml` as they exist today, not
merely "designed to be safe" — see `tests/unit/test_flatten_gate.py`.

**Idempotence is deliberately not a gate leg.** It is structural, in the
driver (`application/execution.py::ExecutionOrchestrator.flatten_once()`),
via the claim result and the `events[-1]` check — exactly where item 6
put it for `_recover_ambiguous_submission`, not in a gate. Keeping this
module pure and database-free is what makes it unit-testable without a
fixture.
"""

from __future__ import annotations

from dataclasses import dataclass

from crumblr.config import DEMO_ONLY_ENVIRONMENTS
from crumblr.domain.enums import Environment, ReasonCode, ReconciliationStatus
from crumblr.domain.models import AccountState
from crumblr.domain.timeutils import UtcDatetime
from crumblr.risk.kill_switch import KillSwitch

_TOLERATED_HALT_REASONS: frozenset[ReasonCode] = frozenset({ReasonCode.OVERNIGHT_EXPOSURE})
"""Halt reasons that do not, by themselves, close this gate. See the

module docstring's "condition 7" section for why: becoming flat is the
safe resolution of exactly this halt, not a further risk."""


@dataclass(frozen=True)
class FlattenGateDecision:
    open: bool
    reason_codes: tuple[ReasonCode, ...]

    def __post_init__(self) -> None:
        if not self.open and not self.reason_codes:
            raise ValueError("a closed gate must carry reason codes")
        if self.open and self.reason_codes:
            raise ValueError("an open gate must not carry reason codes")


@dataclass(frozen=True)
class FlattenGateContext:
    """Every signal the gate reads, all pre-observed — mirrors

    `SubmissionGateContext`'s pure-function style: this module fetches
    nothing itself, so it stays trivially testable and cannot silently
    develop a second opinion about account/broker state from whatever it
    reads on its own.
    """

    environment: Environment
    account: AccountState
    terminal_trade_allowed: bool
    position_book_complete: bool
    reconciliation_status: ReconciliationStatus
    kill_switch: KillSwitch
    flatten_required: bool
    risk_config_version: str
    approved_risk_config_version: str | None
    flatten_submission_enabled: bool
    feedback_2_0_approved: bool
    now: UtcDatetime


def evaluate_flatten_gate(context: FlattenGateContext) -> FlattenGateDecision:
    """Collects every failing reason rather than short-circuiting, matching

    `evaluate_submission_gate`/`evaluate_preflight_gate`'s own philosophy:
    an operator seeing one of eleven closed legs should see all of them,
    not just the first.
    """
    reasons: list[ReasonCode] = []

    if context.environment not in DEMO_ONLY_ENVIRONMENTS:
        reasons.append(ReasonCode.LIVE_EXECUTION_NOT_PERMITTED)

    if not context.account.is_demo:
        reasons.append(ReasonCode.LIVE_ACCOUNT_IN_PAPER_MODE)
    if not context.account.connected:
        reasons.append(ReasonCode.ACCOUNT_NOT_CONNECTED)

    if not context.terminal_trade_allowed:
        reasons.append(ReasonCode.ALGOTRADING_DISABLED)

    if not context.position_book_complete:
        reasons.append(ReasonCode.POSITION_BOOK_INCOMPLETE)

    if context.reconciliation_status is ReconciliationStatus.UNKNOWN:
        reasons.append(ReasonCode.RECONCILIATION_UNKNOWN)

    if context.kill_switch.is_halted and (
        set(context.kill_switch.active_reasons) - _TOLERATED_HALT_REASONS
    ):
        reasons.append(ReasonCode.SYSTEM_HALTED)

    if not context.flatten_required:
        reasons.append(ReasonCode.FLATTEN_NOT_REQUIRED)

    if context.approved_risk_config_version != context.risk_config_version:
        reasons.append(ReasonCode.RISK_POLICY_NOT_APPROVED)

    if not context.flatten_submission_enabled:
        reasons.append(ReasonCode.FLATTEN_SUBMISSION_NOT_ENABLED)

    if not context.feedback_2_0_approved:
        reasons.append(ReasonCode.FEEDBACK_2_0_NOT_APPROVED)

    if reasons:
        return FlattenGateDecision(open=False, reason_codes=tuple(reasons))
    return FlattenGateDecision(open=True, reason_codes=())
