"""Assemble one read-only snapshot of platform state for Dashboard v0.

Every value here comes from PostgreSQL (via `MarketDataStore`/`EventJournal`/
`PostgresSafetyStateStore`, all already-existing read paths) or from the
`LiveReader` health JSON file (`reader_health.py`). Nothing here opens an MT5
connection, reads a credential, or writes anything — see the package
docstring for the boundary this must hold.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from sqlalchemy import Engine

from crumblr.config import AccountGuardConfig
from crumblr.dashboard.reader_health import read_health_snapshot
from crumblr.domain.enums import Environment
from crumblr.domain.events import EventType, SignalGenerated
from crumblr.domain.models import MarketBar, MarketTick, RiskDecision
from crumblr.domain.models import SupervisorDecision as SupervisorDecisionPayload
from crumblr.domain.timeutils import UtcDatetime, utc_now
from crumblr.persistence.journal import EventJournal
from crumblr.persistence.market_data import MarketDataStore
from crumblr.persistence.safety_state import PostgresSafetyStateStore
from crumblr.risk.safety_state import SafetyState


@dataclass(frozen=True)
class DecisionSummary:
    """One journalled decision, reduced to what a status screen needs."""

    occurred_at_utc: UtcDatetime
    summary: str
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class DashboardState:
    """Everything Dashboard v0's single screen renders, gathered once per request."""

    generated_at_utc: UtcDatetime
    environment: str
    expected_broker_server: str
    expected_currency: str | None
    canonical_symbol: str
    timeframe: str

    reader_health: dict[str, Any] | None
    """`ReaderHealth.to_payload()` as last written by `mt5_live_reader.py`,

    or `None` if the snapshot file does not exist yet (the reader has never
    run) or could not be read."""

    latest_tick: MarketTick | None
    latest_bar: MarketBar | None
    tick_count: int
    bar_count: int

    halt: SafetyState

    latest_signal: DecisionSummary | None
    latest_risk_decision: DecisionSummary | None
    latest_supervisor_decision: DecisionSummary | None
    uncalibrated_supervisor_checks: tuple[str, ...]
    """Named on the latest `SupervisorDecision`, or empty if none exists yet.

    build.md §22 asks the observability dashboard to show which controls are
    not actually in force (review F-024) rather than let an approval read as
    though every configured check passed."""


def _signal_summary(payload: SignalGenerated) -> str:
    return f"{payload.proposed_side.value} (confidence {payload.confidence:.2f})"


def _risk_summary(payload: RiskDecision) -> str:
    if payload.approved_volume is not None:
        return f"{payload.verdict.value} — volume {payload.approved_volume}"
    return payload.verdict.value


def _supervisor_summary(payload: SupervisorDecisionPayload) -> str:
    return payload.verdict.value


def build_state(
    *,
    engine: Engine,
    guard: AccountGuardConfig,
    environment: Environment,
    canonical_symbol: str,
    timeframe: str,
    reader_health_path: Path,
    clock: Callable[[], UtcDatetime] = utc_now,
) -> DashboardState:
    """Read every source once and return one consistent-enough snapshot.

    "Consistent-enough": each read is its own query rather than one shared
    transaction, so two panels could in principle reflect state a few
    milliseconds apart. For a read-only status screen refreshed every few
    seconds that is not worth the complexity a shared snapshot read would add.
    """
    market = MarketDataStore(engine)
    journal = EventJournal(engine)
    halt = PostgresSafetyStateStore(engine).load()

    counts = market.counts()
    latest_signal = journal.latest(EventType.SIGNAL_GENERATED)
    latest_risk = journal.latest(EventType.RISK_DECISION_MADE)
    latest_supervisor = journal.latest(EventType.SUPERVISOR_DECISION_MADE)

    return DashboardState(
        generated_at_utc=clock(),
        environment=environment.value,
        expected_broker_server=guard.expected_server,
        expected_currency=guard.expected_currency,
        canonical_symbol=canonical_symbol,
        timeframe=timeframe,
        reader_health=read_health_snapshot(reader_health_path),
        latest_tick=market.latest_tick(canonical_symbol=canonical_symbol),
        latest_bar=market.latest_bar(canonical_symbol=canonical_symbol, timeframe=timeframe),
        tick_count=counts.get("ticks", 0),
        bar_count=counts.get("bars", 0),
        halt=halt,
        latest_signal=(
            DecisionSummary(
                occurred_at_utc=latest_signal.occurred_at_utc,
                summary=_signal_summary(cast(SignalGenerated, latest_signal.payload)),
                reason_codes=cast(SignalGenerated, latest_signal.payload).reason_codes,
            )
            if latest_signal is not None
            else None
        ),
        latest_risk_decision=(
            DecisionSummary(
                occurred_at_utc=latest_risk.occurred_at_utc,
                summary=_risk_summary(cast(RiskDecision, latest_risk.payload)),
                reason_codes=tuple(
                    code.value for code in cast(RiskDecision, latest_risk.payload).reason_codes
                ),
            )
            if latest_risk is not None
            else None
        ),
        latest_supervisor_decision=(
            DecisionSummary(
                occurred_at_utc=latest_supervisor.occurred_at_utc,
                summary=_supervisor_summary(
                    cast(SupervisorDecisionPayload, latest_supervisor.payload)
                ),
                reason_codes=tuple(
                    code.value
                    for code in cast(
                        SupervisorDecisionPayload, latest_supervisor.payload
                    ).reason_codes
                ),
            )
            if latest_supervisor is not None
            else None
        ),
        uncalibrated_supervisor_checks=(
            cast(SupervisorDecisionPayload, latest_supervisor.payload).uncalibrated_checks
            if latest_supervisor is not None
            else ()
        ),
    )
