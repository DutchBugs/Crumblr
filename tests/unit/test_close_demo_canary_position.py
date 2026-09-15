"""`scripts/close_demo_canary_position.py`'s pure logic, in isolation --

no MT5, no network. These pin the exact match/mismatch and instruction-
construction rules a real close depends on, since a defect here would be
found live, against a real broker position, if it were found at all.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from scripts.close_demo_canary_position import (
    _build_close_instruction,
    _find_target_position,
    _verify_target_position,
)

from crumblr.domain.enums import Side
from crumblr.domain.hashing import mt5_magic_number
from crumblr.domain.models import PositionState

_NOW = datetime(2026, 9, 15, 9, 38, 23, tzinfo=UTC)
_ORDER_REQUEST_ID = UUID("e845ddac-028e-5bf7-9f86-785119674752")
_MAGIC = mt5_magic_number(_ORDER_REQUEST_ID)


def _position(**overrides: Any) -> PositionState:
    fields: dict[str, Any] = {
        "ticket": 89517316,
        "broker_symbol": "EURUSD",
        "side": Side.BUY,
        "volume": Decimal("0.02"),
        "open_price": Decimal("1.15346"),
        "current_price": Decimal("1.15345"),
        "stop_loss_price": Decimal("1.15148"),
        "take_profit_price": Decimal("1.15748"),
        "opened_at_utc": _NOW,
        "profit": Decimal("-0.02"),
        "swap": Decimal("0"),
        "magic": _MAGIC,
        "observed_at_utc": _NOW,
    }
    fields.update(overrides)
    return PositionState(**fields)


class TestFindTargetPosition:
    def test_a_single_match_is_returned_with_no_reason(self) -> None:
        target = _position()
        other = _position(ticket=1, magic=999)
        position, reason = _find_target_position((other, target), ticket=89517316)
        assert position is target
        assert reason is None

    def test_no_match_refuses_with_a_count(self) -> None:
        position, reason = _find_target_position((_position(ticket=1),), ticket=89517316)
        assert position is None
        assert reason is not None
        assert "not found among 1 open position" in reason

    def test_zero_positions_refuses_cleanly(self) -> None:
        position, reason = _find_target_position((), ticket=89517316)
        assert position is None
        assert reason is not None
        assert "not found among 0 open position" in reason

    def test_more_than_one_match_refuses_rather_than_picking_one(self) -> None:
        """Two positions sharing a ticket should never happen, but if MT5

        ever reported it, silently picking the first would be exactly the
        kind of ambiguity this runner must never resolve on its own.
        """
        duplicate_a = _position()
        duplicate_b = _position(volume=Decimal("0.03"))
        position, reason = _find_target_position((duplicate_a, duplicate_b), ticket=89517316)
        assert position is None
        assert reason is not None
        assert "matched 2 positions" in reason


class TestVerifyTargetPosition:
    def _verify(self, position: PositionState) -> list[str]:
        return _verify_target_position(
            position,
            expected_broker_symbol="EURUSD",
            expected_side=Side.BUY,
            expected_volume=Decimal("0.02"),
            originating_order_request_id=_ORDER_REQUEST_ID,
        )

    def test_a_clean_match_has_no_problems(self) -> None:
        assert self._verify(_position()) == []

    def test_a_wrong_symbol_is_reported(self) -> None:
        problems = self._verify(_position(broker_symbol="GBPUSD"))
        assert any("broker_symbol mismatch" in p for p in problems)

    def test_a_wrong_side_is_reported(self) -> None:
        problems = self._verify(
            _position(side=Side.SELL, stop_loss_price=Decimal("1.15748"), take_profit_price=None)
        )
        assert any("side mismatch" in p for p in problems)

    def test_a_wrong_volume_is_reported(self) -> None:
        problems = self._verify(_position(volume=Decimal("0.05")))
        assert any("volume mismatch" in p for p in problems)

    def test_a_wrong_magic_is_reported(self) -> None:
        """The single most important check: a ticket number is not proof of

        provenance. A position that merely happens to share the expected
        symbol/side/volume but was opened by something else entirely must
        still be refused.
        """
        problems = self._verify(_position(magic=1))
        assert any("magic mismatch" in p for p in problems)

    def test_every_mismatch_is_collected_not_short_circuited(self) -> None:
        problems = self._verify(_position(broker_symbol="GBPUSD", volume=Decimal("0.05"), magic=1))
        assert len(problems) == 3

    def test_a_magic_of_none_is_reported_not_silently_accepted(self) -> None:
        problems = self._verify(_position(magic=None))
        assert any("magic mismatch" in p for p in problems)


class TestBuildCloseInstruction:
    def test_close_side_is_the_inverse_of_a_buy_position(self) -> None:
        instruction = _build_close_instruction(_position(side=Side.BUY))
        assert instruction.close_side is Side.SELL

    def test_close_side_is_the_inverse_of_a_sell_position(self) -> None:
        instruction = _build_close_instruction(
            _position(side=Side.SELL, stop_loss_price=Decimal("1.15748"), take_profit_price=None)
        )
        assert instruction.close_side is Side.BUY

    def test_volume_is_exactly_the_positions_own_currently_open_volume(self) -> None:
        instruction = _build_close_instruction(_position(volume=Decimal("0.02")))
        assert instruction.volume == Decimal("0.02")

    def test_ticket_broker_symbol_and_magic_are_carried_through_unchanged(self) -> None:
        position = _position()
        instruction = _build_close_instruction(position)
        assert instruction.ticket == position.ticket
        assert instruction.broker_symbol == position.broker_symbol
        assert instruction.magic == position.magic

    def test_crossed_weekly_close_is_always_false_for_this_same_day_manual_close(self) -> None:
        instruction = _build_close_instruction(_position())
        assert instruction.crossed_weekly_close is False

    def test_flatten_request_id_is_a_fresh_uuid_each_call(self) -> None:
        position = _position()
        first = _build_close_instruction(position)
        second = _build_close_instruction(position)
        assert first.flatten_request_id != second.flatten_request_id
        assert isinstance(first.flatten_request_id, type(uuid4()))
