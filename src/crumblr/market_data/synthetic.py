"""Deterministic synthetic market data for replay.

This exists to exercise the platform, not to represent the market. build.md
§9.2 is explicit that a baseline strategy's purpose is infrastructure
validation, not an edge; the same applies twice over to a random walk. Any P&L
produced from this generator is an artefact of the seed.

What it does give us is a reproducible event stream with known regime changes
and injectable faults, so the risk engine and supervisor can be observed
refusing trades under conditions that are otherwise hard to arrange on demand.
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from crumblr.domain.enums import BarOrigin, DataQuality, SessionState
from crumblr.domain.models import Bar, InstrumentSpec, MarketBar, MarketSnapshot, MarketTick
from crumblr.domain.money import price_to_points, quantize_price
from crumblr.domain.timeutils import UtcDatetime
from crumblr.persistence.market_data import bar_identity, tick_identity
from crumblr.trading_agent.sessions import NEW_YORK, is_market_open

REPLAY_EPOCH = datetime(2026, 1, 5, 8, 0, 0, tzinfo=UTC)
"""Default start of a generated series.

Fixed rather than "now": a replay whose start time comes from the wall clock is
not reproducible, and build.md §13.3 makes reproducibility a promotion
requirement. Every input to a replay must be stated, including when it began.
"""


@dataclass(frozen=True)
class FaultInjection:
    """build.md §20 / §25.5 — faults the replay can inject on demand.

    Each is expressed as "one bar in N", so a run of a given length contains a
    predictable number of them.
    """

    spread_spike_every: int = 0
    """Widen the spread far beyond the configured limit."""

    stale_tick_every: int = 0
    """Emit a snapshot whose event time is far older than the receive time."""

    suspect_quality_every: int = 0
    """Flag a snapshot as SUSPECT without changing its numbers."""

    spread_spike_multiplier: int = 12
    stale_tick_age: timedelta = timedelta(seconds=45)

    @property
    def enabled(self) -> bool:
        return bool(self.spread_spike_every or self.stale_tick_every or self.suspect_quality_every)


@dataclass(frozen=True)
class SyntheticMarketConfig:
    """Parameters of the generated series. The seed alone determines the path."""

    seed: int = 20260817
    bar_count: int = 600
    start_price: Decimal = Decimal("1.08500")
    timeframe: str = "M5"
    bar_interval: timedelta = timedelta(minutes=5)
    base_spread_points: int = 8

    calm_volatility_points: int = 12
    """Typical per-bar move during a low-volatility stretch."""

    active_volatility_points: int = 45
    """Typical per-bar move during a high-volatility stretch."""

    regime_length_bars: int = 90
    """How long the generator stays in one volatility regime."""

    trend_strength_points: int = 4
    """Per-bar drift applied during a trending stretch."""

    session_profile: bool = True
    """Scale volatility by session, so time-of-day logic has something to act on."""

    asian_multiplier: Decimal = Decimal("0.5")
    london_multiplier: Decimal = Decimal("1.7")
    new_york_multiplier: Decimal = Decimal("1.5")

    skip_weekend: bool = True
    """Skip bars when the FX market is closed, so the week has real boundaries."""

    faults: FaultInjection = field(default_factory=FaultInjection)


@dataclass(frozen=True)
class GeneratedTick:
    """One replay step: a closed bar plus the quote observed at its close."""

    bar: Bar
    bid: Decimal
    ask: Decimal
    spread_points: int
    event_time_utc: UtcDatetime
    received_time_utc: UtcDatetime
    data_quality: DataQuality
    injected_fault: str | None = None


def _regime_at(index: int, config: SyntheticMarketConfig) -> tuple[int, int]:
    """Return (volatility_points, drift_points) for the bar at `index`.

    The generator cycles through four stretches — calm range, calm trend,
    volatile range, volatile trend — so a single run covers every regime the
    strategy and supervisor are expected to distinguish.
    """
    phase = (index // config.regime_length_bars) % 4
    volatile = phase >= 2
    trending = phase % 2 == 1
    volatility = config.active_volatility_points if volatile else config.calm_volatility_points
    drift = config.trend_strength_points if trending else 0
    if trending and (index // config.regime_length_bars) % 8 >= 4:
        drift = -drift
    return volatility, drift


def _session_multiplier(moment: UtcDatetime, config: SyntheticMarketConfig) -> Decimal:
    """Scale volatility by time of day, in New York terms.

    Without this the series is stationary around the clock, and any session
    logic — killzones, an Asian range that later gets swept — has nothing to
    act on. The shape is the familiar one: a quiet Asian session, expansion at
    the London open, a second expansion at the New York open, and a fade into
    the close.

    This makes the data *shaped* like a trading day. It does not make it a
    trading day: there is still no order flow and no participants.
    """
    if not config.session_profile:
        return Decimal(1)

    hour = moment.astimezone(NEW_YORK).hour
    if 2 <= hour < 5:  # London open
        return config.london_multiplier
    if 7 <= hour < 11:  # New York morning
        return config.new_york_multiplier
    if 11 <= hour < 16:  # afternoon fade
        return Decimal("0.8")
    return config.asian_multiplier  # overnight range


def generate_ticks(
    config: SyntheticMarketConfig,
    spec: InstrumentSpec,
    *,
    start_time: UtcDatetime | None = None,
) -> Iterator[GeneratedTick]:
    """Yield `config.bar_count` closed bars with their closing quotes.

    Deterministic: the same config and spec always produce the same sequence,
    which is what makes a replay comparable against a previous run.
    """
    rng = random.Random(config.seed)
    point = spec.point
    price = config.start_price
    clock = start_time if start_time is not None else REPLAY_EPOCH
    faults = config.faults

    index = 0
    while index < config.bar_count:
        if config.skip_weekend and not is_market_open(clock):
            # Advance the clock without emitting a bar; the market is shut.
            clock = clock + config.bar_interval
            continue

        volatility_points, drift_points = _regime_at(index, config)
        session_scale = _session_multiplier(clock, config)
        volatility_points = max(1, int(Decimal(volatility_points) * session_scale))

        open_price = price
        moves = [
            Decimal(round(rng.gauss(drift_points, volatility_points))) * point for _ in range(4)
        ]
        path = [open_price]
        for move in moves:
            path.append(max(path[-1] + move, point * 1000))
        close_price = path[-1]

        high = max(path)
        low = min(path)
        digits = spec.digits
        open_price = quantize_price(open_price, digits, ROUND_HALF_EVEN)
        close_price = quantize_price(close_price, digits, ROUND_HALF_EVEN)
        high = quantize_price(high, digits, ROUND_HALF_EVEN)
        low = quantize_price(low, digits, ROUND_HALF_EVEN)
        high = max(high, open_price, close_price)
        low = min(low, open_price, close_price)

        spread_points = config.base_spread_points + int(
            abs(rng.gauss(0, volatility_points / 6)) if volatility_points else 0
        )
        data_quality = DataQuality.GOOD
        injected_fault: str | None = None
        event_time = clock
        received_time = clock + timedelta(milliseconds=rng.randint(3, 40))

        if faults.spread_spike_every and index % faults.spread_spike_every == 0 and index:
            spread_points *= faults.spread_spike_multiplier
            injected_fault = "spread_spike"
        elif faults.stale_tick_every and index % faults.stale_tick_every == 0 and index:
            event_time = clock - faults.stale_tick_age
            injected_fault = "stale_tick"
        elif faults.suspect_quality_every and index % faults.suspect_quality_every == 0 and index:
            data_quality = DataQuality.SUSPECT
            injected_fault = "suspect_quality"

        half_spread = (Decimal(spread_points) * point) / Decimal(2)
        bid = quantize_price(close_price - half_spread, digits, ROUND_HALF_EVEN)
        ask = quantize_price(close_price + half_spread, digits, ROUND_HALF_EVEN)

        yield GeneratedTick(
            bar=Bar(
                open_time_utc=clock,
                open=open_price,
                high=high,
                low=low,
                close=close_price,
                tick_volume=rng.randint(40, 400),
                spread_points=spread_points,
            ),
            bid=bid,
            ask=ask,
            spread_points=price_to_points(ask - bid, point),
            event_time_utc=event_time,
            received_time_utc=received_time,
            data_quality=data_quality,
            injected_fault=injected_fault,
        )

        price = close_price
        clock = clock + config.bar_interval
        index += 1


SYNTHETIC_SOURCE = "synthetic:seeded-random-walk-v1"
"""Where these observations came from.

Stored on every tick and bar so that synthetic history can never be pooled
with a broker feed by accident. A row that says this is not evidence about any
real market, and says so in the column rather than in a README.
"""


def as_market_tick(tick: GeneratedTick, spec: InstrumentSpec) -> MarketTick:
    """The raw record of the quote this replay step observed (F-022)."""
    return MarketTick(
        tick_id=tick_identity(
            source=SYNTHETIC_SOURCE,
            canonical_symbol=spec.canonical_symbol,
            event_time_utc=tick.event_time_utc,
            bid=tick.bid,
            ask=tick.ask,
        ),
        source=SYNTHETIC_SOURCE,
        canonical_symbol=spec.canonical_symbol,
        broker_symbol=spec.broker_symbol,
        event_time_utc=tick.event_time_utc,
        received_time_utc=tick.received_time_utc,
        bid=tick.bid,
        ask=tick.ask,
        data_quality=tick.data_quality,
    )


def as_market_bar(tick: GeneratedTick, spec: InstrumentSpec, *, timeframe: str) -> MarketBar:
    """The raw record of the bar this replay step closed.

    `origin` is SYNTHETIC and not BROKER: the generator emits bars directly
    rather than aggregating ticks, and a reader six months from now must not
    have to guess which of the two produced a row.
    """
    return MarketBar(
        bar_id=bar_identity(
            source=SYNTHETIC_SOURCE,
            canonical_symbol=spec.canonical_symbol,
            timeframe=timeframe,
            open_time_utc=tick.bar.open_time_utc,
        ),
        source=SYNTHETIC_SOURCE,
        canonical_symbol=spec.canonical_symbol,
        broker_symbol=spec.broker_symbol,
        timeframe=timeframe,
        bar=tick.bar,
        origin=BarOrigin.SYNTHETIC,
        received_time_utc=tick.received_time_utc,
        data_quality=tick.data_quality,
    )


def snapshot_id_for(canonical_symbol: str, event_time_utc: UtcDatetime) -> UUID:
    """The identity of the decision window at `event_time_utc`.

    Derived rather than randomly generated, so two replays of the same series
    produce identical ids and therefore identical decision capsules. Exposed
    separately from `build_snapshot` because a window has an identity even
    when no snapshot was built for it — a warm-up window, or a halt raised
    between windows, still has to name where it happened.
    """
    return uuid5(
        NAMESPACE_URL,
        f"crumblr:snapshot:{canonical_symbol}:{event_time_utc.isoformat()}",
    )


def build_snapshot(
    tick: GeneratedTick,
    *,
    history: tuple[Bar, ...],
    spec: InstrumentSpec,
    timeframe: str,
    session_state: SessionState = SessionState.OPEN,
) -> MarketSnapshot:
    """Assemble the normalised snapshot handed to the Trading Agent."""
    return MarketSnapshot(
        snapshot_id=snapshot_id_for(spec.canonical_symbol, tick.event_time_utc),
        symbol=spec.canonical_symbol,
        event_time_utc=tick.event_time_utc,
        received_time_utc=tick.received_time_utc,
        bid=tick.bid,
        ask=tick.ask,
        spread_points=tick.spread_points,
        timeframe=timeframe,
        bars=history,
        session_state=session_state,
        symbol_spec_version=spec.spec_version,
        data_quality=tick.data_quality,
    )
