"""risk_session_states gains canonical_symbol (Market Universe, ADR-022).

Before this, the risk ledger was one global row for the whole platform —
`PostgresRiskSessionStore.load_latest()` read "the single latest row in the
table," full stop. A second market would have silently shared EUR/USD's
equity/drawdown/loss ledger. Every existing row is backfilled to 'EUR/USD',
the only symbol that has ever written one, then the column is made
NOT NULL. `ix_risk_session_order` (on `sequence` alone) is replaced by a
composite `(canonical_symbol, sequence)` index, matching how
`ix_risk_session_day` is already keyed.

Revision ID: 8801080869a6
Revises: e91f4a7c2b53
Create Date: 2026-09-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8801080869a6"
down_revision: str | None = "e91f4a7c2b53"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LEGACY_SYMBOL = "EUR/USD"
"""The only canonical_symbol any row in this table could have been written
under before the Market Universe existed."""


def upgrade() -> None:
    op.add_column(
        "risk_session_states",
        sa.Column("canonical_symbol", sa.String(length=64), nullable=True),
    )
    op.execute(
        sa.text("UPDATE risk_session_states SET canonical_symbol = :symbol").bindparams(
            symbol=_LEGACY_SYMBOL
        )
    )
    op.alter_column(
        "risk_session_states",
        "canonical_symbol",
        existing_type=sa.String(length=64),
        nullable=False,
    )
    op.drop_index("ix_risk_session_order", table_name="risk_session_states")
    op.create_index(
        "ix_risk_session_order",
        "risk_session_states",
        ["canonical_symbol", "sequence"],
    )


def downgrade() -> None:
    op.drop_index("ix_risk_session_order", table_name="risk_session_states")
    op.create_index(
        "ix_risk_session_order",
        "risk_session_states",
        ["sequence"],
    )
    op.drop_column("risk_session_states", "canonical_symbol")
