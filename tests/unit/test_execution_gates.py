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

from crumblr.domain.enums import DataQuality, Environment, ReasonCode, ReconciliationStatus
from crumblr.domain.models import DecisionCapsule, MarketTick
from crumblr.risk.execution_eligibility import (
    EligibilityDecision,
    evaluate_execution_eligibility,
)
from crumblr.risk.execution_preflight_gate import evaluate_preflight_gate
from crumblr.risk.kill_switch import KillSwitch
from crumblr.risk.submission_gate import (
    SubmissionGateContext,
    evaluate_submission_gate,
)
from crumblr.risk.trading_window import IntradayPolicy
from tests.conftest import (
    FIXED_NOW,
    make_account_state,
    make_intent,
    make_risk_decision,
    make_supervisor_decision,
)

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


def fresh_tick(**overrides: Any) -> MarketTick:
    fields: dict[str, Any] = {
        "tick_id": uuid4(),
        "source": "test",
        "canonical_symbol": "EUR/USD",
        "broker_symbol": "EURUSD",
        "event_time_utc": FIXED_NOW,
        "received_time_utc": FIXED_NOW,
        "bid": "1.08500",
        "ask": "1.08512",
        "data_quality": DataQuality.GOOD,
    }
    fields.update(overrides)
    return MarketTick(**fields)


def submission_context(**overrides: Any) -> SubmissionGateContext:
    """All ten legs pass by default — every test overrides exactly the

    leg(s) it means to fail, proving the gate closes independently on
    each. `approved_account_ref` is derived from the fixture's own
    `account` rather than hardcoded, so it can never silently drift from
    `make_account_state()`'s own defaults (Phase B item B7)."""
    account = make_account_state()
    fields: dict[str, Any] = {
        "environment": Environment.PAPER,
        "account": account,
        "reconciliation_status": ReconciliationStatus.MATCHED,
        "fresh_tick": fresh_tick(),
        "max_market_data_age_ms": 2_000,
        "kill_switch": KillSwitch(),
        "risk_config_version": "cfg-v1",
        "approved_risk_config_version": "cfg-v1",
        "submission_enabled": True,
        "terminal_trade_allowed": True,
        "feedback_2_0_approved": True,
        "approved_account_ref": account.login_hash,
        "now": FIXED_NOW,
    }
    fields.update(overrides)
    return SubmissionGateContext(**fields)


class TestSubmissionGate:
    """F-049: ten conditions, all required simultaneously (review 1.15

    §14; condition 10 added by Phase B item B7,
    `review/adr/ADR-017-account-reference-pin.md`).
    `submission_context()` is fully open by default; every test below
    fails exactly one leg to prove it alone closes the gate — the same
    "one leg failing closes the whole gate" discipline `TestPreflightGate`
    already exercises above."""

    def test_a_fully_satisfied_context_opens_the_gate(self) -> None:
        decision = evaluate_submission_gate(submission_context())
        assert decision.open is True
        assert decision.reason_codes == ()

    def test_live_environment_closes_it(self) -> None:
        decision = evaluate_submission_gate(submission_context(environment=Environment.LIVE))
        assert decision.open is False
        assert ReasonCode.LIVE_EXECUTION_NOT_PERMITTED in decision.reason_codes

    def test_a_non_demo_account_closes_it(self) -> None:
        decision = evaluate_submission_gate(
            submission_context(account=make_account_state(is_demo=False))
        )
        assert decision.open is False
        assert ReasonCode.LIVE_ACCOUNT_IN_PAPER_MODE in decision.reason_codes

    def test_a_disconnected_account_closes_it(self) -> None:
        decision = evaluate_submission_gate(
            submission_context(account=make_account_state(connected=False))
        )
        assert decision.open is False
        assert ReasonCode.ACCOUNT_NOT_CONNECTED in decision.reason_codes

    def test_mismatched_reconciliation_closes_it(self) -> None:
        decision = evaluate_submission_gate(
            submission_context(reconciliation_status=ReconciliationStatus.MISMATCHED)
        )
        assert decision.open is False
        assert ReasonCode.RECONCILIATION_MISMATCH in decision.reason_codes

    def test_unknown_reconciliation_closes_it(self) -> None:
        decision = evaluate_submission_gate(
            submission_context(reconciliation_status=ReconciliationStatus.UNKNOWN)
        )
        assert decision.open is False
        assert ReasonCode.RECONCILIATION_UNKNOWN in decision.reason_codes

    def test_no_fresh_tick_closes_it(self) -> None:
        decision = evaluate_submission_gate(submission_context(fresh_tick=None))
        assert decision.open is False
        assert ReasonCode.STALE_MARKET_DATA in decision.reason_codes

    def test_a_stale_tick_closes_it(self) -> None:
        stale = fresh_tick(event_time_utc=FIXED_NOW - timedelta(seconds=30))
        decision = evaluate_submission_gate(submission_context(fresh_tick=stale))
        assert decision.open is False
        assert ReasonCode.STALE_MARKET_DATA in decision.reason_codes

    def test_a_suspect_tick_closes_it(self) -> None:
        suspect = fresh_tick(data_quality=DataQuality.SUSPECT)
        decision = evaluate_submission_gate(submission_context(fresh_tick=suspect))
        assert decision.open is False
        assert ReasonCode.INVALID_QUOTE in decision.reason_codes

    def test_a_halted_kill_switch_closes_it(self) -> None:
        kill_switch = KillSwitch()
        kill_switch.trip(
            reason_codes=(ReasonCode.MANUAL_HALT,), tripped_by="operator", occurred_at_utc=FIXED_NOW
        )
        decision = evaluate_submission_gate(submission_context(kill_switch=kill_switch))
        assert decision.open is False
        assert ReasonCode.SYSTEM_HALTED in decision.reason_codes

    def test_an_unapproved_risk_config_version_closes_it(self) -> None:
        decision = evaluate_submission_gate(submission_context(approved_risk_config_version=None))
        assert decision.open is False
        assert ReasonCode.RISK_POLICY_NOT_APPROVED in decision.reason_codes

    def test_a_risk_config_version_mismatch_closes_it(self) -> None:
        """An approval on record for a *different* version does not carry

        over — a config edit invalidates the prior approval, exactly like
        F-055's spec pin."""
        decision = evaluate_submission_gate(
            submission_context(approved_risk_config_version="cfg-v0")
        )
        assert decision.open is False
        assert ReasonCode.RISK_POLICY_NOT_APPROVED in decision.reason_codes

    def test_submission_not_enabled_closes_it(self) -> None:
        decision = evaluate_submission_gate(submission_context(submission_enabled=False))
        assert decision.open is False
        assert ReasonCode.EXECUTION_NOT_EXPLICITLY_ENABLED in decision.reason_codes

    def test_algotrading_disabled_closes_it(self) -> None:
        decision = evaluate_submission_gate(submission_context(terminal_trade_allowed=False))
        assert decision.open is False
        assert ReasonCode.ALGOTRADING_DISABLED in decision.reason_codes

    def test_no_feedback_2_0_approval_closes_it(self) -> None:
        decision = evaluate_submission_gate(submission_context(feedback_2_0_approved=False))
        assert decision.open is False
        assert ReasonCode.FEEDBACK_2_0_NOT_APPROVED in decision.reason_codes

    def test_an_unapproved_account_reference_closes_it(self) -> None:
        """Phase B item B7: `approved_account_ref=None` (every shipped

        config's real state today) fails closed automatically, mirroring
        condition 6's own plain-inequality idiom — no separate null-check
        needed."""
        decision = evaluate_submission_gate(submission_context(approved_account_ref=None))
        assert decision.open is False
        assert ReasonCode.WRONG_ACCOUNT in decision.reason_codes

    def test_a_mismatched_account_reference_closes_it(self) -> None:
        """A different demo account is a refusal even if every other leg

        looks plausible — a stale or wrong approval does not carry over,
        exactly like `test_a_risk_config_version_mismatch_closes_it`
        above."""
        decision = evaluate_submission_gate(
            submission_context(approved_account_ref="not-the-approved-account")
        )
        assert decision.open is False
        assert ReasonCode.WRONG_ACCOUNT in decision.reason_codes

    def test_the_exact_matching_account_reference_does_not_by_itself_close_it(self) -> None:
        account = make_account_state()
        decision = evaluate_submission_gate(
            submission_context(account=account, approved_account_ref=account.login_hash)
        )
        assert decision.open is True
        assert decision.reason_codes == ()

    def test_every_failing_leg_is_reported_not_just_the_first(self) -> None:
        decision = evaluate_submission_gate(
            submission_context(
                environment=Environment.LIVE,
                submission_enabled=False,
                feedback_2_0_approved=False,
            )
        )
        assert ReasonCode.LIVE_EXECUTION_NOT_PERMITTED in decision.reason_codes
        assert ReasonCode.EXECUTION_NOT_EXPLICITLY_ENABLED in decision.reason_codes
        assert ReasonCode.FEEDBACK_2_0_NOT_APPROVED in decision.reason_codes

    def test_the_gate_is_closed_against_the_actual_shipped_config(self) -> None:
        """The concrete proof, not just the design intent: build a context

        from `load_config`'s real, current `config/paper.yaml` values —
        the gate must stay closed, because none of the four durable
        approval fields are set in any shipped config file."""
        from pathlib import Path

        from crumblr.config import load_config

        repo_config_dir = Path(__file__).resolve().parents[2] / "config"
        config = load_config(Environment.PAPER, config_dir=repo_config_dir)
        decision = evaluate_submission_gate(
            submission_context(
                environment=config.environment,
                risk_config_version=config.config_version,
                approved_risk_config_version=config.risk.approved_config_version,
                submission_enabled=config.execution.submission_enabled,
                feedback_2_0_approved=config.execution.feedback_2_0_approved,
                approved_account_ref=config.execution.approved_canary_account_ref,
            )
        )
        assert decision.open is False
        assert ReasonCode.RISK_POLICY_NOT_APPROVED in decision.reason_codes
        assert ReasonCode.EXECUTION_NOT_EXPLICITLY_ENABLED in decision.reason_codes
        assert ReasonCode.FEEDBACK_2_0_NOT_APPROVED in decision.reason_codes
        assert ReasonCode.WRONG_ACCOUNT in decision.reason_codes

    def test_decision_is_internally_consistent(self) -> None:
        from crumblr.risk.submission_gate import SubmissionGateDecision

        with pytest.raises(ValueError):
            SubmissionGateDecision(open=False, reason_codes=())
        with pytest.raises(ValueError):
            SubmissionGateDecision(open=True, reason_codes=(ReasonCode.SYSTEM_HALTED,))
