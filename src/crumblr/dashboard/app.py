"""The FastAPI app for Dashboard v0. Read-only by construction.

Only `GET` routes are registered — there is no handler anywhere in this
module for `POST`/`PUT`/`PATCH`/`DELETE`, so there is no HALT-reset, no
order button and no risk-config write for a route to even accidentally
expose. `tests/integration/test_dashboard.py::test_no_route_accepts_a_mutation`
checks this holds, not only that it was intended.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from crumblr.config import AccountGuardConfig, ExecutionConfig, RiskConfig
from crumblr.dashboard.pipeline import pipeline_stage_class
from crumblr.dashboard.state import DashboardState, build_state
from crumblr.domain.enums import Environment
from crumblr.observability.logging import get_logger

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

_log = get_logger("dashboard")

_DATABASE_UNAVAILABLE_MESSAGE = "database unavailable — see server logs"
"""Never the raw `str(error)` — a SQLAlchemy/psycopg connection error's text

can include the DSN (host, user, sometimes more). The full error still goes
to the server-side `_log.warning(...)` call below; only the client-facing
message is fixed and generic, since both `/api/state` and the rendered HTML
must never carry anything credential-shaped."""

_GOOD_STATES = frozenset(
    {"CONNECTED", "HEALTHY", "RUNNING", "GOOD", "MATCHED", "ACTIVE", "PAPER_FILLED"}
)
_WARN_STATES = frozenset(
    {
        "STALE",
        "UNCALIBRATED",
        "WAITING",
        "NOT_YET_VALID",
        "AWAITING_OUTCOME",
        "AWAITING_EVIDENCE",
        "CONFIG GATES OPEN",
    }
)
"""`CONFIG GATES OPEN` is deliberately `warn`, never `good`: all four

execution config flags reading `True` is not proof real `order_send` is
reachable — that also needs the execution adapter actually wired into the
orchestrator, which is a separate, structural fact this card does not
assert (review feedback, third pass)."""
_BAD_STATES = frozenset(
    {
        "DISCONNECTED",
        "HALTED",
        "UNKNOWN",
        "MISMATCHED",
        "DOWN",
        "UNHEALTHY",
        "NOT PROVISIONED",
        "EXPIRED",
        "DISABLED",
        "DEGRADED",
        "GATEWAY_REJECTED",
        "RISK_BLOCKED",
        "SESSION_BLOCKED",
        "POLICY_BLOCKED",
        "PAPER_ORDER_CHECK_BLOCKED",
    }
)
"""Review 1.13 §9's visual-state semantics, as a lookup instead of a chain of

conditionals repeated across the template. `UNKNOWN` is deliberately in the
unsafe bucket, not a neutral one — "the most conservative state should
dominate visually" is the review's own rule. `NO_TRADE`/`PAPER_FILLED`/
`AWAITING_OUTCOME` are deliberately in neither bucket — `NO_TRADE` is a
normal strategy result, not a warning or an error, and `state_class` already
falls back to `"neutral"` for anything unlisted."""


def format_age(delta: timedelta) -> str:
    """A human-scale age string ("1.6s", "15m 4s", "3h 12m") instead of

    Python's raw `timedelta` repr — review 1.13 §5's example is "Last tick:
    1.6s ago", not "15:07:15.600767 ago".
    """
    seconds = max(0.0, delta.total_seconds())
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, seconds = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}m {seconds}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def state_class(value: str | None) -> str:
    """`good` / `warn` / `bad` / `neutral` for a status badge's CSS class."""
    upper = (value or "").upper()
    if upper in _GOOD_STATES:
        return "good"
    if upper in _WARN_STATES:
        return "warn"
    if upper in _BAD_STATES:
        return "bad"
    return "neutral"


def _bar_to_json(bar: Any) -> dict[str, Any]:
    return {
        "open_time_utc": bar.bar.open_time_utc.isoformat(),
        "open": str(bar.bar.open),
        "high": str(bar.bar.high),
        "low": str(bar.bar.low),
        "close": str(bar.bar.close),
        "tick_volume": bar.bar.tick_volume,
        "data_quality": bar.data_quality.value,
        "anomalies": [anomaly.value for anomaly in bar.anomalies],
    }


def _decision_to_json(summary: Any) -> dict[str, Any]:
    return {**asdict(summary), "occurred_at_utc": summary.occurred_at_utc.isoformat()}


def _agent_panel_to_json(panel: Any) -> dict[str, Any] | None:
    if panel is None:
        return None
    payload = asdict(panel)
    payload["agent_id"] = str(panel.agent_id)
    payload["assignment_id"] = str(panel.assignment_id)
    payload["strategy_artifact_id"] = str(panel.strategy_artifact_id)
    payload["valid_from_utc"] = panel.valid_from_utc.isoformat()
    payload["valid_until_utc"] = panel.valid_until_utc.isoformat()
    payload["latest_context_issued_at_utc"] = (
        panel.latest_context_issued_at_utc.isoformat()
        if panel.latest_context_issued_at_utc is not None
        else None
    )
    return payload


def _last_decision_to_json(decision: Any) -> dict[str, Any] | None:
    if decision is None:
        return None
    payload = asdict(decision)
    payload["occurred_at_utc"] = (
        decision.occurred_at_utc.isoformat() if decision.occurred_at_utc is not None else None
    )
    return payload


def _paper_portfolio_to_json(panel: Any) -> dict[str, Any]:
    portfolio = None
    if panel.portfolio is not None:
        portfolio = {
            "balance": str(panel.portfolio.balance),
            "equity": str(panel.portfolio.equity),
            "unrealised_profit": str(panel.portfolio.unrealised_profit),
            "realized_profit": str(panel.portfolio.realized_profit),
            "open_position_count": panel.portfolio.open_position_count,
            "closed_trade_count": panel.portfolio.closed_trade_count,
            "authorized_open_risk_amount": str(panel.portfolio.authorized_open_risk_amount),
            "exact_open_risk_amount": (
                str(panel.portfolio.exact_open_risk_amount)
                if panel.portfolio.exact_open_risk_amount is not None
                else None
            ),
            "exact_open_risk_fraction": (
                str(panel.portfolio.exact_open_risk_fraction)
                if panel.portfolio.exact_open_risk_fraction is not None
                else None
            ),
            "latest_observation_time_utc": panel.portfolio.latest_observation_time_utc,
        }
    return {
        "status": panel.status,
        "detail": panel.detail,
        "portfolio": portfolio,
        "positions": [asdict(row) for row in panel.positions],
    }


def state_to_json(state: DashboardState) -> dict[str, Any]:
    """A JSON-safe rendering of `DashboardState`, for the polling refresh and the chart."""
    payload: dict[str, Any] = {
        "generated_at_utc": state.generated_at_utc.isoformat(),
        "environment": state.environment,
        "environment_badge_label": state.environment_badge_label,
        "expected_broker_server": state.expected_broker_server,
        "expected_currency": state.expected_currency,
        "expected_leverage": state.expected_leverage,
        "canonical_symbol": state.canonical_symbol,
        "timeframe": state.timeframe,
        "reader_health": state.reader_health,
        "mt5_connectivity": state.mt5_connectivity,
        "data_feed_state": state.data_feed_state,
        "tick_count": state.tick_count,
        "bar_count": state.bar_count,
        "bar_gap_count": state.bar_gap_count,
        "bar_anomaly_count": state.bar_anomaly_count,
        "halt": state.halt.to_payload(),
        "uncalibrated_supervisor_checks": list(state.uncalibrated_supervisor_checks),
        "agent_health": state.agent_health,
        "agent_panel": _agent_panel_to_json(state.agent_panel),
        "last_decision": _last_decision_to_json(state.last_decision),
        "pipeline": asdict(state.pipeline),
        "risk_panel": asdict(state.risk_panel),
        "reconciliation": {
            **asdict(state.reconciliation),
            "checked_at_utc": state.reconciliation.checked_at_utc.isoformat(),
        },
        "execution_gate": asdict(state.execution_gate),
        "paper_portfolio": _paper_portfolio_to_json(state.paper_portfolio),
        "broker": {
            "account": (
                {
                    **asdict(state.broker.account),
                    "observed_at_utc": state.broker.account.observed_at_utc.isoformat(),
                }
                if state.broker.account is not None
                else None
            ),
            "positions": [
                {**asdict(p), "opened_at_utc": p.opened_at_utc.isoformat()}
                for p in state.broker.positions
            ],
            "pending_orders": [
                {
                    **asdict(o),
                    "expires_at_utc": o.expires_at_utc.isoformat() if o.expires_at_utc else None,
                }
                for o in state.broker.pending_orders
            ],
        },
        "latest_tick": (
            {
                "event_time_utc": state.latest_tick.event_time_utc.isoformat(),
                "bid": str(state.latest_tick.bid),
                "ask": str(state.latest_tick.ask),
                "spread": str(state.latest_tick.spread),
                "data_quality": state.latest_tick.data_quality.value,
            }
            if state.latest_tick is not None
            else None
        ),
        "latest_bar": (_bar_to_json(state.latest_bar) if state.latest_bar is not None else None),
        "recent_bars": [_bar_to_json(bar) for bar in state.recent_bars],
        "recent_events": [
            {
                "occurred_at_utc": event.occurred_at_utc.isoformat(),
                "component": event.component,
                "event_type": event.event_type,
                "summary": event.summary,
            }
            for event in state.recent_events
        ],
        "paper_lite_activity": [asdict(row) for row in state.paper_lite_activity],
    }
    for key in ("latest_signal", "latest_risk_decision", "latest_supervisor_decision"):
        summary = getattr(state, key)
        payload[key] = _decision_to_json(summary) if summary is not None else None
    return payload


def create_app(
    *,
    engine: Engine,
    guard: AccountGuardConfig,
    risk_config: RiskConfig,
    execution_config: ExecutionConfig,
    live_trading_acknowledged: bool,
    environment: Environment,
    canonical_symbol: str = "EUR/USD",
    timeframe: str = "M5",
    reader_health_path: Path,
    agent_assignment_id: UUID | None = None,
    paper_lite_journal_path: Path | None = None,
    paper_lite_settings_path: Path | None = None,
    expected_spec_version: str | None = None,
) -> FastAPI:
    """Build the dashboard app against one already-open database engine.

    The caller owns the engine's lifecycle (disposal, connection pooling) —
    this function only reads through it, the same convention every other
    read path in this codebase (`MarketDataStore`, `EventJournal`, ...) uses.
    `agent_assignment_id`/`paper_lite_journal_path`/`paper_lite_settings_path`
    are all optional: with none supplied, the Agent panel renders `NOT
    PROVISIONED`, the Last Decision card renders `NO EVIDENCE`, and the paper
    portfolio panel renders `NO EVIDENCE` — never an error.
    """
    app = FastAPI(
        title="Crumblr — read-only",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.filters["state_class"] = state_class
    templates.env.filters["pipeline_stage_class"] = pipeline_stage_class
    templates.env.filters["age"] = format_age

    def _current_state() -> DashboardState:
        return build_state(
            engine=engine,
            guard=guard,
            risk_config=risk_config,
            execution_config=execution_config,
            live_trading_acknowledged=live_trading_acknowledged,
            environment=environment,
            canonical_symbol=canonical_symbol,
            timeframe=timeframe,
            reader_health_path=reader_health_path,
            agent_assignment_id=agent_assignment_id,
            paper_lite_journal_path=paper_lite_journal_path,
            paper_lite_settings_path=paper_lite_settings_path,
            expected_spec_version=expected_spec_version,
        )

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        try:
            state = _current_state()
        except SQLAlchemyError as error:
            # F-043: a database outage must read as "DATABASE UNAVAILABLE",
            # never as an empty-but-otherwise-normal page — those are
            # different claims and this template distinguishes them. The
            # full error goes to the server log only — never to the client,
            # since the exception text can carry the database DSN.
            _log.warning("dashboard.database_unavailable", error=str(error))
            return templates.TemplateResponse(
                request,
                "dashboard.html",
                {
                    "state": None,
                    "state_json": None,
                    "database_error": _DATABASE_UNAVAILABLE_MESSAGE,
                },
                status_code=503,
            )
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {"state": state, "state_json": state_to_json(state), "database_error": None},
        )

    @app.get("/api/state", response_class=JSONResponse)
    def api_state() -> JSONResponse:
        try:
            state = _current_state()
        except SQLAlchemyError as error:
            _log.warning("dashboard.database_unavailable", error=str(error))
            return JSONResponse(
                {"error": "database_unavailable", "detail": _DATABASE_UNAVAILABLE_MESSAGE},
                status_code=503,
            )
        return JSONResponse(state_to_json(state))

    return app
