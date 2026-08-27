"""The Execution Service, end to end, against real PostgreSQL and a fake

MT5 terminal — never the real one. This is the test that proves the
non-sending guarantee holds for the whole assembled chain, not just for
each piece in isolation: a sealed, approved capsule reaches a persisted
`ORDER_CHECKED`/`ORDER_CHECK_REJECTED` event, and `order_send` is never
called.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import Engine

from crumblr.application.execution import ExecutionOrchestrator
from crumblr.config import (
    AccountGuardConfig,
    ExecutionConfig,
    IntradayConfig,
    MarketConfig,
    PlatformConfig,
    RiskConfig,
    SupervisorConfig,
    TradingAgentConfig,
)
from crumblr.domain.enums import Environment, ExecutionEventType
from crumblr.domain.models import DecisionCapsule, InstrumentSpec
from crumblr.mt5_gateway.client import Mt5Client, Mt5Credentials
from crumblr.mt5_gateway.execution import OrderCheckMt5Gateway
from crumblr.persistence.broker_state import BrokerStateStore
from crumblr.persistence.execution import ExecutionEventStore, ExecutionRequestStore
from crumblr.persistence.instrument_specs import InstrumentSpecStore
from crumblr.persistence.journal import CapsuleStore
from crumblr.persistence.risk_session import PostgresRiskSessionStore
from crumblr.risk.kill_switch import KillSwitch
from tests.conftest import FIXED_NOW, make_intent, make_risk_decision, make_supervisor_decision

pytestmark = pytest.mark.integration

SERVER = "Test-Demo"
CURRENCY = "EUR"
LEVERAGE = 30
BROKER_SYMBOL = "EURUSD"
STRATEGY_VERSION = "0.1.0"


def account_info(**overrides: Any) -> Any:
    from types import SimpleNamespace

    fields: dict[str, Any] = {
        "login": 5_000_123,
        "server": SERVER,
        "currency": CURRENCY,
        "trade_mode": 0,
        "trade_allowed": True,
        "trade_expert": True,
        "balance": 10_000.0,
        "equity": 10_000.0,
        "margin": 0.0,
        "margin_free": 10_000.0,
        "margin_level": None,
        "leverage": LEVERAGE,
        "profit": 0.0,
        "margin_mode": 2,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def symbol_info(**overrides: Any) -> Any:
    from types import SimpleNamespace

    fields: dict[str, Any] = {
        "currency_base": "EUR",
        "currency_profit": "USD",
        "trade_contract_size": 100_000.0,
        "digits": 5,
        "point": 1e-05,
        "trade_tick_size": 1e-05,
        "trade_tick_value": 1.0,
        "volume_min": 0.01,
        "volume_max": 100.0,
        "volume_step": 0.01,
        "trade_stops_level": 10,
        "trade_freeze_level": 0,
        "filling_mode": 3,
        "trade_mode": 4,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def order_check_result(**overrides: Any) -> Any:
    from types import SimpleNamespace

    fields: dict[str, Any] = {
        "retcode": 0,
        "balance": 10_000.0,
        "equity": 10_000.0,
        "profit": 0.0,
        "margin": 43.2,
        "margin_free": 9_956.8,
        "margin_level": 23_150.0,
        "comment": "Done",
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


class FakeMt5:
    """A flat demo account, a stable EUR/USD spec, and an accepting

    `order_check` — everything `ExecutionOrchestrator.run_once()` needs to
    reach `ORDER_CHECKED` for a clean capsule, plus counters proving
    `order_send` is never reached.
    """

    COPY_TICKS_ALL = 3
    TIMEFRAME_M1 = 1
    TIMEFRAME_M5 = 5
    TIMEFRAME_M15 = 15
    TIMEFRAME_M30 = 30
    TIMEFRAME_H1 = 16385
    TIMEFRAME_H4 = 16388
    TIMEFRAME_D1 = 16408
    TRADE_ACTION_DEAL = 1
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_IOC = 1
    TRADE_RETCODE_DONE = 0

    def __init__(self, *, tick_bid: float = 1.08500, tick_ask: float = 1.08512) -> None:
        self._tick_bid = tick_bid
        self._tick_ask = tick_ask
        self.order_check_requests: list[dict[str, Any]] = []
        self.order_send_calls = 0

    def initialize(self, *_a: Any, **_k: Any) -> bool:
        return True

    def login(self, *_a: Any, **_k: Any) -> bool:
        return True

    def shutdown(self) -> None:
        pass

    def last_error(self) -> tuple[int, str]:
        return (1, "Success")

    def version(self) -> tuple[Any, ...]:
        return (500, 4620, "27 Aug 2026")

    def terminal_info(self) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(connected=True, trade_allowed=True, ping_last=10)

    def account_info(self) -> Any:
        return account_info()

    def symbols_get(self, *_a: Any, **_k: Any) -> tuple[Any, ...]:
        from types import SimpleNamespace

        return (SimpleNamespace(name=BROKER_SYMBOL),)

    def symbol_select(self, *_a: Any, **_k: Any) -> bool:
        return True

    def symbol_info(self, _symbol: str) -> Any:
        return symbol_info()

    def symbol_info_tick(self, _symbol: str) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(
            bid=self._tick_bid, ask=self._tick_ask, time=int(FIXED_NOW.timestamp())
        )

    def copy_rates_from_pos(self, *_a: Any, **_k: Any) -> tuple[Any, ...]:
        return ()

    def copy_ticks_from(self, *_a: Any, **_k: Any) -> tuple[Any, ...]:
        from types import SimpleNamespace

        return (
            SimpleNamespace(
                bid=self._tick_bid,
                ask=self._tick_ask,
                last=0.0,
                time=int(FIXED_NOW.timestamp()),
                time_msc=int(FIXED_NOW.timestamp() * 1000),
                volume=0,
                flags=6,
            ),
        )

    def positions_get(self, *_a: Any, **_k: Any) -> tuple[Any, ...]:
        return ()

    def orders_get(self, *_a: Any, **_k: Any) -> tuple[Any, ...]:
        return ()

    def order_check(self, request: dict[str, Any]) -> Any:
        self.order_check_requests.append(request)
        return order_check_result()

    def order_send(self, *_a: Any, **_k: Any) -> Any:
        self.order_send_calls += 1
        raise AssertionError("order_send must never be called by ExecutionOrchestrator")

    def cancel_pending_orders(self, *_a: Any, **_k: Any) -> Any:
        raise AssertionError("cancel_pending_orders must never be called")

    def close_all_positions(self, *_a: Any, **_k: Any) -> Any:
        raise AssertionError("close_all_positions must never be called")


def guard() -> AccountGuardConfig:
    return AccountGuardConfig.model_validate(
        {
            "expected_server": SERVER,
            "expected_login": None,
            "require_demo_account": True,
            "expected_currency": CURRENCY,
            "expected_leverage": LEVERAGE,
        }
    )


def spec() -> InstrumentSpec:
    return InstrumentSpec(
        canonical_symbol="EUR/USD",
        broker_symbol=BROKER_SYMBOL,
        currency_base="EUR",
        currency_profit="USD",
        contract_size=Decimal("100000"),
        digits=5,
        point=Decimal("0.00001"),
        tick_size=Decimal("0.00001"),
        tick_value=Decimal("1"),
        volume_min=Decimal("0.01"),
        volume_max=Decimal("100"),
        volume_step=Decimal("0.01"),
        stops_level=10,
        freeze_level=0,
        filling_modes=("IOC", "FOK"),
        trade_mode="FULL",
        captured_at_utc=FIXED_NOW,
    )


def platform_config(*, expected_spec_version: str) -> PlatformConfig:
    return PlatformConfig(
        environment=Environment.PAPER,
        markets=(
            MarketConfig(
                canonical_symbol="EUR/USD",
                enabled=True,
                expected_spec_version=expected_spec_version,
            ),
        ),
        risk=RiskConfig.model_validate(
            {
                "max_risk_per_trade": "0.005",
                "max_open_risk": "0.02",
                "max_daily_loss": "0.02",
                "max_drawdown": "0.10",
                "max_orders_per_hour": 6,
                "max_open_positions": 1,
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
            {
                "strategy_id": "baseline_v1",
                "strategy_version": STRATEGY_VERSION,
                "model_version": None,
            }
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


def sealed_capsule(engine: Engine, config: PlatformConfig, **overrides: Any) -> DecisionCapsule:
    intent = overrides.pop("trade_intent", None) or make_intent(
        created_at_utc=FIXED_NOW,
        expires_at_utc=FIXED_NOW + timedelta(minutes=10),
        reference_price="1.08500",
        stop_loss_price="1.08000",
        take_profit_price="1.09000",
        requested_risk_fraction="0.005",
    )
    fields: dict[str, Any] = {
        "capsule_id": uuid4(),
        "occurred_at_utc": FIXED_NOW,
        "correlation_id": uuid4(),
        "canonical_symbol": "EUR/USD",
        "broker_symbol": BROKER_SYMBOL,
        "market_snapshot_id": uuid4(),
        "feature_set_version": "features-v1",
        "feature_values_hash": "abc123",
        "strategy_version": STRATEGY_VERSION,
        "model_version": None,
        "trade_intent": intent,
        "risk_config_version": config.config_version,
        "risk_decision": make_risk_decision(
            intent.intent_id,
            risk_config_version=config.config_version,
            approved_volume="0.05",
            account_equity="10000",
            stop_distance_points=500,
            risk_amount="50",
        ),
        "supervisor_decision": make_supervisor_decision(intent.intent_id),
        "code_commit": "deadbeef",
        "environment": Environment.PAPER,
    }
    fields.update(overrides)
    capsule = DecisionCapsule(**fields)
    CapsuleStore(engine).seal(capsule)
    return capsule


def orchestrator(
    engine: Engine,
    config: PlatformConfig,
    fake: FakeMt5,
    *,
    activation_watermark: datetime | None,
) -> ExecutionOrchestrator:
    client = Mt5Client(fake)
    client.connect(Mt5Credentials(login=5_000_123, password="x", server=SERVER))
    adapter = OrderCheckMt5Gateway(client, guard(), clock=lambda: FIXED_NOW + timedelta(seconds=1))
    return ExecutionOrchestrator(
        config,
        capsules=CapsuleStore(engine),
        requests=ExecutionRequestStore(engine),
        events=ExecutionEventStore(engine),
        broker_state=BrokerStateStore(engine),
        instrument_specs=InstrumentSpecStore(engine),
        session_store=PostgresRiskSessionStore(engine),
        kill_switch=KillSwitch(),
        adapter=adapter,
        canonical_symbol="EUR/USD",
        activation_watermark=activation_watermark,
        worker_id="test-worker",
        clock=lambda: FIXED_NOW + timedelta(seconds=1),
    )


class TestEndToEnd:
    def test_a_clean_eligible_capsule_reaches_order_checked(self, engine: Engine) -> None:
        the_spec = spec()
        InstrumentSpecStore(engine).record(the_spec)
        config = platform_config(expected_spec_version=the_spec.spec_version)
        capsule = sealed_capsule(engine, config)
        fake = FakeMt5()

        orch = orchestrator(
            engine, config, fake, activation_watermark=FIXED_NOW - timedelta(seconds=1)
        )
        outcomes = orch.run_once()

        assert len(outcomes) == 1
        assert outcomes[0].capsule_id == capsule.capsule_id
        assert outcomes[0].event_type == ExecutionEventType.ORDER_CHECKED
        assert fake.order_send_calls == 0
        assert fake.order_check_requests  # the real order_check call happened

        events = ExecutionEventStore(engine).events_for(outcomes[0].order_request_id)
        event_types = [event.event_type for event in events]
        assert event_types == [
            ExecutionEventType.REQUEST_CLAIMED,
            ExecutionEventType.ORDER_CHECKED,
        ]

    def test_order_send_is_never_called_even_when_everything_passes(self, engine: Engine) -> None:
        """The hard assertion: not "the test passed", but that the one method

        which would place a real order was never invoked.
        """
        the_spec = spec()
        InstrumentSpecStore(engine).record(the_spec)
        config = platform_config(expected_spec_version=the_spec.spec_version)
        sealed_capsule(engine, config)
        fake = FakeMt5()

        orchestrator(
            engine, config, fake, activation_watermark=FIXED_NOW - timedelta(seconds=1)
        ).run_once()

        assert fake.order_send_calls == 0

    def test_a_capsule_sealed_before_the_watermark_is_ineligible_and_touches_no_broker(
        self, engine: Engine
    ) -> None:
        the_spec = spec()
        InstrumentSpecStore(engine).record(the_spec)
        config = platform_config(expected_spec_version=the_spec.spec_version)
        capsule = sealed_capsule(engine, config)
        fake = FakeMt5()

        # Watermark strictly after the capsule was sealed.
        orch = orchestrator(
            engine, config, fake, activation_watermark=FIXED_NOW + timedelta(hours=1)
        )
        outcomes = orch.run_once()

        assert len(outcomes) == 1
        assert outcomes[0].capsule_id == capsule.capsule_id
        assert outcomes[0].event_type == ExecutionEventType.INELIGIBLE
        assert not fake.order_check_requests
        assert fake.order_send_calls == 0

    def test_no_watermark_ever_set_means_nothing_is_ever_eligible(self, engine: Engine) -> None:
        """The shipped-config default: `activation_watermark=None`. Every

        capsule, however clean, is refused — "building the execution path
        != enabling the execution path."
        """
        the_spec = spec()
        InstrumentSpecStore(engine).record(the_spec)
        config = platform_config(expected_spec_version=the_spec.spec_version)
        sealed_capsule(engine, config)
        fake = FakeMt5()

        orch = orchestrator(engine, config, fake, activation_watermark=None)
        outcomes = orch.run_once()

        assert len(outcomes) == 1
        assert outcomes[0].event_type == ExecutionEventType.INELIGIBLE
        assert fake.order_send_calls == 0

    def test_a_second_run_once_does_not_reprocess_an_already_claimed_capsule(
        self, engine: Engine
    ) -> None:
        the_spec = spec()
        InstrumentSpecStore(engine).record(the_spec)
        config = platform_config(expected_spec_version=the_spec.spec_version)
        sealed_capsule(engine, config)
        fake = FakeMt5()
        orch = orchestrator(
            engine, config, fake, activation_watermark=FIXED_NOW - timedelta(seconds=1)
        )

        first = orch.run_once()
        second = orch.run_once()

        assert len(first) == 1
        assert second == ()
        assert len(fake.order_check_requests) == 1

    def test_an_unpinned_instrument_spec_blocks_on_reconciliation(self, engine: Engine) -> None:
        the_spec = spec()
        InstrumentSpecStore(engine).record(the_spec)
        # No expected_spec_version pinned -> reconciliation must read UNKNOWN.
        config = platform_config(expected_spec_version=the_spec.spec_version).model_copy(
            update={
                "markets": (
                    MarketConfig(
                        canonical_symbol="EUR/USD", enabled=True, expected_spec_version=None
                    ),
                )
            }
        )
        sealed_capsule(engine, config)
        fake = FakeMt5()

        orch = orchestrator(
            engine, config, fake, activation_watermark=FIXED_NOW - timedelta(seconds=1)
        )
        outcomes = orch.run_once()

        assert len(outcomes) == 1
        assert outcomes[0].event_type == ExecutionEventType.RECONCILIATION_BLOCKED
        assert not fake.order_check_requests
        assert fake.order_send_calls == 0
