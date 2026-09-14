"""FIRST DEMO CANARY eligibility wiring fix (Dev 1 review, post-BLOCK):

`ExecutionOrchestrator`'s own `current_strategy_version` parameter and
`activation_watermark` before/after behaviour, against real PostgreSQL and
a fake MT5 terminal -- never the real one. The root cause this fixes: an
Agent-driven capsule's `strategy_version` is the assignment's own
`strategy_artifact_hash`, never `config.trading_agent.strategy_version`
(the *internal* `ict_v1`/`baseline_v1` version); and a sealed capsule must
never be eligible before the owner actually issued the authorizing permit.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import Engine

from crumblr.config import PlatformConfig
from crumblr.domain.enums import ExecutionEventType, ReasonCode
from crumblr.persistence.instrument_specs import InstrumentSpecStore
from tests.conftest import FIXED_NOW
from tests.integration._execution_fixtures import (
    STRATEGY_VERSION,
    FakeMt5,
    orchestrator,
    platform_config,
    spec,
)
from tests.integration.test_execution_orchestrator import sealed_capsule

pytestmark = pytest.mark.integration

_CANARY_VERSION = "owner-demo-execution-canary-fixture-v1"


def _config(engine: Engine) -> PlatformConfig:
    the_spec = spec()
    InstrumentSpecStore(engine).record(the_spec)
    return platform_config(expected_spec_version=the_spec.spec_version)


class TestCurrentStrategyVersionDefaultsToConfig:
    def test_default_orchestrator_still_uses_config_strategy_version(self, engine: Engine) -> None:
        """No `current_strategy_version` passed -- the exact prior

        behaviour (every existing caller/test) must be unchanged."""
        config = _config(engine)
        capsule = sealed_capsule(engine, config, strategy_version=STRATEGY_VERSION)
        fake = FakeMt5()
        orch = orchestrator(
            engine, config, fake, activation_watermark=FIXED_NOW - timedelta(seconds=1)
        )
        outcomes = orch.run_once()
        assert outcomes[0].capsule_id == capsule.capsule_id
        assert outcomes[0].event_type != ExecutionEventType.INELIGIBLE


class TestCurrentStrategyVersionExplicitOverride:
    def test_matching_capsule_passes_the_strategy_version_check(self, engine: Engine) -> None:
        config = _config(engine)
        capsule = sealed_capsule(engine, config, strategy_version=_CANARY_VERSION)
        fake = FakeMt5()
        orch = orchestrator(
            engine,
            config,
            fake,
            activation_watermark=FIXED_NOW - timedelta(seconds=1),
            current_strategy_version=_CANARY_VERSION,
        )
        outcomes = orch.run_once()
        assert outcomes[0].capsule_id == capsule.capsule_id
        assert outcomes[0].event_type != ExecutionEventType.INELIGIBLE

    def test_a_different_strategy_version_is_still_not_current(self, engine: Engine) -> None:
        config = _config(engine)
        capsule = sealed_capsule(engine, config, strategy_version="some-other-strategy-version")
        fake = FakeMt5()
        orch = orchestrator(
            engine,
            config,
            fake,
            activation_watermark=FIXED_NOW - timedelta(seconds=1),
            current_strategy_version=_CANARY_VERSION,
        )
        outcomes = orch.run_once()
        assert outcomes[0].capsule_id == capsule.capsule_id
        assert outcomes[0].event_type == ExecutionEventType.INELIGIBLE
        assert ReasonCode.STRATEGY_VERSION_NOT_CURRENT in outcomes[0].reason_codes


class TestActivationWatermark:
    def test_none_watermark_is_ineligible(self, engine: Engine) -> None:
        config = _config(engine)
        capsule = sealed_capsule(engine, config, strategy_version=STRATEGY_VERSION)
        fake = FakeMt5()
        orch = orchestrator(engine, config, fake, activation_watermark=None)
        outcomes = orch.run_once()
        assert outcomes[0].capsule_id == capsule.capsule_id
        assert outcomes[0].event_type == ExecutionEventType.INELIGIBLE
        assert ReasonCode.DECISION_PREDATES_EXECUTION_ACTIVATION in outcomes[0].reason_codes

    def test_capsule_sealed_before_the_watermark_is_ineligible(self, engine: Engine) -> None:
        watermark = FIXED_NOW
        config = _config(engine)
        capsule = sealed_capsule(
            engine,
            config,
            strategy_version=STRATEGY_VERSION,
            occurred_at_utc=watermark - timedelta(seconds=1),
        )
        fake = FakeMt5()
        orch = orchestrator(engine, config, fake, activation_watermark=watermark)
        outcomes = orch.run_once()
        assert outcomes[0].capsule_id == capsule.capsule_id
        assert outcomes[0].event_type == ExecutionEventType.INELIGIBLE
        assert ReasonCode.DECISION_PREDATES_EXECUTION_ACTIVATION in outcomes[0].reason_codes

    def test_capsule_sealed_after_the_watermark_can_pass_eligibility(self, engine: Engine) -> None:
        watermark = FIXED_NOW
        config = _config(engine)
        capsule = sealed_capsule(
            engine,
            config,
            strategy_version=STRATEGY_VERSION,
            occurred_at_utc=watermark + timedelta(seconds=1),
        )
        fake = FakeMt5()
        orch = orchestrator(engine, config, fake, activation_watermark=watermark)
        outcomes = orch.run_once()
        assert outcomes[0].capsule_id == capsule.capsule_id
        assert outcomes[0].event_type != ExecutionEventType.INELIGIBLE
