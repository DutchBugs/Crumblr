"""`risk/calendars.py` (Market Universe, ADR-022).

`FxWeekdayCalendar` must be byte-for-byte identical to `trading_agent.sessions`'s
own functions — it is a thin wrapper, not a reimplementation, so EUR/USD's real
phase/rollover behaviour must be provably unchanged by this refactor.
`AlwaysOpenCalendar` must never report CLOSED and must never invent a
weekly-close boundary nobody has approved.
"""

from __future__ import annotations

from datetime import UTC, datetime

from crumblr.domain.enums import AssetClass
from crumblr.risk.calendars import AlwaysOpenCalendar, FxWeekdayCalendar, calendar_for
from crumblr.trading_agent import sessions

# Same fixture moments as tests/unit/test_trading_window.py, for direct comparison.
WINTER = datetime(2026, 1, 6, 12, 0, tzinfo=UTC)
SUMMER = datetime(2026, 7, 7, 12, 0, tzinfo=UTC)
WINTER_FRIDAY = datetime(2026, 1, 9, 12, 0, tzinfo=UTC)
SUMMER_FRIDAY = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
WEEKEND_MIDNIGHT = datetime(2026, 1, 10, 0, 0, tzinfo=UTC)  # Saturday 00:00 UTC

FIXTURE_MOMENTS = (WINTER, SUMMER, WINTER_FRIDAY, SUMMER_FRIDAY, WEEKEND_MIDNIGHT)


class TestFxWeekdayCalendarMatchesSessionsExactly:
    def test_is_market_open_matches_for_every_fixture_moment(self) -> None:
        calendar = FxWeekdayCalendar()
        for moment in FIXTURE_MOMENTS:
            assert calendar.is_market_open(moment) == sessions.is_market_open(moment)

    def test_trading_day_matches_for_every_fixture_moment(self) -> None:
        calendar = FxWeekdayCalendar()
        for moment in FIXTURE_MOMENTS:
            assert calendar.trading_day(moment) == sessions.trading_day(moment)

    def test_weekly_close_matches_for_every_fixture_moment(self) -> None:
        calendar = FxWeekdayCalendar()
        for moment in FIXTURE_MOMENTS:
            assert calendar.weekly_close(moment) == sessions.weekly_close(moment)


class TestAlwaysOpenCalendarInventsNoBoundary:
    def test_market_is_always_open(self) -> None:
        calendar = AlwaysOpenCalendar()
        for moment in FIXTURE_MOMENTS:
            assert calendar.is_market_open(moment) is True

    def test_weekly_close_is_always_none(self) -> None:
        calendar = AlwaysOpenCalendar()
        for moment in FIXTURE_MOMENTS:
            assert calendar.weekly_close(moment) is None

    def test_trading_day_buckets_by_plain_utc_calendar_day(self) -> None:
        calendar = AlwaysOpenCalendar()
        assert calendar.trading_day(WEEKEND_MIDNIGHT) == WEEKEND_MIDNIGHT.date()


class TestCalendarFor:
    def test_fx_and_metal_use_the_fx_weekday_calendar(self) -> None:
        assert isinstance(calendar_for(AssetClass.FX), FxWeekdayCalendar)
        assert isinstance(calendar_for(AssetClass.METAL), FxWeekdayCalendar)

    def test_crypto_uses_the_always_open_calendar(self) -> None:
        assert isinstance(calendar_for(AssetClass.CRYPTO), AlwaysOpenCalendar)
