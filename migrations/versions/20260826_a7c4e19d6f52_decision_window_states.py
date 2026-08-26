"""Live-decision idempotence checkpoints (review 1.17 §8, F-054).

Durable record of which bar window a live decision worker last decided and
which `TradeIntent` hashes its duplicate-protection check has already seen
— previously held only in process memory. See
`application/decision_window.py` and `persistence/decision_window.py`.

Revision ID: a7c4e19d6f52
Revises: f3a8c1d9b2e4
Create Date: 2026-08-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a7c4e19d6f52"
down_revision: str | None = "f3a8c1d9b2e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "decision_window_states",
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("canonical_symbol", sa.String(length=64), nullable=False),
        sa.Column("strategy_id", sa.String(length=128), nullable=False),
        sa.Column("config_version", sa.Text(), nullable=False),
        sa.Column("last_decided_open_time_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("seen_decision_hashes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "recorded_at_utc",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
        sa.UniqueConstraint("sequence"),
    )
    op.create_index(
        "ix_decision_window_key",
        "decision_window_states",
        ["canonical_symbol", "strategy_id", "config_version", "sequence"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_decision_window_key", table_name="decision_window_states")
    op.drop_table("decision_window_states")
