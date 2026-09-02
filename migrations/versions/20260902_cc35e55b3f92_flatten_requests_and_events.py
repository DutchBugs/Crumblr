"""Flatten requests + flatten events (core critical path item 7).

The commitment/record half of automatic flatten submission — see
`review/adr/ADR-009-automatic-flatten-submission.md` and
`persistence/flatten.py`. A dedicated table pair, structurally parallel to
`execution_requests`/`execution_events` but with no FK into
`decision_capsules`: a flatten is policy-driven, not proposal-driven, and
has no `TradeIntent` behind it.

Revision ID: cc35e55b3f92
Revises: d4b6e2f81a37
Create Date: 2026-09-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "cc35e55b3f92"
down_revision: str | None = "d4b6e2f81a37"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "flatten_requests",
        sa.Column("flatten_request_id", sa.UUID(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("environment", sa.String(length=16), nullable=False),
        sa.Column("canonical_symbol", sa.String(length=64), nullable=False),
        sa.Column("trading_day", sa.Date(), nullable=False),
        sa.Column("session_close_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("flatten_deadline_utc", sa.DateTime(timezone=True), nullable=False),
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
        sa.PrimaryKeyConstraint("flatten_request_id"),
        sa.UniqueConstraint("sequence"),
    )
    op.create_index(
        "ix_flatten_requests_day",
        "flatten_requests",
        ["environment", "canonical_symbol", "trading_day"],
        unique=False,
    )

    op.create_table(
        "flatten_events",
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("flatten_request_id", sa.UUID(), nullable=False),
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
        sa.ForeignKeyConstraint(["flatten_request_id"], ["flatten_requests.flatten_request_id"]),
        sa.PrimaryKeyConstraint("event_id"),
        sa.UniqueConstraint("sequence"),
    )
    op.create_index(
        "ix_flatten_events_request",
        "flatten_events",
        ["flatten_request_id", "sequence"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_flatten_events_request", table_name="flatten_events")
    op.drop_table("flatten_events")
    op.drop_index("ix_flatten_requests_day", table_name="flatten_requests")
    op.drop_table("flatten_requests")
