"""Intraday-only trading (owner decision O-003; review 1.6 F-025).

The owner's decision is that v1 holds nothing overnight. Review 1.5 §1 warns
against reading "intraday" as "close at midnight UTC", and it is right to: the
FX day rolls at 17:00 New York, so a position closed at midnight UTC has
already been carried across a rollover and charged swap for it.

The day therefore ends where `trading_agent.sessions.trading_day` already says
it does. That boundary is a market fact and is not configurable — two
definitions of when the day ends is one definition too many, and the drift
between them would be invisible until a position sat through it.

What *is* configurable is how far in front of the boundary the two deadlines
sit, because those are risk policy and belong to the owner:

    ├─────────── OPEN ───────────┼─ NO_NEW_ENTRIES ─┼─ FLATTEN_REQUIRED ─┤ 17:00 NY
                                 │                  │                    │
                          last entry cutoff   flatten deadline      session close

This module decides which phase a moment is in. It does **not** flatten
anything: closing a position needs the execution path, which is M5, and
building execution behaviour ahead of that gate is what the freeze exists to
prevent. What exists now is the refusal of new entries and the detection of an
exposure that outlived its deadline — see ADR-004 for what M5 must add.

The asymmetry is deliberate. Refusing to open is safe and can ship now.
Promising to close is a promise this system cannot yet keep, and a policy that
claims it would be worse than one that says plainly that it does not.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from enum import StrEnum
from typing import Self

from crumblr.config import IntradayConfig
from crumblr.domain.timeutils import UtcDatetime
from crumblr.trading_agent.sessions import (
    NEW_YORK,
    WEEK_CLOSE_HOUR_ET,
    is_market_open,
    trading_day,
)


class SessionPhase(StrEnum):
    """Where a moment sits relative to the end of its trading day."""

    OPEN = "OPEN"
    """New entries permitted, subject to every other rule."""

    NO_NEW_ENTRIES = "NO_NEW_ENTRIES"
    """Close enough to the boundary that a new position could not be managed out."""

    FLATTEN_REQUIRED = "FLATTEN_REQUIRED"
    """Any remaining exposure must be closed before the day rolls."""

    CLOSED = "CLOSED"
    """The market is not open at all — the weekend gap."""

    @property
    def permits_new_entries(self) -> bool:
        """Only OPEN does.

        Written as an equality rather than as "not CLOSED" so that a phase
        added later refuses by default instead of permitting.
        """
        return self is SessionPhase.OPEN


@dataclass(frozen=True)
class IntradayPolicy:
    """How long before the day rolls the two deadlines fall.

    Both are offsets rather than clock times, so they follow the boundary
    through daylight-saving changes instead of drifting an hour off it twice a
    year.
    """

    enabled: bool
    last_entry_offset: timedelta
    flatten_offset: timedelta

    def __post_init__(self) -> None:
        if self.last_entry_offset < self.flatten_offset:
            raise ValueError(
                f"last_entry_offset {self.last_entry_offset} is closer to the boundary than "
                f"flatten_offset {self.flatten_offset}: entries would still be accepted after "
                "the position was required to be flat"
            )
        if self.flatten_offset < timedelta(0) or self.last_entry_offset < timedelta(0):
            raise ValueError(
                "offsets are measured back from the session close and cannot be negative"
            )

    @classmethod
    def disabled(cls) -> Self:
        """A policy that imposes nothing.

        For replays and tests that are not exercising the session rules. It is
        explicit rather than a default, because "no intraday policy" must be a
        stated choice after O-003 rather than something that happens by not
        configuring anything.
        """
        return cls(enabled=False, last_entry_offset=timedelta(0), flatten_offset=timedelta(0))


def policy_from_config(config: IntradayConfig) -> IntradayPolicy:
    """Turn the configured minutes into the offsets this module reasons in."""
    return IntradayPolicy(
        enabled=config.enabled,
        last_entry_offset=timedelta(minutes=config.last_entry_minutes_before_close),
        flatten_offset=timedelta(minutes=config.flatten_minutes_before_close),
    )


def session_close(moment: UtcDatetime) -> datetime:
    """When the trading day containing `moment` ends, in UTC.

    17:00 New York on the trading day's own date. Derived from the same
    function the daily-loss baseline uses, so the risk day and the session day
    cannot disagree.
    """
    local_date = trading_day(moment)
    local_close = datetime.combine(local_date, time(WEEK_CLOSE_HOUR_ET, 0), tzinfo=NEW_YORK)
    return local_close.astimezone(moment.tzinfo)


def phase_at(moment: UtcDatetime, policy: IntradayPolicy) -> SessionPhase:
    """Which phase of the trading day `moment` falls in."""
    if not is_market_open(moment):
        return SessionPhase.CLOSED
    if not policy.enabled:
        return SessionPhase.OPEN

    close = session_close(moment)
    if moment >= close - policy.flatten_offset:
        return SessionPhase.FLATTEN_REQUIRED
    if moment >= close - policy.last_entry_offset:
        return SessionPhase.NO_NEW_ENTRIES
    return SessionPhase.OPEN


def permits_new_entry(moment: UtcDatetime, policy: IntradayPolicy) -> bool:
    """Whether a new position may be opened at `moment`."""
    return phase_at(moment, policy).permits_new_entries


def requires_flat(moment: UtcDatetime, policy: IntradayPolicy) -> bool:
    """Whether any exposure at `moment` is already past its deadline.

    A `True` here with a position still open is the condition O-003 forbids,
    and review 1.6 §4 is explicit that failing to prove flatness must not
    quietly become permission to hold overnight. It is therefore a halt, not a
    warning — see `ADR-004` and `risk.policies`.
    """
    if not policy.enabled:
        return False
    return phase_at(moment, policy) in {SessionPhase.FLATTEN_REQUIRED, SessionPhase.CLOSED}


def has_crossed_rollover(opened_at_utc: UtcDatetime, moment: UtcDatetime) -> bool:
    """Whether a position opened at `opened_at_utc` is still open past a rollover.

    The definition of an overnight position, stated directly rather than
    inferred from where the clock happens to be. It closes a hole the phase
    check has on its own: at 17:00 New York the day rolls, `phase_at` returns
    OPEN for the *new* day, and a position that survived the old day's flatten
    deadline would stop looking like a breach one second after becoming one.

    Comparing trading days instead means the breach stays a breach until
    somebody deals with it.
    """
    return trading_day(opened_at_utc) != trading_day(moment)


def time_until_close(moment: UtcDatetime) -> timedelta:
    """How long the trading day has left. Negative past the boundary."""
    return session_close(moment) - moment
