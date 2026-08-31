"""Configuration loading (build.md §17, §21).

The rule under test is that omission is never permission: a limit that is not
configured must stop the system, not default to something tolerable.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from crumblr.config import (
    LIVE_OVERRIDE_ENV_VAR,
    PlatformConfig,
    load_config,
)
from crumblr.domain.enums import Environment
from tests.conftest import paper_config_payload

REPO_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


def write_config_dir(tmp_path: Path, base: dict[str, Any], overlay: dict[str, Any]) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "base.yaml").write_text(yaml.safe_dump(base), encoding="utf-8")
    for environment, content in overlay.items():
        (config_dir / f"{environment}.yaml").write_text(yaml.safe_dump(content), encoding="utf-8")
    return config_dir


class TestShippedConfiguration:
    """The configuration committed to the repository must actually load."""

    def test_paper_config_loads(self) -> None:
        config = load_config(Environment.PAPER, config_dir=REPO_CONFIG_DIR)
        assert config.environment is Environment.PAPER
        assert config.enabled_symbols() == ("EUR/USD",)

    def test_paper_config_requires_a_demo_account(self) -> None:
        config = load_config(Environment.PAPER, config_dir=REPO_CONFIG_DIR)
        assert config.account_guard.require_demo_account is True

    def test_no_live_config_is_shipped(self) -> None:
        """A live overlay is created deliberately at gate P4, not by default."""
        assert not (REPO_CONFIG_DIR / "live.yaml").exists()


class TestRiskLimitsAreMandatory:
    @pytest.mark.parametrize(
        "omitted",
        [
            "max_risk_per_trade",
            "max_open_risk",
            "max_daily_loss",
            "max_drawdown",
            "max_orders_per_hour",
            "max_open_positions",
            "min_stop_distance_points",
        ],
    )
    def test_omitting_a_risk_limit_fails_the_load(self, omitted: str) -> None:
        payload = paper_config_payload()
        del payload["risk"][omitted]
        with pytest.raises(ValidationError, match=omitted):
            PlatformConfig.model_validate(payload)

    @pytest.mark.parametrize(
        "omitted",
        ["max_spread_points", "max_market_data_age_ms", "order_timeout_ms", "max_slippage_points"],
    )
    def test_omitting_an_execution_limit_fails_the_load(self, omitted: str) -> None:
        payload = paper_config_payload()
        del payload["execution"][omitted]
        with pytest.raises(ValidationError, match=omitted):
            PlatformConfig.model_validate(payload)

    def test_an_entire_missing_section_fails_the_load(self) -> None:
        payload = paper_config_payload()
        del payload["risk"]
        with pytest.raises(ValidationError, match="risk"):
            PlatformConfig.model_validate(payload)

    def test_float_risk_values_are_refused(self) -> None:
        payload = paper_config_payload()
        payload["risk"]["max_risk_per_trade"] = 0.005
        with pytest.raises(ValidationError, match="float is not accepted"):
            PlatformConfig.model_validate(payload)


class TestRiskBudgetCoherence:
    def test_single_trade_may_not_exceed_portfolio_risk(self) -> None:
        payload = paper_config_payload()
        payload["risk"]["max_risk_per_trade"] = "0.05"
        payload["risk"]["max_open_risk"] = "0.02"
        with pytest.raises(ValidationError, match="one trade cannot outweigh the portfolio"):
            PlatformConfig.model_validate(payload)

    def test_daily_loss_gate_must_trip_before_the_drawdown_gate(self) -> None:
        payload = paper_config_payload()
        payload["risk"]["max_daily_loss"] = "0.20"
        payload["risk"]["max_drawdown"] = "0.10"
        with pytest.raises(ValidationError, match="daily gate would never trip first"):
            PlatformConfig.model_validate(payload)

    def test_zero_risk_per_trade_is_refused(self) -> None:
        payload = paper_config_payload()
        payload["risk"]["max_risk_per_trade"] = "0"
        with pytest.raises(ValidationError):
            PlatformConfig.model_validate(payload)


class TestEnvironmentGuardrails:
    def test_paper_must_require_a_demo_account(self) -> None:
        payload = paper_config_payload()
        payload["account_guard"]["require_demo_account"] = False
        with pytest.raises(ValidationError, match="require_demo_account"):
            PlatformConfig.model_validate(payload)

    def test_shadow_must_require_a_demo_account(self) -> None:
        payload = paper_config_payload()
        payload["environment"] = Environment.SHADOW.value
        payload["account_guard"]["require_demo_account"] = False
        with pytest.raises(ValidationError, match="require_demo_account"):
            PlatformConfig.model_validate(payload)

    def test_live_requires_an_explicit_acknowledgement(self) -> None:
        payload = paper_config_payload()
        payload["environment"] = Environment.LIVE.value
        payload["account_guard"]["require_demo_account"] = False
        with pytest.raises(ValidationError, match="live_trading_acknowledged"):
            PlatformConfig.model_validate(payload)

    def test_live_load_is_refused_without_the_override(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(LIVE_OVERRIDE_ENV_VAR, raising=False)
        payload = paper_config_payload()
        payload["environment"] = Environment.LIVE.value
        payload["live_trading_acknowledged"] = True
        payload["account_guard"]["require_demo_account"] = False
        config_dir = write_config_dir(tmp_path, {}, {"live": payload})
        with pytest.raises(PermissionError, match=LIVE_OVERRIDE_ENV_VAR):
            load_config(Environment.LIVE, config_dir=config_dir)

    def test_live_load_succeeds_only_with_both_gates_open(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(LIVE_OVERRIDE_ENV_VAR, "1")
        payload = paper_config_payload()
        payload["environment"] = Environment.LIVE.value
        payload["live_trading_acknowledged"] = True
        payload["account_guard"]["require_demo_account"] = False
        config_dir = write_config_dir(tmp_path, {}, {"live": payload})
        config = load_config(Environment.LIVE, config_dir=config_dir)
        assert config.environment is Environment.LIVE

    def test_a_paper_load_ignores_a_live_overlay(self, tmp_path: Path) -> None:
        """Selecting paper must not be able to pick up live settings by accident."""
        paper = paper_config_payload()
        live = copy.deepcopy(paper)
        live["account_guard"]["expected_server"] = "RealBroker-Live"
        config_dir = write_config_dir(tmp_path, {}, {"paper": paper, "live": live})
        config = load_config(Environment.PAPER, config_dir=config_dir)
        assert config.account_guard.expected_server == "DemoBroker-Demo"


class TestSecretsAreRefused:
    @pytest.mark.parametrize(
        "key", ["password", "mt5_password", "api_key", "secret_key", "auth_token", "credentials"]
    )
    def test_credential_shaped_keys_are_rejected(self, tmp_path: Path, key: str) -> None:
        payload = paper_config_payload()
        payload["account_guard"][key] = "hunter2"
        config_dir = write_config_dir(tmp_path, {}, {"paper": payload})
        with pytest.raises(ValueError, match="looks like a credential"):
            load_config(Environment.PAPER, config_dir=config_dir)

    def test_nested_credentials_are_found(self, tmp_path: Path) -> None:
        payload = paper_config_payload()
        payload["markets"][0]["broker_password"] = "hunter2"
        config_dir = write_config_dir(tmp_path, {}, {"paper": payload})
        with pytest.raises(ValueError, match="looks like a credential"):
            load_config(Environment.PAPER, config_dir=config_dir)


class TestOverlayMerging:
    def test_overlay_sections_merge_with_base(self, tmp_path: Path) -> None:
        payload = paper_config_payload()
        base = {"markets": payload["markets"], "trading_agent": payload["trading_agent"]}
        overlay = {
            "risk": payload["risk"],
            "execution": payload["execution"],
            "supervisor": payload["supervisor"],
            "account_guard": payload["account_guard"],
            "intraday": payload["intraday"],
        }
        config_dir = write_config_dir(tmp_path, base, {"paper": overlay})
        config = load_config(Environment.PAPER, config_dir=config_dir)
        assert config.trading_agent.strategy_id == "baseline_v1"
        assert config.risk.max_open_positions == 1

    def test_overlay_overrides_a_single_nested_value(self, tmp_path: Path) -> None:
        payload = paper_config_payload()
        base = copy.deepcopy(payload)
        config_dir = write_config_dir(
            tmp_path, base, {"paper": {"risk": {"max_open_positions": 3}}}
        )
        config = load_config(Environment.PAPER, config_dir=config_dir)
        assert config.risk.max_open_positions == 3
        assert config.risk.max_orders_per_hour == 6

    def test_a_missing_environment_overlay_is_an_error(self, tmp_path: Path) -> None:
        config_dir = write_config_dir(tmp_path, paper_config_payload(), {})
        with pytest.raises(FileNotFoundError, match=r"paper\.yaml"):
            load_config(Environment.PAPER, config_dir=config_dir)

    def test_unknown_keys_are_refused(self) -> None:
        payload = paper_config_payload()
        payload["risk"]["max_risk_per_traed"] = "0.005"
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            PlatformConfig.model_validate(payload)


class TestMarkets:
    def test_at_least_one_market_is_required(self) -> None:
        payload = paper_config_payload()
        payload["markets"] = []
        with pytest.raises(ValidationError, match="at least one market"):
            PlatformConfig.model_validate(payload)

    def test_duplicate_symbols_are_refused(self) -> None:
        payload = paper_config_payload()
        payload["markets"] = [
            {"canonical_symbol": "EUR/USD", "enabled": True},
            {"canonical_symbol": "EUR/USD", "enabled": False},
        ]
        with pytest.raises(ValidationError, match="duplicate canonical_symbol"):
            PlatformConfig.model_validate(payload)

    def test_disabled_markets_are_excluded(self) -> None:
        payload = paper_config_payload()
        payload["markets"] = [
            {"canonical_symbol": "EUR/USD", "enabled": True},
            {"canonical_symbol": "GBP/USD", "enabled": False},
        ]
        assert PlatformConfig.model_validate(payload).enabled_symbols() == ("EUR/USD",)

    def test_market_for_finds_the_matching_symbol(self) -> None:
        config = PlatformConfig.model_validate(paper_config_payload())
        market = config.market_for("EUR/USD")
        assert market is not None
        assert market.canonical_symbol == "EUR/USD"

    def test_market_for_an_unconfigured_symbol_is_none(self) -> None:
        config = PlatformConfig.model_validate(paper_config_payload())
        assert config.market_for("GBP/USD") is None

    def test_expected_spec_version_defaults_to_none(self) -> None:
        """Review 1.19 §4 (F-055): no baseline is pinned unless a human

        explicitly set one — the shipped config must not smuggle in a value
        nobody approved.
        """
        config = PlatformConfig.model_validate(paper_config_payload())
        market = config.market_for("EUR/USD")
        assert market is not None
        assert market.expected_spec_version is None

    def test_expected_spec_version_can_be_pinned(self) -> None:
        payload = paper_config_payload()
        payload["markets"] = [
            {
                "canonical_symbol": "EUR/USD",
                "enabled": True,
                "expected_spec_version": "a" * 64,
            }
        ]
        config = PlatformConfig.model_validate(payload)
        market = config.market_for("EUR/USD")
        assert market is not None
        assert market.expected_spec_version == "a" * 64


class TestConfigVersioning:
    """build.md §17: configuration is versioned and immutable per decision."""

    def test_identical_configs_share_a_version(self) -> None:
        first = PlatformConfig.model_validate(paper_config_payload())
        second = PlatformConfig.model_validate(paper_config_payload())
        assert first.config_version == second.config_version

    def test_any_change_produces_a_new_version(self) -> None:
        baseline = PlatformConfig.model_validate(paper_config_payload())
        payload = paper_config_payload()
        payload["risk"]["max_open_positions"] = 2
        assert PlatformConfig.model_validate(payload).config_version != baseline.config_version

    def test_config_is_immutable(self) -> None:
        config = PlatformConfig.model_validate(paper_config_payload())
        with pytest.raises(ValidationError):
            config.risk.max_open_positions = 99

    def test_approving_this_exact_version_does_not_change_it(self) -> None:
        """F-062: `RiskConfig.approved_config_version` is compared against

        `config_version` (`risk/submission_gate.py` condition 6). Before this
        was fixed, writing the approved hash into the config changed the
        config, which changed the hash the write was supposed to match — an
        approval could never actually be recorded. `config_version` now
        excludes the three governance fields so an approval of this exact
        content is stable once written."""
        baseline = PlatformConfig.model_validate(paper_config_payload())
        version = baseline.config_version

        approved = baseline.model_copy(
            update={
                "risk": baseline.risk.model_copy(update={"approved_config_version": version}),
                "execution": baseline.execution.model_copy(
                    update={"submission_enabled": True, "feedback_2_0_approved": True}
                ),
            }
        )

        assert approved.config_version == version
        assert approved.risk.approved_config_version == approved.config_version
