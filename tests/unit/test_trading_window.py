"""Intraday-only trading (owner decision O-003; review 1.6 F-025).

Review 1.5 §1 gives an instruction that reads like pedantry and is not:

    Do not silently interpret "intraday" as "close at midnight UTC".

The FX day rolls at 17:00 New York. A position closed at midnight UTC has
already been carried through a rollover and charged swap for it, so a system
that believes it is flat by midnight is wrong about the one thing the policy
exists to control. Several tests below are about exactly that hour.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from crumblr.config import IntradayConfig
from crumblr.domain.enums import ReasonCode, RiskVerdict, Side
from crumblr.domain.models import PositionState, RiskDecision
from crumblr.risk.trading_window import (
    IntradayPolicy,
    SessionPhase,
    has_crossed_rollover,
    permits_new_entry,
    phase_at,
    policy_from_config,
    requires_flat,
    session_close,
    time_until_close,
)
from tests.conftest import make_instrument_spec, make_snapshot

POLICY = IntradayPolicy(
    enabled=True,
    last_entry_offset=timedelta(minutes=60),
    flatten_offset=timedelta(minutes=15),
)

# A Tuesday in January (New York on standard time) and one in July (daylight
# saving). The pair exists to catch a boundary pinned to a fixed UTC hour.
WINTER = datetime(2026, 1, 6, 12, 0, tzinfo=UTC)
SUMMER = datetime(2026, 7, 7, 12, 0, tzinfo=UTC)


def at(day: datetime, hour: int, minute: int = 0) -> datetime:
    return day.replace(hour=hour, minute=minute)


class TestTheBoundaryIsSeventeenHundredNewYork:
    def test_the_day_ends_at_five_pm_new_york_in_winter(self) -> None:
        assert session_close(WINTER) == datetime(2026, 1, 6, 22, 0, tzinfo=UTC)

    def test_the_day_ends_at_five_pm_new_york_in_summer(self) -> None:
        """One hour earlier in UTC, because New York moved and the market did not."""
        assert session_close(SUMMER) == datetime(2026, 7, 7, 21, 0, tzinfo=UTC)

    def test_the_boundary_is_not_midnight_utc(self) -> None:
        """The trap review 1.5 §1 names, asserted rather than commented."""
        for day in (WINTER, SUMMER):
            assert session_close(day).hour != 0

    def test_midnight_utc_is_already_the_next_trading_day(self) -> None:
        """A position held to midnight UTC has been through a rollover."""
        midnight = datetime(2026, 1, 7, 0, 0, tzinfo=UTC)

        assert session_close(midnight) > midnight
        assert has_crossed_rollover(WINTER, midnight), (
            "a position opened at noon and held to midnight UTC crossed the 17:00 roll"
        )

    def test_time_until_close_counts_down_to_the_boundary(self) -> None:
        assert time_until_close(at(WINTER, 21, 0)) == timedelta(hours=1)


class TestThePhases:
    @pytest.mark.parametrize(
        ("hour", "minute", "expected"),
        [
            (12, 0, SessionPhase.OPEN),
            (20, 59, SessionPhase.OPEN),
            (21, 0, SessionPhase.NO_NEW_ENTRIES),
            (21, 30, SessionPhase.NO_NEW_ENTRIES),
            (21, 44, SessionPhase.NO_NEW_ENTRIES),
            (21, 45, SessionPhase.FLATTEN_REQUIRED),
            (21, 59, SessionPhase.FLATTEN_REQUIRED),
        ],
    )
    def test_the_day_moves_through_its_phases(
        self, hour: int, minute: int, expected: SessionPhase
    ) -> None:
        assert phase_at(at(WINTER, hour, minute), POLICY) is expected

    def test_the_new_day_opens_at_the_rollover(self) -> None:
        """17:00 New York is the start of a day as much as the end of one."""
        assert phase_at(at(WINTER, 22, 0), POLICY) is SessionPhase.OPEN

    def test_the_weekend_is_closed_whatever_the_policy_says(self) -> None:
        """Saturday. The intraday policy does not control when FX trades."""
        saturday = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)

        assert phase_at(saturday, POLICY) is SessionPhase.CLOSED
        assert phase_at(saturday, IntradayPolicy.disabled()) is SessionPhase.CLOSED

    def test_only_open_permits_a_new_entry(self) -> None:
        for phase in SessionPhase:
            assert phase.permits_new_entries is (phase is SessionPhase.OPEN)


class TestEntriesAndFlatness:
    def test_entries_are_refused_inside_the_last_hour(self) -> None:
        assert permits_new_entry(at(WINTER, 20, 0), POLICY)
        assert not permits_new_entry(at(WINTER, 21, 0), POLICY)

    def test_flatness_is_required_only_after_its_own_deadline(self) -> None:
        assert not requires_flat(at(WINTER, 21, 30), POLICY)
        assert requires_flat(at(WINTER, 21, 45), POLICY)

    def test_there_is_a_window_where_entries_stop_before_flatness_is_demanded(self) -> None:
        """The gap is the point: it is time to manage a position out.

        Demanding flatness at the same instant entries stop would leave no
        interval in which an open position could legitimately be closed.
        """
        moment = at(WINTER, 21, 30)

        assert not permits_new_entry(moment, POLICY)
        assert not requires_flat(moment, POLICY)

    def test_the_weekend_requires_flatness(self) -> None:
        """A position held over a weekend is overnight three times over."""
        assert requires_flat(datetime(2026, 1, 10, 12, 0, tzinfo=UTC), POLICY)


class TestADisabledPolicyImposesNothing:
    def test_it_permits_entries_at_any_open_moment(self) -> None:
        assert permits_new_entry(at(WINTER, 21, 55), IntradayPolicy.disabled())

    def test_it_never_demands_flatness(self) -> None:
        assert not requires_flat(at(WINTER, 21, 55), IntradayPolicy.disabled())

    def test_disabling_is_explicit_rather_than_a_default(self) -> None:
        """After O-003, "no intraday policy" must be a stated choice."""
        with pytest.raises(TypeError):
            IntradayPolicy()  # type: ignore[call-arg]


class TestCrossingTheRollover:
    """The hole the phase check has on its own."""

    def test_a_position_opened_today_has_not_crossed(self) -> None:
        assert not has_crossed_rollover(at(WINTER, 9, 0), at(WINTER, 16, 0))

    def test_a_position_held_through_the_roll_has_crossed(self) -> None:
        opened = at(WINTER, 16, 0)
        later = at(WINTER, 23, 0)  # 18:00 New York — the next trading day

        assert has_crossed_rollover(opened, later)

    def test_the_breach_does_not_expire_when_the_day_rolls(self) -> None:
        """Without this, surviving the deadline stops being a breach one second later.

        At 17:00 New York `phase_at` reports OPEN for the new day, so a check
        that only asked about the phase would forgive a position precisely
        when it became an overnight one.
        """
        opened = at(WINTER, 16, 0)
        just_after_roll = at(WINTER, 22, 1)

        assert phase_at(just_after_roll, POLICY) is SessionPhase.OPEN
        assert has_crossed_rollover(opened, just_after_roll)


class TestPolicyOrdering:
    def test_a_flatten_deadline_after_the_entry_cutoff_is_refused(self) -> None:
        """Otherwise entries are accepted after the book had to be flat."""
        with pytest.raises(ValueError, match="closer to the boundary"):
            IntradayPolicy(
                enabled=True,
                last_entry_offset=timedelta(minutes=10),
                flatten_offset=timedelta(minutes=30),
            )

    def test_negative_offsets_are_refused(self) -> None:
        with pytest.raises(ValueError, match="cannot be negative"):
            IntradayPolicy(
                enabled=True,
                last_entry_offset=timedelta(minutes=-1),
                flatten_offset=timedelta(minutes=-1),
            )

    def test_the_same_ordering_is_enforced_in_configuration(self) -> None:
        """A YAML file must not be able to express what the policy refuses."""
        with pytest.raises(ValueError, match="closer to the close"):
            IntradayConfig(
                enabled=True,
                last_entry_minutes_before_close=10,
                flatten_minutes_before_close=30,
            )

    def test_configuration_converts_to_the_offsets_the_module_reasons_in(self) -> None:
        policy = policy_from_config(
            IntradayConfig(
                enabled=True,
                last_entry_minutes_before_close=45,
                flatten_minutes_before_close=20,
            )
        )

        assert policy.enabled
        assert policy.last_entry_offset == timedelta(minutes=45)
        assert policy.flatten_offset == timedelta(minutes=20)

    def test_both_deadlines_are_required_in_configuration(self) -> None:
        """A missing deadline is a policy that holds overnight."""
        with pytest.raises(ValueError):
            IntradayConfig(enabled=True, last_entry_minutes_before_close=60)  # type: ignore[call-arg]


class TestTheRiskEngineEnforcesIt:
    """The policy on the path that refuses trades, not only as a pure function."""

    @staticmethod
    def _decide(
        moment: datetime,
        *,
        positions: tuple[PositionState, ...] = (),
        policy: IntradayPolicy = POLICY,
    ) -> RiskDecision:
        from tests.unit.test_risk_engine import (
            context,
            evaluate,
            healthy_intent,
            portfolio,
            risk_config,
        )

        return evaluate(
            intent=healthy_intent(
                created_at_utc=moment, expires_at_utc=moment + timedelta(seconds=30)
            ),
            snapshot=make_snapshot(event_time_utc=moment, received_time_utc=moment),
            portfolio_state=portfolio(
                open_positions=positions, open_risk_fraction=Decimal("0.005")
            ),
            risk_context=replace(
                context(risk=risk_config(max_open_positions=99, max_open_risk=Decimal("0.10"))),
                intraday=policy,
            ),
            now=moment + timedelta(milliseconds=100),
        )

    @staticmethod
    def _position(opened_at: datetime) -> PositionState:
        return PositionState(
            ticket=1,
            broker_symbol=make_instrument_spec().broker_symbol,
            side=Side.BUY,
            volume=Decimal("0.10"),
            open_price=Decimal("1.08500"),
            opened_at_utc=opened_at,
            profit=Decimal("0"),
            swap=Decimal("0"),
            observed_at_utc=opened_at,
        )

    def test_an_entry_before_the_cutoff_is_not_refused_for_the_session(self) -> None:
        decision = self._decide(at(WINTER, 20, 0))

        assert ReasonCode.SESSION_BLACKOUT not in decision.reason_codes

    def test_an_entry_inside_the_last_hour_is_blocked(self) -> None:
        decision = self._decide(at(WINTER, 21, 0))

        assert decision.verdict is RiskVerdict.BLOCK
        assert ReasonCode.SESSION_BLACKOUT in decision.reason_codes

    def test_exposure_past_the_flatten_deadline_halts(self) -> None:
        """A block would leave the position to roll over. Only a halt does not."""
        decision = self._decide(at(WINTER, 21, 50), positions=(self._position(at(WINTER, 15, 0)),))

        assert decision.verdict is RiskVerdict.HALT
        assert ReasonCode.OVERNIGHT_EXPOSURE in decision.reason_codes

    def test_a_position_that_crossed_the_roll_halts_even_though_the_day_is_open(self) -> None:
        """The case the phase check alone would forgive."""
        just_after_roll = at(WINTER, 22, 30)
        decision = self._decide(just_after_roll, positions=(self._position(at(WINTER, 15, 0)),))

        assert phase_at(just_after_roll, POLICY) is SessionPhase.OPEN
        assert decision.verdict is RiskVerdict.HALT
        assert ReasonCode.OVERNIGHT_EXPOSURE in decision.reason_codes

    def test_a_flat_book_past_the_deadline_does_not_halt(self) -> None:
        """The rule is about exposure, not about the hour."""
        decision = self._decide(at(WINTER, 21, 50))

        assert ReasonCode.OVERNIGHT_EXPOSURE not in decision.reason_codes

    def test_a_disabled_policy_enforces_neither_rule(self) -> None:
        decision = self._decide(
            at(WINTER, 21, 50),
            positions=(self._position(at(WINTER, 15, 0)),),
            policy=IntradayPolicy.disabled(),
        )

        assert ReasonCode.SESSION_BLACKOUT not in decision.reason_codes
        assert ReasonCode.OVERNIGHT_EXPOSURE not in decision.reason_codes
