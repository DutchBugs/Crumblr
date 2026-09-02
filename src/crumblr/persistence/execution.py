"""Immutable execution requests + append-only execution events (Phase 4).

`review/PHASE4_PLAN_REVIEW_GO_WITH_TWEAKS.md` points 4 and 5, both
non-negotiable:

- Persistence here is **immutable-request + append-only-events**, never a
  single upserted `ExecutionResult` row silently overwritten. The same
  `order_request_id` recorded twice with different content must fail
  closed, never be dropped via a plain `ON CONFLICT DO NOTHING`.
- A request is durably persisted and **atomically claimed before any broker
  interaction**, so two workers — or the same one, retrying after a crash —
  can never independently act on the same decision.

Both properties come from Postgres primitives this schema already uses
everywhere else, not from anything new: `execution_requests` and
`execution_events` are both in `schema.py::APPEND_ONLY_TABLES` — the
application role is never granted `UPDATE` on either. **The claim is the
successful insert.** `INSERT ... ON CONFLICT (order_request_id) DO NOTHING
RETURNING order_request_id` gives exactly one concurrent caller a returned
row for a given key; everyone else gets none back and, on top of that, gets
their fingerprint checked against what is already stored, so a genuine
content conflict still raises rather than looking like a harmless retry.

Review 1.23 §7 (core critical path item 4): the same discipline now covers
`execution_events` too. A retried append for an `event_id` already on
record converges silently only when its content genuinely matches;
different `reason_codes`/`detail`/`payload` under the same identity raises
rather than being dropped by a plain `ON CONFLICT DO NOTHING`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import Engine, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from crumblr.domain.enums import ExecutionEventType, ReasonCode
from crumblr.domain.hashing import fingerprint
from crumblr.domain.timeutils import UtcDatetime
from crumblr.persistence.journal import AppendResult
from crumblr.persistence.schema import execution_events, execution_requests


class ExecutionRequestConflictError(RuntimeError):
    """The same `order_request_id` was already recorded with different content.

    Never silently ignored: an idempotency key that could refer to two
    different orders is exactly the ambiguity idempotency exists to
    prevent.
    """


class ExecutionEventConflictError(RuntimeError):
    """The same execution event identity was already recorded with

    different content. Review 1.23 §7 (core critical path item 4): a
    retried `SUBMISSION_STARTED`/`BROKER_ACK`/`FILLED`/`RECONCILED` (or
    any other execution event) with different `reason_codes`/`detail`/
    `payload` under the same `(order_request_id, event_type)` identity
    cannot be treated as a harmless duplicate — mirrors
    `ExecutionRequestConflictError` exactly, one level down the stack.
    """


def event_id_for(*, order_request_id: UUID, event_type: ExecutionEventType) -> UUID:
    """Derived, not random — a retry logging the same transition converges

    on the same row instead of duplicating it, the same idempotence
    discipline `domain/events.py::build_event`'s docstring describes for the
    main journal.
    """
    return uuid5(NAMESPACE_URL, f"crumblr:execution_event:{order_request_id}:{event_type.value}")


@dataclass(frozen=True)
class ClaimResult:
    claimed: bool
    """`True` only when *this* call's insert is the one that won the claim.

    `False` with no exception means another attempt already holds this
    `order_request_id` with matching content — not an error, and exactly
    what makes retrying the same decision after a crash safe rather than a
    duplicate submission."""


@dataclass(frozen=True)
class ExecutionEventRecord:
    event_id: UUID
    order_request_id: UUID
    event_type: ExecutionEventType
    occurred_at_utc: UtcDatetime
    reason_codes: tuple[ReasonCode, ...]
    detail: str | None
    payload: dict[str, Any] | None


class ExecutionRequestStore:
    """The immutable half: one row per `order_request_id`, ever."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def claim(
        self,
        *,
        order_request_id: UUID,
        capsule_id: UUID,
        intent_id: UUID,
        fingerprint: str,
        claimed_by: str,
        now: UtcDatetime,
        connection: Connection | None = None,
    ) -> ClaimResult:
        if connection is not None:
            return self._claim(
                connection,
                order_request_id=order_request_id,
                capsule_id=capsule_id,
                intent_id=intent_id,
                fingerprint=fingerprint,
                claimed_by=claimed_by,
                now=now,
            )
        with self._engine.begin() as own_connection:
            return self._claim(
                own_connection,
                order_request_id=order_request_id,
                capsule_id=capsule_id,
                intent_id=intent_id,
                fingerprint=fingerprint,
                claimed_by=claimed_by,
                now=now,
            )

    def _claim(
        self,
        connection: Connection,
        *,
        order_request_id: UUID,
        capsule_id: UUID,
        intent_id: UUID,
        fingerprint: str,
        claimed_by: str,
        now: UtcDatetime,
    ) -> ClaimResult:
        statement = (
            pg_insert(execution_requests)
            .values(
                order_request_id=order_request_id,
                capsule_id=capsule_id,
                intent_id=intent_id,
                fingerprint=fingerprint,
                claimed_by=claimed_by,
                claimed_at_utc=now,
                schema_version=1,
            )
            .on_conflict_do_nothing(index_elements=["order_request_id"])
            .returning(execution_requests.c.order_request_id)
        )
        won = connection.execute(statement).first() is not None
        if won:
            return ClaimResult(claimed=True)

        existing_fingerprint = connection.execute(
            select(execution_requests.c.fingerprint).where(
                execution_requests.c.order_request_id == order_request_id
            )
        ).scalar_one()
        if existing_fingerprint != fingerprint:
            raise ExecutionRequestConflictError(
                f"order_request_id {order_request_id} was already recorded with a "
                f"different fingerprint ({existing_fingerprint!r} != {fingerprint!r}) "
                "-- the same idempotency key would refer to two different orders"
            )
        return ClaimResult(claimed=False)


class ExecutionEventStore:
    """The append-only half: every lifecycle step, one row each."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def append(
        self,
        *,
        order_request_id: UUID,
        event_type: ExecutionEventType,
        occurred_at_utc: UtcDatetime,
        reason_codes: tuple[ReasonCode, ...] = (),
        detail: str | None = None,
        payload: dict[str, Any] | None = None,
        connection: Connection | None = None,
    ) -> AppendResult:
        if connection is not None:
            return self._append(
                connection,
                order_request_id=order_request_id,
                event_type=event_type,
                occurred_at_utc=occurred_at_utc,
                reason_codes=reason_codes,
                detail=detail,
                payload=payload,
            )
        with self._engine.begin() as own_connection:
            return self._append(
                own_connection,
                order_request_id=order_request_id,
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
        order_request_id: UUID,
        event_type: ExecutionEventType,
        occurred_at_utc: UtcDatetime,
        reason_codes: tuple[ReasonCode, ...],
        detail: str | None,
        payload: dict[str, Any] | None,
    ) -> AppendResult:
        event_id = event_id_for(order_request_id=order_request_id, event_type=event_type)
        reason_code_values = [code.value for code in reason_codes]
        statement = (
            pg_insert(execution_events)
            .values(
                event_id=event_id,
                order_request_id=order_request_id,
                event_type=event_type.value,
                occurred_at_utc=occurred_at_utc,
                reason_codes=reason_code_values,
                detail=detail,
                payload=payload,
                schema_version=1,
            )
            .on_conflict_do_nothing(index_elements=["event_id"])
            .returning(execution_events.c.event_id)
        )
        won = connection.execute(statement).first() is not None
        if won:
            return AppendResult(event_id=event_id, inserted=True)

        existing = (
            connection.execute(
                select(
                    execution_events.c.reason_codes,
                    execution_events.c.detail,
                    execution_events.c.payload,
                ).where(execution_events.c.event_id == event_id)
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
            raise ExecutionEventConflictError(
                f"execution event {event_id} (order_request_id={order_request_id}, "
                f"event_type={event_type.value}) was already recorded with different "
                "content -- the same event identity would refer to two different outcomes"
            )
        return AppendResult(event_id=event_id, inserted=False)

    def events_for(self, order_request_id: UUID) -> tuple[ExecutionEventRecord, ...]:
        statement = (
            select(execution_events)
            .where(execution_events.c.order_request_id == order_request_id)
            .order_by(execution_events.c.sequence)
        )
        with self._engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return tuple(
            ExecutionEventRecord(
                event_id=row["event_id"],
                order_request_id=row["order_request_id"],
                event_type=ExecutionEventType(row["event_type"]),
                occurred_at_utc=row["occurred_at_utc"],
                reason_codes=tuple(ReasonCode(code) for code in row["reason_codes"]),
                detail=row["detail"],
                payload=row["payload"],
            )
            for row in rows
        )

    def count_events_since(self, event_type: ExecutionEventType, since: UtcDatetime) -> int:
        """How many `event_type` events occurred at or after `since`.

        Review 1.23 F-060 (reopened): the durable order-frequency authority
        FINAL Risk's `orders_in_last_hour` actually needs is a count of
        `ExecutionEventType.SUBMISSION_STARTED` events — "the platform
        committed to attempting one broker submission" — not a count of
        claimed `execution_requests`, which includes every refusal outcome
        along the way. `SUBMISSION_STARTED` is real and emitted now (core
        critical path item 3), but only when `SubmissionGate` opens, and no
        shipped config can open it — every three of its owner-approval
        fields default closed. Calling this with that event type therefore
        stays an honest `0` in every real deployment, not because the event
        type is unemittable, but because nothing shipped can reach it.
        """
        from sqlalchemy import func

        statement = (
            select(func.count())
            .select_from(execution_events)
            .where(
                execution_events.c.event_type == event_type.value,
                execution_events.c.occurred_at_utc >= since,
            )
        )
        with self._engine.connect() as connection:
            return int(connection.execute(statement).scalar_one())

    def request_ids_with_event(
        self, event_type: ExecutionEventType, *, since: UtcDatetime | None = None
    ) -> tuple[UUID, ...]:
        """Every distinct `order_request_id` that has at least one

        `event_type` event, oldest commitment first (core critical path
        item 8, ADR-010). The read seam `events_for()` (per-request) and
        `count_events_since()` (a scalar count) do not provide: "which
        requests could possibly imply live exposure".

        Called with `SUBMISSION_STARTED`, this is not a heuristic
        shortcut — `application/expected_state.py`'s exhaustive mapping
        over `ExecutionEventType` proves every *other* durable state a
        request can be in contributes no exposure, so this is the
        complete candidate set for post-fill reconciliation, which
        happens also to be bounded (provably empty in every shipped
        config today — no config can open `SubmissionGate`) rather than a
        "read every event ever" scan.

        `since` exists for a future narrower caller; the reconciliation
        driver deliberately passes `None` — bounding by time would defeat
        the mechanism's purpose (a position lost track of weeks ago is
        exactly the drift item 8 exists to catch).
        """
        from sqlalchemy import func

        statement = (
            select(execution_events.c.order_request_id)
            .where(execution_events.c.event_type == event_type.value)
            .group_by(execution_events.c.order_request_id)
            .order_by(func.min(execution_events.c.sequence))
        )
        if since is not None:
            statement = statement.where(execution_events.c.occurred_at_utc >= since)
        with self._engine.connect() as connection:
            rows = connection.execute(statement).scalars().all()
        return tuple(rows)
