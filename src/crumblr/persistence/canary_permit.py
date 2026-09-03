"""Durable, atomic one-shot DEMO canary permits (Phase B item B8).

`review/adr/ADR-018-canary-permit.md`. `canary_permits` and
`canary_permit_consumptions` are both in `schema.py::APPEND_ONLY_TABLES` —
the application role is never granted `UPDATE` on either, so "consuming" a
permit cannot be an in-place update of the issued row. `consume()` instead
attempts an `INSERT ... ON CONFLICT (permit_id) DO NOTHING RETURNING
permit_id` into the separate consumption table, mirroring
`persistence/execution.py::ExecutionRequestStore._claim` exactly: `permit_id`
is that table's primary key, so at most one consumption row can ever exist
per permit, and the database itself — not application logic — is what makes
"exactly one submission attempt, ever" true.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from crumblr.domain.enums import EntryType
from crumblr.domain.models import CanaryPermit, CanaryPermitConsumption
from crumblr.domain.timeutils import UtcDatetime
from crumblr.persistence.journal import AppendResult
from crumblr.persistence.schema import canary_permit_consumptions, canary_permits


class CanaryPermitConsumeOutcome(StrEnum):
    CONSUMED = "CONSUMED"
    """This call's `order_request_id` is the one that consumed the permit."""

    ALREADY_CONSUMED = "ALREADY_CONSUMED"
    """A different `order_request_id` already consumed it —

    `CanaryPermitConsumeResult.consumption` names which one. Not an
    error: exactly what makes two racing attempts converge on "one of
    us wins" instead of both proceeding."""

    EXPIRED = "EXPIRED"
    """`now` is past `valid_until_utc`. Refused before any consumption

    row is attempted — an expired permit can never be consumed, even by
    the first caller to try."""

    NOT_FOUND = "NOT_FOUND"
    """No permit exists with this `permit_id`."""


@dataclass(frozen=True)
class CanaryPermitConsumeResult:
    outcome: CanaryPermitConsumeOutcome
    consumption: CanaryPermitConsumption | None = None
    """Set for `CONSUMED` (the row this call just created) and

    `ALREADY_CONSUMED` (the existing row, naming who actually holds
    it). `None` for `EXPIRED`/`NOT_FOUND`."""


class CanaryPermitStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def issue(self, permit: CanaryPermit, *, connection: Connection | None = None) -> AppendResult:
        if connection is not None:
            return self._issue(connection, permit)
        with self._engine.begin() as own_connection:
            return self._issue(own_connection, permit)

    def _issue(self, connection: Connection, permit: CanaryPermit) -> AppendResult:
        statement = (
            pg_insert(canary_permits)
            .values(
                permit_id=permit.permit_id,
                approved_account_ref=permit.approved_account_ref,
                expected_server=permit.expected_server,
                canonical_symbol=permit.canonical_symbol,
                entry_type=permit.entry_type.value,
                agent_id=permit.agent_id,
                assignment_id=permit.assignment_id,
                strategy_artifact_hash=permit.strategy_artifact_hash,
                max_requested_risk_fraction=permit.max_requested_risk_fraction,
                issued_by=permit.issued_by,
                reason=permit.reason,
                issued_at_utc=permit.issued_at_utc,
                valid_until_utc=permit.valid_until_utc,
                schema_version=1,
            )
            .on_conflict_do_nothing(index_elements=["permit_id"])
            .returning(canary_permits.c.permit_id)
        )
        inserted = connection.execute(statement).first() is not None
        return AppendResult(event_id=permit.permit_id, inserted=inserted)

    def permit_for(self, permit_id: UUID) -> CanaryPermit | None:
        statement = select(canary_permits).where(canary_permits.c.permit_id == permit_id)
        with self._engine.connect() as connection:
            row = connection.execute(statement).mappings().first()
        return _decode_permit(row) if row is not None else None

    def consumption_for(self, permit_id: UUID) -> CanaryPermitConsumption | None:
        statement = select(canary_permit_consumptions).where(
            canary_permit_consumptions.c.permit_id == permit_id
        )
        with self._engine.connect() as connection:
            row = connection.execute(statement).mappings().first()
        return _decode_consumption(row) if row is not None else None

    def consume(
        self,
        permit_id: UUID,
        *,
        order_request_id: UUID,
        now: UtcDatetime,
        connection: Connection | None = None,
    ) -> CanaryPermitConsumeResult:
        if connection is not None:
            return self._consume(connection, permit_id, order_request_id=order_request_id, now=now)
        with self._engine.begin() as own_connection:
            return self._consume(
                own_connection, permit_id, order_request_id=order_request_id, now=now
            )

    def _consume(
        self,
        connection: Connection,
        permit_id: UUID,
        *,
        order_request_id: UUID,
        now: UtcDatetime,
    ) -> CanaryPermitConsumeResult:
        permit_row = (
            connection.execute(
                select(canary_permits).where(canary_permits.c.permit_id == permit_id)
            )
            .mappings()
            .first()
        )
        if permit_row is None:
            return CanaryPermitConsumeResult(outcome=CanaryPermitConsumeOutcome.NOT_FOUND)
        permit = _decode_permit(permit_row)
        if now > permit.valid_until_utc:
            return CanaryPermitConsumeResult(outcome=CanaryPermitConsumeOutcome.EXPIRED)

        statement = (
            pg_insert(canary_permit_consumptions)
            .values(
                permit_id=permit_id,
                order_request_id=order_request_id,
                consumed_at_utc=now,
                schema_version=1,
            )
            .on_conflict_do_nothing(index_elements=["permit_id"])
            .returning(canary_permit_consumptions.c.permit_id)
        )
        won = connection.execute(statement).first() is not None
        if won:
            return CanaryPermitConsumeResult(
                outcome=CanaryPermitConsumeOutcome.CONSUMED,
                consumption=CanaryPermitConsumption(
                    permit_id=permit_id,
                    order_request_id=order_request_id,
                    consumed_at_utc=now,
                ),
            )

        existing_row = (
            connection.execute(
                select(canary_permit_consumptions).where(
                    canary_permit_consumptions.c.permit_id == permit_id
                )
            )
            .mappings()
            .first()
        )
        assert existing_row is not None  # the conflict itself proves a row exists
        return CanaryPermitConsumeResult(
            outcome=CanaryPermitConsumeOutcome.ALREADY_CONSUMED,
            consumption=_decode_consumption(existing_row),
        )


def _decode_permit(row: Any) -> CanaryPermit:
    return CanaryPermit(
        permit_id=row["permit_id"],
        approved_account_ref=row["approved_account_ref"],
        expected_server=row["expected_server"],
        canonical_symbol=row["canonical_symbol"],
        entry_type=EntryType(row["entry_type"]),
        agent_id=row["agent_id"],
        assignment_id=row["assignment_id"],
        strategy_artifact_hash=row["strategy_artifact_hash"],
        max_requested_risk_fraction=row["max_requested_risk_fraction"],
        issued_by=row["issued_by"],
        reason=row["reason"],
        issued_at_utc=row["issued_at_utc"],
        valid_until_utc=row["valid_until_utc"],
    )


def _decode_consumption(row: Any) -> CanaryPermitConsumption:
    return CanaryPermitConsumption(
        permit_id=row["permit_id"],
        order_request_id=row["order_request_id"],
        consumed_at_utc=row["consumed_at_utc"],
    )
