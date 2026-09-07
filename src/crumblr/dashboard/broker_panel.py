"""Read-only broker account/positions/pending-orders panel.

Everything here reads `BrokerStateStore`'s already-durable observations —
never MT5, never a credential, never a write. The one rule this module exists
to enforce in the display layer (work order §10, "truthfulness rule"): a
`SnapshotCompleteness` of anything other than `COMPLETE` means the absence of
rows is *not* evidence of an empty book. Zero rows plus `COMPLETE` means
"none"; zero rows plus `FAILED`/`UNKNOWN` means "we do not know" — the two
must never render the same way.
"""

from __future__ import annotations

from dataclasses import dataclass

from crumblr.domain.enums import SnapshotCompleteness
from crumblr.domain.models import (
    BrokerAccountSnapshot,
    BrokerPendingOrderSnapshot,
    BrokerPositionSnapshot,
)
from crumblr.domain.timeutils import UtcDatetime
from crumblr.persistence.broker_state import BrokerStateStore


@dataclass(frozen=True)
class AccountPanelState:
    """`None` fields mean the snapshot genuinely did not carry that
    optional MT5 fact (`margin_level`/`terminal_trade_allowed`); the panel
    itself is `None` on `AccountPanelState` when no snapshot exists at all
    (`build_account_panel` returns `None` for that case, never a fabricated
    zeroed-out state)."""

    observed_at_utc: UtcDatetime
    environment: str
    server: str
    account_ref: str
    """Already a non-reversible fingerprint (`mask_login`) at the point the
    gateway wrote this snapshot — never a raw MT5 login number."""
    currency: str
    leverage: int
    margin_mode: str | None
    balance: str
    equity: str
    profit: str
    margin: str
    margin_free: str
    margin_level: str | None
    account_trade_allowed: bool
    terminal_trade_allowed: bool | None
    position_set_state: str
    pending_order_set_state: str


@dataclass(frozen=True)
class PositionRow:
    canonical_symbol: str
    broker_symbol: str
    side: str
    volume: str
    opened_at_utc: UtcDatetime
    open_price: str
    current_price: str | None
    stop_loss_price: str | None
    take_profit_price: str | None
    profit: str
    swap: str
    magic: int | None
    comment: str | None


@dataclass(frozen=True)
class PendingOrderRow:
    canonical_symbol: str
    broker_symbol: str
    order_type: str
    state: str
    volume: str
    price: str
    stop_loss_price: str | None
    take_profit_price: str | None
    expires_at_utc: UtcDatetime | None
    order_id: int


def _account_panel(snapshot: BrokerAccountSnapshot) -> AccountPanelState:
    return AccountPanelState(
        observed_at_utc=snapshot.observed_at_utc,
        environment=snapshot.environment.value,
        server=snapshot.server,
        account_ref=snapshot.account_ref,
        currency=snapshot.currency,
        leverage=snapshot.leverage,
        margin_mode=snapshot.margin_mode,
        balance=str(snapshot.balance),
        equity=str(snapshot.equity),
        profit=str(snapshot.profit),
        margin=str(snapshot.margin),
        margin_free=str(snapshot.margin_free),
        margin_level=(str(snapshot.margin_level) if snapshot.margin_level is not None else None),
        account_trade_allowed=snapshot.account_trade_allowed,
        terminal_trade_allowed=snapshot.terminal_trade_allowed,
        position_set_state=snapshot.position_set_state.value,
        pending_order_set_state=snapshot.pending_order_set_state.value,
    )


def _position_row(position: BrokerPositionSnapshot) -> PositionRow:
    return PositionRow(
        canonical_symbol=position.canonical_symbol,
        broker_symbol=position.broker_symbol,
        side=position.side.value,
        volume=str(position.volume),
        opened_at_utc=position.opened_at_utc,
        open_price=str(position.open_price),
        current_price=(str(position.current_price) if position.current_price is not None else None),
        stop_loss_price=(
            str(position.stop_loss_price) if position.stop_loss_price is not None else None
        ),
        take_profit_price=(
            str(position.take_profit_price) if position.take_profit_price is not None else None
        ),
        profit=str(position.profit),
        swap=str(position.swap),
        magic=position.magic,
        comment=position.comment,
    )


def _pending_order_row(order: BrokerPendingOrderSnapshot) -> PendingOrderRow:
    return PendingOrderRow(
        canonical_symbol=order.canonical_symbol,
        broker_symbol=order.broker_symbol,
        order_type=order.order_type,
        state=order.state,
        volume=str(order.volume),
        price=str(order.price),
        stop_loss_price=(str(order.stop_loss_price) if order.stop_loss_price is not None else None),
        take_profit_price=(
            str(order.take_profit_price) if order.take_profit_price is not None else None
        ),
        expires_at_utc=order.expires_at_utc,
        order_id=order.order_id,
    )


@dataclass(frozen=True)
class BrokerReadModel:
    """One consistent read: account, positions and pending orders all drawn
    from the exact same `BrokerAccountSnapshot` row — a single
    `latest_account_snapshot()` call, never re-queried per panel, so a new
    snapshot arriving mid-request can never mismatch the account card
    against the position/order tables shown next to it."""

    account: AccountPanelState | None
    positions: tuple[PositionRow, ...]
    pending_orders: tuple[PendingOrderRow, ...]


def build_broker_read_model(*, broker_state: BrokerStateStore) -> BrokerReadModel:
    """`account=None` means no broker-state observation has ever been
    recorded — "NO EVIDENCE", not a zeroed-out account. Positions/pending
    orders are only ever populated when the matching `*_set_state` on that
    same snapshot is `COMPLETE` (work order §10's truthfulness rule) — an
    incomplete/failed set renders as an empty tuple too, so the template
    reads `account.position_set_state`/`pending_order_set_state` to tell
    "confirmed empty" apart from "unknown", exactly as it already must to
    render the account card itself.
    """
    snapshot = broker_state.latest_account_snapshot()
    if snapshot is None:
        return BrokerReadModel(account=None, positions=(), pending_orders=())

    account = _account_panel(snapshot)
    positions = (
        tuple(_position_row(p) for p in broker_state.positions_for(snapshot.snapshot_id))
        if snapshot.position_set_state is SnapshotCompleteness.COMPLETE
        else ()
    )
    pending_orders = (
        tuple(_pending_order_row(o) for o in broker_state.pending_orders_for(snapshot.snapshot_id))
        if snapshot.pending_order_set_state is SnapshotCompleteness.COMPLETE
        else ()
    )
    return BrokerReadModel(account=account, positions=positions, pending_orders=pending_orders)
