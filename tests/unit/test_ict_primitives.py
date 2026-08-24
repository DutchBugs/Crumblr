"""ICT primitives, tested against sequences built by hand.

Random data cannot tell you whether a fair-value-gap detector is correct — it
can only tell you how often it fires. Every test here constructs a bar sequence
where the right answer is known by construction, so a failure names the
definition that drifted.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from crumblr.domain.enums import Side
from crumblr.domain.models import Bar
from crumblr.trading_agent.imbalance import (
    average_true_range,
    find_fair_value_gaps,
    find_order_block,
)
from crumblr.trading_agent.liquidity import (
    LiquidityPool,
    detect_sweep,
    find_liquidity_pools,
    nearest_target,
)
from crumblr.trading_agent.sessions import Killzone, is_market_open, killzone_at
from crumblr.trading_agent.structure import (
    DealingRange,
    RangePosition,
    StructureState,
    find_swings,
    read_structure,
)

START = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)


def bar(
    index: int,
    *,
    open_: str,
    high: str,
    low: str,
    close: str,
) -> Bar:
    """One bar, `index` five-minute intervals after START."""
    return Bar(
        open_time_utc=START + timedelta(minutes=5 * index),
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        tick_volume=100,
    )


def zigzag(turning_points: list[str], *, legs: int = 3, pad: str = "0.00050") -> tuple[Bar, ...]:
    """Build a valid zigzag through `turning_points`.

    Each turning point becomes a bar that extends beyond its neighbours by
    `pad`, so it registers as a fractal swing. Intermediate bars interpolate.
    Constructing bars this way makes it impossible to write one that violates
    the OHLC invariant — which the first draft of these tests did.
    """
    padding = Decimal(pad)
    bars: list[Bar] = []
    index = 0
    for point_index, point in enumerate(turning_points):
        price = Decimal(point)
        is_peak = point_index % 2 == 1
        for leg in range(legs):
            is_turn = leg == legs - 1
            if is_turn:
                high = price + (padding if is_peak else padding / 4)
                low = price - (padding / 4 if is_peak else padding)
                open_price = price - padding / 8 if is_peak else price + padding / 8
                close_price = price + padding / 8 if is_peak else price - padding / 8
            else:
                open_price = close_price = price
                high = price + padding / 8
                low = price - padding / 8
            bars.append(
                Bar(
                    open_time_utc=START + timedelta(minutes=5 * index),
                    open=open_price,
                    high=max(high, open_price, close_price),
                    low=min(low, open_price, close_price),
                    close=close_price,
                    tick_volume=100,
                )
            )
            index += 1

    # A fractal needs bars on *both* sides, so the final turning point is only
    # detectable once something follows it. These trail off toward the previous
    # point, which cannot create a new extreme in either direction.
    if len(turning_points) >= 2:
        settle = (Decimal(turning_points[-1]) + Decimal(turning_points[-2])) / 2
        for _ in range(legs):
            bars.append(
                Bar(
                    open_time_utc=START + timedelta(minutes=5 * index),
                    open=settle,
                    high=settle + padding / 8,
                    low=settle - padding / 8,
                    close=settle,
                    tick_volume=100,
                )
            )
            index += 1
    return tuple(bars)


def flat_series(count: int, *, price: str = "1.10000", spread: str = "0.00020") -> list[Bar]:
    """A run of identical, unremarkable bars to pad a sequence."""
    centre = Decimal(price)
    half = Decimal(spread) / 2
    return [
        bar(
            i,
            open_=str(centre),
            high=str(centre + half),
            low=str(centre - half),
            close=str(centre),
        )
        for i in range(count)
    ]


class TestSwingDetection:
    def test_a_single_peak_is_a_swing_high(self) -> None:
        bars = (
            bar(0, open_="1.1000", high="1.1010", low="1.0990", close="1.1005"),
            bar(1, open_="1.1005", high="1.1015", low="1.0995", close="1.1010"),
            bar(2, open_="1.1010", high="1.1050", low="1.1005", close="1.1040"),  # peak
            bar(3, open_="1.1040", high="1.1045", low="1.1020", close="1.1025"),
            bar(4, open_="1.1025", high="1.1030", low="1.1010", close="1.1015"),
        )
        highs, _ = find_swings(bars, strength=2)
        assert len(highs) == 1
        assert highs[0].price == Decimal("1.1050")
        assert highs[0].index == 2

    def test_a_single_trough_is_a_swing_low(self) -> None:
        bars = (
            bar(0, open_="1.1010", high="1.1015", low="1.1000", close="1.1005"),
            bar(1, open_="1.1005", high="1.1010", low="1.0995", close="1.1000"),
            bar(2, open_="1.1000", high="1.1005", low="1.0960", close="1.0970"),  # trough
            bar(3, open_="1.0970", high="1.0990", low="1.0965", close="1.0985"),
            bar(4, open_="1.0985", high="1.1000", low="1.0980", close="1.0995"),
        )
        _, lows = find_swings(bars, strength=2)
        assert len(lows) == 1
        assert lows[0].price == Decimal("1.0960")

    def test_equal_highs_produce_no_swing(self) -> None:
        """Strict comparison: a double top is not a confirmed turning point."""
        bars = tuple(flat_series(7))
        highs, lows = find_swings(bars, strength=2)
        assert highs == ()
        assert lows == ()

    def test_too_short_a_series_yields_nothing(self) -> None:
        assert find_swings(tuple(flat_series(3)), strength=2) == ((), ())

    def test_zero_strength_is_refused(self) -> None:
        with pytest.raises(ValueError, match="swing strength must be at least 1"):
            find_swings(tuple(flat_series(9)), strength=0)


class TestStructureState:
    def test_higher_highs_and_higher_lows_read_bullish(self) -> None:
        bars = zigzag(["1.1000", "1.1050", "1.1020", "1.1090"])
        read = read_structure(bars, strength=2)
        assert read.state is StructureState.BULLISH

    def test_lower_highs_and_lower_lows_read_bearish(self) -> None:
        bars = zigzag(["1.1100", "1.1150", "1.1050", "1.1090"])
        read = read_structure(bars, strength=2)
        assert read.state is StructureState.BEARISH

    def test_an_expanding_range_is_not_a_trend(self) -> None:
        """A higher high with a lower low is expansion, not direction."""
        bars = zigzag(["1.1000", "1.1050", "1.0950", "1.1100"])
        assert read_structure(bars, strength=2).state is StructureState.UNCLEAR

    def test_insufficient_swings_read_unclear(self) -> None:
        read = read_structure(tuple(flat_series(20)), strength=2)
        assert read.state is StructureState.UNCLEAR
        assert read.dealing_range is None


class TestDealingRange:
    def _range(self) -> DealingRange:
        return DealingRange(
            low=Decimal("1.1000"),
            high=Decimal("1.1100"),
            low_time_utc=START,
            high_time_utc=START + timedelta(hours=1),
        )

    def test_equilibrium_is_the_midpoint(self) -> None:
        assert self._range().equilibrium == Decimal("1.1050")

    def test_above_the_midpoint_is_premium(self) -> None:
        assert self._range().position_of(Decimal("1.1090")) is RangePosition.PREMIUM

    def test_below_the_midpoint_is_discount(self) -> None:
        assert self._range().position_of(Decimal("1.1010")) is RangePosition.DISCOUNT

    def test_the_midpoint_itself_is_equilibrium(self) -> None:
        assert self._range().position_of(Decimal("1.1050")) is RangePosition.EQUILIBRIUM

    def test_a_price_just_off_the_midpoint_stays_equilibrium(self) -> None:
        """Tolerance stops the classification flipping on the last decimal."""
        assert self._range().position_of(Decimal("1.10510")) is RangePosition.EQUILIBRIUM

    def test_retracement_is_zero_at_the_high_and_one_at_the_low(self) -> None:
        dealing = self._range()
        assert dealing.retracement_of(Decimal("1.1100")) == Decimal(0)
        assert dealing.retracement_of(Decimal("1.1000")) == Decimal(1)

    def test_the_ote_band_sits_where_expected(self) -> None:
        """0.62-0.79 retracement is the lower-middle of the range."""
        dealing = self._range()
        assert dealing.retracement_of(Decimal("1.10380")) == Decimal("0.62")
        assert dealing.retracement_of(Decimal("1.10210")) == Decimal("0.79")

    def test_a_degenerate_range_is_equilibrium(self) -> None:
        flat = DealingRange(
            low=Decimal("1.1000"),
            high=Decimal("1.1000"),
            low_time_utc=START,
            high_time_utc=START,
        )
        assert flat.position_of(Decimal("1.1000")) is RangePosition.EQUILIBRIUM
        assert flat.retracement_of(Decimal("1.1000")) == Decimal(0)


class TestFairValueGap:
    def _gap_sequence(self, *, bullish: bool) -> tuple[Bar, ...]:
        """Fourteen quiet bars to set ATR, then a three-bar displacement gap."""
        bars = flat_series(15, price="1.10000", spread="0.00040")
        if bullish:
            bars += [
                bar(15, open_="1.10000", high="1.10020", low="1.09980", close="1.10010"),
                bar(16, open_="1.10010", high="1.10400", low="1.10000", close="1.10380"),
                bar(17, open_="1.10380", high="1.10450", low="1.10100", close="1.10400"),
            ]
        else:
            bars += [
                bar(15, open_="1.10000", high="1.10020", low="1.09980", close="1.09990"),
                bar(16, open_="1.09990", high="1.10000", low="1.09600", close="1.09620"),
                bar(17, open_="1.09620", high="1.09900", low="1.09550", close="1.09600"),
            ]
        return tuple(bars)

    def test_a_bullish_gap_is_found_between_bar_one_high_and_bar_three_low(self) -> None:
        gaps = find_fair_value_gaps(self._gap_sequence(bullish=True))
        assert len(gaps) == 1
        gap = gaps[0]
        assert gap.direction is Side.BUY
        assert gap.lower == Decimal("1.10020")
        assert gap.upper == Decimal("1.10100")

    def test_a_bearish_gap_mirrors_it(self) -> None:
        gaps = find_fair_value_gaps(self._gap_sequence(bullish=False))
        assert len(gaps) == 1
        gap = gaps[0]
        assert gap.direction is Side.SELL
        assert gap.lower == Decimal("1.09900")
        assert gap.upper == Decimal("1.09980")

    def test_overlapping_bars_leave_no_gap(self) -> None:
        bars = tuple(flat_series(20, price="1.10000", spread="0.00100"))
        assert find_fair_value_gaps(bars, min_displacement_ratio=Decimal(0)) == ()

    def test_a_gap_without_displacement_is_filtered_out(self) -> None:
        """A gap is only interesting when the move that made it was forceful."""
        sequence = self._gap_sequence(bullish=True)
        assert find_fair_value_gaps(sequence, min_displacement_ratio=Decimal("50")) == ()

    def test_gap_geometry(self) -> None:
        gap = find_fair_value_gaps(self._gap_sequence(bullish=True))[0]
        assert gap.size == Decimal("0.00080")
        assert gap.midpoint == Decimal("1.10060")
        assert gap.contains(Decimal("1.10050"))
        assert not gap.contains(Decimal("1.10200"))
        assert gap.displacement_ratio > Decimal("1.5"), "the fixture must displace"

    def test_a_gap_traded_through_is_mitigated(self) -> None:
        gap = find_fair_value_gaps(self._gap_sequence(bullish=True))[0]
        later = (bar(18, open_="1.10100", high="1.10120", low="1.09900", close="1.09950"),)
        assert gap.is_mitigated_by(later)

    def test_a_gap_only_touched_is_not_mitigated(self) -> None:
        gap = find_fair_value_gaps(self._gap_sequence(bullish=True))[0]
        later = (bar(18, open_="1.10100", high="1.10120", low="1.10050", close="1.10090"),)
        assert not gap.is_mitigated_by(later)


class TestOrderBlock:
    def test_the_last_down_candle_before_a_bullish_leg_is_the_block(self) -> None:
        bars = (
            bar(0, open_="1.1010", high="1.1015", low="1.1000", close="1.1005"),  # down
            bar(1, open_="1.1005", high="1.1050", low="1.1000", close="1.1045"),  # up
            bar(2, open_="1.1045", high="1.1080", low="1.1040", close="1.1075"),
        )
        block = find_order_block(bars, displacement_index=2, direction=Side.BUY)
        assert block is not None
        assert block.index == 0
        assert block.low == Decimal("1.1000")
        assert block.high == Decimal("1.1015")

    def test_the_last_up_candle_before_a_bearish_leg_is_the_block(self) -> None:
        bars = (
            bar(0, open_="1.1000", high="1.1020", low="1.0995", close="1.1015"),  # up
            bar(1, open_="1.1015", high="1.1018", low="1.0970", close="1.0975"),  # down
            bar(2, open_="1.0975", high="1.0980", low="1.0930", close="1.0935"),
        )
        block = find_order_block(bars, displacement_index=2, direction=Side.SELL)
        assert block is not None
        assert block.index == 0

    def test_a_one_sided_run_has_no_order_block(self) -> None:
        bars = tuple(
            bar(i, open_="1.1000", high="1.1050", low="1.0995", close="1.1045") for i in range(5)
        )
        assert find_order_block(bars, displacement_index=4, direction=Side.BUY) is None

    def test_an_out_of_range_index_returns_nothing(self) -> None:
        bars = tuple(flat_series(5))
        assert find_order_block(bars, displacement_index=99, direction=Side.BUY) is None


class TestLiquidityPools:
    def test_equal_levels_merge_into_one_pool(self) -> None:
        bars = zigzag(["1.1000", "1.1050", "1.1010", "1.1050"])
        highs, _ = find_swings(bars, strength=2)
        assert len(highs) == 2, "the fixture must produce two swing highs"
        pools = find_liquidity_pools(highs, is_buy_side=True, equality_tolerance=Decimal("0.0002"))
        assert len(pools) == 1
        assert pools[0].touch_count == 2

    def test_distinct_levels_stay_separate(self) -> None:
        from crumblr.trading_agent.structure import SwingPoint

        swings = (
            SwingPoint(index=1, time_utc=START, price=Decimal("1.1000"), is_high=True),
            SwingPoint(
                index=5,
                time_utc=START + timedelta(hours=1),
                price=Decimal("1.1500"),
                is_high=True,
            ),
        )
        assert len(find_liquidity_pools(swings, is_buy_side=True)) == 2

    def test_no_swings_means_no_pools(self) -> None:
        assert find_liquidity_pools((), is_buy_side=True) == ()


class TestSweepDetection:
    def _pool(self, price: str, *, buy_side: bool) -> LiquidityPool:
        return LiquidityPool(
            price=Decimal(price), time_utc=START, is_buy_side=buy_side, touch_count=1
        )

    def test_a_low_taken_and_rejected_is_a_buy_sweep(self) -> None:
        bars = (
            bar(1, open_="1.1010", high="1.1015", low="1.1005", close="1.1010"),
            bar(2, open_="1.1010", high="1.1012", low="1.0980", close="1.1008"),  # sweep
        )
        sweep = detect_sweep(bars, (self._pool("1.1000", buy_side=False),), lookback=5)
        assert sweep is not None
        assert sweep.direction is Side.BUY
        assert sweep.extreme == Decimal("1.0980")
        assert sweep.closed_back_inside

    def test_a_high_taken_and_rejected_is_a_sell_sweep(self) -> None:
        bars = (
            bar(1, open_="1.0990", high="1.0995", low="1.0985", close="1.0990"),
            bar(2, open_="1.0990", high="1.1020", low="1.0988", close="1.0995"),  # sweep
        )
        sweep = detect_sweep(bars, (self._pool("1.1000", buy_side=True),), lookback=5)
        assert sweep is not None
        assert sweep.direction is Side.SELL
        assert sweep.extreme == Decimal("1.1020")

    def test_closing_beyond_the_level_is_not_a_sweep(self) -> None:
        """Price that breaks and holds has broken the level, not swept it."""
        bars = (
            bar(1, open_="1.1010", high="1.1015", low="1.1005", close="1.1010"),
            bar(2, open_="1.1010", high="1.1012", low="1.0980", close="1.0985"),
        )
        assert detect_sweep(bars, (self._pool("1.1000", buy_side=False),), lookback=5) is None

    def test_a_level_already_traded_through_cannot_be_swept_again(self) -> None:
        """Spent liquidity is spent. This is what keeps sweeps rare."""
        bars = (
            bar(1, open_="1.1010", high="1.1015", low="1.0975", close="1.1010"),  # takes it
            *[
                bar(i, open_="1.1010", high="1.1015", low="1.1005", close="1.1010")
                for i in range(2, 8)
            ],
            bar(8, open_="1.1010", high="1.1012", low="1.0980", close="1.1008"),  # again
        )
        # A lookback of 3 sees only the re-break, not the original take.
        assert detect_sweep(bars, (self._pool("1.1000", buy_side=False),), lookback=3) is None

    def test_the_first_take_of_a_level_is_the_sweep(self) -> None:
        bars = (
            bar(1, open_="1.1010", high="1.1015", low="1.0975", close="1.1010"),
            bar(2, open_="1.1010", high="1.1015", low="1.1005", close="1.1010"),
        )
        sweep = detect_sweep(bars, (self._pool("1.1000", buy_side=False),), lookback=5)
        assert sweep is not None
        assert sweep.bar_index == 0

    def test_a_level_formed_after_the_bar_is_ignored(self) -> None:
        pool = LiquidityPool(
            price=Decimal("1.1000"),
            time_utc=START + timedelta(hours=5),
            is_buy_side=False,
        )
        bars = (bar(1, open_="1.1010", high="1.1015", low="1.0980", close="1.1008"),)
        assert detect_sweep(bars, (pool,), lookback=5) is None

    def test_no_pools_means_no_sweep(self) -> None:
        assert detect_sweep(tuple(flat_series(5)), (), lookback=5) is None


class TestLiquidityTargets:
    def test_a_long_targets_the_nearest_pool_above(self) -> None:
        pools = (
            LiquidityPool(price=Decimal("1.1050"), time_utc=START, is_buy_side=True),
            LiquidityPool(price=Decimal("1.1200"), time_utc=START, is_buy_side=True),
        )
        assert nearest_target(pools, from_price=Decimal("1.1000"), direction=Side.BUY) == Decimal(
            "1.1050"
        )

    def test_a_short_targets_the_nearest_pool_below(self) -> None:
        pools = (
            LiquidityPool(price=Decimal("1.0950"), time_utc=START, is_buy_side=False),
            LiquidityPool(price=Decimal("1.0800"), time_utc=START, is_buy_side=False),
        )
        assert nearest_target(pools, from_price=Decimal("1.1000"), direction=Side.SELL) == Decimal(
            "1.0950"
        )

    def test_no_pool_in_the_right_direction_yields_nothing(self) -> None:
        pools = (LiquidityPool(price=Decimal("1.0900"), time_utc=START, is_buy_side=True),)
        assert nearest_target(pools, from_price=Decimal("1.1000"), direction=Side.BUY) is None


class TestAverageTrueRange:
    def test_atr_of_uniform_bars_is_the_bar_range(self) -> None:
        bars = tuple(flat_series(20, price="1.10000", spread="0.00100"))
        assert average_true_range(bars, period=14) == Decimal("0.00100")

    def test_too_little_history_yields_zero(self) -> None:
        assert average_true_range(tuple(flat_series(5)), period=14) == Decimal(0)


class TestKillzones:
    """Windows are defined in New York local time, so DST must be handled."""

    def test_london_open_in_winter(self) -> None:
        # 07:00 UTC on 15 January is 02:00 EST.
        moment = datetime(2026, 1, 15, 7, 30, tzinfo=UTC)
        assert killzone_at(moment) is Killzone.LONDON_OPEN

    def test_london_open_in_summer(self) -> None:
        # 07:00 UTC on 15 July is 03:00 EDT — still inside the 02:00-05:00 window.
        moment = datetime(2026, 7, 15, 7, 30, tzinfo=UTC)
        assert killzone_at(moment) is Killzone.LONDON_OPEN

    def test_the_same_utc_hour_falls_in_different_zones_across_dst(self) -> None:
        """The bug a hard-coded UTC window would introduce, caught explicitly."""
        winter = datetime(2026, 1, 15, 12, 30, tzinfo=UTC)  # 07:30 EST
        summer = datetime(2026, 7, 15, 12, 30, tzinfo=UTC)  # 08:30 EDT
        assert killzone_at(winter) is Killzone.NEW_YORK_AM
        assert killzone_at(summer) is Killzone.NEW_YORK_AM

    def test_new_york_am_boundaries(self) -> None:
        assert killzone_at(datetime(2026, 1, 15, 12, 0, tzinfo=UTC)) is Killzone.NEW_YORK_AM
        # 15:00 UTC is 10:00 EST, which is London close, not New York AM.
        assert killzone_at(datetime(2026, 1, 15, 15, 0, tzinfo=UTC)) is Killzone.LONDON_CLOSE

    def test_the_asian_range_spans_midnight(self) -> None:
        assert killzone_at(datetime(2026, 1, 15, 2, 0, tzinfo=UTC)) is Killzone.ASIAN_RANGE
        assert killzone_at(datetime(2026, 1, 16, 4, 0, tzinfo=UTC)) is Killzone.ASIAN_RANGE

    def test_dead_hours_are_no_killzone(self) -> None:
        # 06:00 EST — between London open and New York.
        assert killzone_at(datetime(2026, 1, 15, 11, 0, tzinfo=UTC)) is Killzone.NONE


class TestMarketHours:
    def test_the_market_is_open_midweek(self) -> None:
        assert is_market_open(datetime(2026, 1, 14, 12, 0, tzinfo=UTC))

    def test_the_market_is_shut_on_saturday(self) -> None:
        assert not is_market_open(datetime(2026, 1, 17, 12, 0, tzinfo=UTC))

    def test_the_market_shuts_friday_evening(self) -> None:
        # 23:00 UTC Friday is 18:00 EST, after the 17:00 close.
        assert not is_market_open(datetime(2026, 1, 16, 23, 0, tzinfo=UTC))
        assert is_market_open(datetime(2026, 1, 16, 20, 0, tzinfo=UTC))

    def test_the_market_reopens_sunday_evening(self) -> None:
        # 18 January 2026 is a Sunday. 23:00 UTC is 18:00 EST, after the open.
        assert not is_market_open(datetime(2026, 1, 18, 20, 0, tzinfo=UTC))
        assert is_market_open(datetime(2026, 1, 18, 23, 0, tzinfo=UTC))
