"""Imbalance primitives: fair value gaps, displacement and order blocks.

The three concepts are related and are often conflated in the source material,
so they are kept separate here:

- A **fair value gap** is a geometric fact about three bars — a price range the
  middle bar traded through so quickly that the neighbours never overlapped it.
- **Displacement** is a claim about force — that the move was large relative to
  recent range, rather than ordinary drift.
- An **order block** is a location — the last opposing candle before that move.

A gap without displacement is just a thin patch of tape. The entry model
requires both, which is why they are computed independently and combined by the
caller rather than bundled here.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from itertools import pairwise

from crumblr.domain.enums import Side
from crumblr.domain.models import Bar
from crumblr.domain.money import ZERO
from crumblr.domain.timeutils import UtcDatetime


@dataclass(frozen=True)
class FairValueGap:
    """An unfilled three-bar imbalance.

    For a bullish gap the range is (first.high, third.low): price moved up so
    hard that bar one's high never met bar three's low. Bearish is the mirror.
    """

    direction: Side
    lower: Decimal
    upper: Decimal
    created_index: int
    created_at_utc: UtcDatetime
    displacement_ratio: Decimal
    """Middle-bar range divided by ATR. The strength of the move that made it."""

    @property
    def size(self) -> Decimal:
        return self.upper - self.lower

    @property
    def midpoint(self) -> Decimal:
        """The 50% of the gap — ICT's consequent encroachment."""
        return (self.lower + self.upper) / Decimal(2)

    def contains(self, price: Decimal) -> bool:
        return self.lower <= price <= self.upper

    def is_mitigated_by(self, bars: tuple[Bar, ...]) -> bool:
        """True once later price action has traded fully through the gap.

        A gap price has already closed is spent; the entry model only considers
        gaps that are still open.
        """
        for bar in bars:
            if self.direction is Side.BUY and bar.low <= self.lower:
                return True
            if self.direction is Side.SELL and bar.high >= self.upper:
                return True
        return False


@dataclass(frozen=True)
class OrderBlock:
    """The last opposing candle before a displacement leg."""

    direction: Side
    low: Decimal
    high: Decimal
    index: int
    time_utc: UtcDatetime

    def contains(self, price: Decimal) -> bool:
        return self.low <= price <= self.high

    @property
    def midpoint(self) -> Decimal:
        return (self.low + self.high) / Decimal(2)


def average_true_range(bars: tuple[Bar, ...], period: int = 14) -> Decimal:
    """Mean true range over the last `period` bars, or zero when too short."""
    if len(bars) < period + 1:
        return ZERO
    # `period + 1` bars yield exactly `period` true ranges, since each range
    # needs the preceding close.
    ranges = [
        max(
            current.high - current.low,
            abs(current.high - previous.close),
            abs(current.low - previous.close),
        )
        for previous, current in pairwise(bars[-period - 1 :])
    ]
    return sum(ranges, ZERO) / Decimal(len(ranges))


def find_fair_value_gaps(
    bars: tuple[Bar, ...],
    *,
    min_displacement_ratio: Decimal = Decimal("1.5"),
    atr_period: int = 14,
    max_age_bars: int = 30,
    include_mitigated: bool = False,
) -> tuple[FairValueGap, ...]:
    """Find open fair value gaps in the recent slice, newest last.

    `min_displacement_ratio` filters out gaps created by ordinary bars: the
    middle bar's range must be at least this multiple of ATR. Setting it to
    zero returns every geometric gap, which is useful for testing the detection
    itself but not for trading.
    """
    atr = average_true_range(bars, atr_period)
    if atr <= ZERO or len(bars) < 3:
        return ()

    gaps: list[FairValueGap] = []
    start = max(2, len(bars) - max_age_bars)

    for i in range(start, len(bars)):
        first, middle, third = bars[i - 2], bars[i - 1], bars[i]
        ratio = (middle.high - middle.low) / atr
        if ratio < min_displacement_ratio:
            continue

        if third.low > first.high:
            gap = FairValueGap(
                direction=Side.BUY,
                lower=first.high,
                upper=third.low,
                created_index=i,
                created_at_utc=third.open_time_utc,
                displacement_ratio=ratio,
            )
        elif third.high < first.low:
            gap = FairValueGap(
                direction=Side.SELL,
                lower=third.high,
                upper=first.low,
                created_index=i,
                created_at_utc=third.open_time_utc,
                displacement_ratio=ratio,
            )
        else:
            continue

        if include_mitigated or not gap.is_mitigated_by(bars[i + 1 :]):
            gaps.append(gap)

    return tuple(gaps)


def find_order_block(
    bars: tuple[Bar, ...], *, displacement_index: int, direction: Side, max_lookback: int = 10
) -> OrderBlock | None:
    """The last opposing candle before the displacement at `displacement_index`.

    For a bullish leg that is the last down-close bar before it; for a bearish
    leg, the last up-close bar. Returns None when no opposing candle exists
    within `max_lookback`, which happens in a one-sided run.
    """
    if not 0 <= displacement_index < len(bars):
        return None

    earliest = max(0, displacement_index - max_lookback)
    for i in range(displacement_index - 1, earliest - 1, -1):
        bar = bars[i]
        is_down = bar.close < bar.open
        is_up = bar.close > bar.open
        if (direction is Side.BUY and is_down) or (direction is Side.SELL and is_up):
            return OrderBlock(
                direction=direction,
                low=bar.low,
                high=bar.high,
                index=i,
                time_utc=bar.open_time_utc,
            )
    return None
