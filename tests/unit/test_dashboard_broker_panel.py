"""`dashboard.broker_panel`: account/positions/pending-orders read model.

The one invariant every test here ultimately checks (work order §10's
truthfulness rule): zero rows only ever means "confirmed none" when the
matching `*_set_state` on the same snapshot is `COMPLETE` — a `FAILED`/
`UNKNOWN` set must render as unknown, never as a silently-empty book.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from crumblr.dashboard.broker_panel import build_broker_read_model
from crumblr.domain.enums import SnapshotCompleteness
from crumblr.domain.models import (
    BrokerAccountSnapshot,
    BrokerPendingOrderSnapshot,
    BrokerPositionSnapshot,
)
from tests.conftest import (
    make_broker_account_snapshot,
    make_broker_pending_order_snapshot,
    make_broker_position_snapshot,
)


@dataclass
class FakeBrokerStateStore:
    account: BrokerAccountSnapshot | None
    positions: tuple[BrokerPositionSnapshot, ...] = ()
    pending_orders: tuple[BrokerPendingOrderSnapshot, ...] = ()
    positions_for_calls: list[UUID] | None = None
    pending_orders_for_calls: list[UUID] | None = None

    def __post_init__(self) -> None:
        self.positions_for_calls = []
        self.pending_orders_for_calls = []

    def latest_account_snapshot(self) -> BrokerAccountSnapshot | None:
        return self.account

    def positions_for(self, snapshot_id: UUID) -> tuple[BrokerPositionSnapshot, ...]:
        assert self.positions_for_calls is not None
        self.positions_for_calls.append(snapshot_id)
        return self.positions

    def pending_orders_for(self, snapshot_id: UUID) -> tuple[BrokerPendingOrderSnapshot, ...]:
        assert self.pending_orders_for_calls is not None
        self.pending_orders_for_calls.append(snapshot_id)
        return self.pending_orders


def test_no_snapshot_at_all_is_no_evidence_not_a_zeroed_account() -> None:
    store: Any = FakeBrokerStateStore(account=None)

    model = build_broker_read_model(broker_state=store)

    assert model.account is None
    assert model.positions == ()
    assert model.pending_orders == ()


def test_account_ref_is_passed_through_verbatim_already_a_fingerprint() -> None:
    snapshot = make_broker_account_snapshot(account_ref="already-masked-fingerprint")
    store: Any = FakeBrokerStateStore(account=snapshot)

    model = build_broker_read_model(broker_state=store)

    assert model.account is not None
    assert model.account.account_ref == "already-masked-fingerprint"


def test_a_complete_set_with_zero_positions_reads_as_confirmed_empty() -> None:
    snapshot = make_broker_account_snapshot(position_set_state=SnapshotCompleteness.COMPLETE)
    store: Any = FakeBrokerStateStore(account=snapshot, positions=())

    model = build_broker_read_model(broker_state=store)

    assert model.account is not None
    assert model.account.position_set_state == "COMPLETE"
    assert model.positions == ()
    assert store.positions_for_calls == [snapshot.snapshot_id]


def test_a_failed_position_set_never_renders_as_confirmed_empty() -> None:
    snapshot = make_broker_account_snapshot(position_set_state=SnapshotCompleteness.FAILED)
    store: Any = FakeBrokerStateStore(
        account=snapshot, positions=(make_broker_position_snapshot(snapshot.snapshot_id),)
    )

    model = build_broker_read_model(broker_state=store)

    # Even though the fake store *has* rows, an incomplete set must not
    # surface them — the store is never even asked.
    assert model.positions == ()
    assert store.positions_for_calls == []


def test_an_unknown_position_set_never_renders_as_confirmed_empty() -> None:
    snapshot = make_broker_account_snapshot(position_set_state=SnapshotCompleteness.UNKNOWN)
    store: Any = FakeBrokerStateStore(account=snapshot)

    model = build_broker_read_model(broker_state=store)

    assert model.positions == ()
    assert store.positions_for_calls == []


def test_a_complete_position_set_with_real_rows_is_shown() -> None:
    snapshot = make_broker_account_snapshot(position_set_state=SnapshotCompleteness.COMPLETE)
    position = make_broker_position_snapshot(snapshot.snapshot_id, magic=42, comment="test")
    store: Any = FakeBrokerStateStore(account=snapshot, positions=(position,))

    model = build_broker_read_model(broker_state=store)

    assert len(model.positions) == 1
    row = model.positions[0]
    assert row.canonical_symbol == "EUR/USD"
    assert row.broker_symbol == "EURUSD"
    assert row.side == "BUY"
    assert row.volume == "0.05"
    assert row.open_price == "1.08512"
    assert row.current_price == "1.08600"
    assert row.magic == 42
    assert row.comment == "test"


def test_pending_orders_follow_the_same_completeness_discipline_independently() -> None:
    """Position and pending-order completeness are independent facts on the
    same snapshot — one can be COMPLETE while the other is not."""
    snapshot = make_broker_account_snapshot(
        position_set_state=SnapshotCompleteness.COMPLETE,
        pending_order_set_state=SnapshotCompleteness.FAILED,
    )
    order = make_broker_pending_order_snapshot(snapshot.snapshot_id)
    store: Any = FakeBrokerStateStore(account=snapshot, positions=(), pending_orders=(order,))

    model = build_broker_read_model(broker_state=store)

    assert model.positions == ()  # COMPLETE, genuinely empty
    assert model.pending_orders == ()  # FAILED, must not surface the row
    assert store.pending_orders_for_calls == []


def test_a_complete_pending_order_set_with_real_rows_is_shown() -> None:
    snapshot = make_broker_account_snapshot(pending_order_set_state=SnapshotCompleteness.COMPLETE)
    order = make_broker_pending_order_snapshot(snapshot.snapshot_id, order_id=999)
    store: Any = FakeBrokerStateStore(account=snapshot, pending_orders=(order,))

    model = build_broker_read_model(broker_state=store)

    assert len(model.pending_orders) == 1
    assert model.pending_orders[0].order_id == 999
    assert model.pending_orders[0].order_type == "BUY_LIMIT"


def test_only_one_latest_account_snapshot_call_per_build_no_mid_request_mismatch() -> None:
    """Regression guard for the exact race the module docstring names: if
    positions/pending-orders each re-queried `latest_account_snapshot()`
    independently, a new snapshot arriving between those calls could bind
    the account card to one row and the tables to another."""
    snapshot = make_broker_account_snapshot()
    calls = {"count": 0}

    class CountingStore(FakeBrokerStateStore):
        def latest_account_snapshot(self) -> BrokerAccountSnapshot | None:
            calls["count"] += 1
            return super().latest_account_snapshot()

    store: Any = CountingStore(account=snapshot)

    build_broker_read_model(broker_state=store)

    assert calls["count"] == 1


def test_margin_level_and_terminal_trade_allowed_are_none_when_the_broker_never_reported_them() -> (
    None
):
    snapshot = make_broker_account_snapshot(margin_level=None, terminal_trade_allowed=None)
    store: Any = FakeBrokerStateStore(account=snapshot)

    model = build_broker_read_model(broker_state=store)

    assert model.account is not None
    assert model.account.margin_level is None
    assert model.account.terminal_trade_allowed is None
