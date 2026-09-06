"""Asset-class-aware trading calendars (Market Universe, ADR-022).

`trading_agent.sessions.is_market_open`/`trading_day`/`weekly_close`
implement **owner risk policy v1** (`review/adr/ADR-012-owner-session-
policy-v1.md`, D1.5 — "weekend holding verboden"): a real, deliberate
decision for FX specifically (Friday 17:00 America/New_York close, Sunday
17:00 open), not an assumption that leaked into the code. There is no
equivalent owner-approved session policy for a 24/7 asset class — should
crypto have a weekly flatten requirement at all? what, if anything, replaces
"weekend"? Nobody has decided that, and this module does not decide it
either: `AlwaysOpenCalendar` reports `weekly_close() -> None` rather than
inventing a boundary, and `risk/trading_window.py::phase_at` treats "no
weekly-close concept" as "no `IntradayPolicy` to evaluate" — the same
"a stated choice, not a default" discipline `IntradayPolicy.disabled()`
already uses for exactly this reason. A market on `AlwaysOpenCalendar`
therefore always resolves to `SessionPhase.OPEN` (subject to every other
gate), by construction, until an owner makes a real decision for that
asset class and a new `TradingCalendar` implementation encodes it.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Protocol

from crumblr.domain.enums import AssetClass
from crumblr.domain.timeutils import UtcDatetime
from crumblr.trading_agent import sessions


class TradingCalendar(Protocol):
    """What `risk/trading_window.py` needs to know about a market's own

    trading week — deliberately narrow (build.md §24 names `SessionCalendar`
    as one of the seams a real multi-market platform needs)."""

    def is_market_open(self, moment: UtcDatetime) -> bool: ...

    def trading_day(self, moment: UtcDatetime) -> date: ...

    """Which trading day `moment` belongs to — the boundary a daily-loss

    baseline resets against. Never raises; every real moment belongs to
    some trading day, even one inside a closed period (bucketed forward to
    the next one that opens, mirroring `sessions.trading_day`'s own
    documented behaviour)."""

    def weekly_close(self, moment: UtcDatetime) -> datetime | None:
        """The boundary an `IntradayPolicy` is measured against, or `None`

        if this calendar has no weekly-close concept at all — not "the
        close is far away," an actual absence of the concept. `phase_at`
        treats `None` as "no owner-approved session policy exists for this
        calendar yet," not as "closed" or "open by default for an
        unrelated reason."
        """
        ...


class FxWeekdayCalendar:
    """The FX trading week, unchanged from `trading_agent.sessions`'s own

    functions — owner risk policy v1 (D1.5), real-terminal-validated.
    Zero behaviour change from before ADR-022: this is a thin wrapper, not
    a reimplementation, so EUR/USD's actual phase/rollover behaviour is
    provably identical to what it already was.
    """

    def is_market_open(self, moment: UtcDatetime) -> bool:
        return sessions.is_market_open(moment)

    def trading_day(self, moment: UtcDatetime) -> date:
        return sessions.trading_day(moment)

    def weekly_close(self, moment: UtcDatetime) -> datetime:
        return sessions.weekly_close(moment)


class AlwaysOpenCalendar:
    """A 24/7 market with no owner-approved session policy yet.

    `trading_day` buckets by the plain UTC calendar day — a simple, honest
    default for a market that has no other stated boundary, not a
    disguised claim about what the *right* trading-day definition for this
    asset class should be. `weekly_close` returns `None`: there is no
    weekly-close concept here at all, by construction, until a real owner
    decision exists and gets its own `TradingCalendar` implementation.
    """

    def is_market_open(self, moment: UtcDatetime) -> bool:
        del moment
        return True

    def trading_day(self, moment: UtcDatetime) -> date:
        return moment.astimezone(UTC).date()

    def weekly_close(self, moment: UtcDatetime) -> datetime | None:
        del moment
        return None


_CALENDARS_BY_ASSET_CLASS: dict[AssetClass, TradingCalendar] = {
    AssetClass.FX: FxWeekdayCalendar(),
    AssetClass.METAL: FxWeekdayCalendar(),
    AssetClass.CRYPTO: AlwaysOpenCalendar(),
}
"""`METAL` maps to the same FX-weekday calendar as `FX`: this broker trades

metals on FX-like hours. Revisit with real evidence if that is ever wrong
for a specific metal — this is a starting assumption stated explicitly, not
a claim verified against a real metals session."""


def calendar_for(asset_class: AssetClass) -> TradingCalendar:
    return _CALENDARS_BY_ASSET_CLASS[asset_class]


__all__ = [
    "AlwaysOpenCalendar",
    "FxWeekdayCalendar",
    "TradingCalendar",
    "calendar_for",
]
