"""Safety state in PostgreSQL, and the composite store from ADR-002.

Two stores answer the same question and can disagree. The rule is the whole
point of ADR-002:

    Any disagreement in safety-critical state → HALTED.
    Never prefer the more permissive source.

The journal is the record of authority. The local file is an independent latch
that keeps working when the database does not — a system that can only learn it
is halted by querying a database cannot fail closed when the database is what
failed.
"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import Engine, desc, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from crumblr.domain.enums import KillSwitchState, ReasonCode
from crumblr.observability.logging import get_logger
from crumblr.persistence.schema import safety_state_events
from crumblr.risk.safety_state import (
    SCHEMA_VERSION,
    SafetyState,
    SafetyStateStore,
    unknown_state,
)

_log = get_logger("safety_state")


class PostgresSafetyStateStore:
    """Append-only safety-state history in the journal.

    `load` returns the most recent recorded state. Every failure mode — no
    rows, unreadable rows, an unreachable database — resolves to `UNKNOWN`
    rather than raising, because the caller must be able to treat "I could not
    find out" as a state rather than as an exception to handle.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def load(self) -> SafetyState:
        try:
            statement = (
                select(safety_state_events).order_by(desc(safety_state_events.c.sequence)).limit(1)
            )
            with self._engine.connect() as connection:
                row = connection.execute(statement).mappings().first()
        except Exception as error:
            return unknown_state(f"safety-state journal unreachable: {error}")

        if row is None:
            return unknown_state("no safety state recorded in the journal")
        if row["schema_version"] != SCHEMA_VERSION:
            return unknown_state(
                f"journal safety-state schema {row['schema_version']!r} "
                f"is not the expected {SCHEMA_VERSION}"
            )

        try:
            return SafetyState(
                state=KillSwitchState(row["state"]),
                reason_codes=tuple(ReasonCode(code) for code in row["reason_codes"]),
                recorded_at_utc=row["occurred_at_utc"],
                tripped_by=row["tripped_by"],
                detail=row["detail"],
            )
        except (ValueError, TypeError) as error:
            return unknown_state(f"journal safety-state row is malformed: {error}")

    def save(self, state: SafetyState) -> None:
        """Append a state change. Never updates an earlier row."""
        statement = pg_insert(safety_state_events).values(
            event_id=uuid4(),
            state=state.state.value,
            occurred_at_utc=state.recorded_at_utc,
            reason_codes=[code.value for code in state.reason_codes],
            tripped_by=state.tripped_by,
            detail=state.detail,
            schema_version=state.schema_version,
        )
        with self._engine.begin() as connection:
            connection.execute(statement)


class CompositeSafetyStateStore:
    """The journal and the local latch, read together (ADR-002).

    Writes go to the journal first, then the latch — the same ordering rule the
    kill switch already applies to persistence versus in-memory state. A latch
    write that fails after the journal succeeded leaves the pair disagreeing in
    the *safe* direction, and the table below resolves that to halted.
    """

    def __init__(self, journal: SafetyStateStore, latch: SafetyStateStore) -> None:
        self._journal = journal
        self._latch = latch

    def load(self) -> SafetyState:
        journal_state = self._journal.load()
        latch_state = self._latch.load()

        if journal_state.permits_new_orders and latch_state.permits_new_orders:
            return journal_state

        # Anything else halts. Which source objected is recorded, because
        # "the database says halted" and "the file is unreadable" need
        # different responses from an operator.
        if not journal_state.permits_new_orders and not latch_state.permits_new_orders:
            disagreement = None
            authoritative = journal_state
        elif not journal_state.permits_new_orders:
            disagreement = "journal halted, latch running"
            authoritative = journal_state
        else:
            disagreement = "latch halted, journal running"
            authoritative = latch_state

        if disagreement is not None:
            _log.error(
                "safety_state.disagreement",
                journal_state=journal_state.state.value,
                latch_state=latch_state.state.value,
                resolution="HALTED",
                detail=disagreement,
            )
            return SafetyState(
                state=KillSwitchState.UNKNOWN,
                reason_codes=(ReasonCode.SAFETY_STATE_UNKNOWN,),
                recorded_at_utc=authoritative.recorded_at_utc,
                tripped_by="safety_state_reconciliation",
                detail=(
                    f"{disagreement}; reconcile before enabling orders "
                    f"(journal: {journal_state.detail or journal_state.state.value}; "
                    f"latch: {latch_state.detail or latch_state.state.value})"
                ),
            )
        return authoritative

    def save(self, state: SafetyState) -> None:
        """Journal first, latch second. A reset must clear both (ADR-002 §5)."""
        self._journal.save(state)
        self._latch.save(state)
