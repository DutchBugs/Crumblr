"""Supervisor pre-trade policy (build.md §10).

The supervisor is the second, independent opinion. These tests check that it
refuses for the right reasons — and, just as importantly, that it has no way to
do anything other than approve, veto or halt.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

import pytest

from crumblr.domain.enums import (
    IncidentStatus,
    ReasonCode,
    ReconciliationStatus,
    Regime,
    SupervisorVerdict,
)
from crumblr.domain.models import SupervisorDecision, TradeIntent
from crumblr.evaluator import pretrade
from crumblr.trading_agent.features import FEATURE_SET_VERSION, FeatureSnapshot
from tests.conftest import FIXED_NOW, make_intent

NOW = FIXED_NOW + timedelta(milliseconds=50)


def features(regime: Regime = Regime.TREND) -> FeatureSnapshot:
    return FeatureSnapshot(
        feature_snapshot_id=uuid5(NAMESPACE_URL, f"test:features:{regime.value}"),
        feature_set_version=FEATURE_SET_VERSION,
        symbol="EUR/USD",
        computed_at_utc=FIXED_NOW,
        bars_used=200,
        ema_fast=Decimal("1.08550"),
        ema_slow=Decimal("1.08500"),
        atr=Decimal("0.00080"),
        atr_baseline=Decimal("0.00075"),
        volatility_ratio=Decimal("1.07"),
        trend_score=Decimal("0.63"),
        regime=regime,
    )


def known_good_context(**overrides: object) -> pretrade.SupervisorContext:
    """A context where safety state is known and clear.

    The dataclass now defaults both safety fields to UNKNOWN, so a test that
    wants to exercise a policy rule has to say explicitly that reconciliation
    and incidents were checked. That is the point of finding F-002: nothing
    gets a clean bill of health by omission, including a test.
    """
    fields: dict[str, object] = {
        "intents_in_last_hour": 0,
        "incident_status": IncidentStatus.CLEAR,
        "reconciliation_status": ReconciliationStatus.MATCHED,
    }
    fields.update(overrides)
    return pretrade.SupervisorContext(**fields)  # type: ignore[arg-type]


def judge(
    *,
    intent: TradeIntent | None = None,
    regime: Regime = Regime.TREND,
    policy: pretrade.SupervisorPolicy | None = None,
    context: pretrade.SupervisorContext | None = None,
) -> SupervisorDecision:
    return pretrade.evaluate(
        intent or make_intent(confidence=0.6),
        features(regime),
        policy or pretrade.SupervisorPolicy(),
        context or known_good_context(),
        now=NOW,
    )


class TestApproval:
    def test_a_healthy_intent_is_approved(self) -> None:
        decision = judge()
        assert decision.verdict is SupervisorVerdict.APPROVE
        assert decision.reason_codes == ()

    def test_the_observed_regime_is_recorded(self) -> None:
        decision = judge(regime=Regime.TREND)
        assert decision.observed_regime is Regime.TREND

    def test_a_disabled_supervisor_still_records_a_decision(self) -> None:
        """Silence would leave a hole in the audit trail where an approval belongs."""
        decision = judge(policy=pretrade.SupervisorPolicy(enabled=False))
        assert decision.verdict is SupervisorVerdict.APPROVE


class TestVeto:
    def test_an_unknown_regime_is_vetoed(self) -> None:
        decision = judge(regime=Regime.UNKNOWN)
        assert decision.verdict is SupervisorVerdict.VETO
        assert ReasonCode.UNKNOWN_REGIME in decision.reason_codes

    def test_an_unknown_regime_passes_when_the_policy_allows_it(self) -> None:
        decision = judge(
            regime=Regime.UNKNOWN,
            policy=pretrade.SupervisorPolicy(veto_on_unknown_regime=False),
        )
        assert decision.verdict is SupervisorVerdict.APPROVE

    def test_an_unlisted_strategy_is_vetoed(self) -> None:
        decision = judge(
            policy=pretrade.SupervisorPolicy(allowed_strategy_ids=frozenset({"other_v9"}))
        )
        assert ReasonCode.STRATEGY_NOT_ENABLED in decision.reason_codes

    def test_an_unexpected_model_version_is_vetoed(self) -> None:
        """The baseline declares no model, so an intent naming one is out of envelope."""
        decision = judge(intent=make_intent(model_version="lgbm-0.3.1", confidence=0.6))
        assert ReasonCode.MODEL_VERSION_NOT_ALLOWED in decision.reason_codes

    def test_a_permitted_model_version_is_approved(self) -> None:
        decision = judge(
            intent=make_intent(model_version="lgbm-0.3.1", confidence=0.6),
            policy=pretrade.SupervisorPolicy(allowed_model_versions=frozenset({"lgbm-0.3.1"})),
        )
        assert decision.verdict is SupervisorVerdict.APPROVE

    def test_confidence_outside_the_envelope_is_vetoed(self) -> None:
        decision = judge(
            intent=make_intent(confidence=0.2),
            policy=pretrade.SupervisorPolicy(min_confidence=0.4),
        )
        assert ReasonCode.CONFIDENCE_OUT_OF_RANGE in decision.reason_codes

    def test_a_signal_flood_is_vetoed_when_the_check_is_calibrated(self) -> None:
        """A strategy emitting far more signals than usual is misbehaving.

        The threshold has to be supplied. The shipped configuration leaves it
        uncalibrated (F-024) because no real feed has been observed, so this
        test states one rather than relying on a default that no longer exists.
        """
        decision = judge(
            policy=pretrade.SupervisorPolicy(max_intents_per_hour=12),
            context=known_good_context(intents_in_last_hour=99),
        )
        assert ReasonCode.SIGNAL_FREQUENCY_ANOMALY in decision.reason_codes

    def test_an_open_incident_blocks_trading(self) -> None:
        decision = judge(context=known_good_context(incident_status=IncidentStatus.ACTIVE))
        assert ReasonCode.ACTIVE_INCIDENT in decision.reason_codes

    def test_every_violated_rule_is_reported(self) -> None:
        """One reason code would send the operator down one of several paths."""
        decision = judge(
            regime=Regime.UNKNOWN,
            policy=pretrade.SupervisorPolicy(max_intents_per_hour=12),
            context=known_good_context(
                intents_in_last_hour=99, incident_status=IncidentStatus.ACTIVE
            ),
        )
        assert {
            ReasonCode.UNKNOWN_REGIME,
            ReasonCode.SIGNAL_FREQUENCY_ANOMALY,
            ReasonCode.ACTIVE_INCIDENT,
        } <= set(decision.reason_codes)


class TestHalt:
    def test_a_reconciliation_mismatch_halts(self) -> None:
        """build.md §7 invariant 7: unknown state means halt, not another trade."""
        decision = judge(
            context=known_good_context(reconciliation_status=ReconciliationStatus.MISMATCHED)
        )
        assert decision.verdict is SupervisorVerdict.HALT
        assert ReasonCode.RECONCILIATION_MISMATCH in decision.reason_codes


class TestDeterminism:
    def test_the_same_intent_yields_the_same_decision_id(self) -> None:
        intent = make_intent(confidence=0.6)
        assert judge(intent=intent).decision_id == judge(intent=intent).decision_id

    @pytest.mark.parametrize("regime", list(Regime))
    def test_every_regime_produces_a_decision(self, regime: Regime) -> None:
        decision = judge(regime=regime)
        assert decision.verdict in set(SupervisorVerdict)


class TestUncalibratedChecksAreNamed:
    """Review 1.6 F-024, and the misreading D-028 was raised about.

    An approval from a seven-rule control plane reads as though seven rules
    passed. Two of them currently cannot fail — the confidence band spans the
    range the contract already enforces, and the frequency threshold has never
    been calibrated against a real feed. Saying so on the decision is the
    difference between a control that is known to be absent and one that
    merely looks present.
    """

    def test_an_uncalibrated_frequency_check_does_not_veto(self) -> None:
        """It cannot judge, so it must not pretend to."""
        decision = judge(context=known_good_context(intents_in_last_hour=10_000))

        assert ReasonCode.SIGNAL_FREQUENCY_ANOMALY not in decision.reason_codes
        assert decision.verdict is SupervisorVerdict.APPROVE

    def test_an_uncalibrated_frequency_check_says_so_on_the_decision(self) -> None:
        """The part that stops the approval from being misleading."""
        decision = judge(context=known_good_context(intents_in_last_hour=10_000))

        assert pretrade.SIGNAL_FREQUENCY_CHECK in decision.uncalibrated_checks

    def test_a_calibrated_check_is_not_listed_as_uncalibrated(self) -> None:
        decision = judge(policy=pretrade.SupervisorPolicy(max_intents_per_hour=12))

        assert pretrade.SIGNAL_FREQUENCY_CHECK not in decision.uncalibrated_checks

    def test_a_full_range_confidence_band_is_reported_as_absent(self) -> None:
        """0.0-1.0 is the range the TradeIntent contract already enforces."""
        decision = judge()

        assert pretrade.CONFIDENCE_BAND_CHECK in decision.uncalibrated_checks

    def test_a_narrowed_confidence_band_is_a_real_check_again(self) -> None:
        narrowed = pretrade.SupervisorPolicy(min_confidence=0.5, max_confidence=0.9)

        approved = judge(intent=make_intent(confidence=0.6), policy=narrowed)
        rejected = judge(intent=make_intent(confidence=0.2), policy=narrowed)

        assert pretrade.CONFIDENCE_BAND_CHECK not in approved.uncalibrated_checks
        assert ReasonCode.CONFIDENCE_OUT_OF_RANGE in rejected.reason_codes

    def test_a_veto_also_reports_which_checks_were_absent(self) -> None:
        """A refusal on other grounds still has to be honest about coverage."""
        decision = judge(regime=Regime.UNKNOWN)

        assert decision.verdict is SupervisorVerdict.VETO
        assert pretrade.SIGNAL_FREQUENCY_CHECK in decision.uncalibrated_checks

    def test_the_shipped_configuration_leaves_the_frequency_check_uncalibrated(self) -> None:
        """The claim in config/base.yaml, asserted rather than trusted."""
        from pathlib import Path

        from crumblr.config import load_config
        from crumblr.domain.enums import Environment

        repo_root = Path(__file__).resolve().parents[2]
        config = load_config(Environment.PAPER, config_dir=repo_root / "config")

        assert config.supervisor.max_intents_per_hour is None

    def test_the_configured_policy_version_matches_the_one_decisions_carry(self) -> None:
        """Two sources of truth for one version is one too many.

        `SupervisorConfig.policy_version` is documentation; the constant in
        `pretrade` is what a stored decision is stamped with. If they drift, a
        capsule names a policy the configuration never described.
        """
        from pathlib import Path

        from crumblr.config import load_config
        from crumblr.domain.enums import Environment

        repo_root = Path(__file__).resolve().parents[2]
        config = load_config(Environment.PAPER, config_dir=repo_root / "config")

        assert config.supervisor.policy_version == pretrade.POLICY_VERSION
