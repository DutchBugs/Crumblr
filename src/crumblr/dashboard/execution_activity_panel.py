"""Read-only actual execution request/event activity for the dashboard.

Distinct from `execution_panel.py`'s `ExecutionGateState`, which reads
only static config (are the four submission gates open) -- this module
reads the real, durable `execution_requests`/`execution_events` history:
how many requests have ever been claimed, how many events of each type
have ever been recorded, and what the single most recent event was.
Never evaluates a proposal, never touches Risk/Policy, never opens an
MT5 connection.
"""

from __future__ import annotations

from dataclasses import dataclass

from crumblr.domain.timeutils import UtcDatetime
from crumblr.persistence.execution import ExecutionEventStore, ExecutionRequestStore


@dataclass(frozen=True)
class LatestExecutionEvent:
    event_type: str
    occurred_at_utc: UtcDatetime
    order_request_id: str
    detail: str | None


@dataclass(frozen=True)
class ExecutionActivityState:
    requests_claimed_count: int
    event_counts_by_type: dict[str, int]
    latest_event: LatestExecutionEvent | None


def build_execution_activity(
    *, request_store: ExecutionRequestStore, event_store: ExecutionEventStore
) -> ExecutionActivityState:
    latest = event_store.latest_event()
    return ExecutionActivityState(
        requests_claimed_count=request_store.count_claimed(),
        event_counts_by_type=event_store.counts_by_type(),
        latest_event=(
            LatestExecutionEvent(
                event_type=latest.event_type.value,
                occurred_at_utc=latest.occurred_at_utc,
                order_request_id=str(latest.order_request_id),
                detail=latest.detail,
            )
            if latest is not None
            else None
        ),
    )
