"""Broker account/position/pending-order snapshots (review 1.15 F-047).

Durable, auditable observations of the broker's own account, position and
pending-order state — previously held only in memory by whichever call last
returned them. See `application/broker_state.py` and
`persistence/broker_state.py`.

Revision ID: f3a8c1d9b2e4
Revises: ce70efeb9fe9
Create Date: 2026-08-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f3a8c1d9b2e4"
down_revision: str | None = "ce70efeb9fe9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "broker_account_snapshots",
        sa.Column("snapshot_id", sa.UUID(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("observed_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at_utc",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("environment", sa.String(length=16), nullable=False),
        sa.Column("server", sa.String(length=128), nullable=False),
        sa.Column("account_ref", sa.String(length=32), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("leverage", sa.Integer(), nullable=False),
        sa.Column("margin_mode", sa.String(length=32), nullable=True),
        sa.Column("balance", sa.Numeric(), nullable=False),
        sa.Column("equity", sa.Numeric(), nullable=False),
        sa.Column("profit", sa.Numeric(), nullable=False),
        sa.Column("margin", sa.Numeric(), nullable=False),
        sa.Column("margin_free", sa.Numeric(), nullable=False),
        sa.Column("margin_level", sa.Numeric(), nullable=True),
        sa.Column("account_trade_allowed", sa.Boolean(), nullable=False),
        sa.Column("terminal_trade_allowed", sa.Boolean(), nullable=True),
        sa.Column("position_set_state", sa.String(length=16), nullable=False),
        sa.Column("pending_order_set_state", sa.String(length=16), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("snapshot_id"),
        sa.UniqueConstraint("sequence"),
    )
    op.create_index(
        "ix_broker_account_snapshots_ref_time",
        "broker_account_snapshots",
        ["account_ref", "observed_at_utc"],
        unique=False,
    )

    op.create_table(
        "broker_position_snapshots",
        sa.Column("row_id", sa.UUID(), nullable=False),
        sa.Column("snapshot_id", sa.UUID(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("observed_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ticket", sa.BigInteger(), nullable=False),
        sa.Column("canonical_symbol", sa.String(length=64), nullable=False),
        sa.Column("broker_symbol", sa.String(length=64), nullable=False),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("volume", sa.Numeric(), nullable=False),
        sa.Column("opened_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open_price", sa.Numeric(), nullable=False),
        sa.Column("current_price", sa.Numeric(), nullable=True),
        sa.Column("stop_loss_price", sa.Numeric(), nullable=True),
        sa.Column("take_profit_price", sa.Numeric(), nullable=True),
        sa.Column("profit", sa.Numeric(), nullable=False),
        sa.Column("swap", sa.Numeric(), nullable=False),
        sa.Column("magic", sa.BigInteger(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["snapshot_id"], ["broker_account_snapshots.snapshot_id"]),
        sa.PrimaryKeyConstraint("row_id"),
        sa.UniqueConstraint("sequence"),
    )
    op.create_index(
        "ix_broker_position_snapshots_snapshot",
        "broker_position_snapshots",
        ["snapshot_id"],
        unique=False,
    )
    op.create_index(
        "ix_broker_position_snapshots_symbol_time",
        "broker_position_snapshots",
        ["canonical_symbol", "observed_at_utc"],
        unique=False,
    )

    op.create_table(
        "broker_pending_order_snapshots",
        sa.Column("row_id", sa.UUID(), nullable=False),
        sa.Column("snapshot_id", sa.UUID(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("observed_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("canonical_symbol", sa.String(length=64), nullable=False),
        sa.Column("broker_symbol", sa.String(length=64), nullable=False),
        sa.Column("order_type", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("volume", sa.Numeric(), nullable=False),
        sa.Column("price", sa.Numeric(), nullable=False),
        sa.Column("stop_loss_price", sa.Numeric(), nullable=True),
        sa.Column("take_profit_price", sa.Numeric(), nullable=True),
        sa.Column("expires_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["snapshot_id"], ["broker_account_snapshots.snapshot_id"]),
        sa.PrimaryKeyConstraint("row_id"),
        sa.UniqueConstraint("sequence"),
    )
    op.create_index(
        "ix_broker_pending_order_snapshots_snapshot",
        "broker_pending_order_snapshots",
        ["snapshot_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_broker_pending_order_snapshots_snapshot", table_name="broker_pending_order_snapshots"
    )
    op.drop_table("broker_pending_order_snapshots")
    op.drop_index(
        "ix_broker_position_snapshots_symbol_time", table_name="broker_position_snapshots"
    )
    op.drop_index("ix_broker_position_snapshots_snapshot", table_name="broker_position_snapshots")
    op.drop_table("broker_position_snapshots")
    op.drop_index("ix_broker_account_snapshots_ref_time", table_name="broker_account_snapshots")
    op.drop_table("broker_account_snapshots")
