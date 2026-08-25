"""Capture a durable, auditable snapshot of the broker's own state (F-047).

Review 1.15 §4-§5: the platform persists ticks, bars, journal decisions,
risk-session state and safety state, but nothing durably records the
*observed real broker's* balance/equity/margin, open positions or pending
orders — only ever held in-memory by whichever call last returned them. A
trading system cannot safely say "I know my balance" or "I know I am flat"
on that basis alone. This module turns one gateway read into the three
durable contracts `persistence.broker_state.BrokerStateStore` writes.

**Scope, deliberately.** This captures and returns a snapshot; it does not
decide when to capture one (that is `LiveReader`'s job — connect, reconnect,
and a periodic interval, per review §5 "When to capture broker state") and
it does not compare the snapshot against anything (that is reconciliation,
the review's own next phase, not this one). Composing the two into one
function would make the capture step depend on a reconciliation policy that
does not exist yet.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

from crumblr.domain.enums import Environment, SnapshotCompleteness
from crumblr.domain.hashing import fingerprint
from crumblr.domain.models import (
    BrokerAccountSnapshot,
    BrokerPendingOrderSnapshot,
    BrokerPositionSnapshot,
    PendingOrderState,
    PositionState,
)
from crumblr.domain.timeutils import UtcDatetime, utc_now
from crumblr.mt5_gateway.client import Mt5CallFailedError
from crumblr.mt5_gateway.readonly import ReadOnlyMt5Gateway
from crumblr.observability.logging import get_logger

_log = get_logger("broker_state")


@dataclass(frozen=True)
class BrokerStateObservation:
    """One capture: an account snapshot plus whatever positions/pending

    orders were observed alongside it, all sharing `account.snapshot_id`.
    """

    account: BrokerAccountSnapshot
    positions: tuple[BrokerPositionSnapshot, ...]
    pending_orders: tuple[BrokerPendingOrderSnapshot, ...]


def capture_broker_state(
    gateway: ReadOnlyMt5Gateway,
    *,
    environment: Environment,
    canonical_symbol: str,
    clock: Callable[[], UtcDatetime] = utc_now,
) -> BrokerStateObservation:
    """Read the broker's account, positions and pending orders.

    The account and its extras come from `gateway.account_with_extras()` —
    one `account_info()` read, not two (review 1.16 F-052) — so the stored
    `BrokerAccountSnapshot` always reflects one coherent broker observation
    rather than possibly straddling a real change (a fill, a swap charge)
    that happened between two separate reads.

    Positions and pending orders are read independently, each wrapped in its
    own `try`/`except`: one failing must not discard a successful read of
    the other, and must not be silently reported as "confirmed empty" — see
    `SnapshotCompleteness`. The account read itself is not caught here; an
    account read failing (or the account guard rejecting it) is the same
    fail-closed signal it already is everywhere else this gateway is called,
    and there is no snapshot worth recording without it.
    """
    snapshot_id = uuid4()
    observed_at_utc = clock()

    # F-052: one `account_info()` read, not two — see `account_with_extras`.
    account, extras = gateway.account_with_extras()
    terminal = gateway.terminal_health()

    positions, position_state = _read_positions(gateway)
    pending_orders, pending_order_state = _read_pending_orders(gateway)

    account_snapshot = BrokerAccountSnapshot(
        snapshot_id=snapshot_id,
        observed_at_utc=observed_at_utc,
        recorded_at_utc=clock(),
        environment=environment,
        server=account.server,
        account_ref=fingerprint({"login": account.login, "server": account.server})[:16],
        currency=account.currency,
        leverage=account.leverage,
        margin_mode=extras.margin_mode,
        balance=account.balance,
        equity=account.equity,
        profit=extras.profit,
        margin=account.margin,
        margin_free=account.margin_free,
        margin_level=account.margin_level,
        account_trade_allowed=account.trade_allowed,
        terminal_trade_allowed=terminal.get("trade_allowed"),
        position_set_state=position_state,
        pending_order_set_state=pending_order_state,
    )

    position_snapshots = tuple(
        BrokerPositionSnapshot(
            snapshot_id=snapshot_id,
            observed_at_utc=observed_at_utc,
            ticket=position.ticket,
            canonical_symbol=canonical_symbol,
            broker_symbol=position.broker_symbol,
            side=position.side,
            volume=position.volume,
            opened_at_utc=position.opened_at_utc,
            open_price=position.open_price,
            current_price=position.current_price,
            stop_loss_price=position.stop_loss_price,
            take_profit_price=position.take_profit_price,
            profit=position.profit,
            swap=position.swap,
            magic=position.magic,
        )
        for position in positions
    )
    pending_order_snapshots = tuple(
        BrokerPendingOrderSnapshot(
            snapshot_id=snapshot_id,
            observed_at_utc=observed_at_utc,
            order_id=order.order_id,
            canonical_symbol=canonical_symbol,
            broker_symbol=order.broker_symbol,
            order_type=order.order_type,
            state=order.state,
            volume=order.volume,
            price=order.price,
            stop_loss_price=order.stop_loss_price,
            take_profit_price=order.take_profit_price,
            expires_at_utc=order.expires_at_utc,
        )
        for order in pending_orders
    )

    return BrokerStateObservation(
        account=account_snapshot,
        positions=position_snapshots,
        pending_orders=pending_order_snapshots,
    )


def _read_positions(
    gateway: ReadOnlyMt5Gateway,
) -> tuple[tuple[PositionState, ...], SnapshotCompleteness]:
    try:
        positions = gateway.positions()
    except Mt5CallFailedError as error:
        _log.warning("broker_state.positions_failed", error=str(error))
        return (), SnapshotCompleteness.FAILED
    return positions, SnapshotCompleteness.COMPLETE


def _read_pending_orders(
    gateway: ReadOnlyMt5Gateway,
) -> tuple[tuple[PendingOrderState, ...], SnapshotCompleteness]:
    try:
        orders = gateway.pending_orders()
    except Mt5CallFailedError as error:
        _log.warning("broker_state.pending_orders_failed", error=str(error))
        return (), SnapshotCompleteness.FAILED
    return orders, SnapshotCompleteness.COMPLETE
