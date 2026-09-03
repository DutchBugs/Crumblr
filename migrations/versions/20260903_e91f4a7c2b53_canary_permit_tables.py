"""Canary permit tables (Phase B item B8).

Operator-issued one-shot DEMO submission permits — see
`review/adr/ADR-018-canary-permit.md` and `persistence/canary_permit.py`.
`canary_permits` is the immutable issued record; `canary_permit_consumptions`
is a separate append-only table recording whether one was ever used —
`permit_id` as its primary key is what makes "at most one consumption per
permit, ever" enforceable by the database itself, the same way
`execution_requests.order_request_id` already enforces the idempotency-key
invariant.

Revision ID: e91f4a7c2b53
Revises: d3b2e828b5b0
Create Date: 2026-09-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e91f4a7c2b53"
down_revision: str | None = "d3b2e828b5b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "canary_permits",
        sa.Column("permit_id", sa.UUID(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("approved_account_ref", sa.String(length=32), nullable=False),
        sa.Column("expected_server", sa.String(length=128), nullable=False),
        sa.Column("canonical_symbol", sa.String(length=64), nullable=False),
        sa.Column("entry_type", sa.String(length=16), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=True),
        sa.Column("assignment_id", sa.UUID(), nullable=True),
        sa.Column("strategy_artifact_hash", sa.String(length=128), nullable=True),
        sa.Column("max_requested_risk_fraction", sa.Numeric(), nullable=False),
        sa.Column("issued_by", sa.String(length=128), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("issued_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at_utc",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("permit_id"),
        sa.UniqueConstraint("sequence"),
    )
    op.create_index(
        "ix_canary_permits_order",
        "canary_permits",
        ["sequence"],
        unique=False,
    )

    op.create_table(
        "canary_permit_consumptions",
        sa.Column("permit_id", sa.UUID(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("order_request_id", sa.UUID(), nullable=False),
        sa.Column("consumed_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at_utc",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["permit_id"], ["canary_permits.permit_id"]),
        sa.PrimaryKeyConstraint("permit_id"),
        sa.UniqueConstraint("sequence"),
    )


def downgrade() -> None:
    op.drop_table("canary_permit_consumptions")
    op.drop_index("ix_canary_permits_order", table_name="canary_permits")
    op.drop_table("canary_permits")
