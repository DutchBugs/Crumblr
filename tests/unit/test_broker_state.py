"""`capture_broker_state` (review 1.15 F-047): one gateway read, three

durable contracts. Built against the same fake terminal
`test_mt5_readonly_gateway.py` already uses — this module composes that
gateway's own read methods, so a fake exercising the gateway is the right
fixture, not a second one duplicating it.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from crumblr.application.broker_state import capture_broker_state
from crumblr.domain.enums import Environment, SnapshotCompleteness
from crumblr.mt5_gateway.client import Mt5CallFailedError
from tests.unit.test_mt5_readonly_gateway import FakeMt5, a_pending_order, a_position, gateway


class TestHappyPath:
    def test_an_empty_book_is_complete_not_unknown(self) -> None:
        observation = capture_broker_state(
            gateway(FakeMt5()), environment=Environment.PAPER, canonical_symbol="EUR/USD"
        )
        assert observation.account.position_set_state is SnapshotCompleteness.COMPLETE
        assert observation.account.pending_order_set_state is SnapshotCompleteness.COMPLETE
        assert observation.positions == ()
        assert observation.pending_orders == ()

    def test_the_account_snapshot_carries_balance_equity_and_profit(self) -> None:
        observation = capture_broker_state(
            gateway(FakeMt5()), environment=Environment.PAPER, canonical_symbol="EUR/USD"
        )
        account = observation.account
        assert account.balance == Decimal("10000.0")
        assert account.equity == Decimal("10012.5")
        assert account.profit == Decimal("12.5")
        assert account.margin_mode == "RETAIL_HEDGING"
        assert account.environment is Environment.PAPER

    def test_the_account_snapshot_never_carries_the_raw_login(self) -> None:
        """build.md §21: a credential-shaped value must not reach a persisted row."""
        observation = capture_broker_state(
            gateway(FakeMt5()), environment=Environment.PAPER, canonical_symbol="EUR/USD"
        )
        dumped = observation.account.model_dump(mode="json")
        assert "login" not in dumped
        assert "5000123" not in str(dumped)

    def test_a_position_and_a_pending_order_share_the_account_snapshot_id(self) -> None:
        fake = FakeMt5(positions=(a_position(),), orders=(a_pending_order(),))
        observation = capture_broker_state(
            gateway(fake), environment=Environment.PAPER, canonical_symbol="EUR/USD"
        )
        assert len(observation.positions) == 1
        assert len(observation.pending_orders) == 1
        assert observation.positions[0].snapshot_id == observation.account.snapshot_id
        assert observation.pending_orders[0].snapshot_id == observation.account.snapshot_id

    def test_the_canonical_symbol_is_attached_to_child_rows(self) -> None:
        """`PositionState`/`PendingOrderState` only carry the broker symbol."""
        fake = FakeMt5(positions=(a_position(),), orders=(a_pending_order(),))
        observation = capture_broker_state(
            gateway(fake), environment=Environment.PAPER, canonical_symbol="EUR/USD"
        )
        assert observation.positions[0].canonical_symbol == "EUR/USD"
        assert observation.pending_orders[0].canonical_symbol == "EUR/USD"


class TestPartialFailure:
    """One collection failing must not discard the other, and must not be

    reported as a confirmed-empty book — see `SnapshotCompleteness`.
    """

    def test_a_failed_positions_read_marks_only_that_set_failed(self) -> None:
        fake = FakeMt5(positions=None, error=(-10004, "No IPC connection"))
        observation = capture_broker_state(
            gateway(fake), environment=Environment.PAPER, canonical_symbol="EUR/USD"
        )
        assert observation.account.position_set_state is SnapshotCompleteness.FAILED
        assert observation.account.pending_order_set_state is SnapshotCompleteness.COMPLETE
        assert observation.positions == ()

    def test_a_failed_pending_orders_read_marks_only_that_set_failed(self) -> None:
        fake = FakeMt5(orders=None, error=(-10004, "No IPC connection"))
        observation = capture_broker_state(
            gateway(fake), environment=Environment.PAPER, canonical_symbol="EUR/USD"
        )
        assert observation.account.pending_order_set_state is SnapshotCompleteness.FAILED
        assert observation.account.position_set_state is SnapshotCompleteness.COMPLETE
        assert observation.pending_orders == ()

    def test_an_account_read_failure_is_not_caught_here(self) -> None:
        """No snapshot is worth recording without a valid account read —

        this propagates the same way every other gateway call does.
        """
        fake = FakeMt5(error=(-10004, "No IPC connection"))
        fake.account_info = lambda: None  # type: ignore[method-assign]
        with pytest.raises(Mt5CallFailedError):
            capture_broker_state(
                gateway(fake), environment=Environment.PAPER, canonical_symbol="EUR/USD"
            )
