"""Immutable flatten requests + append-only flatten events (core critical
path item 7, ADR-009).

`persistence/execution.py`'s immutable-request + append-only-events
discipline, applied to a structurally different kind of request. An
execution request is proposal-driven: it always has a `DecisionCapsule`, a
`TradeIntent`, an intent-time `RiskDecision` and a `SupervisorDecision`
behind it, and `execution_requests.capsule_id` is `nullable=False` because
every real row genuinely has one. A flatten has none of that — it is
policy-driven, triggered by an observed deadline and an observed position
book, not by anyone proposing anything. Reusing `execution_requests` would
mean either fabricating a placeholder approval chain that never happened
(unacceptable in the one table whose whole purpose is auditable
provenance) or weakening its FK for every other row to accommodate a
minority case. Neither is acceptable, so this is a dedicated table pair,
`flatten_requests`/`flatten_events`, with the same claim-before-action and
content-conflict-hardening properties, just no capsule/intent to point at.

The idempotency key is one flatten *occurrence* — `(environment,
canonical_symbol, trading_day)` — not the observed position book. One
commitment per trading day per symbol, ever: keying on book contents
would mint a new key every time a position's volume changed between
passes, which is a resubmission mechanism ADR-003 §6 forbids. The request
`fingerprint` covers the policy that produced the occurrence (offsets,
deadline), so an edited policy mid-day is caught as a real conflict; the
observed book goes in the *event* payload instead — mirroring how
`SUBMISSION_STARTED` carries the complete `ApprovedOrder` while
`execution_requests.fingerprint` carries the approval chain.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import Engine, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from crumblr.domain.enums import Environment, FlattenEventType, ReasonCode
from crumblr.domain.hashing import fingerprint
from crumblr.domain.timeutils import UtcDatetime
from crumblr.persistence.journal import AppendResult
from crumblr.persistence.schema import flatten_events, flatten_requests


class FlattenRequestConflictError(RuntimeError):
    """The same flatten occurrence was already recorded with different content.

    Mirrors `ExecutionRequestConflictError` exactly, one table over: an
    idempotency key that could refer to two different flatten policies is
    exactly the ambiguity idempotency exists to prevent.
    """


class FlattenEventConflictError(RuntimeError):
    """The same flatten event identity was already recorded with different

    content. Mirrors `ExecutionEventConflictError` — item 4's
    content-conflict hardening, applied here rather than re-derived.
    """


def flatten_request_id_for(
    *, environment: Environment, canonical_symbol: str, trading_day: date
) -> UUID:
    """Derived, not random — one flatten commitment per trading day per

    symbol, ever. A retry (this worker or another, after a crash) derives
    the identical key and converges on the same row instead of minting a
    second one.
    """
    return uuid5(
        NAMESPACE_URL,
        f"crumblr:flatten:{environment.value}:{canonical_symbol}:{trading_day.isoformat()}",
    )


def flatten_event_id_for(*, flatten_request_id: UUID, event_type: FlattenEventType) -> UUID:
    """Derived, not random — same idempotence discipline as `event_id_for`."""
    return uuid5(NAMESPACE_URL, f"crumblr:flatten_event:{flatten_request_id}:{event_type.value}")


@dataclass(frozen=True)
class FlattenClaimResult:
    claimed: bool
    """`True` only when *this* call's insert is the one that won the claim.

    `False` with no exception means another attempt already holds this
    flatten occurrence with matching content — not an error, and exactly
    what makes revisiting the same trading day's flatten after a crash
    safe rather than a duplicate commitment."""


@dataclass(frozen=True)
class FlattenEventRecord:
    event_id: UUID
    flatten_request_id: UUID
    event_type: FlattenEventType
    occurred_at_utc: UtcDatetime
    reason_codes: tuple[ReasonCode, ...]
    detail: str | None
    payload: dict[str, Any] | None


class FlattenRequestStore:
    """The immutable half: one row per flatten occurrence, ever."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def claim(
        self,
        *,
        flatten_request_id: UUID,
        environment: Environment,
        canonical_symbol: str,
        trading_day: date,
        session_close_utc: UtcDatetime,
        flatten_deadline_utc: UtcDatetime,
        fingerprint: str,
        claimed_by: str,
        now: UtcDatetime,
        connection: Connection | None = None,
    ) -> FlattenClaimResult:
        if connection is not None:
            return self._claim(
                connection,
                flatten_request_id=flatten_request_id,
                environment=environment,
                canonical_symbol=canonical_symbol,
                trading_day=trading_day,
                session_close_utc=session_close_utc,
                flatten_deadline_utc=flatten_deadline_utc,
                fingerprint=fingerprint,
                claimed_by=claimed_by,
                now=now,
            )
        with self._engine.begin() as own_connection:
            return self._claim(
                own_connection,
                flatten_request_id=flatten_request_id,
                environment=environment,
                canonical_symbol=canonical_symbol,
                trading_day=trading_day,
                session_close_utc=session_close_utc,
                flatten_deadline_utc=flatten_deadline_utc,
                fingerprint=fingerprint,
                claimed_by=claimed_by,
                now=now,
            )

    def _claim(
        self,
        connection: Connection,
        *,
        flatten_request_id: UUID,
        environment: Environment,
        canonical_symbol: str,
        trading_day: date,
        session_close_utc: UtcDatetime,
        flatten_deadline_utc: UtcDatetime,
        fingerprint: str,
        claimed_by: str,
        now: UtcDatetime,
    ) -> FlattenClaimResult:
        statement = (
            pg_insert(flatten_requests)
            .values(
                flatten_request_id=flatten_request_id,
                environment=environment.value,
                canonical_symbol=canonical_symbol,
                trading_day=trading_day,
                session_close_utc=session_close_utc,
                flatten_deadline_utc=flatten_deadline_utc,
                fingerprint=fingerprint,
                claimed_by=claimed_by,
                claimed_at_utc=now,
                schema_version=1,
            )
            .on_conflict_do_nothing(index_elements=["flatten_request_id"])
            .returning(flatten_requests.c.flatten_request_id)
        )
        won = connection.execute(statement).first() is not None
        if won:
            return FlattenClaimResult(claimed=True)

        existing_fingerprint = connection.execute(
            select(flatten_requests.c.fingerprint).where(
                flatten_requests.c.flatten_request_id == flatten_request_id
            )
        ).scalar_one()
        if existing_fingerprint != fingerprint:
            raise FlattenRequestConflictError(
                f"flatten_request_id {flatten_request_id} was already recorded with a "
                f"different fingerprint ({existing_fingerprint!r} != {fingerprint!r}) "
                "-- the same idempotency key would refer to two different flatten policies"
            )
        return FlattenClaimResult(claimed=False)


class FlattenEventStore:
    """The append-only half: every step of one flatten occurrence, one row each."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def append(
        self,
        *,
        flatten_request_id: UUID,
        event_type: FlattenEventType,
        occurred_at_utc: UtcDatetime,
        reason_codes: tuple[ReasonCode, ...] = (),
        detail: str | None = None,
        payload: dict[str, Any] | None = None,
        connection: Connection | None = None,
    ) -> AppendResult:
        if connection is not None:
            return self._append(
                connection,
                flatten_request_id=flatten_request_id,
                event_type=event_type,
                occurred_at_utc=occurred_at_utc,
                reason_codes=reason_codes,
                detail=detail,
                payload=payload,
            )
        with self._engine.begin() as own_connection:
            return self._append(
                own_connection,
                flatten_request_id=flatten_request_id,
                event_type=event_type,
                occurred_at_utc=occurred_at_utc,
                reason_codes=reason_codes,
                detail=detail,
                payload=payload,
            )

    def _append(
        self,
        connection: Connection,
        *,
        flatten_request_id: UUID,
        event_type: FlattenEventType,
        occurred_at_utc: UtcDatetime,
        reason_codes: tuple[ReasonCode, ...],
        detail: str | None,
        payload: dict[str, Any] | None,
    ) -> AppendResult:
        event_id = flatten_event_id_for(
            flatten_request_id=flatten_request_id, event_type=event_type
        )
        reason_code_values = [code.value for code in reason_codes]
        statement = (
            pg_insert(flatten_events)
            .values(
                event_id=event_id,
                flatten_request_id=flatten_request_id,
                event_type=event_type.value,
                occurred_at_utc=occurred_at_utc,
                reason_codes=reason_code_values,
                detail=detail,
                payload=payload,
                schema_version=1,
            )
            .on_conflict_do_nothing(index_elements=["event_id"])
            .returning(flatten_events.c.event_id)
        )
        won = connection.execute(statement).first() is not None
        if won:
            return AppendResult(event_id=event_id, inserted=True)

        existing = (
            connection.execute(
                select(
                    flatten_events.c.reason_codes,
                    flatten_events.c.detail,
                    flatten_events.c.payload,
                ).where(flatten_events.c.event_id == event_id)
            )
            .mappings()
            .one()
        )
        new_fingerprint = fingerprint(
            {"reason_codes": reason_code_values, "detail": detail, "payload": payload}
        )
        existing_fingerprint = fingerprint(
            {
                "reason_codes": existing["reason_codes"],
                "detail": existing["detail"],
                "payload": existing["payload"],
            }
        )
        if new_fingerprint != existing_fingerprint:
            raise FlattenEventConflictError(
                f"flatten event {event_id} (flatten_request_id={flatten_request_id}, "
                f"event_type={event_type.value}) was already recorded with different "
                "content -- the same event identity would refer to two different outcomes"
            )
        return AppendResult(event_id=event_id, inserted=False)

    def events_for(self, flatten_request_id: UUID) -> tuple[FlattenEventRecord, ...]:
        statement = (
            select(flatten_events)
            .where(flatten_events.c.flatten_request_id == flatten_request_id)
            .order_by(flatten_events.c.sequence)
        )
        with self._engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return tuple(
            FlattenEventRecord(
                event_id=row["event_id"],
                flatten_request_id=row["flatten_request_id"],
                event_type=FlattenEventType(row["event_type"]),
                occurred_at_utc=row["occurred_at_utc"],
                reason_codes=tuple(ReasonCode(code) for code in row["reason_codes"]),
                detail=row["detail"],
                payload=row["payload"],
            )
            for row in rows
        )

    def occurrence_histories(
        self, *, environment: Environment, canonical_symbol: str
    ) -> tuple[tuple[UUID, tuple[FlattenEventRecord, ...]], ...]:
        """Every flatten occurrence for this environment/symbol, each with

        its full event history, oldest trading day first (core critical
        path item 8, ADR-010). `flatten_events` carries no environment or
        symbol of its own — both live on `flatten_requests` — so this
        joins the two rather than duplicating those columns onto the
        event table. Bounded forever by real occurrence volume: at most
        one row per `(environment, canonical_symbol, trading_day)`,
        served by the existing `ix_flatten_requests_day` index.
        """
        statement = (
            select(flatten_events, flatten_requests.c.trading_day)
            .select_from(
                flatten_requests.join(
                    flatten_events,
                    flatten_events.c.flatten_request_id == flatten_requests.c.flatten_request_id,
                )
            )
            .where(
                flatten_requests.c.environment == environment.value,
                flatten_requests.c.canonical_symbol == canonical_symbol,
            )
            .order_by(flatten_requests.c.trading_day, flatten_events.c.sequence)
        )
        with self._engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()

        histories: dict[UUID, list[FlattenEventRecord]] = {}
        order: list[UUID] = []
        for row in rows:
            flatten_request_id = row["flatten_request_id"]
            if flatten_request_id not in histories:
                histories[flatten_request_id] = []
                order.append(flatten_request_id)
            histories[flatten_request_id].append(
                FlattenEventRecord(
                    event_id=row["event_id"],
                    flatten_request_id=flatten_request_id,
                    event_type=FlattenEventType(row["event_type"]),
                    occurred_at_utc=row["occurred_at_utc"],
                    reason_codes=tuple(ReasonCode(code) for code in row["reason_codes"]),
                    detail=row["detail"],
                    payload=row["payload"],
                )
            )
        return tuple((rid, tuple(histories[rid])) for rid in order)
