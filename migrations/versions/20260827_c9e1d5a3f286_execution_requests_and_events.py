"""Immutable execution requests + append-only execution events (Phase 4).

The claim-before-broker-interaction and immutable-request-plus-append-only-
event persistence design from `review/PHASE4_PLAN_REVIEW_GO_WITH_TWEAKS.md`.
See `persistence/execution.py`.

Revision ID: c9e1d5a3f286
Revises: b3f8a2c7d914
Create Date: 2026-08-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c9e1d5a3f286"
down_revision: str | None = "b3f8a2c7d914"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "execution_requests",
        sa.Column("order_request_id", sa.UUID(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("capsule_id", sa.UUID(), nullable=False),
        sa.Column("intent_id", sa.UUID(), nullable=False),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("claimed_by", sa.String(length=128), nullable=False),
        sa.Column("claimed_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at_utc",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["capsule_id"], ["decision_capsules.capsule_id"]),
        sa.PrimaryKeyConstraint("order_request_id"),
        sa.UniqueConstraint("sequence"),
    )
    op.create_index(
        "ix_execution_requests_capsule", "execution_requests", ["capsule_id"], unique=False
    )

    op.create_table(
        "execution_events",
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("order_request_id", sa.UUID(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("occurred_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at_utc",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("reason_codes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["order_request_id"], ["execution_requests.order_request_id"]),
        sa.PrimaryKeyConstraint("event_id"),
        sa.UniqueConstraint("sequence"),
    )
    op.create_index(
        "ix_execution_events_request",
        "execution_events",
        ["order_request_id", "sequence"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_execution_events_request", table_name="execution_events")
    op.drop_table("execution_events")
    op.drop_index("ix_execution_requests_capsule", table_name="execution_requests")
    op.drop_table("execution_requests")
