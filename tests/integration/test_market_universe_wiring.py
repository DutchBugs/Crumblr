"""`ExecutionOrchestrator` must not silently execute a second market

against EUR/USD's platform-default risk/execution thresholds or trading
calendar (Market Universe, ADR-022, owner correction 2026-09-07).

Real PostgreSQL for the stores (construction only — this proves wiring,
not a full `run_once()` cycle), a fake MT5 terminal for the adapter
(`_execution_fixtures.FakeMt5`, the same one `test_execution_orchestrator
.py` uses).
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
from sqlalchemy import Engine

from crumblr.application.execution import ExecutionOrchestrator, _approval_chain_fingerprint
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
from crumblr.domain.models import DecisionCapsule
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
from tests.conftest import FIXED_NOW, make_intent, make_risk_decision, make_supervisor_decision
from tests.integration._execution_fixtures import LOGIN, SERVER, STRATEGY_VERSION, FakeMt5, guard

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


def _sealed_capsule(
    engine: Engine, config: PlatformConfig, *, canonical_symbol: str, broker_symbol: str
) -> DecisionCapsule:
    """A minimal PASS/APPROVE-shaped capsule for `canonical_symbol`, sealed

    into real PostgreSQL — enough for `run_once()`'s `_is_intent_time_approved`
    check and the cross-market read/claim proofs below, not a claim that
    it would reach `ORDER_CHECKED` (no `InstrumentSpec` is recorded for
    BTC/USD in these tests).
    """
    intent = make_intent(
        symbol=canonical_symbol,
        created_at_utc=FIXED_NOW,
        expires_at_utc=FIXED_NOW + timedelta(minutes=10),
        reference_price="1.08500",
        stop_loss_price="1.08000",
        take_profit_price="1.09000",
        requested_risk_fraction="0.005",
    )
    capsule = DecisionCapsule(
        capsule_id=uuid4(),
        occurred_at_utc=FIXED_NOW,
        correlation_id=uuid4(),
        canonical_symbol=canonical_symbol,
        broker_symbol=broker_symbol,
        market_snapshot_id=uuid4(),
        feature_set_version="features-v1",
        feature_values_hash="abc123",
        strategy_version=STRATEGY_VERSION,
        model_version=None,
        trade_intent=intent,
        risk_config_version=config.config_version,
        risk_decision=make_risk_decision(
            intent.intent_id,
            risk_config_version=config.config_version,
            approved_volume="0.05",
            account_equity="10000",
            stop_distance_points=500,
            risk_amount="50",
        ),
        supervisor_decision=make_supervisor_decision(intent.intent_id),
        code_commit="deadbeef",
        environment=Environment.PAPER,
    )
    CapsuleStore(engine).seal(capsule)
    return capsule


def _order_request_id(capsule: DecisionCapsule) -> Any:
    assert capsule.trade_intent is not None
    return uuid5(NAMESPACE_URL, f"crumblr:order:{capsule.trade_intent.decision_hash}")


class TestCapsuleStoreReadAllIsBoundedBySymbol:
    def test_a_symbol_filter_excludes_every_other_markets_capsule(self, engine: Engine) -> None:
        config = two_market_config()
        eur_capsule = _sealed_capsule(
            engine, config, canonical_symbol="EUR/USD", broker_symbol="EURUSD"
        )
        btc_capsule = _sealed_capsule(
            engine, config, canonical_symbol="BTC/USD", broker_symbol="BTCUSD"
        )

        eur_only = CapsuleStore(engine).read_all(canonical_symbol="EUR/USD")
        btc_only = CapsuleStore(engine).read_all(canonical_symbol="BTC/USD")

        assert [c.capsule_id for c in eur_only] == [eur_capsule.capsule_id]
        assert [c.capsule_id for c in btc_only] == [btc_capsule.capsule_id]

    def test_no_filter_still_returns_every_market(self, engine: Engine) -> None:
        """Regression guard: the new parameter must stay optional and

        default-preserving — every existing caller that never passes it
        keeps today's exact behaviour."""
        config = two_market_config()
        _sealed_capsule(engine, config, canonical_symbol="EUR/USD", broker_symbol="EURUSD")
        _sealed_capsule(engine, config, canonical_symbol="BTC/USD", broker_symbol="BTCUSD")

        assert len(CapsuleStore(engine).read_all()) == 2


class TestExecutionOrchestratorNeverClaimsAnotherMarketsCapsule:
    """The owner's core finding, 2026-09-07/08: `run_once()` read every

    capsule for the environment and relied on nothing to stop a worker
    from claiming/processing a capsule that belonged to a different
    market. Proven both directions — a EUR/USD worker must never touch a
    BTC/USD capsule, and vice versa — and proven at the persistence
    layer, not just by inspecting `run_once()`'s return value: the wrong
    capsule must leave no `execution_requests` claim and no
    `execution_events` row at all.
    """

    def test_a_eur_usd_worker_never_claims_or_processes_a_btc_usd_capsule(
        self, engine: Engine
    ) -> None:
        config = two_market_config()
        eur_capsule = _sealed_capsule(
            engine, config, canonical_symbol="EUR/USD", broker_symbol="EURUSD"
        )
        btc_capsule = _sealed_capsule(
            engine, config, canonical_symbol="BTC/USD", broker_symbol="BTCUSD"
        )

        orchestrator = _orchestrator(engine, config, canonical_symbol="EUR/USD")
        outcomes = orchestrator.run_once()

        # The BTC/USD capsule was never even seen by this worker's pass.
        assert all(outcome.capsule_id != btc_capsule.capsule_id for outcome in outcomes)

        requests = ExecutionRequestStore(engine)
        events = ExecutionEventStore(engine)

        eur_order_request_id = _order_request_id(eur_capsule)
        btc_order_request_id = _order_request_id(btc_capsule)

        # The EUR/USD capsule *was* claimed by the real run above — a
        # fresh claim attempt with the same id and the same real
        # approval-chain fingerprint now loses the race (a mismatched
        # fingerprint would raise `ExecutionRequestConflictError` instead
        # of reporting `claimed=False` — not what this probe is testing).
        eur_reclaim = requests.claim(
            order_request_id=eur_order_request_id,
            capsule_id=eur_capsule.capsule_id,
            intent_id=eur_capsule.trade_intent.intent_id,  # type: ignore[union-attr]
            fingerprint=_approval_chain_fingerprint(eur_capsule),
            claimed_by="test-probe",
            now=FIXED_NOW,
        )
        assert eur_reclaim.claimed is False, "the real run must have claimed the EUR/USD capsule"

        # The BTC/USD capsule was never claimed at all — this probe claim
        # is the first and only one, so it wins.
        btc_probe_claim = requests.claim(
            order_request_id=btc_order_request_id,
            capsule_id=btc_capsule.capsule_id,
            intent_id=btc_capsule.trade_intent.intent_id,  # type: ignore[union-attr]
            fingerprint="probe",
            claimed_by="test-probe",
            now=FIXED_NOW,
        )
        assert btc_probe_claim.claimed is True, (
            "the BTC/USD capsule must never have been claimed by the EUR/USD worker"
        )

        # And no durable execution event exists for it either, from
        # before this probe claim.
        assert events.events_for(btc_order_request_id) == ()

    def test_a_btc_usd_worker_never_claims_or_processes_a_eur_usd_capsule(
        self, engine: Engine
    ) -> None:
        config = two_market_config()
        eur_capsule = _sealed_capsule(
            engine, config, canonical_symbol="EUR/USD", broker_symbol="EURUSD"
        )
        _sealed_capsule(engine, config, canonical_symbol="BTC/USD", broker_symbol="BTCUSD")

        orchestrator = _orchestrator(engine, config, canonical_symbol="BTC/USD")
        orchestrator.run_once()

        requests = ExecutionRequestStore(engine)
        events = ExecutionEventStore(engine)
        eur_order_request_id = _order_request_id(eur_capsule)

        eur_probe_claim = requests.claim(
            order_request_id=eur_order_request_id,
            capsule_id=eur_capsule.capsule_id,
            intent_id=eur_capsule.trade_intent.intent_id,  # type: ignore[union-attr]
            fingerprint="probe",
            claimed_by="test-probe",
            now=FIXED_NOW,
        )
        assert eur_probe_claim.claimed is True, (
            "the EUR/USD capsule must never have been claimed by the BTC/USD worker"
        )
        assert events.events_for(eur_order_request_id) == ()


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
