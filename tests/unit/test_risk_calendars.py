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


class TestAlwaysOpenCalendarSessionPolicyApproval:
    """Market Universe, ADR-023 (BTC/USD Enablement Readiness,

    2026-09-08) — the one real owner decision this calendar needs:
    `session_policy_approved`, per instance, defaulting to unapproved.
    """

    def test_defaults_to_unapproved(self) -> None:
        assert AlwaysOpenCalendar().session_policy_approved is False

    def test_reports_exactly_what_it_was_constructed_with(self) -> None:
        assert AlwaysOpenCalendar(session_policy_approved=True).session_policy_approved is True
        assert AlwaysOpenCalendar(session_policy_approved=False).session_policy_approved is False


class TestFxWeekdayCalendarIsAlwaysApproved:
    def test_session_policy_approved_is_always_true(self) -> None:
        """D1.5 is already a real, owner-approved policy — `phase_at`

        never actually consults this for `FxWeekdayCalendar` (its
        `weekly_close()` never returns `None`), but it must never report
        `False` either, in case something downstream ever reads it
        directly."""
        assert FxWeekdayCalendar().session_policy_approved is True


class TestCalendarFor:
    def test_fx_and_metal_use_the_fx_weekday_calendar(self) -> None:
        assert isinstance(calendar_for(AssetClass.FX), FxWeekdayCalendar)
        assert isinstance(calendar_for(AssetClass.METAL), FxWeekdayCalendar)

    def test_crypto_uses_the_always_open_calendar(self) -> None:
        assert isinstance(calendar_for(AssetClass.CRYPTO), AlwaysOpenCalendar)

    def test_crypto_defaults_to_an_unapproved_calendar(self) -> None:
        """A caller that does not pass `session_policy_approved` (every

        call site before ADR-023) keeps the exact fail-closed behaviour
        it always had."""
        calendar = calendar_for(AssetClass.CRYPTO)
        assert isinstance(calendar, AlwaysOpenCalendar)
        assert calendar.session_policy_approved is False

    def test_crypto_threads_the_approval_flag_through(self) -> None:
        calendar = calendar_for(AssetClass.CRYPTO, session_policy_approved=True)
        assert isinstance(calendar, AlwaysOpenCalendar)
        assert calendar.session_policy_approved is True

    def test_fx_and_metal_ignore_the_approval_flag(self) -> None:
        """The flag only means something for a calendar with no

        weekly-close concept — passing it for FX/METAL must not change
        anything (there is nothing for it to approve)."""
        assert isinstance(
            calendar_for(AssetClass.FX, session_policy_approved=False), FxWeekdayCalendar
        )
        assert isinstance(
            calendar_for(AssetClass.METAL, session_policy_approved=False), FxWeekdayCalendar
        )
