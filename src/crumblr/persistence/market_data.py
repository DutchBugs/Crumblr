"""What the market showed (build.md §12.1; review 1.6 F-022).

The event journal answers "what did the system decide?". This store answers
"what was it looking at?", and until it exists the second question has no
answer that does not rely on a random seed being reproducible.

    event journal = what the system did
    market store  = what the system saw

Two properties distinguish this from the journal.

**Raw data is immutable, and a rewrite is detected rather than silenced.** A
bar's identity is derived from its source, symbol, timeframe and open time, so
re-ingesting a series is a no-op — but a *different* bar for an interval
already stored is a conflict, and `record_bars` raises instead of quietly
keeping whichever arrived first. build.md §26 requires raw data to be
immutable; an overwrite that nobody notices meets the letter of that and none
of the point.

**A tick's identity includes its content.** Real feeds deliver several quotes
inside the same millisecond, so two ticks sharing a timestamp are ordinary
rather than duplicates. Only a byte-identical repeat collapses.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import Engine, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from crumblr.domain.hashing import canonical_json
from crumblr.domain.models import MarketBar, MarketTick
from crumblr.observability.logging import get_logger
from crumblr.persistence.journal import JournalIntegrityError
from crumblr.persistence.schema import market_bars, market_ticks

_log = get_logger("market_data")


def tick_identity(
    *,
    source: str,
    canonical_symbol: str,
    event_time_utc: datetime,
    bid: object,
    ask: object,
    last: object = None,
) -> UUID:
    """Content-derived identity for a tick.

    Includes the quote, not only the timestamp: a feed that legitimately sends
    two different quotes in the same millisecond must store two rows, and a
    feed that resends the identical quote must store one.
    """
    return uuid5(
        NAMESPACE_URL,
        canonical_json(
            {
                "kind": "crumblr:tick",
                "source": source,
                "symbol": canonical_symbol,
                "event_time": event_time_utc,
                "bid": bid,
                "ask": ask,
                "last": last,
            }
        ),
    )


def bar_identity(
    *, source: str, canonical_symbol: str, timeframe: str, open_time_utc: datetime
) -> UUID:
    """Identity for a bar: one per source, symbol, timeframe and interval.

    Deliberately *excludes* the OHLC values. Two different bars claiming the
    same interval are not two bars — they are a contradiction, and giving them
    one identity is what turns that contradiction into a detectable conflict
    rather than two rows nobody compares.
    """
    return uuid5(
        NAMESPACE_URL,
        canonical_json(
            {
                "kind": "crumblr:bar",
                "source": source,
                "symbol": canonical_symbol,
                "timeframe": timeframe,
                "open_time": open_time_utc,
            }
        ),
    )


class MarketDataStore:
    """Append-only storage for ticks and bars."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # ------------------------------------------------------------------ #
    # Ticks
    # ------------------------------------------------------------------ #

    def record_ticks(
        self, ticks: Sequence[MarketTick], *, connection: Connection | None = None
    ) -> int:
        """Store ticks, ignoring exact repeats. Returns the number inserted."""
        if not ticks:
            return 0
        if connection is not None:
            return self._record_ticks(connection, ticks)
        with self._engine.begin() as own_connection:
            return self._record_ticks(own_connection, ticks)

    def _record_ticks(self, connection: Connection, ticks: Sequence[MarketTick]) -> int:
        rows = [
            {
                "tick_id": tick.tick_id,
                "source": tick.source,
                "canonical_symbol": tick.canonical_symbol,
                "broker_symbol": tick.broker_symbol,
                "event_time_utc": tick.event_time_utc,
                "received_time_utc": tick.received_time_utc,
                "bid": tick.bid,
                "ask": tick.ask,
                "last": tick.last,
                "volume": tick.volume,
                "flags": tick.flags,
                "data_quality": tick.data_quality.value,
                "anomalies": [anomaly.value for anomaly in tick.anomalies],
                "payload": tick.model_dump(mode="json"),
            }
            for tick in ticks
        ]
        statement = (
            pg_insert(market_ticks)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["tick_id"])
            .returning(market_ticks.c.tick_id)
        )
        return len(connection.execute(statement).all())

    def read_ticks(
        self,
        *,
        canonical_symbol: str | None = None,
        source: str | None = None,
        limit: int | None = None,
    ) -> tuple[MarketTick, ...]:
        """Read ticks in market-time order, sequence breaking ties."""
        statement = select(market_ticks).order_by(
            market_ticks.c.event_time_utc, market_ticks.c.sequence
        )
        if canonical_symbol is not None:
            statement = statement.where(market_ticks.c.canonical_symbol == canonical_symbol)
        if source is not None:
            statement = statement.where(market_ticks.c.source == source)
        if limit is not None:
            statement = statement.limit(limit)
        with self._engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return tuple(MarketTick.model_validate(row["payload"]) for row in rows)

    # ------------------------------------------------------------------ #
    # Bars
    # ------------------------------------------------------------------ #

    def record_bars(
        self, bars: Sequence[MarketBar], *, connection: Connection | None = None
    ) -> int:
        """Store bars. A conflicting bar for a stored interval raises.

        "Raise" rather than "overwrite" and rather than "ignore": the first is
        history being rewritten, the second is a contradiction in the feed
        going unrecorded. Both are worse than stopping.
        """
        if not bars:
            return 0
        if connection is not None:
            return self._record_bars(connection, bars)
        with self._engine.begin() as own_connection:
            return self._record_bars(own_connection, bars)

    def _record_bars(self, connection: Connection, bars: Sequence[MarketBar]) -> int:
        inserted = 0
        for bar in bars:
            statement = (
                pg_insert(market_bars)
                .values(
                    bar_id=bar.bar_id,
                    source=bar.source,
                    canonical_symbol=bar.canonical_symbol,
                    broker_symbol=bar.broker_symbol,
                    timeframe=bar.timeframe,
                    open_time_utc=bar.bar.open_time_utc,
                    received_time_utc=bar.received_time_utc,
                    open=bar.bar.open,
                    high=bar.bar.high,
                    low=bar.bar.low,
                    close=bar.bar.close,
                    tick_volume=bar.bar.tick_volume,
                    real_volume=bar.bar.real_volume,
                    spread_points=bar.bar.spread_points,
                    origin=bar.origin.value,
                    pipeline_version=bar.pipeline_version,
                    tick_count=bar.tick_count,
                    data_quality=bar.data_quality.value,
                    anomalies=[anomaly.value for anomaly in bar.anomalies],
                    payload=bar.model_dump(mode="json"),
                )
                .on_conflict_do_nothing(index_elements=["bar_id"])
                .returning(market_bars.c.bar_id)
            )
            if connection.execute(statement).first() is not None:
                inserted += 1
                continue
            self._assert_unchanged(connection, bar)
        return inserted

    def _assert_unchanged(self, connection: Connection, bar: MarketBar) -> None:
        """A bar already stored for this interval must be the same bar."""
        stored = connection.execute(
            select(market_bars.c.payload).where(market_bars.c.bar_id == bar.bar_id)
        ).scalar_one()
        existing = MarketBar.model_validate(stored)
        if existing.bar != bar.bar:
            raise JournalIntegrityError(
                f"bar {bar.bar_id} for {bar.canonical_symbol} {bar.timeframe} at "
                f"{bar.bar.open_time_utc.isoformat()} is already stored with different "
                f"values: stored OHLC "
                f"{existing.bar.open}/{existing.bar.high}/{existing.bar.low}/{existing.bar.close}, "
                f"incoming {bar.bar.open}/{bar.bar.high}/{bar.bar.low}/{bar.bar.close}"
            )
        _log.debug("market_data.bar_already_stored", bar_id=str(bar.bar_id))

    def read_bars(
        self,
        *,
        canonical_symbol: str | None = None,
        timeframe: str | None = None,
        source: str | None = None,
        limit: int | None = None,
    ) -> tuple[MarketBar, ...]:
        statement = select(market_bars).order_by(
            market_bars.c.open_time_utc, market_bars.c.sequence
        )
        if canonical_symbol is not None:
            statement = statement.where(market_bars.c.canonical_symbol == canonical_symbol)
        if timeframe is not None:
            statement = statement.where(market_bars.c.timeframe == timeframe)
        if source is not None:
            statement = statement.where(market_bars.c.source == source)
        if limit is not None:
            statement = statement.limit(limit)
        with self._engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return tuple(MarketBar.model_validate(row["payload"]) for row in rows)

    def counts(self) -> dict[str, int]:
        from sqlalchemy import func

        with self._engine.connect() as connection:
            return {
                "ticks": int(
                    connection.execute(select(func.count()).select_from(market_ticks)).scalar_one()
                ),
                "bars": int(
                    connection.execute(select(func.count()).select_from(market_bars)).scalar_one()
                ),
            }
