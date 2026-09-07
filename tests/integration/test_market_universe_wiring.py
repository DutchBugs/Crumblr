"""`ExecutionOrchestrator` must not silently execute a second market

against EUR/USD's platform-default risk/execution thresholds or trading
calendar (Market Universe, ADR-022, owner correction 2026-09-07).

Real PostgreSQL for the stores (construction only — this proves wiring,
not a full `run_once()` cycle), a fake MT5 terminal for the adapter
(`_execution_fixtures.FakeMt5`, the same one `test_execution_orchestrator
.py` uses).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Engine

from crumblr.application.execution import ExecutionOrchestrator
from crumblr.config import (
    ExecutionConfig,
    ExecutionOverrides,
    IntradayConfig,
    MarketConfig,
    PlatformConfig,
    RiskConfig,
    RiskOverrides,
    SupervisorConfig,
    TradingAgentConfig,
)
from crumblr.domain.enums import AssetClass, Environment
from crumblr.mt5_gateway.client import Mt5Client, Mt5Credentials
from crumblr.mt5_gateway.execution import OrderCheckMt5Gateway
from crumblr.persistence.broker_state import BrokerStateStore
from crumblr.persistence.execution import ExecutionEventStore, ExecutionRequestStore
from crumblr.persistence.flatten import FlattenEventStore, FlattenRequestStore
from crumblr.persistence.instrument_specs import InstrumentSpecStore
from crumblr.persistence.journal import CapsuleStore
from crumblr.persistence.risk_session import PostgresRiskLedgerLock, PostgresRiskSessionStore
from crumblr.risk.calendars import AlwaysOpenCalendar, FxWeekdayCalendar
from crumblr.risk.kill_switch import KillSwitch
from tests.integration._execution_fixtures import LOGIN, SERVER, FakeMt5, guard

pytestmark = pytest.mark.integration

BTC_OVERRIDE_DAILY_LOSS = Decimal("0.01")
BTC_OVERRIDE_SLIPPAGE = 5


def two_market_config() -> PlatformConfig:
    """EUR/USD (platform default, unchanged) plus BTC/USD, `enabled: true`

    **for this test config only** — never touches `config/paper.yaml`,
    which keeps BTC/USD `enabled: false` per the owner's explicit
    instruction.
    """
    return PlatformConfig(
        environment=Environment.PAPER,
        markets=(
            MarketConfig(
                canonical_symbol="EUR/USD",
                enabled=True,
                asset_class=AssetClass.FX,
                broker_symbol="EURUSD",
            ),
            MarketConfig(
                canonical_symbol="BTC/USD",
                enabled=True,
                asset_class=AssetClass.CRYPTO,
                broker_symbol="BTCUSD",
                risk_overrides=RiskOverrides(max_daily_loss=BTC_OVERRIDE_DAILY_LOSS),
                execution_overrides=ExecutionOverrides(max_slippage_points=BTC_OVERRIDE_SLIPPAGE),
            ),
        ),
        risk=RiskConfig.model_validate(
            {
                "max_risk_per_trade": "0.02",
                "max_open_risk": "0.03",
                "max_daily_loss": "0.04",
                "max_drawdown": "0.08",
                "max_orders_per_hour": 6,
                "max_open_positions": 10,
                "min_stop_distance_points": 50,
            }
        ),
        execution=ExecutionConfig.model_validate(
            {
                "max_spread_points": 25,
                "max_market_data_age_ms": 60_000,
                "order_timeout_ms": 5000,
                "max_slippage_points": 20,
            }
        ),
        trading_agent=TradingAgentConfig.model_validate(
            {"strategy_id": "baseline_v1", "strategy_version": "0.1.0", "model_version": None}
        ),
        supervisor=SupervisorConfig.model_validate(
            {
                "enabled": True,
                "veto_on_unknown_regime": False,
                "halt_on_reconciliation_mismatch": True,
                "policy_version": "policy-v1",
                "max_intents_per_hour": None,
            }
        ),
        account_guard=guard(),
        intraday=IntradayConfig.model_validate(
            {
                "enabled": False,
                "last_entry_minutes_before_close": 0,
                "flatten_minutes_before_close": 0,
            }
        ),
    )


def _orchestrator(engine: Engine, config: PlatformConfig, *, canonical_symbol: str) -> Any:
    client = Mt5Client(FakeMt5())
    client.connect(Mt5Credentials(login=LOGIN, password="x", server=SERVER))
    adapter = OrderCheckMt5Gateway(client, guard())
    return ExecutionOrchestrator(
        config,
        capsules=CapsuleStore(engine),
        requests=ExecutionRequestStore(engine),
        events=ExecutionEventStore(engine),
        flatten_requests=FlattenRequestStore(engine),
        flatten_events=FlattenEventStore(engine),
        broker_state=BrokerStateStore(engine),
        instrument_specs=InstrumentSpecStore(engine),
        session_store=PostgresRiskSessionStore(engine),
        risk_ledger_lock=PostgresRiskLedgerLock(engine),
        kill_switch=KillSwitch(),
        adapter=adapter,
        canonical_symbol=canonical_symbol,
        worker_id="test-worker",
    )


class TestExecutionOrchestratorUsesTheMarketsOwnConfig:
    def test_a_second_markets_risk_and_execution_overrides_reach_the_orchestrator(
        self, engine: Engine
    ) -> None:
        config = two_market_config()
        orchestrator = _orchestrator(engine, config, canonical_symbol="BTC/USD")

        assert orchestrator._risk_config.max_daily_loss == BTC_OVERRIDE_DAILY_LOSS
        assert orchestrator._risk_config.max_daily_loss != config.risk.max_daily_loss
        assert orchestrator._execution_config.max_slippage_points == BTC_OVERRIDE_SLIPPAGE
        assert isinstance(orchestrator._calendar, AlwaysOpenCalendar)

    def test_eur_usd_is_unaffected(self, engine: Engine) -> None:
        config = two_market_config()
        orchestrator = _orchestrator(engine, config, canonical_symbol="EUR/USD")

        assert orchestrator._risk_config.max_daily_loss == config.risk.max_daily_loss
        assert (
            orchestrator._execution_config.max_slippage_points
            == config.execution.max_slippage_points
        )
        assert isinstance(orchestrator._calendar, FxWeekdayCalendar)
