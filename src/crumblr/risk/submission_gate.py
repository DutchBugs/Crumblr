"""The real gate on `order_send` itself (F-049, review 1.15 §14).

`review/PHASE4_PLAN_REVIEW_GO_WITH_TWEAKS.md` point 2: this is deliberately
kept separate from `risk/execution_preflight_gate.py`. `ExecutionPreflightGate`
governs whether the non-sending chain (fresh observation → reconciliation →
FINAL Risk → `ApprovedOrder` → `order_check`) may run. `SubmissionGate` is
the different, later question — whether a real `order_send` may run — and
conflating the two behind one flag was explicitly rejected.

`order_send` stays technically impossible regardless of what this module
returns: `mt5_gateway/execution.py::OrderCheckMt5Gateway.order_send` always
raises, unconditionally, with no config read anywhere in that method, and
nothing in this codebase calls `evaluate_submission_gate` yet — there is no
`SubmissionOrchestrator`. This module makes the gate itself real and
tested, ahead of the engine that will one day call it, the same "build the
approved shape before the schedule pressure" reasoning `ApprovedOrder`/
`ExecutionResult` were built under back when they were still unbuilt.

Ten required conditions (review 1.15 §14, `review/FEEDBACK.md` F-049;
condition 10 added by Phase B item B7, `review/adr/ADR-017-account
-reference-pin.md`), **all** required simultaneously — any one false or
unknown closes the gate:

    1. environment is DEMO-only (never LIVE)
    2. the observed account is genuinely a demo account
    3. reconciliation reads MATCHED
    4. market data is fresh and GOOD quality
    5. safety state is RUNNING
    6. the owner has approved this exact risk-config version
    7. the execution adapter is explicitly enabled
    8. the terminal reports AlgoTrading enabled
    9. `feedback.2.0` has given its GO
    10. the observed account is the exact owner-approved canary account

Conditions 6, 7 and 9 read `config.RiskConfig.approved_config_version` /
`config.ExecutionConfig.submission_enabled` / `config.ExecutionConfig
.feedback_2_0_approved` — new durable fields, all defaulting to the
closed/unapproved state, none set by any shipped config file. Condition
10 reads `config.ExecutionConfig.approved_canary_account_ref` the same
way — a `login_hash`-style fingerprint, `None` in every shipped config.
The gate is therefore proven closed against `config/base.yaml`/
`config/paper.yaml` as they exist today, not merely "designed to be
safe" — see `tests/unit/test_execution_gates.py::TestSubmissionGate`.
"""

from __future__ import annotations

from dataclasses import dataclass

from crumblr.config import DEMO_ONLY_ENVIRONMENTS
from crumblr.domain.enums import DataQuality, Environment, ReasonCode, ReconciliationStatus
from crumblr.domain.models import AccountState, MarketTick
from crumblr.domain.timeutils import UtcDatetime
from crumblr.risk.kill_switch import KillSwitch


@dataclass(frozen=True)
class SubmissionGateDecision:
    open: bool
    reason_codes: tuple[ReasonCode, ...]

    def __post_init__(self) -> None:
        if not self.open and not self.reason_codes:
            raise ValueError("a closed gate must carry reason codes")
        if self.open and self.reason_codes:
            raise ValueError("an open gate must not carry reason codes")


@dataclass(frozen=True)
class SubmissionGateContext:
    """Every signal the gate reads, all pre-observed — mirrors

    `evaluate_preflight_gate`'s pure-function style: this module fetches
    nothing itself, so it stays trivially testable and cannot silently
    develop a second opinion about account/broker state from whatever it
    reads on its own.
    """

    environment: Environment
    account: AccountState
    reconciliation_status: ReconciliationStatus
    fresh_tick: MarketTick | None
    max_market_data_age_ms: int
    kill_switch: KillSwitch
    risk_config_version: str
    approved_risk_config_version: str | None
    submission_enabled: bool
    terminal_trade_allowed: bool
    feedback_2_0_approved: bool
    approved_account_ref: str | None
    now: UtcDatetime


def evaluate_submission_gate(context: SubmissionGateContext) -> SubmissionGateDecision:
    """Collects every failing reason rather than short-circuiting, matching

    `evaluate_preflight_gate`/`risk/policies.py::evaluate()`'s own
    philosophy: an operator seeing one of ten closed legs should see all
    of them, not just the first.
    """
    reasons: list[ReasonCode] = []

    if context.environment not in DEMO_ONLY_ENVIRONMENTS:
        reasons.append(ReasonCode.LIVE_EXECUTION_NOT_PERMITTED)

    if not context.account.is_demo:
        reasons.append(ReasonCode.LIVE_ACCOUNT_IN_PAPER_MODE)
    if not context.account.connected:
        reasons.append(ReasonCode.ACCOUNT_NOT_CONNECTED)

    if context.reconciliation_status is ReconciliationStatus.MISMATCHED:
        reasons.append(ReasonCode.RECONCILIATION_MISMATCH)
    elif context.reconciliation_status is ReconciliationStatus.UNKNOWN:
        reasons.append(ReasonCode.RECONCILIATION_UNKNOWN)

    if context.fresh_tick is None:
        reasons.append(ReasonCode.STALE_MARKET_DATA)
    else:
        age_ms = (context.now - context.fresh_tick.event_time_utc).total_seconds() * 1000
        if age_ms > context.max_market_data_age_ms:
            reasons.append(ReasonCode.STALE_MARKET_DATA)
        if context.fresh_tick.data_quality is not DataQuality.GOOD:
            reasons.append(ReasonCode.INVALID_QUOTE)

    if context.kill_switch.is_halted:
        reasons.append(ReasonCode.SYSTEM_HALTED)

    if context.approved_risk_config_version != context.risk_config_version:
        reasons.append(ReasonCode.RISK_POLICY_NOT_APPROVED)

    if not context.submission_enabled:
        reasons.append(ReasonCode.EXECUTION_NOT_EXPLICITLY_ENABLED)

    if not context.terminal_trade_allowed:
        reasons.append(ReasonCode.ALGOTRADING_DISABLED)

    if not context.feedback_2_0_approved:
        reasons.append(ReasonCode.FEEDBACK_2_0_NOT_APPROVED)

    if context.approved_account_ref != context.account.login_hash:
        reasons.append(ReasonCode.WRONG_ACCOUNT)

    if reasons:
        return SubmissionGateDecision(open=False, reason_codes=tuple(reasons))
    return SubmissionGateDecision(open=True, reason_codes=())
