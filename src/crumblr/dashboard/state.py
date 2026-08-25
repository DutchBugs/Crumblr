"""Assemble one read-only snapshot of platform state for Dashboard v0.

Every value here comes from PostgreSQL (via `MarketDataStore`/`EventJournal`/
`PostgresSafetyStateStore`, all already-existing read paths) or from the
`LiveReader` health JSON file (`reader_health.py`). Nothing here opens an MT5
connection, reads a credential, or writes anything — see the package
docstring for the boundary this must hold.

Two review 1.13 findings shape this module specifically:

- **F-043** — the state model must distinguish fresh data, stale data, a
  disconnected reader, a missing health snapshot and (at the caller level,
  since it means this whole function raised) an unavailable database, rather
  than only exposing raw numbers a template has to interpret. `mt5_connectivity`
  and `data_feed_state` exist for exactly this.
- **F-044** — `LiveReader` (real MT5 ticks/bars) and the replay decision
  pipeline (`TradingAgent`/risk/supervisor) are two unconnected systems today;
  nothing in this codebase feeds a live tick into a live decision. Any journal
  entry this module finds is therefore a replay/backtest decision, never a
  live one, and `decision_pipeline_label` says so rather than letting a
  polished layout imply otherwise.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from sqlalchemy import Engine

from crumblr.config import AccountGuardConfig
from crumblr.dashboard.reader_health import read_health_snapshot
from crumblr.domain.enums import Environment
from crumblr.domain.events import Event, EventType, SignalGenerated
from crumblr.domain.models import Contract, MarketBar, MarketTick, RiskDecision
from crumblr.domain.models import SupervisorDecision as SupervisorDecisionPayload
from crumblr.domain.timeutils import UtcDatetime, utc_now
from crumblr.market_data.pipeline import interval_for
from crumblr.persistence.journal import EventJournal
from crumblr.persistence.market_data import MarketDataStore
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
    """One journalled decision, with the context F-044 requires alongside it.

    `environment`/`source`/`occurred_at_utc`/`correlation_id` are shown next
    to every decision precisely so a viewer never has to guess whether it
    belongs to the live feed they are also looking at (it never does, today —
    see `decision_pipeline_label`).
    """

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
class DashboardState:
    """Everything Dashboard v0's single screen renders, gathered once per request."""

    generated_at_utc: UtcDatetime
    environment: str
    environment_badge_label: str
    """F-045: what the top-bar badge actually says — never the raw

    `Environment` value, because `PAPER` reads as an active paper-execution
    campaign to an owner glancing at the screen, and none has started (this
    build has no order path at all; see F-035). See `_environment_badge_label`.
    """
    milestone_label: str
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
    data_feed_state: DataFeedState

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

    latest_signal: DecisionSummary | None
    latest_risk_decision: DecisionSummary | None
    latest_supervisor_decision: DecisionSummary | None
    uncalibrated_supervisor_checks: tuple[str, ...]
    """Named on the latest `SupervisorDecision`, or empty if none exists yet.

    build.md §22 asks the observability dashboard to show which controls are
    not actually in force (review F-024) rather than let an approval read as
    though every configured check passed."""
    decision_pipeline_label: str
    """F-044: "LATEST REPLAY DECISION" or "NO LIVE DECISION PIPELINE ACTIVE" —

    never phrased as though a decision belongs to the live feed above it."""

    recent_events: tuple[EventSummary, ...]
    """Oldest first, up to `RECENT_EVENT_COUNT` — for the activity timeline."""


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
    """F-045 (review 1.14 §6): `PAPER` must not read as a running campaign.

    `Environment.PAPER` is a config namespace — it selects `config/paper.yaml`
    and the Pepperstone demo account, nothing more. No paper-execution
    campaign exists yet (`status.md` "Paper campaign: NOT STARTED") and this
    build has no order path at all, so showing the raw enum value implied a
    campaign was already running. Every other environment value already says
    what it means without that ambiguity.
    """
    if environment is Environment.PAPER:
        return "DEMO DATA"
    return environment.value.upper()


def _connectivity(reader_health: dict[str, Any] | None) -> tuple[ConnectivityState, DataFeedState]:
    """F-043: derive the two headline health cards from the reader's own status.

    `reader_health["status"]` already encodes `LiveReader`'s real
    `stale_after` threshold and reconnect logic (`HEALTHY`/`STALE`/
    `DISCONNECTED`/`UNHEALTHY`) — this maps that authoritative signal onto
    the two cards review 1.13 §5's layout asks for, rather than re-deriving
    freshness from a raw timestamp with a threshold the dashboard would have
    to guess at independently.
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
    environment: Environment,
    canonical_symbol: str,
    timeframe: str,
    reader_health_path: Path,
    milestone_label: str = "M1 PASSED",
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
    market = MarketDataStore(engine)
    journal = EventJournal(engine)
    halt = PostgresSafetyStateStore(engine).load()

    counts = market.counts()
    latest_signal = journal.latest(EventType.SIGNAL_GENERATED)
    latest_risk = journal.latest(EventType.RISK_DECISION_MADE)
    latest_supervisor = journal.latest(EventType.SUPERVISOR_DECISION_MADE)
    reader_health = read_health_snapshot(reader_health_path)
    mt5_connectivity, data_feed_state = _connectivity(reader_health)

    any_decision = (
        latest_signal is not None or latest_risk is not None or latest_supervisor is not None
    )
    decision_pipeline_label = (
        "LATEST REPLAY DECISION" if any_decision else "NO LIVE DECISION PIPELINE ACTIVE"
    )
    recent_bars = market.recent_bars(
        canonical_symbol=canonical_symbol, timeframe=timeframe, limit=RECENT_BAR_COUNT
    )

    return DashboardState(
        generated_at_utc=clock(),
        environment=environment.value,
        environment_badge_label=_environment_badge_label(environment),
        milestone_label=milestone_label,
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
        decision_pipeline_label=decision_pipeline_label,
        recent_events=tuple(
            _event_summary(event) for event in journal.recent(limit=RECENT_EVENT_COUNT)
        ),
    )
