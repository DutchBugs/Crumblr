"""Core critical path item 7's real gate: `risk/flatten_gate.py`.

Same discipline as `test_execution_gates.py::TestSubmissionGate` —
`flatten_context()` is fully open by default; every test below fails
exactly one leg to prove it alone closes the gate.
"""

from __future__ import annotations

from typing import Any

from crumblr.domain.enums import Environment, ReasonCode, ReconciliationStatus
from crumblr.risk.flatten_gate import (
    FlattenGateContext,
    evaluate_flatten_gate,
)
from crumblr.risk.kill_switch import KillSwitch
from tests.conftest import FIXED_NOW, make_account_state


def flatten_context(**overrides: Any) -> FlattenGateContext:
    """All eleven legs pass by default — every test overrides exactly the

    leg(s) it means to fail, proving the gate closes independently on
    each."""
    fields: dict[str, Any] = {
        "environment": Environment.PAPER,
        "account": make_account_state(),
        "terminal_trade_allowed": True,
        "position_book_complete": True,
        "reconciliation_status": ReconciliationStatus.MISMATCHED,
        "kill_switch": KillSwitch(),
        "flatten_required": True,
        "risk_config_version": "cfg-v1",
        "approved_risk_config_version": "cfg-v1",
        "flatten_submission_enabled": True,
        "feedback_2_0_approved": True,
        "now": FIXED_NOW,
    }
    fields.update(overrides)
    return FlattenGateContext(**fields)


def _halted_switch(reason: ReasonCode) -> KillSwitch:
    switch = KillSwitch()
    switch.trip(reason_codes=(reason,), tripped_by="test", occurred_at_utc=FIXED_NOW)
    return switch


class TestFlattenGate:
    """Core critical path item 7 (ADR-009): eleven conditions, all required

    simultaneously."""

    def test_a_fully_satisfied_context_opens_the_gate(self) -> None:
        decision = evaluate_flatten_gate(flatten_context())
        assert decision.open is True
        assert decision.reason_codes == ()

    def test_live_environment_closes_it(self) -> None:
        decision = evaluate_flatten_gate(flatten_context(environment=Environment.LIVE))
        assert decision.open is False
        assert ReasonCode.LIVE_EXECUTION_NOT_PERMITTED in decision.reason_codes

    def test_a_non_demo_account_closes_it(self) -> None:
        decision = evaluate_flatten_gate(flatten_context(account=make_account_state(is_demo=False)))
        assert decision.open is False
        assert ReasonCode.LIVE_ACCOUNT_IN_PAPER_MODE in decision.reason_codes

    def test_a_disconnected_account_closes_it(self) -> None:
        decision = evaluate_flatten_gate(
            flatten_context(account=make_account_state(connected=False))
        )
        assert decision.open is False
        assert ReasonCode.ACCOUNT_NOT_CONNECTED in decision.reason_codes

    def test_algotrading_disabled_closes_it(self) -> None:
        decision = evaluate_flatten_gate(flatten_context(terminal_trade_allowed=False))
        assert decision.open is False
        assert ReasonCode.ALGOTRADING_DISABLED in decision.reason_codes

    def test_an_incomplete_position_book_closes_it(self) -> None:
        decision = evaluate_flatten_gate(flatten_context(position_book_complete=False))
        assert decision.open is False
        assert ReasonCode.POSITION_BOOK_INCOMPLETE in decision.reason_codes

    def test_unknown_reconciliation_closes_it(self) -> None:
        decision = evaluate_flatten_gate(
            flatten_context(reconciliation_status=ReconciliationStatus.UNKNOWN)
        )
        assert decision.open is False
        assert ReasonCode.RECONCILIATION_UNKNOWN in decision.reason_codes

    def test_a_mismatched_reconciliation_does_not_close_the_gate(self) -> None:
        """The asymmetry with `submission_gate.py`, guarded directly —

        and expectation-independent, not merely a consequence of
        `flat()` being the only expectation this platform could once
        form (core critical path item 8 made a second one available; see
        `risk/flatten_gate.py`'s own module docstring and
        `review/adr/ADR-010-post-fill-reconciliation.md` §2.3). Under
        *any* expectation, a position past the flatten deadline is
        either attributed (MATCHED) or not (MISMATCHED) — requiring
        MATCHED would refuse to flatten precisely the positions the
        platform did not put there, which is more alarming, not less. A
        future "fix" that reintroduces a MATCHED requirement should turn
        this test red, not the default-open context above.
        """
        decision = evaluate_flatten_gate(
            flatten_context(reconciliation_status=ReconciliationStatus.MISMATCHED)
        )
        assert decision.open is True
        assert ReasonCode.RECONCILIATION_MISMATCH not in decision.reason_codes

    def test_an_overnight_exposure_halt_does_not_close_the_gate(self) -> None:
        """The detection path already trips `OVERNIGHT_EXPOSURE` on the

        identical condition this gate exists to resolve — a plain
        "not halted" leg would make the gate permanently closed by the
        very condition it is meant to answer."""
        decision = evaluate_flatten_gate(
            flatten_context(kill_switch=_halted_switch(ReasonCode.OVERNIGHT_EXPOSURE))
        )
        assert decision.open is True
        assert ReasonCode.SYSTEM_HALTED not in decision.reason_codes

    def test_a_halt_for_any_other_reason_closes_the_gate(self) -> None:
        decision = evaluate_flatten_gate(
            flatten_context(kill_switch=_halted_switch(ReasonCode.MAX_DRAWDOWN))
        )
        assert decision.open is False
        assert ReasonCode.SYSTEM_HALTED in decision.reason_codes

    def test_flatten_not_required_closes_it(self) -> None:
        decision = evaluate_flatten_gate(flatten_context(flatten_required=False))
        assert decision.open is False
        assert ReasonCode.FLATTEN_NOT_REQUIRED in decision.reason_codes

    def test_an_unapproved_risk_config_version_closes_it(self) -> None:
        decision = evaluate_flatten_gate(flatten_context(approved_risk_config_version=None))
        assert decision.open is False
        assert ReasonCode.RISK_POLICY_NOT_APPROVED in decision.reason_codes

    def test_a_risk_config_version_mismatch_closes_it(self) -> None:
        decision = evaluate_flatten_gate(flatten_context(approved_risk_config_version="cfg-v0"))
        assert decision.open is False
        assert ReasonCode.RISK_POLICY_NOT_APPROVED in decision.reason_codes

    def test_flatten_submission_not_enabled_closes_it(self) -> None:
        decision = evaluate_flatten_gate(flatten_context(flatten_submission_enabled=False))
        assert decision.open is False
        assert ReasonCode.FLATTEN_SUBMISSION_NOT_ENABLED in decision.reason_codes

    def test_feedback_2_0_not_approved_closes_it(self) -> None:
        decision = evaluate_flatten_gate(flatten_context(feedback_2_0_approved=False))
        assert decision.open is False
        assert ReasonCode.FEEDBACK_2_0_NOT_APPROVED in decision.reason_codes

    def test_every_failing_leg_is_reported_not_just_the_first(self) -> None:
        decision = evaluate_flatten_gate(
            flatten_context(
                environment=Environment.LIVE,
                flatten_submission_enabled=False,
                feedback_2_0_approved=False,
            )
        )
        assert ReasonCode.LIVE_EXECUTION_NOT_PERMITTED in decision.reason_codes
        assert ReasonCode.FLATTEN_SUBMISSION_NOT_ENABLED in decision.reason_codes
        assert ReasonCode.FEEDBACK_2_0_NOT_APPROVED in decision.reason_codes

    def test_decision_is_internally_consistent(self) -> None:
        open_decision = evaluate_flatten_gate(flatten_context())
        assert open_decision.open is True
        assert open_decision.reason_codes == ()

        closed_decision = evaluate_flatten_gate(flatten_context(feedback_2_0_approved=False))
        assert closed_decision.open is False
        assert closed_decision.reason_codes != ()

    def test_the_gate_is_closed_against_the_actual_shipped_config(self) -> None:
        """The concrete proof, not just the design intent: build a context

        from `load_config`'s real, current `config/paper.yaml` values —
        the gate must stay closed, because none of the four durable
        approval fields are set in any shipped config file."""
        from pathlib import Path

        from crumblr.config import load_config

        repo_config_dir = Path(__file__).resolve().parents[2] / "config"
        config = load_config(Environment.PAPER, config_dir=repo_config_dir)
        decision = evaluate_flatten_gate(
            flatten_context(
                environment=config.environment,
                risk_config_version=config.config_version,
                approved_risk_config_version=config.risk.approved_config_version,
                flatten_submission_enabled=config.execution.flatten_submission_enabled,
                feedback_2_0_approved=config.execution.feedback_2_0_approved,
            )
        )
        assert decision.open is False
