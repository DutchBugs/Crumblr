"""Index execution_events(event_type, occurred_at_utc) (core critical

path item 8, post-fill reconciliation). Serves
ExecutionEventStore.request_ids_with_event() and retroactively serves
count_events_since()'s existing filter — see
review/adr/ADR-010-post-fill-reconciliation.md.

Revision ID: 03df83b062a6
Revises: cc35e55b3f92
Create Date: 2026-09-02
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "03df83b062a6"
down_revision: str | None = "cc35e55b3f92"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_execution_events_type_time",
        "execution_events",
        ["event_type", "occurred_at_utc"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_execution_events_type_time", table_name="execution_events")
