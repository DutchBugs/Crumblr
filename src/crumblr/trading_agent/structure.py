"""Market structure primitives for the ICT entry model.

ICT is normally applied discretionarily: a trader looks at a chart and judges
whether structure has shifted. A platform cannot judge, so every term used
downstream is pinned to an exact definition here. Where the literature is
ambiguous, this module picks one reading, states it, and stays consistent —
because a strategy that means something slightly different each time it runs
cannot be evaluated at all.

All computation is causal: functions receive a slice of closed bars and never
look beyond it.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from crumblr.domain.models import Bar
from crumblr.domain.money import ZERO
from crumblr.domain.timeutils import UtcDatetime


class StructureState(StrEnum):
    """Directional read of recent swing structure."""

    BULLISH = "BULLISH"
    """Higher highs and higher lows."""

    BEARISH = "BEARISH"
    """Lower highs and lower lows."""

    UNCLEAR = "UNCLEAR"
    """Mixed or insufficient swings. The supervisor treats this as unknown."""


class RangePosition(StrEnum):
    """Where price sits inside the current dealing range."""

    PREMIUM = "PREMIUM"
    DISCOUNT = "DISCOUNT"
    EQUILIBRIUM = "EQUILIBRIUM"


@dataclass(frozen=True)
class SwingPoint:
    """A fractal turning point.

    `index` is the position within the bar slice it was found in, so callers
    can measure how recent it is.
    """

    index: int
    time_utc: UtcDatetime
    price: Decimal
    is_high: bool


@dataclass(frozen=True)
class DealingRange:
    """The swing low to swing high span that premium/discount is measured against."""

    low: Decimal
    high: Decimal
    low_time_utc: UtcDatetime
    high_time_utc: UtcDatetime

    @property
    def equilibrium(self) -> Decimal:
        """The 50% level. ICT treats this as the fair value of the range."""
        return (self.low + self.high) / Decimal(2)

    @property
    def size(self) -> Decimal:
        return self.high - self.low

    def position_of(self, price: Decimal, *, tolerance: Decimal = Decimal("0.02")) -> RangePosition:
        """Classify `price` within the range.

        `tolerance` is a fraction of range size around the midpoint treated as
        equilibrium rather than as a shallow premium or discount, so a price
        sitting on the 50% line does not flip category on the last decimal.
        """
        if self.size <= ZERO:
            return RangePosition.EQUILIBRIUM
        band = self.size * tolerance
        if price > self.equilibrium + band:
            return RangePosition.PREMIUM
        if price < self.equilibrium - band:
            return RangePosition.DISCOUNT
        return RangePosition.EQUILIBRIUM

    def retracement_of(self, price: Decimal) -> Decimal:
        """How far `price` has retraced from the high back toward the low.

        0 at the high, 1 at the low. The ICT optimal-trade-entry band is
        expressed against this measure.
        """
        if self.size <= ZERO:
            return ZERO
        return (self.high - price) / self.size


@dataclass(frozen=True)
class StructureRead:
    """Everything the entry model needs to know about structure right now."""

    state: StructureState
    swing_highs: tuple[SwingPoint, ...]
    swing_lows: tuple[SwingPoint, ...]
    dealing_range: DealingRange | None
    last_swing_high: SwingPoint | None
    last_swing_low: SwingPoint | None
    broke_structure_up: bool
    broke_structure_down: bool
    shifted_up: bool
    shifted_down: bool

    @property
    def has_shift(self) -> bool:
        return self.shifted_up or self.shifted_down


def find_swings(
    bars: tuple[Bar, ...], *, strength: int = 2
) -> tuple[tuple[SwingPoint, ...], tuple[SwingPoint, ...]]:
    """Locate fractal swing highs and lows.

    A swing high at index `i` is a bar whose high is strictly greater than the
    highs of the `strength` bars on each side. Strict comparison means a flat
    double top produces no swing, which is the conservative reading: an
    unbroken level is not a confirmed turning point.

    Returns `(highs, lows)`, each ordered oldest first.
    """
    if strength < 1:
        raise ValueError(f"swing strength must be at least 1, got {strength}")

    highs: list[SwingPoint] = []
    lows: list[SwingPoint] = []
    if len(bars) < 2 * strength + 1:
        return (), ()

    for i in range(strength, len(bars) - strength):
        window = bars[i - strength : i + strength + 1]
        centre = bars[i]
        others = [bar for j, bar in enumerate(window) if j != strength]

        if all(centre.high > bar.high for bar in others):
            highs.append(
                SwingPoint(index=i, time_utc=centre.open_time_utc, price=centre.high, is_high=True)
            )
        if all(centre.low < bar.low for bar in others):
            lows.append(
                SwingPoint(index=i, time_utc=centre.open_time_utc, price=centre.low, is_high=False)
            )

    return tuple(highs), tuple(lows)


def _classify_state(highs: tuple[SwingPoint, ...], lows: tuple[SwingPoint, ...]) -> StructureState:
    """Bullish needs both a higher high and a higher low; bearish the mirror.

    Requiring both sides prevents an expanding range — a higher high *and* a
    lower low — from reading as a trend in either direction.
    """
    if len(highs) < 2 or len(lows) < 2:
        return StructureState.UNCLEAR

    higher_high = highs[-1].price > highs[-2].price
    higher_low = lows[-1].price > lows[-2].price
    lower_high = highs[-1].price < highs[-2].price
    lower_low = lows[-1].price < lows[-2].price

    if higher_high and higher_low:
        return StructureState.BULLISH
    if lower_high and lower_low:
        return StructureState.BEARISH
    return StructureState.UNCLEAR


def read_structure(
    bars: tuple[Bar, ...],
    *,
    strength: int = 2,
    shift_lookback: int = 6,
) -> StructureRead:
    """Build the full structural picture from a slice of closed bars.

    Two distinct events are reported, because ICT treats them differently:

    - **Break of structure** — price closes beyond the last swing in the same
      direction structure was already running. Continuation.
    - **Market structure shift** — price closes beyond the last swing *against*
      the prevailing structure. Reversal, and the event the entry model waits
      for.

    `shift_lookback` bounds how recently the break must have happened for it to
    still be considered live.
    """
    highs, lows = find_swings(bars, strength=strength)
    state = _classify_state(highs, lows)

    last_high = highs[-1] if highs else None
    last_low = lows[-1] if lows else None

    dealing_range: DealingRange | None = None
    if last_high is not None and last_low is not None:
        low_price, high_price = last_low.price, last_high.price
        if high_price > low_price:
            dealing_range = DealingRange(
                low=low_price,
                high=high_price,
                low_time_utc=last_low.time_utc,
                high_time_utc=last_high.time_utc,
            )

    break_up = _find_break(bars, highs, lookback=shift_lookback, strength=strength, upward=True)
    break_down = _find_break(bars, lows, lookback=shift_lookback, strength=strength, upward=False)

    # Whether a break is continuation or reversal depends on the structure that
    # was in place *before* it — not on the structure now, which the break has
    # already changed. Reading the current state would make a completed shift
    # invisible the moment it succeeded.
    up_prior = _state_before(highs, lows, index=break_up, strength=strength)
    down_prior = _state_before(highs, lows, index=break_down, strength=strength)

    return StructureRead(
        state=state,
        swing_highs=highs,
        swing_lows=lows,
        dealing_range=dealing_range,
        last_swing_high=last_high,
        last_swing_low=last_low,
        broke_structure_up=break_up is not None and up_prior is StructureState.BULLISH,
        broke_structure_down=break_down is not None and down_prior is StructureState.BEARISH,
        shifted_up=break_up is not None and up_prior is not StructureState.BULLISH,
        shifted_down=break_down is not None and down_prior is not StructureState.BEARISH,
    )


def _find_break(
    bars: tuple[Bar, ...],
    swings: tuple[SwingPoint, ...],
    *,
    lookback: int,
    strength: int,
    upward: bool,
) -> int | None:
    """Index of the most recent bar that closed beyond the then-current swing.

    "Then-current" is the point: a swing is only usable once `strength` bars
    have confirmed it, so a bar can only break a swing that was already
    established when that bar printed. Comparing against the latest swing
    instead would ask whether price closed beyond a level it created itself.
    """
    if lookback <= 0 or not swings:
        return None

    window_start = max(0, len(bars) - lookback)
    for index in range(len(bars) - 1, window_start - 1, -1):
        confirmed = [swing for swing in swings if swing.index + strength < index]
        if not confirmed:
            continue
        level = confirmed[-1].price
        close = bars[index].close
        if (upward and close > level) or (not upward and close < level):
            return index
    return None


def _state_before(
    highs: tuple[SwingPoint, ...],
    lows: tuple[SwingPoint, ...],
    *,
    index: int | None,
    strength: int,
) -> StructureState:
    """Structure as it read just before the bar at `index`."""
    if index is None:
        return StructureState.UNCLEAR
    prior_highs = tuple(swing for swing in highs if swing.index + strength < index)
    prior_lows = tuple(swing for swing in lows if swing.index + strength < index)
    return _classify_state(prior_highs, prior_lows)
