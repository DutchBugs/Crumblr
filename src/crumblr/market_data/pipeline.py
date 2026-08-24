"""Ticks into normalized bars (build.md §26 M2, §12; review 1.6 F-022).

Milestone 2 asks for a normalized bar pipeline and, as an acceptance criterion,
for **gaps and out-of-order data to be detected**. Detected is a weaker word
than handled and a deliberate one: this module never invents a bar to cover a
gap and never discards a late tick to make a series look tidy. It puts the
observation where it belongs and records that the stream misbehaved, so a
decision taken on a degraded window can be found afterwards instead of blending
in with the rest.

Three modelling choices are made explicitly here, because leaving any of them
implicit is how a bar series quietly stops meaning what a reader assumes.

**Bars are built from one side of the book.** MT5 delivers bid-based bars for
FX, so that is the default, and the choice is part of the pipeline's identity
rather than a hidden constant — a series built on mid prices is a different
series, and `pipeline_version` says which one you are holding.

**A bar's identity is its interval, not its contents.** Re-running the pipeline
over the same ticks produces the same bars with the same ids, so re-ingesting is
a no-op. Producing a *different* bar for an interval already stored is a
contradiction, and `MarketDataStore.record_bars` raises on it.

**An empty interval produces no bar.** Not a flat bar, not a forward-filled
one. The gap is recorded as an observation and the next real bar carries the
`GAPPED` flag; a strategy that needs continuity can then see it did not have
any.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from crumblr.domain.enums import BarOrigin, DataQuality, StreamAnomaly
from crumblr.domain.models import Bar, InstrumentSpec, MarketBar, MarketTick
from crumblr.domain.money import ZERO, price_to_points
from crumblr.domain.timeutils import UtcDatetime
from crumblr.observability.logging import get_logger
from crumblr.persistence.market_data import bar_identity

_log = get_logger("bar_pipeline")

PIPELINE_NAME = "bars-v1"
"""Bump this when the aggregation rules change.

Bars built by two different versions must never be pooled without someone
deciding that they can be, and a version that never changes cannot express
that they should not be.
"""


class PriceBasis(StrEnum):
    """Which side of the book a bar's OHLC is taken from."""

    BID = "bid"
    ASK = "ask"
    MID = "mid"


TIMEFRAMES: dict[str, timedelta] = {
    "M1": timedelta(minutes=1),
    "M5": timedelta(minutes=5),
    "M15": timedelta(minutes=15),
    "M30": timedelta(minutes=30),
    "H1": timedelta(hours=1),
    "H4": timedelta(hours=4),
    "D1": timedelta(days=1),
}
"""Timeframes the pipeline understands. An unknown one fails loudly."""


def interval_for(timeframe: str) -> timedelta:
    interval = TIMEFRAMES.get(timeframe)
    if interval is None:
        known = ", ".join(sorted(TIMEFRAMES))
        raise KeyError(f"unknown timeframe {timeframe!r}; known: {known}")
    return interval


def bucket_start(moment: UtcDatetime, interval: timedelta) -> datetime:
    """The opening instant of the interval `moment` falls in.

    Anchored on the UNIX epoch rather than on the first observation, so two
    runs over different slices of the same feed agree about where the
    boundaries are.
    """
    epoch = datetime(1970, 1, 1, tzinfo=moment.tzinfo)
    elapsed = (moment - epoch) // interval
    return epoch + elapsed * interval


def pipeline_version(basis: PriceBasis) -> str:
    """The transformation identity stored on every derived bar."""
    return f"{PIPELINE_NAME}/{basis.value}"


@dataclass(frozen=True)
class StreamObservation:
    """Something the stream did that is worth keeping.

    Carries the instant so that an operator can go and look, and a sentence so
    that they know what they are looking for.
    """

    anomaly: StreamAnomaly
    at_utc: UtcDatetime
    detail: str


@dataclass(frozen=True)
class BarBuildResult:
    """Bars, and an honest account of the stream they came from."""

    bars: tuple[MarketBar, ...]
    observations: tuple[StreamObservation, ...]
    ticks_seen: int = 0
    ticks_used: int = 0

    @property
    def is_clean(self) -> bool:
        return not self.observations

    def anomalies_of(self, anomaly: StreamAnomaly) -> tuple[StreamObservation, ...]:
        return tuple(item for item in self.observations if item.anomaly is anomaly)


def bars_from_ticks(
    ticks: Sequence[MarketTick],
    *,
    timeframe: str,
    spec: InstrumentSpec,
    source: str,
    basis: PriceBasis = PriceBasis.BID,
) -> BarBuildResult:
    """Aggregate a tick stream into normalized bars.

    The path M1 will use: Pepperstone ticks in, M5 bars out, with the
    provenance to prove which ticks produced which bar.
    """
    interval = interval_for(timeframe)
    observations: list[StreamObservation] = []
    version = pipeline_version(basis)

    usable = _screen(ticks, observations)
    if not usable:
        return BarBuildResult((), tuple(observations), ticks_seen=len(ticks), ticks_used=0)

    buckets: dict[datetime, list[MarketTick]] = {}
    for tick in usable:
        buckets.setdefault(bucket_start(tick.event_time_utc, interval), []).append(tick)

    bars: list[MarketBar] = []
    previous_open: datetime | None = None
    for open_time in sorted(buckets):
        gapped = False
        if previous_open is not None:
            missing = _missing_intervals(previous_open, open_time, interval)
            if missing:
                gapped = True
                observations.append(
                    StreamObservation(
                        StreamAnomaly.GAP,
                        open_time,
                        f"{missing} {timeframe} interval(s) produced no ticks between "
                        f"{previous_open.isoformat()} and {open_time.isoformat()}",
                    )
                )
        previous_open = open_time
        bars.append(
            _build_bar(
                buckets[open_time],
                open_time=open_time,
                timeframe=timeframe,
                spec=spec,
                source=source,
                basis=basis,
                version=version,
                gapped=gapped,
            )
        )

    _log.info(
        "bar_pipeline.built",
        timeframe=timeframe,
        source=source,
        pipeline_version=version,
        ticks_seen=len(ticks),
        ticks_used=len(usable),
        bars=len(bars),
        observations=len(observations),
    )
    return BarBuildResult(
        tuple(bars), tuple(observations), ticks_seen=len(ticks), ticks_used=len(usable)
    )


def normalize_bars(
    bars: Iterable[Bar],
    *,
    timeframe: str,
    spec: InstrumentSpec,
    source: str,
    origin: BarOrigin,
    received_time_utc: UtcDatetime,
) -> BarBuildResult:
    """Validate and stamp a bar series that arrived already formed.

    Used for a broker's own bar feed and for the replay generator, neither of
    which goes through tick aggregation. The ordering, gap and duplicate checks
    are the same ones — a delivered series is not more trustworthy than a
    derived one, only differently sourced.
    """
    if origin is BarOrigin.AGGREGATED_FROM_TICKS:
        raise ValueError("aggregated bars come from bars_from_ticks, which records the tick count")

    interval = interval_for(timeframe)
    observations: list[StreamObservation] = []
    stamped: list[MarketBar] = []
    seen: set[datetime] = set()
    previous_open: datetime | None = None

    for bar in bars:
        open_time = bar.open_time_utc
        if open_time in seen:
            observations.append(
                StreamObservation(
                    StreamAnomaly.DUPLICATE,
                    open_time,
                    f"a bar for {open_time.isoformat()} was delivered more than once",
                )
            )
            continue
        seen.add(open_time)

        quality = DataQuality.GOOD
        anomalies: list[StreamAnomaly] = []

        if previous_open is not None and open_time < previous_open:
            observations.append(
                StreamObservation(
                    StreamAnomaly.OUT_OF_ORDER,
                    open_time,
                    f"bar at {open_time.isoformat()} arrived after {previous_open.isoformat()}",
                )
            )
            anomalies.append(StreamAnomaly.OUT_OF_ORDER)
            quality = DataQuality.OUT_OF_ORDER
        elif previous_open is not None:
            missing = _missing_intervals(previous_open, open_time, interval)
            if missing:
                observations.append(
                    StreamObservation(
                        StreamAnomaly.GAP,
                        open_time,
                        f"{missing} {timeframe} interval(s) missing before {open_time.isoformat()}",
                    )
                )
                anomalies.append(StreamAnomaly.GAP)
                quality = DataQuality.GAPPED

        if bucket_start(open_time, interval) != open_time:
            observations.append(
                StreamObservation(
                    StreamAnomaly.OUT_OF_ORDER,
                    open_time,
                    f"bar at {open_time.isoformat()} is not aligned to a {timeframe} boundary",
                )
            )

        previous_open = max(previous_open, open_time) if previous_open else open_time
        stamped.append(
            MarketBar(
                bar_id=bar_identity(
                    source=source,
                    canonical_symbol=spec.canonical_symbol,
                    timeframe=timeframe,
                    open_time_utc=open_time,
                ),
                source=source,
                canonical_symbol=spec.canonical_symbol,
                broker_symbol=spec.broker_symbol,
                timeframe=timeframe,
                bar=bar,
                origin=origin,
                pipeline_version=None,
                tick_count=None,
                received_time_utc=received_time_utc,
                data_quality=quality,
                anomalies=tuple(anomalies),
            )
        )

    return BarBuildResult(tuple(stamped), tuple(observations), ticks_seen=0, ticks_used=0)


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #


def _screen(ticks: Sequence[MarketTick], observations: list[StreamObservation]) -> list[MarketTick]:
    """Drop what cannot price a bar, and record everything noticed on the way.

    A crossed quote is the only tick refused outright: an ask below a bid is
    not a price, and letting one into an OHLC would put a number in the audit
    trail that never existed in the market. Out-of-order ticks are *kept* —
    they belong in the interval they happened in, and the reordering is
    recorded rather than being the reason to lose the data.
    """
    kept: list[MarketTick] = []
    seen_ids: set[str] = set()
    previous: datetime | None = None

    for tick in ticks:
        identity = str(tick.tick_id)
        if identity in seen_ids:
            observations.append(
                StreamObservation(
                    StreamAnomaly.DUPLICATE,
                    tick.event_time_utc,
                    f"tick {identity} repeated at {tick.event_time_utc.isoformat()}",
                )
            )
            continue
        seen_ids.add(identity)

        if tick.ask < tick.bid:
            observations.append(
                StreamObservation(
                    StreamAnomaly.CROSSED_QUOTE,
                    tick.event_time_utc,
                    f"ask {tick.ask} below bid {tick.bid}; refused as a price source",
                )
            )
            continue

        if previous is not None and tick.event_time_utc < previous:
            observations.append(
                StreamObservation(
                    StreamAnomaly.OUT_OF_ORDER,
                    tick.event_time_utc,
                    f"tick at {tick.event_time_utc.isoformat()} arrived after "
                    f"{previous.isoformat()}; placed in its own interval",
                )
            )
        else:
            previous = tick.event_time_utc

        kept.append(tick)

    return kept


def _missing_intervals(previous_open: datetime, open_time: datetime, interval: timedelta) -> int:
    """How many whole intervals produced nothing between two bars."""
    steps = (open_time - previous_open) // interval
    return max(0, int(steps) - 1)


def _price(tick: MarketTick, basis: PriceBasis) -> Decimal:
    if basis is PriceBasis.BID:
        return tick.bid
    if basis is PriceBasis.ASK:
        return tick.ask
    return (tick.bid + tick.ask) / Decimal(2)


def _build_bar(
    ticks: list[MarketTick],
    *,
    open_time: datetime,
    timeframe: str,
    spec: InstrumentSpec,
    source: str,
    basis: PriceBasis,
    version: str,
    gapped: bool,
) -> MarketBar:
    """One bar from the ticks that fell inside its interval."""
    ordered = sorted(ticks, key=lambda tick: (tick.event_time_utc, str(tick.tick_id)))
    prices = [_price(tick, basis) for tick in ordered]

    out_of_order = any(
        later.event_time_utc < earlier.event_time_utc
        for earlier, later in itertools.pairwise(ticks)
    )
    anomalies: list[StreamAnomaly] = []
    quality = DataQuality.GOOD
    if gapped:
        anomalies.append(StreamAnomaly.GAP)
        quality = DataQuality.GAPPED
    if out_of_order:
        anomalies.append(StreamAnomaly.OUT_OF_ORDER)
        quality = DataQuality.OUT_OF_ORDER
    if any(tick.data_quality is DataQuality.SUSPECT for tick in ordered):
        quality = DataQuality.SUSPECT

    last = ordered[-1]
    spread = last.ask - last.bid
    return MarketBar(
        bar_id=bar_identity(
            source=source,
            canonical_symbol=spec.canonical_symbol,
            timeframe=timeframe,
            open_time_utc=open_time,
        ),
        source=source,
        canonical_symbol=spec.canonical_symbol,
        broker_symbol=spec.broker_symbol,
        timeframe=timeframe,
        bar=Bar(
            open_time_utc=open_time,
            open=prices[0],
            high=max(prices),
            low=min(prices),
            close=prices[-1],
            tick_volume=len(ordered),
            real_volume=sum(tick.volume for tick in ordered if tick.volume is not None) or None,
            spread_points=price_to_points(spread, spec.point) if spread > ZERO else 0,
        ),
        origin=BarOrigin.AGGREGATED_FROM_TICKS,
        pipeline_version=version,
        tick_count=len(ordered),
        received_time_utc=max(tick.received_time_utc for tick in ordered),
        data_quality=quality,
        anomalies=tuple(anomalies),
    )
