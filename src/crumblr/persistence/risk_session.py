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

from uuid import uuid4

from sqlalchemy import Engine, desc, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from crumblr.observability.logging import get_logger
from crumblr.persistence.schema import risk_session_states
from crumblr.risk.session import SCHEMA_VERSION, RiskSessionState, SessionRecord

_log = get_logger("risk_session_store")


class PostgresRiskSessionStore:
    """Append-only risk-session history."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def load_latest(self) -> SessionRecord:
        try:
            statement = (
                select(risk_session_states).order_by(desc(risk_session_states.c.sequence)).limit(1)
            )
            with self._engine.connect() as connection:
                row = connection.execute(statement).mappings().first()
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

    def save(self, state: RiskSessionState) -> None:
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
        with self._engine.begin() as connection:
            connection.execute(statement)
