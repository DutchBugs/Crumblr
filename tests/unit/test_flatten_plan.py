"""Core critical path item 7's pure builder: `application/flatten_plan.py`."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from crumblr.application.flatten_plan import build_flatten_plan
from crumblr.domain.enums import Environment, Side
from crumblr.domain.models import PositionState
from tests.conftest import FIXED_NOW


def position(**overrides: Any) -> PositionState:
    fields: dict[str, Any] = {
        "ticket": 900001,
        "broker_symbol": "EURUSD",
        "side": Side.BUY,
        "volume": Decimal("0.05"),
        "open_price": Decimal("1.08500"),
        "current_price": Decimal("1.08600"),
        "stop_loss_price": Decimal("1.08300"),
        "take_profit_price": Decimal("1.08900"),
        "opened_at_utc": FIXED_NOW - timedelta(hours=2),
        "profit": Decimal("5.00"),
        "swap": Decimal("0.00"),
        "magic": 12345,
        "observed_at_utc": FIXED_NOW,
    }
    fields.update(overrides)
    return PositionState(**fields)


def _build(**overrides: Any) -> Any:
    fields: dict[str, Any] = {
        "positions": [position()],
        "flatten_request_id": uuid4(),
        "environment": Environment.PAPER,
        "canonical_symbol": "EUR/USD",
        "trading_day": FIXED_NOW.date(),
        "session_close_utc": FIXED_NOW + timedelta(hours=5),
        "flatten_deadline_utc": FIXED_NOW + timedelta(hours=4),
        "past_deadline": True,
        "broker_state_snapshot_id": uuid4(),
        "now": FIXED_NOW,
    }
    fields.update(overrides)
    return build_flatten_plan(**fields)


class TestBuildFlattenPlan:
    def test_close_side_is_the_inverse_of_position_side(self) -> None:
        plan = _build(positions=[position(side=Side.BUY)])
        assert plan.instructions[0].position_side is Side.BUY
        assert plan.instructions[0].close_side is Side.SELL

        plan = _build(positions=[position(side=Side.SELL)])
        assert plan.instructions[0].position_side is Side.SELL
        assert plan.instructions[0].close_side is Side.BUY

    def test_close_volume_is_the_brokers_reported_volume_not_risk_sized(self) -> None:
        broker_volume = Decimal("0.37")
        plan = _build(positions=[position(volume=broker_volume)])
        assert plan.instructions[0].volume == broker_volume

    def test_ticket_magic_and_open_time_are_preserved(self) -> None:
        opened = FIXED_NOW - timedelta(hours=6)
        plan = _build(positions=[position(ticket=987654, magic=555, opened_at_utc=opened)])
        instruction = plan.instructions[0]
        assert instruction.ticket == 987654
        assert instruction.magic == 555
        assert instruction.opened_at_utc == opened

    def test_a_position_from_before_the_weekly_close_is_marked_crossed_weekly_close(self) -> None:
        """`FIXED_NOW` is a Monday; four days earlier is the prior week's

        Thursday — a genuine weekend crossing, not merely an earlier
        calendar day (owner risk policy v1, D1.5: an ordinary weekday
        rollover is no longer a breach, only a weekly-close crossing is)."""
        opened_last_week = FIXED_NOW - timedelta(days=4)
        plan = _build(positions=[position(opened_at_utc=opened_last_week)], past_deadline=False)
        assert plan.instructions[0].crossed_weekly_close is True
        assert plan.crossed_weekly_close is True

    def test_a_position_opened_today_is_not_marked_crossed_weekly_close(self) -> None:
        plan = _build(positions=[position(opened_at_utc=FIXED_NOW - timedelta(minutes=5))])
        assert plan.instructions[0].crossed_weekly_close is False

    def test_an_empty_position_tuple_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="at least one position"):
            _build(positions=[])

    def test_multiple_positions_each_get_their_own_instruction(self) -> None:
        plan = _build(
            positions=[
                position(ticket=1, side=Side.BUY),
                position(ticket=2, side=Side.SELL),
            ]
        )
        assert len(plan.instructions) == 2
        tickets = {instruction.ticket for instruction in plan.instructions}
        assert tickets == {1, 2}
