"""Assembling a run that remembers (ADR-002; review 1.5 step 1).

The orchestrator takes a recorder and a kill switch and does not care where
they came from. This module is where they come from when the run is meant to
be durable: it opens the database, builds the journal-backed recorder, and
pairs the safety-state journal with the local file latch that ADR-002
requires.

The important property is what happens on a database that has never recorded
a RUNNING state — a fresh install, a wiped volume, a wrong connection string.
The composite store answers UNKNOWN, `KillSwitch.on_startup` refuses new
orders, and the run proceeds observing and refusing rather than trading. That
is not an error path to be smoothed over; it is the behaviour, and clearing it
takes an operator with an incident note.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine

from crumblr.application.recording import JournalRecorder
from crumblr.domain.enums import Environment
from crumblr.observability.logging import get_logger
from crumblr.persistence.engine import create_db_engine, database_url
from crumblr.persistence.migrations import upgrade_to_head
from crumblr.persistence.risk_session import PostgresRiskLedgerLock, PostgresRiskSessionStore
from crumblr.persistence.safety_state import (
    CompositeSafetyStateStore,
    PostgresSafetyStateStore,
)
from crumblr.risk.kill_switch import KillSwitch
from crumblr.risk.safety_state import FileSafetyStateStore
from crumblr.risk.session import RiskLedgerLock, RiskSessionStore

_log = get_logger("bootstrap")

DEFAULT_STATE_FILENAME = "safety_state.json"
"""The local latch. Small, atomic, and readable when PostgreSQL is not."""


@dataclass(frozen=True)
class DurableRuntime:
    """Everything a persistent run needs, already wired together."""

    engine: Engine
    recorder: JournalRecorder
    kill_switch: KillSwitch
    safety_state: CompositeSafetyStateStore
    session_store: RiskSessionStore
    risk_ledger_lock: RiskLedgerLock
    """ADR-021 (AG-012/Phase C) — the single-authority lock every real

    `LiveDecisionOrchestrator`/`ExecutionOrchestrator` process must be
    constructed with, alongside `session_store`."""

    def dispose(self) -> None:
        self.engine.dispose()


def build_durable_runtime(
    *,
    environment: Environment,
    state_file: Path,
    url: str | None = None,
    create_schema: bool = False,
) -> DurableRuntime:
    """Open the database and recover the safety state from it.

    `create_schema` runs the Alembic migrations rather than `create_all`, so
    the ordinary local path exercises the same mechanism a deployment uses.
    A schema built one way and migrated another is two schemas that happen to
    agree today (review 1.6 F-023).
    """
    resolved = url or database_url()
    engine = create_db_engine(resolved)
    if create_schema:
        upgrade_to_head(resolved)

    safety_state = CompositeSafetyStateStore(
        journal=PostgresSafetyStateStore(engine),
        latch=FileSafetyStateStore(state_file),
    )
    # Not `KillSwitch(store)`: that constructor starts RUNNING and would treat
    # an unread database as permission to trade. `on_startup` begins closed and
    # only opens on an explicitly RUNNING record.
    kill_switch = KillSwitch.on_startup(safety_state)

    _log.info(
        "runtime.ready",
        environment=environment.value,
        state_file=str(state_file),
        kill_switch=kill_switch.state.value,
        new_orders="disabled" if kill_switch.is_halted else "enabled",
    )
    return DurableRuntime(
        engine=engine,
        recorder=JournalRecorder(engine, environment=environment),
        kill_switch=kill_switch,
        safety_state=safety_state,
        session_store=PostgresRiskSessionStore(engine),
        risk_ledger_lock=PostgresRiskLedgerLock(engine),
    )
