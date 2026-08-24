"""The event journal (build.md §18, §23; ADR-003).

Two operations matter: appending an event exactly once, and reading events back
in the order they happened. Everything else in this module exists to make those
two safe.

The journal is the audit trail. If it cannot reproduce a run, it is storage.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from crumblr.domain.enums import Environment
from crumblr.domain.events import Event, EventType, decode_event
from crumblr.domain.models import Contract, DecisionCapsule
from crumblr.observability.logging import get_logger
from crumblr.persistence.schema import decision_capsules, events

_log = get_logger("journal")


class JournalIntegrityError(RuntimeError):
    """A stored record does not match what it claims to be."""


@dataclass(frozen=True)
class AppendResult:
    """Whether the append created a row or found one already there.

    The distinction is information the caller sometimes needs: a duplicate
    heartbeat is noise, a duplicate order event is a question worth asking.
    """

    event_id: UUID
    inserted: bool

    @property
    def was_duplicate(self) -> bool:
        return not self.inserted


class EventJournal:
    """Append-only event storage.

    Writes are idempotent on `event_id`, so a retry after an ambiguous outcome
    converges rather than duplicating (ADR-003 invariant 3). Reads order by
    market time with the insertion sequence as a deterministic tie-break, never
    by insertion time (invariant 4).
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # ------------------------------------------------------------------ #
    # Writing
    # ------------------------------------------------------------------ #

    def append(self, event: Event[Any], *, connection: Connection | None = None) -> AppendResult:
        """Store one event. Safe to call again with the same event."""
        if connection is not None:
            return self._append(connection, event)
        with self._engine.begin() as own_connection:
            return self._append(own_connection, event)

    def append_many(self, batch: Sequence[Event[Any]]) -> tuple[AppendResult, ...]:
        """Store several events in one transaction.

        Either all of them are visible or none are — which is what invariant 5
        requires of a transition that spans records.
        """
        with self._engine.begin() as connection:
            return tuple(self._append(connection, event) for event in batch)

    def _append(self, connection: Connection, event: Event[Any]) -> AppendResult:
        payload = event.model_dump(mode="json")
        statement = (
            pg_insert(events)
            .values(
                event_id=event.event_id,
                event_type=event.event_type.value,
                schema_version=event.schema_version,
                occurred_at_utc=event.occurred_at_utc,
                correlation_id=event.correlation_id,
                causation_id=event.causation_id,
                environment=event.environment.value,
                source=event.source,
                payload=payload,
            )
            # Idempotent by construction: a repeat is a no-op, never a second
            # logical copy of the same event.
            .on_conflict_do_nothing(index_elements=["event_id"])
            .returning(events.c.event_id)
        )
        inserted = connection.execute(statement).first() is not None
        if not inserted:
            _log.debug(
                "journal.duplicate_ignored",
                event_id=str(event.event_id),
                event_type=event.event_type.value,
            )
        return AppendResult(event_id=event.event_id, inserted=inserted)

    # ------------------------------------------------------------------ #
    # Reading
    # ------------------------------------------------------------------ #

    def read_all(
        self,
        *,
        correlation_id: UUID | None = None,
        event_type: EventType | None = None,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> tuple[Event[Contract], ...]:
        """Read events in the order they happened.

        Ordered by `occurred_at_utc`, then `sequence`. Insertion order is never
        the primary key of the ordering: a reconnect backfill arrives late but
        belongs where it happened.
        """
        statement = select(events).order_by(events.c.occurred_at_utc, events.c.sequence)
        if correlation_id is not None:
            statement = statement.where(events.c.correlation_id == correlation_id)
        if event_type is not None:
            statement = statement.where(events.c.event_type == event_type.value)
        if since is not None:
            statement = statement.where(events.c.occurred_at_utc >= since)
        if limit is not None:
            statement = statement.limit(limit)

        with self._engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return tuple(_decode_row(row) for row in rows)

    def stream(self, *, batch_size: int = 1000) -> Iterator[Event[Contract]]:
        """Read the whole journal without holding it all in memory."""
        statement = (
            select(events)
            .order_by(events.c.occurred_at_utc, events.c.sequence)
            .execution_options(yield_per=batch_size)
        )
        with self._engine.connect() as connection:
            for row in connection.execute(statement).mappings():
                yield _decode_row(row)

    def count(self) -> int:
        from sqlalchemy import func

        with self._engine.connect() as connection:
            return int(connection.execute(select(func.count()).select_from(events)).scalar_one())


def _decode_row(row: Any) -> Event[Contract]:
    """Rebuild an event from its stored payload.

    The payload is decoded rather than reassembled from columns: the columns
    exist for querying, the payload is the record. Reconstructing from columns
    would let the two drift apart without anything noticing.
    """
    stored_version = row["schema_version"]
    payload = dict(row["payload"])
    if payload.get("schema_version") != stored_version:
        raise JournalIntegrityError(
            f"event {row['event_id']} has schema_version {stored_version} in its column "
            f"but {payload.get('schema_version')!r} in its payload"
        )
    return decode_event(payload)


class CapsuleStore:
    """Sealed decision capsules (build.md §11; ADR-003 invariant 8).

    A capsule is written once. On read its `provenance_fingerprint` is
    recomputed and compared with the stored one, so a row altered underneath us
    raises rather than being returned as though it were sound.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def seal(
        self, capsule: DecisionCapsule, *, connection: Connection | None = None
    ) -> AppendResult:
        if connection is not None:
            return self._seal(connection, capsule)
        with self._engine.begin() as own_connection:
            return self._seal(own_connection, capsule)

    def _seal(self, connection: Connection, capsule: DecisionCapsule) -> AppendResult:
        statement = (
            pg_insert(decision_capsules)
            .values(
                capsule_id=capsule.capsule_id,
                occurred_at_utc=capsule.occurred_at_utc,
                correlation_id=capsule.correlation_id,
                canonical_symbol=capsule.canonical_symbol,
                broker_symbol=capsule.broker_symbol,
                environment=capsule.environment.value,
                strategy_version=capsule.strategy_version,
                model_version=capsule.model_version,
                feature_set_version=capsule.feature_set_version,
                risk_config_version=capsule.risk_config_version,
                code_commit=capsule.code_commit,
                provenance_fingerprint=capsule.provenance_fingerprint,
                payload=capsule.model_dump(mode="json"),
            )
            .on_conflict_do_nothing(index_elements=["capsule_id"])
            .returning(decision_capsules.c.capsule_id)
        )
        inserted = connection.execute(statement).first() is not None
        return AppendResult(event_id=capsule.capsule_id, inserted=inserted)

    def read_all(self, *, environment: Environment | None = None) -> tuple[DecisionCapsule, ...]:
        statement = select(decision_capsules).order_by(
            decision_capsules.c.occurred_at_utc, decision_capsules.c.sequence
        )
        if environment is not None:
            statement = statement.where(decision_capsules.c.environment == environment.value)

        with self._engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return tuple(_decode_capsule(row) for row in rows)

    def get(self, capsule_id: UUID) -> DecisionCapsule | None:
        statement = select(decision_capsules).where(decision_capsules.c.capsule_id == capsule_id)
        with self._engine.connect() as connection:
            row = connection.execute(statement).mappings().first()
        return _decode_capsule(row) if row is not None else None


def _decode_capsule(row: Any) -> DecisionCapsule:
    capsule = DecisionCapsule.model_validate(row["payload"])
    stored = row["provenance_fingerprint"]
    if capsule.provenance_fingerprint != stored:
        raise JournalIntegrityError(
            f"capsule {row['capsule_id']} fingerprint mismatch: stored {stored}, "
            f"recomputed {capsule.provenance_fingerprint}. The row was altered."
        )
    return capsule
