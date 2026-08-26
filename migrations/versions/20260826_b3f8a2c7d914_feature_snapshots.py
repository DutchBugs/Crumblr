"""Durable feature-value storage (review 1.17 §9 / review 1.18 §8, D-031).

What the Trading Agent actually saw for one decision window — previously
only `feature_set_version` and a hash of these values were journalled with
the capsule. See `trading_agent/base.py::FeatureEvidence`,
`persistence/features.py`.

Revision ID: b3f8a2c7d914
Revises: a7c4e19d6f52
Create Date: 2026-08-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b3f8a2c7d914"
down_revision: str | None = "a7c4e19d6f52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "feature_snapshots",
        sa.Column("feature_snapshot_id", sa.UUID(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("feature_set_version", sa.String(length=128), nullable=False),
        sa.Column("canonical_symbol", sa.String(length=64), nullable=False),
        sa.Column("computed_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at_utc",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("feature_snapshot_id"),
        sa.UniqueConstraint("sequence"),
    )
    op.create_index(
        "ix_feature_snapshots_symbol_time",
        "feature_snapshots",
        ["canonical_symbol", "computed_at_utc"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_feature_snapshots_symbol_time", table_name="feature_snapshots")
    op.drop_table("feature_snapshots")
