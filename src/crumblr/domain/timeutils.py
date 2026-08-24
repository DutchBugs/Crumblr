"""UTC-only time handling.

build.md §12.2: all internal timestamps are UTC, and local machine time or the
broker's chart timezone are never trusted on their own. A naive datetime has no
defined instant, so it is rejected at the model boundary instead of being
guessed at.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from pydantic import BeforeValidator


def _require_utc(value: Any) -> Any:
    """Reject naive datetimes; normalise any aware datetime to UTC."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("naive datetime is not accepted; supply a timezone-aware UTC datetime")
        return value.astimezone(UTC)
    return value


UtcDatetime = Annotated[datetime, BeforeValidator(_require_utc)]
"""A timezone-aware datetime, normalised to UTC."""


def utc_now() -> datetime:
    """Current instant in UTC. The single sanctioned wall-clock read."""
    return datetime.now(UTC)


def age_ms(event_time: datetime, *, now: datetime | None = None) -> int:
    """Age of `event_time` in milliseconds, for staleness checks.

    Negative when `event_time` is in the future — a clock-skew signal the risk
    engine treats as suspect rather than as fresh data.
    """
    reference = now if now is not None else utc_now()
    return int((reference - event_time).total_seconds() * 1000)
