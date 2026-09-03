"""Weekly session policy (owner risk policy v1, D1.5; supersedes O-003).

The owner's original v1 decision (O-003, review 1.5 §1) was that the platform
holds nothing overnight at all. `review/OWNER_POLICY_V1.md` (2026-09-02)
replaced that with a weekly policy instead: weekday overnight holding is
permitted, and only the approach to the week's own close is restricted —
`review/adr/ADR-012-owner-session-policy-v1.md` records the decision in full.
ADR-004 (the original daily policy) is marked partially superseded there, not
rewritten.

The week has exactly one close, Friday 17:00 America/New_York
(`trading_agent.sessions.weekly_close`), derived from the same
`trading_agent.sessions.trading_day` the daily-loss baseline uses — the
platform's one calendar authority, not a second definition that could drift
from it.

What *is* configurable is how far in front of that one weekly close the two
deadlines sit, because those are risk policy and belong to the owner:

    ├──────────── OPEN ────────────┼─ NO_NEW_ENTRIES ─┼─ FLATTEN_REQUIRED ─┤ Fri 17:00 NY
                                   │                  │                    │
                            last entry cutoff   flatten deadline      weekly close

Monday through Thursday, the weekly close sits days away, so both offset
comparisons fall through arithmetically and every trading-day phase is OPEN —
"no daily cutoff/flatten" is a consequence of measuring against the weekly
close, not a special case coded for it.

This module decides which phase a moment is in and detects an exposure that
has already crossed the weekly close. It does not by itself flatten anything
— `application/execution.py`'s automatic-flatten machinery (item 7,
`review/adr/ADR-009-automatic-flatten-submission.md`) consumes what this
module detects.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import Self

from crumblr.config import IntradayConfig
from crumblr.domain.timeutils import UtcDatetime
from crumblr.trading_agent.sessions import is_market_open, weekly_close


class SessionPhase(StrEnum):
    """Where a moment sits relative to the week's own close."""

    OPEN = "OPEN"
    """New entries permitted, subject to every other rule.

    Monday through Thursday this is the only reachable phase while the
    market is open — the weekly close is always too far away for either
    offset to fire."""

    NO_NEW_ENTRIES = "NO_NEW_ENTRIES"
    """Close enough to the weekly close that a new position could not be

    managed out before it. Only reachable on the Friday trading day."""

    FLATTEN_REQUIRED = "FLATTEN_REQUIRED"
    """Any remaining exposure must be closed before the week closes. Only

    reachable on the Friday trading day."""

    CLOSED = "CLOSED"
    """The market is not open at all — the weekend gap. Holding any

    exposure here is exactly what owner risk policy v1 forbids
    ("weekend holding verboden")."""

    @property
    def permits_new_entries(self) -> bool:
        """Only OPEN does.

        Written as an equality rather than as "not CLOSED" so that a phase
        added later refuses by default instead of permitting.
        """
        return self is SessionPhase.OPEN


@dataclass(frozen=True)
class IntradayPolicy:
    """How long before the *weekly* close the two deadlines fall.

    The name predates owner risk policy v1's weekly redesign (D1.5) and is
    kept rather than renamed — it is a pure Python type plus one YAML config
    key (`config/paper.yaml`'s `intraday:` block), and a rename would touch
    every call site's imports for no behaviour change while also breaking the
    operator-facing config key. `review/adr/ADR-012-owner-session-policy-v1.md`
    §6 records this naming decision explicitly, mirroring how D1.4 reclassified
    `RiskConfig.max_open_positions` via its docstring rather than renaming it.

    Both offsets are measured back from the weekly close rather than as clock
    times, so they follow the boundary through daylight-saving changes instead
    of drifting an hour off it twice a year.
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
                "offsets are measured back from the weekly close and cannot be negative"
            )

    @classmethod
    def disabled(cls) -> Self:
        """A policy that imposes nothing.

        For replays and tests that are not exercising the session rules. It is
        explicit rather than a default, because "no session policy" must be a
        stated choice rather than something that happens by not configuring
        anything.
        """
        return cls(enabled=False, last_entry_offset=timedelta(0), flatten_offset=timedelta(0))


def policy_from_config(config: IntradayConfig) -> IntradayPolicy:
    """Turn the configured minutes into the offsets this module reasons in."""
    return IntradayPolicy(
        enabled=config.enabled,
        last_entry_offset=timedelta(minutes=config.last_entry_minutes_before_close),
        flatten_offset=timedelta(minutes=config.flatten_minutes_before_close),
    )


def phase_at(moment: UtcDatetime, policy: IntradayPolicy) -> SessionPhase:
    """Which phase of the trading week `moment` falls in.

    Compared directly against the weekly close, with no day-of-week branch:
    Monday's close is days away, so both offset comparisons are false and the
    function falls through to OPEN by arithmetic alone — Monday-Thursday
    "no cutoff" is a consequence of this shape, not a special case in it.
    """
    if not is_market_open(moment):
        return SessionPhase.CLOSED
    if not policy.enabled:
        return SessionPhase.OPEN

    close = weekly_close(moment)
    if moment >= close - policy.flatten_offset:
        return SessionPhase.FLATTEN_REQUIRED
    if moment >= close - policy.last_entry_offset:
        return SessionPhase.NO_NEW_ENTRIES
    return SessionPhase.OPEN


def permits_new_entry(moment: UtcDatetime, policy: IntradayPolicy) -> bool:
    """Whether a new position may be opened at `moment`."""
    return phase_at(moment, policy).permits_new_entries


def requires_flat(moment: UtcDatetime, policy: IntradayPolicy) -> bool:
    """Whether any exposure at `moment` is already past its deadline —

    either the Friday flatten deadline, or the market is simply closed
    (the weekend-exposure prohibition, covered here for free since CLOSED
    is one of the two phases this checks).

    A `True` here with a position still open is a real breach: review 1.6
    §4's original requirement — that failing to prove flatness must not
    quietly become permission to hold — carries forward unchanged under
    the weekly policy, only the deadline it is measured against changed.
    See `ReasonCode.OVERNIGHT_EXPOSURE` and `risk.policies`.
    """
    if not policy.enabled:
        return False
    return phase_at(moment, policy) in {SessionPhase.FLATTEN_REQUIRED, SessionPhase.CLOSED}


def has_crossed_weekly_close(opened_at_utc: UtcDatetime, moment: UtcDatetime) -> bool:
    """Whether a position opened at `opened_at_utc` is still open past the

    weekly close — i.e. it survived into or through the weekend. A normal
    weekday rollover (Monday through Thursday) does *not* trigger this;
    owner risk policy v1 permits exactly that.

    Named for what it now detects, replacing the old daily
    `has_crossed_rollover` (any day-boundary crossing was a breach under
    the pre-D1.5 daily policy). Compares `weekly_close` on each side rather
    than `moment`'s own phase, for the same reason the old function
    compared trading days rather than phases: at the moment the week
    rolls, `phase_at` returns OPEN for the *new* week, and a position that
    survived the old week's flatten deadline would stop looking like a
    breach one second after becoming one. Comparing weekly closes instead
    means the breach stays a breach until somebody deals with it.
    """
    return weekly_close(opened_at_utc) != weekly_close(moment)


def time_until_weekly_close(moment: UtcDatetime) -> timedelta:
    """How long the trading week has left. Negative past the boundary.

    Renamed from the old daily `time_until_close` — same zero production
    callers as before (it exists for tests/tooling), now measured against
    the one weekly boundary rather than a fabricated daily one.
    """
    return weekly_close(moment) - moment
