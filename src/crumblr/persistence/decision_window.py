"""Decision-window idempotence state in PostgreSQL (review 1.17 §8, F-054).

Same shape as `persistence/risk_session.py`: appended, never updated, and
`load_latest` returns the most recently appended row rather than raising.
Unlike the risk session (one budget per process), this is keyed by
`(canonical_symbol, strategy_id, config_version)` so more than one live
decision worker can share a database without reading each other's
idempotence state.
"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import Engine, and_, desc, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from crumblr.application.decision_window import SCHEMA_VERSION, DecisionWindowState
from crumblr.observability.logging import get_logger
from crumblr.persistence.schema import decision_window_states

_log = get_logger("decision_window_store")


class PostgresDecisionWindowStore:
    """Append-only decision-window idempotence checkpoints."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def load_latest(
        self, *, canonical_symbol: str, strategy_id: str, config_version: str
    ) -> DecisionWindowState | None:
        statement = (
            select(decision_window_states)
            .where(
                and_(
                    decision_window_states.c.canonical_symbol == canonical_symbol,
                    decision_window_states.c.strategy_id == strategy_id,
                    decision_window_states.c.config_version == config_version,
                )
            )
            .order_by(desc(decision_window_states.c.sequence))
            .limit(1)
        )
        try:
            with self._engine.connect() as connection:
                row = connection.execute(statement).mappings().first()
        except Exception as error:
            # Unreadable collapses to "nothing recorded" here — see the
            # module docstring in application/decision_window.py for why
            # that is a deliberately different rule from RiskSessionStore.
            _log.error("decision_window.unreadable", error=str(error))
            return None

        if row is None:
            return None
        if row["schema_version"] != SCHEMA_VERSION:
            _log.error(
                "decision_window.schema_mismatch",
                stored=row["schema_version"],
                expected=SCHEMA_VERSION,
            )
            return None

        try:
            return DecisionWindowState(
                canonical_symbol=row["canonical_symbol"],
                strategy_id=row["strategy_id"],
                config_version=row["config_version"],
                last_decided_open_time_utc=row["last_decided_open_time_utc"],
                seen_decision_hashes=frozenset(row["seen_decision_hashes"]),
                recorded_at_utc=row["recorded_at_utc"],
                schema_version=row["schema_version"],
            )
        except (ValueError, TypeError, KeyError) as error:
            _log.error("decision_window.malformed_row", error=str(error))
            return None

    def save(self, state: DecisionWindowState) -> None:
        statement = pg_insert(decision_window_states).values(
            event_id=uuid4(),
            canonical_symbol=state.canonical_symbol,
            strategy_id=state.strategy_id,
            config_version=state.config_version,
            last_decided_open_time_utc=state.last_decided_open_time_utc,
            seen_decision_hashes=sorted(state.seen_decision_hashes),
            recorded_at_utc=state.recorded_at_utc,
            schema_version=state.schema_version,
        )
        with self._engine.begin() as connection:
            connection.execute(statement)
