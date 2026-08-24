"""Liquidity pools and sweeps.

The premise is that resting stop orders cluster just beyond obvious swing
points, and that price is drawn to them before reversing. Whether that premise
holds is an empirical question this module takes no position on; what it does
is define the observable pattern precisely enough to detect and to test.

A **sweep** — the pattern the entry model waits for — is a bar that trades
through a prior swing level and then closes back on the original side. Price
reached beyond the level and did not hold there.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from crumblr.domain.enums import Side
from crumblr.domain.models import Bar
from crumblr.domain.money import ZERO
from crumblr.domain.timeutils import UtcDatetime
from crumblr.trading_agent.structure import SwingPoint


@dataclass(frozen=True)
class LiquidityPool:
    """A swing level with stops presumed resting beyond it."""

    price: Decimal
    time_utc: UtcDatetime
    is_buy_side: bool
    """Buy-side liquidity sits above old highs; sell-side below old lows."""

    touch_count: int = 1
    """How many swings share this level. Equal highs are a stronger draw."""


@dataclass(frozen=True)
class LiquiditySweep:
    """A level taken out and rejected within the same bar."""

    direction: Side
    """The trade direction the sweep argues for — a low swept implies BUY."""

    swept_level: Decimal
    extreme: Decimal
    """How far beyond the level price actually reached. Stops go beyond this."""

    bar_index: int
    time_utc: UtcDatetime
    closed_back_inside: bool

    @property
    def penetration(self) -> Decimal:
        return abs(self.extreme - self.swept_level)


def find_liquidity_pools(
    swings: tuple[SwingPoint, ...],
    *,
    is_buy_side: bool,
    equality_tolerance: Decimal = Decimal("0.0002"),
) -> tuple[LiquidityPool, ...]:
    """Group swings into pools, merging levels that are equal within tolerance.

    Equal highs and equal lows matter in this methodology: two swings at the
    same price are read as a larger cluster of resting orders than one. Merged
    pools report a `touch_count` above 1.
    """
    if not swings:
        return ()

    ordered = sorted(swings, key=lambda swing: swing.price)
    pools: list[LiquidityPool] = []
    bucket: list[SwingPoint] = [ordered[0]]

    for swing in ordered[1:]:
        if abs(swing.price - bucket[-1].price) <= equality_tolerance:
            bucket.append(swing)
        else:
            pools.append(_pool_from(bucket, is_buy_side=is_buy_side))
            bucket = [swing]
    pools.append(_pool_from(bucket, is_buy_side=is_buy_side))

    return tuple(sorted(pools, key=lambda pool: pool.time_utc))


def _pool_from(bucket: list[SwingPoint], *, is_buy_side: bool) -> LiquidityPool:
    # The extreme of the cluster is the level stops sit beyond, not its mean.
    price = max(s.price for s in bucket) if is_buy_side else min(s.price for s in bucket)
    latest = max(bucket, key=lambda swing: swing.time_utc)
    return LiquidityPool(
        price=price,
        time_utc=latest.time_utc,
        is_buy_side=is_buy_side,
        touch_count=len(bucket),
    )


def _first_touch_index(bars: tuple[Bar, ...], pool: LiquidityPool) -> int:
    """Index of the first bar after the pool formed that traded through it.

    A level price has already traded through has had its stops taken; it is
    spent, and running through it again is not a sweep. Without this every
    swing in the history stays permanently "sweepable", and since a few hundred
    bars hold a few dozen swings, almost any bar appears to sweep something —
    which makes the condition meaningless.

    Computed once per pool rather than per candidate bar: the naive form is
    quadratic in bar count and dominates the runtime of a long replay.
    """
    for index, bar in enumerate(bars):
        if bar.open_time_utc <= pool.time_utc:
            continue
        if pool.is_buy_side and bar.high > pool.price:
            return index
        if not pool.is_buy_side and bar.low < pool.price:
            return index
    return len(bars)


def detect_sweep(
    bars: tuple[Bar, ...],
    pools: tuple[LiquidityPool, ...],
    *,
    lookback: int = 5,
    min_penetration: Decimal = ZERO,
    require_close_back_inside: bool = True,
) -> LiquiditySweep | None:
    """Find the most recent sweep of one of `pools` within `lookback` bars.

    A sweep requires three things: the level predates the bar, the level was
    still untouched when the bar reached it, and price closed back on the
    original side. When several qualify, the deepest penetration wins, on the
    reading that the most decisive rejection is the most informative.
    """
    if not bars or not pools:
        return None

    window_start = max(0, len(bars) - lookback)
    best: LiquiditySweep | None = None
    first_touch = {id(pool): _first_touch_index(bars, pool) for pool in pools}

    for index in range(window_start, len(bars)):
        bar = bars[index]
        for pool in pools:
            if pool.time_utc >= bar.open_time_utc:
                continue
            if first_touch[id(pool)] < index:
                continue

            if pool.is_buy_side:
                if bar.high <= pool.price:
                    continue
                closed_back = bar.close < pool.price
                candidate = LiquiditySweep(
                    direction=Side.SELL,
                    swept_level=pool.price,
                    extreme=bar.high,
                    bar_index=index,
                    time_utc=bar.open_time_utc,
                    closed_back_inside=closed_back,
                )
            else:
                if bar.low >= pool.price:
                    continue
                closed_back = bar.close > pool.price
                candidate = LiquiditySweep(
                    direction=Side.BUY,
                    swept_level=pool.price,
                    extreme=bar.low,
                    bar_index=index,
                    time_utc=bar.open_time_utc,
                    closed_back_inside=closed_back,
                )

            if require_close_back_inside and not candidate.closed_back_inside:
                continue
            if candidate.penetration < min_penetration:
                continue
            if best is None or candidate.penetration > best.penetration:
                best = candidate

    return best


def nearest_target(
    pools: tuple[LiquidityPool, ...], *, from_price: Decimal, direction: Side
) -> Decimal | None:
    """The closest opposing pool to aim at.

    ICT targets liquidity rather than a fixed reward multiple: a long is taken
    toward the nearest untouched buy-side pool above.
    """
    if direction is Side.BUY:
        above = [pool.price for pool in pools if pool.price > from_price]
        return min(above) if above else None
    below = [pool.price for pool in pools if pool.price < from_price]
    return max(below) if below else None
