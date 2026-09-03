"""risk_session_states.open_risk_fraction becomes nullable (owner risk

policy v1, D1.4). `None` means the platform could not establish open
risk (an open position with untrustworthy stop geometry) — never
treated as zero. See review/adr/ADR-011-owner-risk-policy-v1.md.

Revision ID: d3b2e828b5b0
Revises: 03df83b062a6
Create Date: 2026-09-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d3b2e828b5b0"
down_revision: str | None = "03df83b062a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "risk_session_states",
        "open_risk_fraction",
        existing_type=sa.Numeric(),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "risk_session_states",
        "open_risk_fraction",
        existing_type=sa.Numeric(),
        nullable=False,
    )
