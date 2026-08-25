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

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from crumblr.config import AccountGuardConfig
from crumblr.dashboard.state import DashboardState, build_state
from crumblr.domain.enums import Environment
from crumblr.observability.logging import get_logger

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

_log = get_logger("dashboard")

_GOOD_STATES = frozenset({"CONNECTED", "HEALTHY", "RUNNING", "GOOD", "MATCHED"})
_WARN_STATES = frozenset({"STALE", "UNCALIBRATED", "DEGRADED"})
_BAD_STATES = frozenset({"DISCONNECTED", "HALTED", "UNKNOWN", "MISMATCHED", "DOWN", "UNHEALTHY"})
"""Review 1.13 §9's visual-state semantics, as a lookup instead of a chain of

conditionals repeated across the template. `UNKNOWN` is deliberately in the
unsafe bucket, not a neutral one — "the most conservative state should
dominate visually" is the review's own rule."""


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


def state_to_json(state: DashboardState) -> dict[str, Any]:
    """A JSON-safe rendering of `DashboardState`, for the polling refresh and the chart."""
    payload: dict[str, Any] = {
        "generated_at_utc": state.generated_at_utc.isoformat(),
        "environment": state.environment,
        "environment_badge_label": state.environment_badge_label,
        "milestone_label": state.milestone_label,
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
        "decision_pipeline_label": state.decision_pipeline_label,
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
    }
    for key in ("latest_signal", "latest_risk_decision", "latest_supervisor_decision"):
        summary = getattr(state, key)
        payload[key] = _decision_to_json(summary) if summary is not None else None
    return payload


def create_app(
    *,
    engine: Engine,
    guard: AccountGuardConfig,
    environment: Environment,
    canonical_symbol: str = "EUR/USD",
    timeframe: str = "M5",
    reader_health_path: Path,
    milestone_label: str = "M1 PASSED",
) -> FastAPI:
    """Build the dashboard app against one already-open database engine.

    The caller owns the engine's lifecycle (disposal, connection pooling) —
    this function only reads through it, the same convention every other
    read path in this codebase (`MarketDataStore`, `EventJournal`, ...) uses.
    """
    app = FastAPI(
        title="Crumblr — read-only",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.filters["state_class"] = state_class
    templates.env.filters["age"] = format_age

    def _current_state() -> DashboardState:
        return build_state(
            engine=engine,
            guard=guard,
            environment=environment,
            canonical_symbol=canonical_symbol,
            timeframe=timeframe,
            reader_health_path=reader_health_path,
            milestone_label=milestone_label,
        )

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        try:
            state = _current_state()
        except SQLAlchemyError as error:
            # F-043: a database outage must read as "DATABASE UNAVAILABLE",
            # never as an empty-but-otherwise-normal page — those are
            # different claims and this template distinguishes them.
            _log.warning("dashboard.database_unavailable", error=str(error))
            return templates.TemplateResponse(
                request,
                "dashboard.html",
                {"state": None, "state_json": None, "database_error": str(error)},
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
                {"error": "database_unavailable", "detail": str(error)}, status_code=503
            )
        return JSONResponse(state_to_json(state))

    return app
