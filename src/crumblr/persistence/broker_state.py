"""Durable storage for observed broker account/position/pending-order state.

Review 1.15 F-047 — see `application/broker_state.py` for what builds the
snapshot this stores. Three properties carried over from `market_data.py`
deliberately, for the same reasons:

- **Append-only.** A broker-state observation is a historical fact about a
  moment that has passed; nothing here is ever updated in place.
- **Content-derived identity for child rows.** A position/pending-order row's
  id is derived from its snapshot and ticket/order id, so re-recording the
  same capture twice collapses rather than duplicating.
- **Exact money.** Every monetary field is `Decimal`, matching the `NUMERIC`
  columns in `schema.py`.
"""

from __future__ import annotations

from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import Engine, desc, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from crumblr.application.broker_state import BrokerStateObservation
from crumblr.domain.hashing import canonical_json
from crumblr.domain.models import (
    BrokerAccountSnapshot,
    BrokerPendingOrderSnapshot,
    BrokerPositionSnapshot,
)
from crumblr.persistence.schema import (
    broker_account_snapshots,
    broker_pending_order_snapshots,
    broker_position_snapshots,
)


def _position_row_id(*, snapshot_id: UUID, ticket: int) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        canonical_json(
            {"kind": "crumblr:broker_position", "snapshot_id": str(snapshot_id), "ticket": ticket}
        ),
    )


def _pending_order_row_id(*, snapshot_id: UUID, order_id: int) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        canonical_json(
            {
                "kind": "crumblr:broker_pending_order",
                "snapshot_id": str(snapshot_id),
                "order_id": order_id,
            }
        ),
    )


class BrokerStateStore:
    """Append-only storage for broker-state observations."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def record(
        self, observation: BrokerStateObservation, *, connection: Connection | None = None
    ) -> None:
        """Store the account snapshot and every position/pending-order row.

        One transaction: the account row and its children are one logical
        observation, and a database that has the children but not their
        parent (or vice versa) is not a state a reconciliation reading these
        tables should ever have to consider.
        """
        if connection is not None:
            self._record(connection, observation)
            return
        with self._engine.begin() as own_connection:
            self._record(own_connection, observation)

    def _record(self, connection: Connection, observation: BrokerStateObservation) -> None:
        account = observation.account
        connection.execute(
            pg_insert(broker_account_snapshots)
            .values(
                snapshot_id=account.snapshot_id,
                observed_at_utc=account.observed_at_utc,
                recorded_at_utc=account.recorded_at_utc,
                environment=account.environment.value,
                server=account.server,
                account_ref=account.account_ref,
                currency=account.currency,
                leverage=account.leverage,
                margin_mode=account.margin_mode,
                balance=account.balance,
                equity=account.equity,
                profit=account.profit,
                margin=account.margin,
                margin_free=account.margin_free,
                margin_level=account.margin_level,
                account_trade_allowed=account.account_trade_allowed,
                terminal_trade_allowed=account.terminal_trade_allowed,
                position_set_state=account.position_set_state.value,
                pending_order_set_state=account.pending_order_set_state.value,
                payload=account.model_dump(mode="json"),
            )
            .on_conflict_do_nothing(index_elements=["snapshot_id"])
        )

        if observation.positions:
            connection.execute(
                pg_insert(broker_position_snapshots)
                .values(
                    [
                        {
                            "row_id": _position_row_id(
                                snapshot_id=position.snapshot_id, ticket=position.ticket
                            ),
                            "snapshot_id": position.snapshot_id,
                            "observed_at_utc": position.observed_at_utc,
                            "ticket": position.ticket,
                            "canonical_symbol": position.canonical_symbol,
                            "broker_symbol": position.broker_symbol,
                            "side": position.side.value,
                            "volume": position.volume,
                            "opened_at_utc": position.opened_at_utc,
                            "open_price": position.open_price,
                            "current_price": position.current_price,
                            "stop_loss_price": position.stop_loss_price,
                            "take_profit_price": position.take_profit_price,
                            "profit": position.profit,
                            "swap": position.swap,
                            "magic": position.magic,
                            "comment": position.comment,
                            "payload": position.model_dump(mode="json"),
                        }
                        for position in observation.positions
                    ]
                )
                .on_conflict_do_nothing(index_elements=["row_id"])
            )

        if observation.pending_orders:
            connection.execute(
                pg_insert(broker_pending_order_snapshots)
                .values(
                    [
                        {
                            "row_id": _pending_order_row_id(
                                snapshot_id=order.snapshot_id, order_id=order.order_id
                            ),
                            "snapshot_id": order.snapshot_id,
                            "observed_at_utc": order.observed_at_utc,
                            "order_id": order.order_id,
                            "canonical_symbol": order.canonical_symbol,
                            "broker_symbol": order.broker_symbol,
                            "order_type": order.order_type,
                            "state": order.state,
                            "volume": order.volume,
                            "price": order.price,
                            "stop_loss_price": order.stop_loss_price,
                            "take_profit_price": order.take_profit_price,
                            "expires_at_utc": order.expires_at_utc,
                            "payload": order.model_dump(mode="json"),
                        }
                        for order in observation.pending_orders
                    ]
                )
                .on_conflict_do_nothing(index_elements=["row_id"])
            )

    def latest_account_snapshot(self) -> BrokerAccountSnapshot | None:
        """The most recent account snapshot, or `None` — for a dashboard."""
        statement = (
            select(broker_account_snapshots.c.payload)
            .order_by(
                desc(broker_account_snapshots.c.observed_at_utc),
                desc(broker_account_snapshots.c.sequence),
            )
            .limit(1)
        )
        with self._engine.connect() as connection:
            row = connection.execute(statement).scalar_one_or_none()
        return BrokerAccountSnapshot.model_validate(row) if row is not None else None

    def positions_for(self, snapshot_id: UUID) -> tuple[BrokerPositionSnapshot, ...]:
        """Every position row recorded for one snapshot."""
        statement = (
            select(broker_position_snapshots.c.payload)
            .where(broker_position_snapshots.c.snapshot_id == snapshot_id)
            .order_by(broker_position_snapshots.c.sequence)
        )
        with self._engine.connect() as connection:
            rows = connection.execute(statement).scalars().all()
        return tuple(BrokerPositionSnapshot.model_validate(row) for row in rows)

    def pending_orders_for(self, snapshot_id: UUID) -> tuple[BrokerPendingOrderSnapshot, ...]:
        """Every pending-order row recorded for one snapshot."""
        statement = (
            select(broker_pending_order_snapshots.c.payload)
            .where(broker_pending_order_snapshots.c.snapshot_id == snapshot_id)
            .order_by(broker_pending_order_snapshots.c.sequence)
        )
        with self._engine.connect() as connection:
            rows = connection.execute(statement).scalars().all()
        return tuple(BrokerPendingOrderSnapshot.model_validate(row) for row in rows)

    def record_count(self) -> int:
        from sqlalchemy import func

        with self._engine.connect() as connection:
            return int(
                connection.execute(
                    select(func.count()).select_from(broker_account_snapshots)
                ).scalar_one()
            )
