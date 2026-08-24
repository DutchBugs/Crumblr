"""Writing a run into the journal (review 1.5 step 1; D-030).

M2 built the storage layer and proved its invariants against a real
PostgreSQL, but the running orchestrator kept its capsules in a list. This
module is the seam that closes that gap: the orchestrator hands each stage of
the transaction flow to a `RunRecorder`, and which recorder it holds decides
whether the run is durable.

Two rules shape the implementation.

**A replayed event must have a replayable identity.** The journal's append is
idempotent on `event_id` (ADR-003 invariant 3), which only converges if the
same logical event yields the same id every time it is produced. Every id here
is therefore derived from the event type, the window it belongs to and the
content of its payload — never from `uuid4`. Re-running an identical replay
against a journal that already holds it is a no-op rather than a second copy
of history.

**A window commits once or not at all.** Events are buffered as the window
progresses and written with the sealed capsule in a single transaction
(invariant 5), so the journal never shows a risk decision whose capsule is
missing. The exception is a halt, which is flushed the moment it happens: a
safety event that waits for the next commit is a safety event that a crash can
lose.
"""

from __future__ import annotations

import hashlib
import json
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import Engine

from crumblr.domain.enums import Environment
from crumblr.domain.events import Event, build_event, event_type_for
from crumblr.domain.models import Contract, DecisionCapsule, MarketBar, MarketTick
from crumblr.domain.timeutils import UtcDatetime
from crumblr.observability.logging import get_logger
from crumblr.persistence.journal import CapsuleStore, EventJournal
from crumblr.persistence.market_data import MarketDataStore

_log = get_logger("recording")

MARKET_DATA_BATCH = 500
"""How many observations to accumulate before writing them.

Market data arrives one row per window and is not safety-critical the way a
halt is, so it is batched. The batch is flushed whenever a window seals and at
the end of a run, which bounds how much can be lost to a crash to the tail of
one batch rather than to the whole run.
"""


def payload_digest(payload: Contract) -> str:
    """A stable digest of a payload as the journal will store it.

    Deliberately not `domain.hashing.fingerprint`: that function refuses
    floats, because a *decision* fingerprint must never depend on binary
    floating point. This digest is a different thing — it hashes the exact
    JSON the journal persists, floats included, purely to give the event a
    content-addressed identity. It is never used to prove what a decision was.
    """
    encoded = json.dumps(
        payload.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def event_identity(payload: Contract, *, correlation_id: UUID) -> UUID:
    """The deterministic `event_id` for this payload in this window.

    Two events collapse to one row only when they are the same type, in the
    same window, carrying byte-identical payloads — which is precisely the
    definition of "the same event, produced twice".
    """
    event_type = event_type_for(payload)
    return uuid5(
        NAMESPACE_URL,
        f"crumblr:event:{event_type.value}:{correlation_id}:{payload_digest(payload)}",
    )


class RunRecorder(Protocol):
    """Where a run's decision flow is written.

    The orchestrator depends on this and never on a database, so a replay with
    no PostgreSQL behind it runs exactly the same decision path — it simply
    leaves no record. That is the difference between the two implementations
    below, and it is the only difference.
    """

    def record(
        self,
        payload: Contract,
        *,
        correlation_id: UUID,
        occurred_at_utc: UtcDatetime,
        source: str,
    ) -> None:
        """Note that a stage completed.

        Returns nothing on purpose. Causation is chained by the recorder that
        is actually writing, so a caller never needs the id back — and the
        null implementation must not pay to compute one it will discard.
        """
        ...

    def observe(self, tick: MarketTick, bar: MarketBar | None = None) -> None:
        """Record what the market showed, before anything was decided about it.

        Written to the market store rather than the journal (review 1.6
        F-022): the journal is what the system did, and these two answer
        different questions and must stay separable. It is the same writer
        because the orchestrator should not have to hold two of them; it is
        not the same table.

        Called for *every* window, including the ones where the strategy had
        too little history to say anything — those produce no event at all, so
        without this they leave no trace whatsoever.
        """
        ...

    def seal(self, capsule: DecisionCapsule) -> None:
        """Commit the window: its buffered events and the sealed capsule."""
        ...

    def flush(self) -> None:
        """Write anything still buffered. Called after a halt and at run end."""
        ...


class NullRecorder:
    """Records nothing.

    The default, so that `ReplayOrchestrator` keeps working with no database —
    which the unit suite, the determinism gate and `scripts/run_replay.py`
    all rely on. It is not a stub for missing work: a replay whose output is a
    report rather than an audit trail has nothing to persist.
    """

    def record(
        self,
        payload: Contract,
        *,
        correlation_id: UUID,
        occurred_at_utc: UtcDatetime,
        source: str,
    ) -> None:
        return None

    def observe(self, tick: MarketTick, bar: MarketBar | None = None) -> None:
        return None

    def seal(self, capsule: DecisionCapsule) -> None:
        return None

    def flush(self) -> None:
        return None


class JournalRecorder:
    """Writes the run to PostgreSQL through the M2 abstractions.

    Events are chained as they arrive: within one window each event's
    `causation_id` is the previous event's id. build.md §3 makes the flow
    strictly linear and forbids skipping a stage, so "the event before this
    one, in this window" *is* the event that caused it. The chain resets when
    the window does.
    """

    def __init__(self, engine: Engine, *, environment: Environment) -> None:
        self._journal = EventJournal(engine)
        self._capsules = CapsuleStore(engine)
        self._market_data = MarketDataStore(engine)
        self._engine = engine
        self._environment = environment
        self._pending: list[Event[Contract]] = []
        self._pending_ticks: list[MarketTick] = []
        self._pending_bars: list[MarketBar] = []
        self._ticks_written = 0
        self._bars_written = 0
        self._chain_correlation_id: UUID | None = None
        self._chain_last_event_id: UUID | None = None
        self._events_written = 0
        self._capsules_written = 0

    @property
    def events_written(self) -> int:
        return self._events_written

    @property
    def capsules_written(self) -> int:
        return self._capsules_written

    @property
    def ticks_written(self) -> int:
        return self._ticks_written

    @property
    def bars_written(self) -> int:
        return self._bars_written

    def observe(self, tick: MarketTick, bar: MarketBar | None = None) -> None:
        self._pending_ticks.append(tick)
        if bar is not None:
            self._pending_bars.append(bar)
        if len(self._pending_ticks) >= MARKET_DATA_BATCH:
            self._flush_market_data()

    def record(
        self,
        payload: Contract,
        *,
        correlation_id: UUID,
        occurred_at_utc: UtcDatetime,
        source: str,
    ) -> None:
        if correlation_id != self._chain_correlation_id:
            self._chain_correlation_id = correlation_id
            self._chain_last_event_id = None

        event_id = event_identity(payload, correlation_id=correlation_id)
        self._pending.append(
            build_event(
                payload,
                correlation_id=correlation_id,
                causation_id=self._chain_last_event_id,
                environment=self._environment,
                source=source,
                event_id=event_id,
                # Market time, not write time: replay orders by this, and a
                # backfilled event belongs where it happened rather than where
                # it arrived (ADR-003 invariant 4).
                occurred_at_utc=occurred_at_utc,
            )
        )
        self._chain_last_event_id = event_id

    def _flush_market_data(self) -> None:
        """Write the observations in one transaction, then forget them."""
        if not self._pending_ticks and not self._pending_bars:
            return
        ticks, bars = tuple(self._pending_ticks), tuple(self._pending_bars)
        self._pending_ticks.clear()
        self._pending_bars.clear()
        with self._engine.begin() as connection:
            self._ticks_written += self._market_data.record_ticks(ticks, connection=connection)
            self._bars_written += self._market_data.record_bars(bars, connection=connection)

    def seal(self, capsule: DecisionCapsule) -> None:
        """One transaction per window: its events, its sealing, its capsule."""
        self.record(
            capsule,
            correlation_id=capsule.correlation_id,
            occurred_at_utc=capsule.occurred_at_utc,
            source="orchestration",
        )
        batch = tuple(self._pending)
        self._pending.clear()
        with self._engine.begin() as connection:
            for event in batch:
                self._journal.append(event, connection=connection)
            self._capsules.seal(capsule, connection=connection)
        self._events_written += len(batch)
        self._capsules_written += 1

    def flush(self) -> None:
        self._flush_market_data()
        if not self._pending:
            return
        batch = tuple(self._pending)
        self._pending.clear()
        with self._engine.begin() as connection:
            for event in batch:
                self._journal.append(event, connection=connection)
        self._events_written += len(batch)
        _log.debug("journal.flushed", events=len(batch))
