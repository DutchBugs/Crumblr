"""Risk-session state in PostgreSQL (review 1.5 step 2; F-019).

The same shape as `persistence/safety_state.py`, for the same reason: what the
system must not be allowed to forget is appended, never updated, and a read
that fails resolves to "I do not know" rather than to an exception the caller
might handle by carrying on.

`load_latest` returns the most recently appended snapshot. Ordering is by the
insertion sequence rather than by trading day — a snapshot for an earlier day
written later is still the most recent thing the system believed, and picking
the highest trading day instead would let a stale row about a busier day
override it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import uuid4

from sqlalchemy import Connection, Engine, desc, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from crumblr.observability.logging import get_logger
from crumblr.persistence.schema import risk_session_states
from crumblr.risk.session import SCHEMA_VERSION, RiskSessionState, SessionRecord

_log = get_logger("risk_session_store")


class PostgresRiskLedgerLock:
    """One `pg_advisory_xact_lock` per canonical symbol (ADR-021,

    AG-012/Phase C) — serializes the recover→evaluate(→persist) critical
    section across every process that reads or writes
    `risk_session_states` for that symbol. Reuses
    `persistence/agent_gateway.py::lock_assignment()`'s exact proven
    primitive, not a new mechanism.

    Opens its own transaction and yields the connection out (`held()`'s
    own docstring, `risk/session.py::RiskLedgerLock`) — released
    automatically when the `with self._engine.begin()` block exits
    (commit or rollback), the same as `lock_assignment()`'s own
    transaction-scoped lock.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @contextmanager
    def held(self, canonical_symbol: str) -> Iterator[Connection]:
        with self._engine.begin() as connection:
            connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                {"key": f"risk-ledger:{canonical_symbol}"},
            )
            yield connection


class PostgresRiskSessionStore:
    """Append-only risk-session history."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def load_latest(self, *, connection: Connection | None = None) -> SessionRecord:
        """`connection` (ADR-021): when given, runs inside the caller's

        already-open transaction (`RiskLedgerLock.held()`'s block) instead
        of opening a second one — every other behaviour is unchanged."""
        try:
            statement = (
                select(risk_session_states).order_by(desc(risk_session_states.c.sequence)).limit(1)
            )
            if connection is not None:
                row = connection.execute(statement).mappings().first()
            else:
                with self._engine.connect() as own_connection:
                    row = own_connection.execute(statement).mappings().first()
        except Exception as error:
            return SessionRecord(unreadable=f"risk-session journal unreachable: {error}")

        if row is None:
            return SessionRecord()
        if row["schema_version"] != SCHEMA_VERSION:
            return SessionRecord(
                unreadable=(
                    f"risk-session schema {row['schema_version']!r} "
                    f"is not the expected {SCHEMA_VERSION}"
                )
            )

        try:
            return SessionRecord(
                state=RiskSessionState(
                    trading_day=row["trading_day"],
                    session_start_equity=row["session_start_equity"],
                    current_equity=row["current_equity"],
                    peak_equity=row["peak_equity"],
                    realized_pnl=row["realized_pnl"],
                    max_drawdown_fraction=row["max_drawdown_fraction"],
                    max_session_loss_fraction=row["max_session_loss_fraction"],
                    open_risk_fraction=row["open_risk_fraction"],
                    open_position_count=row["open_position_count"],
                    recorded_at_utc=row["occurred_at_utc"],
                )
            )
        except (ValueError, TypeError, KeyError) as error:
            return SessionRecord(unreadable=f"risk-session row is malformed: {error}")

    def save(self, state: RiskSessionState, *, connection: Connection | None = None) -> None:
        """`connection` (ADR-021): when given, runs inside the caller's

        already-open transaction (`RiskLedgerLock.held()`'s block) instead
        of opening a second one."""
        statement = pg_insert(risk_session_states).values(
            event_id=uuid4(),
            trading_day=state.trading_day,
            session_start_equity=state.session_start_equity,
            current_equity=state.current_equity,
            peak_equity=state.peak_equity,
            realized_pnl=state.realized_pnl,
            max_drawdown_fraction=state.max_drawdown_fraction,
            max_session_loss_fraction=state.max_session_loss_fraction,
            open_risk_fraction=state.open_risk_fraction,
            open_position_count=state.open_position_count,
            occurred_at_utc=state.recorded_at_utc,
            schema_version=state.schema_version,
        )
        if connection is not None:
            connection.execute(statement)
            return
        with self._engine.begin() as own_connection:
            own_connection.execute(statement)
