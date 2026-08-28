"""Agent Gateway ingestion + audit tables (ADR-005 Step B).

Identity, credentials, trading assignments, decision context bundles and
the idempotent decision-outcome claim + event log. See
`persistence/schema.py`'s docstrings on each table and
`agent_gateway/gateway.py`'s module docstring for the design.

Revision ID: d4b6e2f81a37
Revises: c9e1d5a3f286
Create Date: 2026-08-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d4b6e2f81a37"
down_revision: str | None = "c9e1d5a3f286"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_identities",
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("service_identity", sa.String(length=256), nullable=False),
        sa.Column("registered_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at_utc",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
        sa.UniqueConstraint("sequence"),
    )
    op.create_index(
        "ix_agent_identities_agent", "agent_identities", ["agent_id", "sequence"], unique=False
    )

    op.create_table(
        "agent_credentials",
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("credential_hash", sa.String(length=128), nullable=False),
        sa.Column(
            "recorded_at_utc",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("event_id"),
        sa.UniqueConstraint("sequence"),
    )
    op.create_index(
        "ix_agent_credentials_agent", "agent_credentials", ["agent_id", "sequence"], unique=False
    )

    op.create_table(
        "agent_trading_assignments",
        sa.Column("assignment_id", sa.UUID(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("allowed_agent_id", sa.UUID(), nullable=False),
        sa.Column("canonical_symbol", sa.String(length=64), nullable=False),
        sa.Column("valid_from_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column(
            "recorded_at_utc",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("assignment_id"),
        sa.UniqueConstraint("sequence"),
    )
    op.create_index(
        "ix_agent_assignments_agent",
        "agent_trading_assignments",
        ["allowed_agent_id"],
        unique=False,
    )

    op.create_table(
        "agent_decision_context_bundles",
        sa.Column("context_id", sa.UUID(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("assignment_id", sa.UUID(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("issued_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at_utc",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("context_id"),
        sa.UniqueConstraint("sequence"),
    )
    op.create_index(
        "ix_agent_context_bundles_hash",
        "agent_decision_context_bundles",
        ["content_hash"],
        unique=False,
    )
    op.create_index(
        "ix_agent_context_bundles_assignment",
        "agent_decision_context_bundles",
        ["assignment_id"],
        unique=False,
    )

    op.create_table(
        "agent_decision_outcomes",
        sa.Column("outcome_id", sa.UUID(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("outcome_type", sa.String(length=16), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("assignment_id", sa.UUID(), nullable=False),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("claimed_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at_utc",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("outcome_id"),
        sa.UniqueConstraint("sequence"),
    )
    op.create_index(
        "ix_agent_decision_outcomes_assignment",
        "agent_decision_outcomes",
        ["assignment_id", "claimed_at_utc"],
        unique=False,
    )
    op.create_index(
        "ix_agent_decision_outcomes_agent",
        "agent_decision_outcomes",
        ["agent_id", "claimed_at_utc"],
        unique=False,
    )

    op.create_table(
        "agent_decision_events",
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("outcome_id", sa.UUID(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("occurred_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at_utc",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("reason_codes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["outcome_id"], ["agent_decision_outcomes.outcome_id"]),
        sa.PrimaryKeyConstraint("event_id"),
        sa.UniqueConstraint("sequence"),
    )
    op.create_index(
        "ix_agent_decision_events_outcome",
        "agent_decision_events",
        ["outcome_id", "sequence"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_agent_decision_events_outcome", table_name="agent_decision_events")
    op.drop_table("agent_decision_events")
    op.drop_index("ix_agent_decision_outcomes_agent", table_name="agent_decision_outcomes")
    op.drop_index("ix_agent_decision_outcomes_assignment", table_name="agent_decision_outcomes")
    op.drop_table("agent_decision_outcomes")
    op.drop_index(
        "ix_agent_context_bundles_assignment", table_name="agent_decision_context_bundles"
    )
    op.drop_index("ix_agent_context_bundles_hash", table_name="agent_decision_context_bundles")
    op.drop_table("agent_decision_context_bundles")
    op.drop_index("ix_agent_assignments_agent", table_name="agent_trading_assignments")
    op.drop_table("agent_trading_assignments")
    op.drop_index("ix_agent_credentials_agent", table_name="agent_credentials")
    op.drop_table("agent_credentials")
    op.drop_index("ix_agent_identities_agent", table_name="agent_identities")
    op.drop_table("agent_identities")
