"""broker_pending_order_snapshots gains magic (D-049, ICT LIMIT DEMO EXECUTION Slice 1).

`magic` was never tracked for pending orders at any layer — confirmed by
direct search before implementation (D-049). `broker_position_snapshots`
already carries it for open positions; this mirrors that column exactly
(nullable `BigInteger`) so a submitted `EntryType.LIMIT` order sitting
pending can be found by magic the same way a filled one already can.

Revision ID: fb1abaa2d137
Revises: 8801080869a6
Create Date: 2026-09-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "fb1abaa2d137"
down_revision: str | None = "8801080869a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "broker_pending_order_snapshots",
        sa.Column("magic", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("broker_pending_order_snapshots", "magic")
