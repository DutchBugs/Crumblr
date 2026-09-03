"""Shared fixtures for `test_execution_orchestrator.py` and

`test_execution_flatten.py` — a fake MT5 terminal and the platform-config
building blocks both files need to construct a real `ExecutionOrchestrator`
against real PostgreSQL. Extracted rather than duplicated, and rather than
cross-imported between test modules (no precedent for that in this repo);
this underscore-prefixed module is not itself collected as a test file.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

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
from crumblr.domain.enums import Environment
from crumblr.domain.hashing import fingerprint
from crumblr.domain.models import InstrumentSpec
from crumblr.mt5_gateway.client import Mt5Client, Mt5Credentials
from crumblr.mt5_gateway.execution import OrderCheckMt5Gateway
from crumblr.persistence.broker_state import BrokerStateStore
from crumblr.persistence.execution import ExecutionEventStore, ExecutionRequestStore
from crumblr.persistence.flatten import FlattenEventStore, FlattenRequestStore
from crumblr.persistence.instrument_specs import InstrumentSpecStore
from crumblr.persistence.journal import CapsuleStore
from crumblr.persistence.risk_session import PostgresRiskSessionStore
from crumblr.risk.kill_switch import KillSwitch
from tests.conftest import FIXED_NOW

SERVER = "Test-Demo"
CURRENCY = "EUR"
LEVERAGE = 30
LOGIN = 5_000_123
BROKER_SYMBOL = "EURUSD"
STRATEGY_VERSION = "0.1.0"

APPROVED_CANARY_ACCOUNT_REF = fingerprint({"login": LOGIN, "server": SERVER})[:16]
"""The `login_hash` a fully-approved test config's `ExecutionConfig

.approved_canary_account_ref` (Phase B item B7) must match for
`account_info()`'s own default identity — computed the same way
`AccountState.login_hash` itself is, never hardcoded, so it cannot
silently drift from `LOGIN`/`SERVER` above."""


def account_info(**overrides: Any) -> Any:
    from types import SimpleNamespace

    fields: dict[str, Any] = {
        "login": LOGIN,
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


def fake_position(**overrides: Any) -> Any:
    """A raw MT5 position, shaped the way `readonly.py::positions()` decodes

    it — core critical path item 6, so a test can simulate a broker-side
    match for a specific `magic` number."""
    from types import SimpleNamespace

    fields: dict[str, Any] = {
        "ticket": 900001,
        "symbol": BROKER_SYMBOL,
        "type": 0,  # MT5_POSITION_TYPE_BUY
        "volume": 0.05,
        "price_open": 1.08500,
        "price_current": 1.08600,
        "sl": 1.08300,
        "tp": 1.08900,
        "time": int(FIXED_NOW.timestamp()),
        "profit": 5.0,
        "swap": 0.0,
        "magic": 0,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


class FakeMt5:
    """A flat demo account, a stable EUR/USD spec, and an accepting

    `order_check` — everything `ExecutionOrchestrator.run_once()` needs to
    reach `ORDER_CHECKED` for a clean capsule, plus counters proving
    `order_send`/`close_all_positions` are never reached.
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
    TRADE_RETCODE_DONE_PARTIAL = 1

    def __init__(self, *, tick_bid: float = 1.08500, tick_ask: float = 1.08512) -> None:
        self._tick_bid = tick_bid
        self._tick_ask = tick_ask
        self.order_check_requests: list[dict[str, Any]] = []
        self.order_send_calls = 0
        self.close_all_positions_calls = 0
        """Incremented *before* the assertion raises, so the counter is

        meaningful even though reaching it fails the test — core critical
        path item 7's direct analogue of `order_send_calls`."""
        self.positions_get_calls = 0
        self.open_positions: tuple[Any, ...] = ()
        """Core critical path item 6: settable so a test can simulate a

        broker-side position matching a specific magic number — never
        populated by order_send itself, which stays unreachable."""

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
        self.positions_get_calls += 1
        return self.open_positions

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
        self.close_all_positions_calls += 1
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


def platform_config(
    *, expected_spec_version: str, intraday: IntradayConfig | None = None
) -> PlatformConfig:
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
        intraday=intraday
        or IntradayConfig.model_validate(
            {
                "enabled": False,
                "last_entry_minutes_before_close": 0,
                "flatten_minutes_before_close": 0,
            }
        ),
    )


def orchestrator(
    engine: Engine,
    config: PlatformConfig,
    fake: FakeMt5,
    *,
    activation_watermark: datetime | None,
    clock: Any = None,
    kill_switch: KillSwitch | None = None,
) -> ExecutionOrchestrator:
    client = Mt5Client(fake)
    client.connect(Mt5Credentials(login=LOGIN, password="x", server=SERVER))
    # The adapter's own clock (broker-clock-offset detection against the
    # fake terminal's fixed tick timestamp) stays constant regardless of
    # what the orchestrator's own clock does — the two are independent.
    adapter = OrderCheckMt5Gateway(client, guard(), clock=lambda: FIXED_NOW + timedelta(seconds=1))
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
        kill_switch=kill_switch or KillSwitch(),
        adapter=adapter,
        canonical_symbol="EUR/USD",
        activation_watermark=activation_watermark,
        worker_id="test-worker",
        clock=clock or (lambda: FIXED_NOW + timedelta(seconds=1)),
    )
