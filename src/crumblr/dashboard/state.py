"""Assemble one read-only snapshot of platform state for the dashboard.

Every value here comes from PostgreSQL (via `MarketDataStore`/`EventJournal`/
`PostgresSafetyStateStore`/the Agent-Gateway and PAPER_LITE read paths, all
already-existing read paths or new read-only additions in this package) or
from the `LiveReader`/PAPER_LITE health/journal files on disk. Nothing here
opens an MT5 connection, reads a credential, evaluates a proposal, or writes
anything — see the package docstring for the boundary this must hold.

Two review 1.13 findings still shape this module:

- **F-043** — the state model must distinguish fresh data, stale data, a
  disconnected reader, a missing health snapshot and (at the caller level,
  since it means this whole function raised) an unavailable database, rather
  than only exposing raw numbers a template has to interpret. `mt5_connectivity`
  and `data_feed_state` exist for exactly this, and the same discipline now
  extends to `agent_health`/`last_decision`/`risk_panel`/`reconciliation`:
  `UNKNOWN` is a real, distinguishable outcome, never silently upgraded to
  something that reads as healthy.
- **F-044**'s original concern (a journalled decision must never be presented
  as though it belongs to the live feed shown next to it) is now handled by
  showing the *real* current pipeline (Agent Gateway -> Core Risk -> Platform
  Policy -> External Supervisor -> Paper Broker) and its actual latest
  decision, rather than a fixed "replay" label — the ambiguity F-044 warned
  about no longer applies once the shown decision is genuinely the platform's
  own latest one, not a replay-only artifact.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast
from uuid import UUID

from sqlalchemy import Engine

from crumblr.config import AccountGuardConfig, ExecutionConfig, RiskConfig
from crumblr.dashboard.agent_state import (
    AgentHealthState,
    AgentPanelState,
    LastDecisionState,
    build_agent_health,
    build_agent_panel,
    build_last_decision,
)
from crumblr.dashboard.broker_panel import BrokerReadModel, build_broker_read_model
from crumblr.dashboard.execution_panel import ExecutionGateState, build_execution_gate_state
from crumblr.dashboard.paper_lite_journal import JournalReadResult, read_journal_entries
from crumblr.dashboard.paper_portfolio import PaperPortfolioPanelState, build_paper_portfolio_panel
from crumblr.dashboard.pipeline import PipelineView, build_pipeline_view
from crumblr.dashboard.reader_health import read_health_snapshot
from crumblr.dashboard.reconciliation_panel import (
    ReconciliationPanelState,
    build_reconciliation_panel,
)
from crumblr.dashboard.risk_panel import RiskPanelState, build_risk_panel
from crumblr.domain.enums import Environment
from crumblr.domain.events import Event, EventType, SignalGenerated
from crumblr.domain.models import Contract, MarketBar, MarketTick, RiskDecision
from crumblr.domain.models import SupervisorDecision as SupervisorDecisionPayload
from crumblr.domain.timeutils import UtcDatetime, utc_now
from crumblr.market_data.pipeline import interval_for
from crumblr.persistence.agent_gateway import (
    PostgresAgentDecisionOutcomeStore,
    PostgresAgentIdentityStore,
    PostgresDecisionContextBundleStore,
    PostgresTradingAssignmentStore,
)
from crumblr.persistence.broker_state import BrokerStateStore
from crumblr.persistence.instrument_specs import InstrumentSpecStore
from crumblr.persistence.journal import CapsuleStore, EventJournal
from crumblr.persistence.market_data import MarketDataStore
from crumblr.persistence.risk_session import PostgresRiskSessionStore
from crumblr.persistence.safety_state import PostgresSafetyStateStore
from crumblr.risk.safety_state import SafetyState

ConnectivityState = Literal["CONNECTED", "DISCONNECTED", "UNKNOWN"]
DataFeedState = Literal["HEALTHY", "STALE", "DOWN", "UNKNOWN"]

RECENT_BAR_COUNT = 60
"""Review 1.13 §5: "30-60 recent M5 bars" for the chart."""

RECENT_EVENT_COUNT = 20
"""Review 1.13 §8: "show only recent events by default" for the timeline."""


@dataclass(frozen=True)
class DecisionSummary:
    """One journalled decision, with the context needed to place it —

    `environment`/`source`/`occurred_at_utc`/`correlation_id` are shown next
    to every decision so a viewer never has to guess which pipeline it came
    from."""

    occurred_at_utc: UtcDatetime
    environment: str
    source: str
    correlation_id: str
    version_label: str
    summary: str
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class EventSummary:
    """One journal entry, reduced to an activity-timeline row."""

    occurred_at_utc: UtcDatetime
    component: str
    event_type: str
    summary: str


@dataclass(frozen=True)
class PaperLiteActivityRow:
    """One PAPER_LITE journal entry, reduced to an activity-timeline row.

    No wall-clock timestamp: `persistence.paper_lite.PaperJournalEntry` does
    not carry one (only a monotonic `sequence`) — shown by sequence rather
    than fabricating a time the journal itself does not record."""

    sequence: int
    event_type: str
    detail: str | None


@dataclass(frozen=True)
class DashboardState:
    """Everything the dashboard's single screen renders, gathered once per request."""

    generated_at_utc: UtcDatetime
    environment: str
    environment_badge_label: str
    """What the top-bar badge actually says — never the raw `Environment`

    value, because `PAPER` reads as an active paper-execution campaign to an
    owner glancing at the screen. See `_environment_badge_label`."""
    expected_broker_server: str
    expected_currency: str | None
    expected_leverage: int | None
    canonical_symbol: str
    timeframe: str

    reader_health: dict[str, Any] | None
    """`ReaderHealth.to_payload()` as last written by `mt5_live_reader.py`,

    or `None` if the snapshot file does not exist yet (the reader has never
    run) or could not be read."""

    mt5_connectivity: ConnectivityState
    """Renders as the "Market Reader" header card."""
    data_feed_state: DataFeedState
    """Renders as the "Market Data" header card."""

    latest_tick: MarketTick | None
    latest_bar: MarketBar | None
    recent_bars: tuple[MarketBar, ...]
    """Oldest first, up to `RECENT_BAR_COUNT` — for the EUR/USD chart."""
    tick_count: int
    bar_count: int
    bar_gap_count: int
    """Consecutive-pair gaps within `recent_bars` — a real, computed metric,

    not a guess: two neighbouring stored bars whose open times are not
    exactly one interval apart."""
    bar_anomaly_count: int
    """Total anomalies flagged across `recent_bars`."""

    halt: SafetyState
    """Renders as the "Safety" header card."""

    agent_health: AgentHealthState
    """Renders as the "Agent" header card — `HEALTHY`/`WAITING`/

    `NOT PROVISIONED`/`UNKNOWN`, never fabricated green. See
    `dashboard.agent_state.build_agent_health`."""
    agent_panel: AgentPanelState | None
    """`None` only when no assignment is configured/found — the Agent panel

    then renders `NOT PROVISIONED`, not blank and not an error."""
    last_decision: LastDecisionState | None
    """`None` only when no PAPER_LITE evidence exists anywhere yet — the

    Last Decision card then renders `NO EVIDENCE`."""
    pipeline: PipelineView
    """The same `last_decision` evidence, restaged into the work order §16
    8-stage view — see `dashboard.pipeline` for why this adds no new
    evidence-gathering of its own."""

    risk_panel: RiskPanelState
    reconciliation: ReconciliationPanelState
    execution_gate: ExecutionGateState
    broker: BrokerReadModel
    """Account/positions/pending orders, all drawn from one consistent
    `BrokerAccountSnapshot` read — see `dashboard.broker_panel`."""
    """Renders the "Execution" header card — derived from real config gates,

    never hardcoded."""
    paper_portfolio: PaperPortfolioPanelState
    """PAPER_LITE's own simulated portfolio (balance/equity/P&L/open

    positions), replayed read-only from its durable journal — see
    `dashboard.paper_portfolio`. Explicitly paper-only; never implies real
    broker/trading-authority state, and carries its own `NO EVIDENCE`/
    `DEGRADED`/`OK` status rather than a fabricated zero when nothing (or
    nothing trustworthy) has been recorded yet."""

    latest_signal: DecisionSummary | None
    latest_risk_decision: DecisionSummary | None
    latest_supervisor_decision: DecisionSummary | None
    uncalibrated_supervisor_checks: tuple[str, ...]
    """Named on the latest `SupervisorDecision`, or empty if none exists yet.

    build.md §22 asks the observability dashboard to show which controls are
    not actually in force (review F-024) rather than let an approval read as
    though every configured check passed."""

    recent_events: tuple[EventSummary, ...]
    """Oldest first, up to `RECENT_EVENT_COUNT` — for the activity timeline."""

    paper_lite_activity: tuple[PaperLiteActivityRow, ...]
    """Oldest first, up to `RECENT_EVENT_COUNT` — PAPER_LITE's own audit

    trail, shown separately from `recent_events` since it has no comparable
    wall-clock timestamp to sort-merge against."""


def _signal_summary(payload: SignalGenerated) -> str:
    return f"{payload.proposed_side.value} (confidence {payload.confidence:.2f})"


def _risk_summary(payload: RiskDecision) -> str:
    if payload.approved_volume is not None:
        return f"{payload.verdict.value} — volume {payload.approved_volume}"
    return payload.verdict.value


def _supervisor_summary(payload: SupervisorDecisionPayload) -> str:
    return payload.verdict.value


def _version_label(event_type: EventType, payload: Contract) -> str:
    if isinstance(payload, SignalGenerated):
        return payload.strategy_version
    if isinstance(payload, RiskDecision):
        return payload.risk_config_version
    if isinstance(payload, SupervisorDecisionPayload):
        return payload.policy_version
    return "—"


def _decision_summary(
    event: Event[Contract], summary: str, reason_codes: tuple[str, ...]
) -> DecisionSummary:
    return DecisionSummary(
        occurred_at_utc=event.occurred_at_utc,
        environment=event.environment.value,
        source=event.source,
        correlation_id=str(event.correlation_id),
        version_label=_version_label(event.event_type, event.payload),
        summary=summary,
        reason_codes=reason_codes,
    )


def _event_summary(event: Event[Contract]) -> EventSummary:
    return EventSummary(
        occurred_at_utc=event.occurred_at_utc,
        component=event.source,
        event_type=event.event_type.value,
        summary=str(getattr(event.payload, "verdict", getattr(event.payload, "proposed_side", ""))),
    )


_PAPER_LITE_ACTIVITY_EVENT_TYPES = ("AUDIT_FACT", "PAPER_ORDER_ACCEPTED")
_PAPER_LITE_NOISE_FACTS = frozenset({"PAPER_LITE_DECISION_WINDOW_CLAIMED"})
"""Fires every single cycle regardless of outcome — real, but not the kind

of "activity" this timeline is for; every other audit fact is a genuine
per-cycle event worth a row."""


def _paper_lite_activity(
    entries: tuple[dict[str, Any], ...], *, limit: int
) -> tuple[PaperLiteActivityRow, ...]:
    rows = [
        PaperLiteActivityRow(
            sequence=entry["sequence"],
            event_type=(
                entry["payload"]["fact"]
                if entry["event_type"] == "AUDIT_FACT"
                else entry["event_type"]
            ),
            detail=(
                entry["payload"].get("detail") if entry["event_type"] == "AUDIT_FACT" else None
            ),
        )
        for entry in entries
        if entry.get("event_type") in _PAPER_LITE_ACTIVITY_EVENT_TYPES
        and entry.get("payload", {}).get("fact") not in _PAPER_LITE_NOISE_FACTS
    ]
    return tuple(rows[-limit:])


def _count_bar_gaps(bars: tuple[MarketBar, ...], timeframe: str) -> int:
    if len(bars) < 2:
        return 0
    interval = interval_for(timeframe)
    return sum(
        1
        for earlier, later in itertools.pairwise(bars)
        if later.bar.open_time_utc - earlier.bar.open_time_utc != interval
    )


def _environment_badge_label(environment: Environment) -> str:
    """`Environment.PAPER` must not read as a running campaign.

    `Environment.PAPER` is a config namespace — it selects `config/paper.yaml`
    and the Pepperstone demo account, nothing more. Every other environment
    value already says what it means without that ambiguity.
    """
    if environment is Environment.PAPER:
        return "DEMO DATA"
    return environment.value.upper()


def _connectivity(reader_health: dict[str, Any] | None) -> tuple[ConnectivityState, DataFeedState]:
    """Derive the two headline health cards from the reader's own status.

    `reader_health["status"]` already encodes `LiveReader`'s real
    `stale_after` threshold and reconnect logic (`HEALTHY`/`STALE`/
    `DISCONNECTED`/`UNHEALTHY`) — this maps that authoritative signal onto
    the two cards, rather than re-deriving freshness from a raw timestamp
    with a threshold the dashboard would have to guess at independently.
    """
    if reader_health is None:
        return "UNKNOWN", "UNKNOWN"
    status = reader_health.get("status")
    connected = bool(reader_health.get("connected"))
    if status == "HEALTHY":
        return "CONNECTED", "HEALTHY"
    if status == "STALE":
        return ("CONNECTED" if connected else "DISCONNECTED"), "STALE"
    if status in ("DISCONNECTED", "UNHEALTHY"):
        return "DISCONNECTED", "DOWN"
    return "UNKNOWN", "UNKNOWN"


def build_state(
    *,
    engine: Engine,
    guard: AccountGuardConfig,
    risk_config: RiskConfig,
    execution_config: ExecutionConfig,
    live_trading_acknowledged: bool,
    environment: Environment,
    canonical_symbol: str,
    timeframe: str,
    reader_health_path: Path,
    agent_assignment_id: UUID | None = None,
    paper_lite_journal_path: Path | None = None,
    paper_lite_settings_path: Path | None = None,
    expected_spec_version: str | None = None,
    clock: Callable[[], UtcDatetime] = utc_now,
) -> DashboardState:
    """Read every source once and return one consistent-enough snapshot.

    "Consistent-enough": each read is its own query rather than one shared
    transaction, so two panels could in principle reflect state a few
    milliseconds apart. For a read-only status screen refreshed every few
    seconds that is not worth the complexity a shared snapshot read would add.

    Raises whatever the underlying `Engine` raises if PostgreSQL is
    unreachable — deliberately not swallowed here. The caller (`app.py`)
    decides how to present that; a state-building function that silently
    returned an empty snapshot on a database outage would be indistinguishable
    from "no data yet", which F-043 explicitly asks not to conflate.
    """
    now = clock()

    market = MarketDataStore(engine)
    journal = EventJournal(engine)
    halt = PostgresSafetyStateStore(engine).load()

    counts = market.counts()
    latest_signal = journal.latest(EventType.SIGNAL_GENERATED)
    latest_risk = journal.latest(EventType.RISK_DECISION_MADE)
    latest_supervisor = journal.latest(EventType.SUPERVISOR_DECISION_MADE)
    reader_health = read_health_snapshot(reader_health_path)
    mt5_connectivity, data_feed_state = _connectivity(reader_health)

    recent_bars = market.recent_bars(
        canonical_symbol=canonical_symbol, timeframe=timeframe, limit=RECENT_BAR_COUNT
    )

    agent_panel = build_agent_panel(
        assignment_store=PostgresTradingAssignmentStore(engine),
        identity_store=PostgresAgentIdentityStore(engine),
        context_bundle_store=PostgresDecisionContextBundleStore(engine),
        assignment_id=agent_assignment_id,
        now=now,
    )
    journal_read = (
        read_journal_entries(paper_lite_journal_path)
        if paper_lite_journal_path is not None
        else JournalReadResult((), had_corruption=False)
    )
    last_decision = build_last_decision(
        outcome_store=PostgresAgentDecisionOutcomeStore(engine),
        capsule_store=CapsuleStore(engine),
        assignment_id=agent_assignment_id,
        journal_entries=journal_read.entries,
        journal_had_corruption=journal_read.had_corruption,
    )
    agent_health = build_agent_health(
        agent_panel=agent_panel,
        last_decision=last_decision,
        now=now,
        timeframe=timeframe,
    )
    pipeline = build_pipeline_view(agent_panel=agent_panel, last_decision=last_decision)
    risk_panel = build_risk_panel(
        risk_config=risk_config,
        session_store=PostgresRiskSessionStore(engine),
        canonical_symbol=canonical_symbol,
    )
    instrument_specs = InstrumentSpecStore(engine)
    reconciliation = build_reconciliation_panel(
        broker_state=BrokerStateStore(engine),
        instrument_specs=instrument_specs,
        guard=guard,
        canonical_symbol=canonical_symbol,
        expected_spec_version=expected_spec_version,
        now=now,
    )
    execution_gate = build_execution_gate_state(
        execution_config=execution_config,
        live_trading_acknowledged=live_trading_acknowledged,
    )
    broker = build_broker_read_model(broker_state=BrokerStateStore(engine))
    paper_portfolio = build_paper_portfolio_panel(
        journal_path=paper_lite_journal_path,
        paper_lite_settings_path=paper_lite_settings_path,
        instrument_specs=instrument_specs,
        canonical_symbol=canonical_symbol,
        expected_spec_version=expected_spec_version,
    )

    return DashboardState(
        generated_at_utc=now,
        environment=environment.value,
        environment_badge_label=_environment_badge_label(environment),
        expected_broker_server=guard.expected_server,
        expected_currency=guard.expected_currency,
        expected_leverage=guard.expected_leverage,
        canonical_symbol=canonical_symbol,
        timeframe=timeframe,
        reader_health=reader_health,
        mt5_connectivity=mt5_connectivity,
        data_feed_state=data_feed_state,
        latest_tick=market.latest_tick(canonical_symbol=canonical_symbol),
        latest_bar=market.latest_bar(canonical_symbol=canonical_symbol, timeframe=timeframe),
        recent_bars=recent_bars,
        tick_count=counts.get("ticks", 0),
        bar_count=counts.get("bars", 0),
        bar_gap_count=_count_bar_gaps(recent_bars, timeframe),
        bar_anomaly_count=sum(len(bar.anomalies) for bar in recent_bars),
        halt=halt,
        agent_health=agent_health,
        agent_panel=agent_panel,
        last_decision=last_decision,
        pipeline=pipeline,
        risk_panel=risk_panel,
        reconciliation=reconciliation,
        execution_gate=execution_gate,
        broker=broker,
        paper_portfolio=paper_portfolio,
        latest_signal=(
            _decision_summary(
                latest_signal,
                _signal_summary(cast(SignalGenerated, latest_signal.payload)),
                cast(SignalGenerated, latest_signal.payload).reason_codes,
            )
            if latest_signal is not None
            else None
        ),
        latest_risk_decision=(
            _decision_summary(
                latest_risk,
                _risk_summary(cast(RiskDecision, latest_risk.payload)),
                tuple(code.value for code in cast(RiskDecision, latest_risk.payload).reason_codes),
            )
            if latest_risk is not None
            else None
        ),
        latest_supervisor_decision=(
            _decision_summary(
                latest_supervisor,
                _supervisor_summary(cast(SupervisorDecisionPayload, latest_supervisor.payload)),
                tuple(
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
        recent_events=tuple(
            _event_summary(event) for event in journal.recent(limit=RECENT_EVENT_COUNT)
        ),
        paper_lite_activity=_paper_lite_activity(journal_read.entries, limit=RECENT_EVENT_COUNT),
    )
