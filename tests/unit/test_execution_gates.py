"""Phase 4's cheap pre-filters: execution eligibility and the preflight gate.

Neither of these is the deterministic Risk Engine (`risk/policies.py`) — they
run before it, deciding whether the expensive fresh-observation/
reconciliation/FINAL-Risk chain is even worth attempting for a given sealed
decision. `test_risk_engine.py::TestExecutionTimeRevalidation` covers the
Risk Engine itself.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest

from crumblr.domain.enums import Environment, ReasonCode
from crumblr.domain.models import DecisionCapsule
from crumblr.risk.execution_eligibility import (
    EligibilityDecision,
    evaluate_execution_eligibility,
)
from crumblr.risk.execution_preflight_gate import evaluate_preflight_gate
from crumblr.risk.kill_switch import KillSwitch
from crumblr.risk.submission_gate import evaluate_submission_gate
from crumblr.risk.trading_window import IntradayPolicy
from tests.conftest import FIXED_NOW, make_intent, make_risk_decision, make_supervisor_decision

STRATEGY_VERSION = "0.1.0"
RISK_CONFIG_VERSION = "cfg-v1"


def capsule(**overrides: Any) -> DecisionCapsule:
    intent = overrides.pop("trade_intent", None) or make_intent()
    fields: dict[str, Any] = {
        "capsule_id": uuid4(),
        "occurred_at_utc": FIXED_NOW,
        "correlation_id": uuid4(),
        "canonical_symbol": "EUR/USD",
        "broker_symbol": "EURUSD",
        "market_snapshot_id": uuid4(),
        "feature_set_version": "features-v1",
        "feature_values_hash": "abc123",
        "strategy_version": STRATEGY_VERSION,
        "model_version": None,
        "trade_intent": intent,
        "risk_config_version": RISK_CONFIG_VERSION,
        "risk_decision": make_risk_decision(intent.intent_id),
        "supervisor_decision": make_supervisor_decision(intent.intent_id),
        "code_commit": "deadbeef",
        "environment": Environment.PAPER,
    }
    fields.update(overrides)
    return DecisionCapsule(**fields)


class TestExecutionEligibility:
    def test_no_watermark_ever_set_is_never_eligible(self) -> None:
        decision = evaluate_execution_eligibility(
            capsule(),
            activation_watermark=None,
            now=FIXED_NOW,
            current_strategy_version=STRATEGY_VERSION,
            current_risk_config_version=RISK_CONFIG_VERSION,
            intraday=IntradayPolicy.disabled(),
        )
        assert decision.eligible is False
        assert ReasonCode.DECISION_PREDATES_EXECUTION_ACTIVATION in decision.reason_codes

    def test_a_capsule_sealed_before_the_watermark_is_ineligible(self) -> None:
        decision = evaluate_execution_eligibility(
            capsule(occurred_at_utc=FIXED_NOW),
            activation_watermark=FIXED_NOW + timedelta(seconds=1),
            now=FIXED_NOW + timedelta(seconds=2),
            current_strategy_version=STRATEGY_VERSION,
            current_risk_config_version=RISK_CONFIG_VERSION,
            intraday=IntradayPolicy.disabled(),
        )
        assert decision.eligible is False
        assert ReasonCode.DECISION_PREDATES_EXECUTION_ACTIVATION in decision.reason_codes

    def test_a_capsule_sealed_after_the_watermark_passes_that_leg(self) -> None:
        decision = evaluate_execution_eligibility(
            capsule(
                occurred_at_utc=FIXED_NOW,
                trade_intent=make_intent(expires_at_utc=FIXED_NOW + timedelta(minutes=5)),
            ),
            activation_watermark=FIXED_NOW - timedelta(seconds=1),
            now=FIXED_NOW,
            current_strategy_version=STRATEGY_VERSION,
            current_risk_config_version=RISK_CONFIG_VERSION,
            intraday=IntradayPolicy.disabled(),
        )
        assert decision.eligible is True
        assert decision.reason_codes == ()

    def test_a_superseded_strategy_version_is_ineligible(self) -> None:
        decision = evaluate_execution_eligibility(
            capsule(occurred_at_utc=FIXED_NOW, strategy_version="0.0.9"),
            activation_watermark=FIXED_NOW - timedelta(seconds=1),
            now=FIXED_NOW,
            current_strategy_version=STRATEGY_VERSION,
            current_risk_config_version=RISK_CONFIG_VERSION,
            intraday=IntradayPolicy.disabled(),
        )
        assert decision.eligible is False
        assert ReasonCode.STRATEGY_VERSION_NOT_CURRENT in decision.reason_codes

    def test_a_superseded_risk_config_version_is_ineligible(self) -> None:
        decision = evaluate_execution_eligibility(
            capsule(occurred_at_utc=FIXED_NOW, risk_config_version="cfg-v0"),
            activation_watermark=FIXED_NOW - timedelta(seconds=1),
            now=FIXED_NOW,
            current_strategy_version=STRATEGY_VERSION,
            current_risk_config_version=RISK_CONFIG_VERSION,
            intraday=IntradayPolicy.disabled(),
        )
        assert decision.eligible is False
        assert ReasonCode.STRATEGY_VERSION_NOT_CURRENT in decision.reason_codes

    def test_an_expired_intent_is_ineligible(self) -> None:
        intent = make_intent(
            created_at_utc=FIXED_NOW, expires_at_utc=FIXED_NOW + timedelta(seconds=30)
        )
        decision = evaluate_execution_eligibility(
            capsule(occurred_at_utc=FIXED_NOW, trade_intent=intent),
            activation_watermark=FIXED_NOW - timedelta(seconds=1),
            now=FIXED_NOW + timedelta(minutes=5),
            current_strategy_version=STRATEGY_VERSION,
            current_risk_config_version=RISK_CONFIG_VERSION,
            intraday=IntradayPolicy.disabled(),
        )
        assert decision.eligible is False
        assert ReasonCode.INTENT_EXPIRED in decision.reason_codes

    def test_every_failing_leg_is_reported_not_just_the_first(self) -> None:
        decision = evaluate_execution_eligibility(
            capsule(occurred_at_utc=FIXED_NOW, strategy_version="0.0.9"),
            activation_watermark=None,
            now=FIXED_NOW,
            current_strategy_version=STRATEGY_VERSION,
            current_risk_config_version=RISK_CONFIG_VERSION,
            intraday=IntradayPolicy.disabled(),
        )
        assert ReasonCode.DECISION_PREDATES_EXECUTION_ACTIVATION in decision.reason_codes
        assert ReasonCode.STRATEGY_VERSION_NOT_CURRENT in decision.reason_codes

    def test_a_capsule_with_no_trade_intent_is_a_programming_error(self) -> None:
        no_signal = capsule(trade_intent=None, risk_decision=None, supervisor_decision=None)
        with pytest.raises(ValueError, match="trade_intent"):
            evaluate_execution_eligibility(
                no_signal,
                activation_watermark=FIXED_NOW,
                now=FIXED_NOW,
                current_strategy_version=STRATEGY_VERSION,
                current_risk_config_version=RISK_CONFIG_VERSION,
                intraday=IntradayPolicy.disabled(),
            )

    def test_eligibility_decision_is_internally_consistent(self) -> None:
        with pytest.raises(ValueError):
            EligibilityDecision(eligible=False, reason_codes=())
        with pytest.raises(ValueError):
            EligibilityDecision(eligible=True, reason_codes=(ReasonCode.INTENT_EXPIRED,))


class TestPreflightGate:
    def test_a_clean_environment_and_symbol_open_the_gate(self) -> None:
        decision = evaluate_preflight_gate(
            environment=Environment.PAPER,
            canonical_symbol="EUR/USD",
            allowed_symbols=frozenset({"EUR/USD"}),
            kill_switch=KillSwitch(),
        )
        assert decision.open is True
        assert decision.reason_codes == ()

    def test_live_environment_is_structurally_refused(self) -> None:
        decision = evaluate_preflight_gate(
            environment=Environment.LIVE,
            canonical_symbol="EUR/USD",
            allowed_symbols=frozenset({"EUR/USD"}),
            kill_switch=KillSwitch(),
        )
        assert decision.open is False
        assert ReasonCode.LIVE_EXECUTION_NOT_PERMITTED in decision.reason_codes

    def test_a_symbol_outside_the_allowlist_is_refused(self) -> None:
        decision = evaluate_preflight_gate(
            environment=Environment.PAPER,
            canonical_symbol="GBP/USD",
            allowed_symbols=frozenset({"EUR/USD"}),
            kill_switch=KillSwitch(),
        )
        assert decision.open is False
        assert ReasonCode.SYMBOL_NOT_ALLOWED in decision.reason_codes

    def test_a_halted_kill_switch_closes_the_gate(self) -> None:
        kill_switch = KillSwitch()
        kill_switch.trip(
            reason_codes=(ReasonCode.MANUAL_HALT,), tripped_by="operator", occurred_at_utc=FIXED_NOW
        )
        decision = evaluate_preflight_gate(
            environment=Environment.PAPER,
            canonical_symbol="EUR/USD",
            allowed_symbols=frozenset({"EUR/USD"}),
            kill_switch=kill_switch,
        )
        assert decision.open is False
        assert ReasonCode.SYSTEM_HALTED in decision.reason_codes

    def test_every_failing_leg_is_reported_not_just_the_first(self) -> None:
        decision = evaluate_preflight_gate(
            environment=Environment.LIVE,
            canonical_symbol="GBP/USD",
            allowed_symbols=frozenset({"EUR/USD"}),
            kill_switch=KillSwitch(),
        )
        assert ReasonCode.LIVE_EXECUTION_NOT_PERMITTED in decision.reason_codes
        assert ReasonCode.SYMBOL_NOT_ALLOWED in decision.reason_codes


class TestSubmissionGateStub:
    """Design-only this slice: it must always refuse, unconditionally."""

    def test_it_always_returns_closed(self) -> None:
        decision = evaluate_submission_gate()
        assert decision.open is False
        assert ReasonCode.SUBMISSION_GATE_NOT_IMPLEMENTED in decision.reason_codes

    def test_it_takes_no_arguments_because_nothing_could_open_it_yet(self) -> None:
        import inspect

        signature = inspect.signature(evaluate_submission_gate)
        assert not signature.parameters
