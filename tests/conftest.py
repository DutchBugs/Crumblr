"""Shared builders.

Each builder returns a valid object; tests mutate one field at a time so a
failure names the invariant that broke rather than a wall of validation noise.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from crumblr.domain.enums import (
    DataQuality,
    EntryType,
    Environment,
    IncidentSeverity,
    RiskVerdict,
    SessionState,
    Side,
    SnapshotCompleteness,
    SupervisorVerdict,
)
from crumblr.domain.models import (
    AccountState,
    ApprovedOrder,
    Bar,
    BrokerAccountSnapshot,
    BrokerPendingOrderSnapshot,
    BrokerPositionSnapshot,
    Incident,
    InstrumentSpec,
    MarketSnapshot,
    RiskDecision,
    SupervisorDecision,
    TradeIntent,
)

FIXED_NOW = datetime(2026, 8, 17, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def now() -> datetime:
    return FIXED_NOW


def make_bar(**overrides: Any) -> Bar:
    fields: dict[str, Any] = {
        "open_time_utc": FIXED_NOW,
        "open": Decimal("1.08500"),
        "high": Decimal("1.08600"),
        "low": Decimal("1.08400"),
        "close": Decimal("1.08550"),
        "tick_volume": 120,
    }
    fields.update(overrides)
    return Bar(**fields)


def make_instrument_spec(**overrides: Any) -> InstrumentSpec:
    fields: dict[str, Any] = {
        "canonical_symbol": "EUR/USD",
        "broker_symbol": "EURUSD",
        "currency_base": "EUR",
        "currency_profit": "USD",
        "contract_size": Decimal("100000"),
        "digits": 5,
        "point": Decimal("0.00001"),
        "tick_size": Decimal("0.00001"),
        "tick_value": Decimal("1"),
        "volume_min": Decimal("0.01"),
        "volume_max": Decimal("100"),
        "volume_step": Decimal("0.01"),
        "stops_level": 10,
        "freeze_level": 0,
        "filling_modes": ("IOC", "FOK"),
        "trade_mode": "FULL",
        "captured_at_utc": FIXED_NOW,
    }
    fields.update(overrides)
    return InstrumentSpec(**fields)


def make_snapshot(**overrides: Any) -> MarketSnapshot:
    fields: dict[str, Any] = {
        "snapshot_id": uuid4(),
        "symbol": "EUR/USD",
        "event_time_utc": FIXED_NOW,
        "received_time_utc": FIXED_NOW + timedelta(milliseconds=8),
        "bid": Decimal("1.08500"),
        "ask": Decimal("1.08512"),
        "spread_points": 12,
        "timeframe": "M1",
        "bars": (make_bar(),),
        "session_state": SessionState.OPEN,
        "symbol_spec_version": "spec-v1",
        "data_quality": DataQuality.GOOD,
    }
    fields.update(overrides)
    return MarketSnapshot(**fields)


def make_intent(**overrides: Any) -> TradeIntent:
    fields: dict[str, Any] = {
        "intent_id": uuid4(),
        "strategy_id": "baseline_v1",
        "strategy_version": "0.1.0",
        "model_version": None,
        "symbol": "EUR/USD",
        "side": Side.BUY,
        "created_at_utc": FIXED_NOW,
        "expires_at_utc": FIXED_NOW + timedelta(seconds=30),
        "entry_type": EntryType.MARKET,
        "reference_price": Decimal("1.08500"),
        "stop_loss_price": Decimal("1.08300"),
        "take_profit_price": Decimal("1.08900"),
        "confidence": 0.62,
        "reason_codes": ("trend_up", "spread_ok"),
        "requested_risk_fraction": Decimal("0.005"),
        "feature_snapshot_id": uuid4(),
    }
    fields.update(overrides)
    return TradeIntent(**fields)


def make_risk_decision(intent_id: UUID | None = None, **overrides: Any) -> RiskDecision:
    fields: dict[str, Any] = {
        "decision_id": uuid4(),
        "intent_id": intent_id or uuid4(),
        "verdict": RiskVerdict.PASS,
        "reason_codes": (),
        "decided_at_utc": FIXED_NOW,
        "risk_config_version": "cfg-v1",
        "approved_volume": Decimal("0.05"),
        "account_equity": Decimal("10000"),
        "stop_distance_points": 200,
        "risk_amount": Decimal("50"),
    }
    fields.update(overrides)
    return RiskDecision(**fields)


def make_supervisor_decision(intent_id: UUID | None = None, **overrides: Any) -> SupervisorDecision:
    fields: dict[str, Any] = {
        "decision_id": uuid4(),
        "intent_id": intent_id or uuid4(),
        "verdict": SupervisorVerdict.APPROVE,
        "reason_codes": (),
        "decided_at_utc": FIXED_NOW,
        "policy_version": "policy-v1",
    }
    fields.update(overrides)
    return SupervisorDecision(**fields)


def make_approved_order(**overrides: Any) -> ApprovedOrder:
    fields: dict[str, Any] = {
        "order_request_id": uuid4(),
        "intent_id": uuid4(),
        "risk_decision_id": uuid4(),
        "supervisor_decision_id": uuid4(),
        "broker_symbol": "EURUSD",
        "side": Side.BUY,
        "entry_type": EntryType.MARKET,
        "volume": Decimal("0.05"),
        "price": None,
        "stop_loss_price": Decimal("1.08300"),
        "take_profit_price": Decimal("1.08900"),
        "max_slippage_points": 20,
        "created_at_utc": FIXED_NOW,
        "expires_at_utc": FIXED_NOW + timedelta(seconds=10),
        "environment": Environment.PAPER,
    }
    fields.update(overrides)
    return ApprovedOrder(**fields)


def make_incident(**overrides: Any) -> Incident:
    fields: dict[str, Any] = {
        "incident_id": uuid4(),
        "severity": IncidentSeverity.SEV_2,
        "component": "mt5_gateway",
        "summary": "duplicate order after reconnect",
        "opened_at_utc": FIXED_NOW,
        "closed_at_utc": None,
        "root_cause": None,
    }
    fields.update(overrides)
    return Incident(**fields)


def make_account_state(**overrides: Any) -> AccountState:
    fields: dict[str, Any] = {
        "login": 5000123,
        "server": "DemoBroker-Demo",
        "currency": "EUR",
        "is_demo": True,
        "trade_allowed": True,
        "expert_allowed": True,
        "connected": True,
        "balance": Decimal("10000"),
        "equity": Decimal("10000"),
        "margin": Decimal("0"),
        "margin_free": Decimal("10000"),
        "margin_level": None,
        "leverage": 30,
        "observed_at_utc": FIXED_NOW,
    }
    fields.update(overrides)
    return AccountState(**fields)


def make_broker_account_snapshot(**overrides: Any) -> BrokerAccountSnapshot:
    fields: dict[str, Any] = {
        "snapshot_id": uuid4(),
        "observed_at_utc": FIXED_NOW,
        "recorded_at_utc": FIXED_NOW,
        "environment": Environment.PAPER,
        "server": "DemoBroker-Demo",
        "account_ref": "abc123def4567890",
        "currency": "EUR",
        "leverage": 30,
        "margin_mode": "RETAIL_HEDGING",
        "balance": Decimal("10000"),
        "equity": Decimal("10012.5"),
        "profit": Decimal("12.5"),
        "margin": Decimal("120"),
        "margin_free": Decimal("9892.5"),
        "margin_level": Decimal("8343.75"),
        "account_trade_allowed": True,
        "terminal_trade_allowed": True,
        "position_set_state": SnapshotCompleteness.COMPLETE,
        "pending_order_set_state": SnapshotCompleteness.COMPLETE,
    }
    fields.update(overrides)
    return BrokerAccountSnapshot(**fields)


def make_broker_position_snapshot(
    snapshot_id: UUID | None = None, **overrides: Any
) -> BrokerPositionSnapshot:
    fields: dict[str, Any] = {
        "snapshot_id": snapshot_id or uuid4(),
        "observed_at_utc": FIXED_NOW,
        "ticket": 123_456,
        "canonical_symbol": "EUR/USD",
        "broker_symbol": "EURUSD",
        "side": Side.BUY,
        "volume": Decimal("0.05"),
        "opened_at_utc": FIXED_NOW,
        "open_price": Decimal("1.08512"),
        "current_price": Decimal("1.08600"),
        "stop_loss_price": Decimal("1.08012"),
        "take_profit_price": Decimal("1.09512"),
        "profit": Decimal("12.5"),
        "swap": Decimal("-0.35"),
        "magic": None,
        "comment": None,
    }
    fields.update(overrides)
    return BrokerPositionSnapshot(**fields)


def make_broker_pending_order_snapshot(
    snapshot_id: UUID | None = None, **overrides: Any
) -> BrokerPendingOrderSnapshot:
    fields: dict[str, Any] = {
        "snapshot_id": snapshot_id or uuid4(),
        "observed_at_utc": FIXED_NOW,
        "order_id": 654_321,
        "canonical_symbol": "EUR/USD",
        "broker_symbol": "EURUSD",
        "order_type": "BUY_LIMIT",
        "state": "PLACED",
        "volume": Decimal("0.05"),
        "price": Decimal("1.08000"),
        "stop_loss_price": Decimal("1.07500"),
        "take_profit_price": Decimal("1.09000"),
        "expires_at_utc": None,
    }
    fields.update(overrides)
    return BrokerPendingOrderSnapshot(**fields)


def paper_config_payload() -> dict[str, Any]:
    """A minimal valid paper configuration, as a merged mapping."""
    return {
        "environment": Environment.PAPER.value,
        "markets": [{"canonical_symbol": "EUR/USD", "enabled": True}],
        "risk": {
            "max_risk_per_trade": "0.005",
            "max_open_risk": "0.02",
            "max_daily_loss": "0.02",
            "max_drawdown": "0.10",
            "max_orders_per_hour": 6,
            "max_open_positions": 1,
            "min_stop_distance_points": 50,
        },
        "execution": {
            "max_spread_points": 25,
            "max_market_data_age_ms": 2000,
            "order_timeout_ms": 5000,
            "max_slippage_points": 20,
        },
        "trading_agent": {
            "strategy_id": "baseline_v1",
            "strategy_version": "0.1.0",
            "model_version": None,
        },
        "supervisor": {
            "enabled": True,
            "veto_on_unknown_regime": True,
            "halt_on_reconciliation_mismatch": True,
            "policy_version": "policy-v1",
            "max_intents_per_hour": None,
        },
        "intraday": {
            "enabled": True,
            "last_entry_minutes_before_close": 60,
            "flatten_minutes_before_close": 15,
        },
        "account_guard": {
            "expected_server": "DemoBroker-Demo",
            "expected_login": None,
            "require_demo_account": True,
        },
    }
