"""Killzones and the FX trading week.

ICT killzones are defined in **New York local time**, not UTC. London open is
2:00 AM ET whether or not the United States is observing daylight saving, so a
hard-coded UTC window is wrong for roughly half the year — and wrong by an hour
in a methodology whose windows are one to three hours wide.

Everything here therefore converts to `America/New_York` before comparing, and
the platform's own timestamps stay UTC throughout (build.md §12.2).

The United States and Europe also change clocks on different dates, so for a
few weeks each spring and autumn the London and New York sessions sit at an
unusual offset to each other. Deriving both from their own local zones handles
that; deriving either from a fixed UTC offset does not.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from crumblr.domain.timeutils import UtcDatetime

NEW_YORK = ZoneInfo("America/New_York")
LONDON = ZoneInfo("Europe/London")


class Killzone(StrEnum):
    """The windows this methodology treats as worth trading."""

    ASIAN_RANGE = "ASIAN_RANGE"
    LONDON_OPEN = "LONDON_OPEN"
    NEW_YORK_AM = "NEW_YORK_AM"
    LONDON_CLOSE = "LONDON_CLOSE"
    NONE = "NONE"


@dataclass(frozen=True)
class KillzoneWindow:
    """A window expressed in New York local time."""

    zone: Killzone
    start: time
    end: time

    def contains(self, local_time: time) -> bool:
        """Windows here do not wrap midnight; the Asian range is split instead."""
        return self.start <= local_time < self.end


DEFAULT_WINDOWS: tuple[KillzoneWindow, ...] = (
    # Asian range runs 8pm to midnight ET; the post-midnight portion is a
    # separate entry rather than a wrapping window, so containment stays a
    # simple comparison.
    KillzoneWindow(Killzone.ASIAN_RANGE, time(20, 0), time(23, 59, 59)),
    KillzoneWindow(Killzone.ASIAN_RANGE, time(0, 0), time(2, 0)),
    KillzoneWindow(Killzone.LONDON_OPEN, time(2, 0), time(5, 0)),
    KillzoneWindow(Killzone.NEW_YORK_AM, time(7, 0), time(10, 0)),
    KillzoneWindow(Killzone.LONDON_CLOSE, time(10, 0), time(12, 0)),
)

TRADING_KILLZONES: frozenset[Killzone] = frozenset({Killzone.LONDON_OPEN, Killzone.NEW_YORK_AM})
"""Windows the entry model will trade by default.

The Asian range is used to *build* the range that later gets swept, not to
trade inside; London close is where positions are commonly managed rather than
opened.
"""

WEEK_OPEN_HOUR_ET = 17
"""FX opens Sunday 5pm ET."""

WEEK_CLOSE_HOUR_ET = 17
"""FX closes Friday 5pm ET."""


def killzone_at(
    moment: UtcDatetime, windows: tuple[KillzoneWindow, ...] = DEFAULT_WINDOWS
) -> Killzone:
    """Which killzone `moment` falls in, converting to exchange local time."""
    local = moment.astimezone(NEW_YORK)
    local_time = local.time()
    for window in windows:
        if window.contains(local_time):
            return window.zone
    return Killzone.NONE


def is_market_open(moment: UtcDatetime) -> bool:
    """Whether the FX market is open, in New York terms.

    Closed from Friday 17:00 ET until Sunday 17:00 ET. A strategy that proposes
    a trade into a closed market is proposing an order that cannot fill, and the
    risk engine would have to catch what the agent should not have sent.
    """
    local = moment.astimezone(NEW_YORK)
    weekday = local.weekday()  # Monday is 0, Sunday is 6

    saturday = weekday == 5
    after_friday_close = weekday == 4 and local.hour >= WEEK_CLOSE_HOUR_ET
    before_sunday_open = weekday == 6 and local.hour < WEEK_OPEN_HOUR_ET
    return not (saturday or after_friday_close or before_sunday_open)


def trading_day(moment: UtcDatetime) -> date:
    """The FX trading day `moment` belongs to.

    The week rolls at 17:00 New York time, not at midnight UTC, so anything
    after the roll counts toward the next day. This is what a "daily" loss
    limit has to be measured against — measuring from the start of a run makes
    the limit a total-loss cap wearing a daily label, and it would trip once
    and never reset.

    A moment inside the closed weekend gap (Friday 17:00 ET through Sunday
    17:00 ET) belongs to no trading day at all — it is bucketed into the next
    one that will actually open, Monday, rather than into a fabricated
    Saturday or Sunday. Getting this right here matters beyond this function's
    own callers: `persistence/flatten.py::flatten_request_id_for()` is keyed
    on this value alone, so a version that invented weekend dates produced up
    to two spurious extra flatten-request rows across every real weekend, and
    `application/orchestration.py::_roll_session` reset the daily-loss ledger
    the same extra number of times — both fixed by this function alone being
    correct, not by a second calendar authority elsewhere.
    """
    local = moment.astimezone(NEW_YORK)
    if not is_market_open(moment):
        return (local + timedelta(days=(7 - local.weekday()) % 7)).date()
    if local.hour >= WEEK_CLOSE_HOUR_ET:
        return (local + timedelta(days=1)).date()
    return local.date()


def weekly_close(moment: UtcDatetime) -> datetime:
    """Friday 17:00 America/New_York ending the trading week `moment`'s

    trading day belongs to — the FX week's only close, and therefore the
    one boundary a weekly session policy (owner risk policy v1, D1.5) is
    measured against. Derived from `trading_day()` alone, the platform's
    one calendar authority, rather than an independent week computation
    that would have to agree with it."""
    day = trading_day(moment)
    monday = day - timedelta(days=day.weekday())
    friday = monday + timedelta(days=4)
    local_close = datetime.combine(friday, time(WEEK_CLOSE_HOUR_ET, 0), tzinfo=NEW_YORK)
    return local_close.astimezone(moment.tzinfo)


def london_local_hour(moment: UtcDatetime) -> int:
    """Hour of day in London, for session analysis that is London-relative."""
    return moment.astimezone(LONDON).hour


def new_york_local_hour(moment: UtcDatetime) -> int:
    return moment.astimezone(NEW_YORK).hour
