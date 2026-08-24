"""The FastAPI app for Dashboard v0. Read-only by construction.

Only `GET` routes are registered — there is no handler anywhere in this
module for `POST`/`PUT`/`PATCH`/`DELETE`, so there is no HALT-reset, no
order button and no risk-config write for a route to even accidentally
expose. `tests/integration/test_dashboard.py::test_no_route_accepts_a_mutation`
checks this holds, not only that it was intended.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import Engine

from crumblr.config import AccountGuardConfig
from crumblr.dashboard.state import DashboardState, build_state
from crumblr.domain.enums import Environment

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


def _state_to_json(state: DashboardState) -> dict[str, Any]:
    """A JSON-safe rendering of `DashboardState`, for the polling refresh."""
    payload: dict[str, Any] = {
        "generated_at_utc": state.generated_at_utc.isoformat(),
        "environment": state.environment,
        "expected_broker_server": state.expected_broker_server,
        "expected_currency": state.expected_currency,
        "canonical_symbol": state.canonical_symbol,
        "timeframe": state.timeframe,
        "reader_health": state.reader_health,
        "tick_count": state.tick_count,
        "bar_count": state.bar_count,
        "halt": state.halt.to_payload(),
        "uncalibrated_supervisor_checks": list(state.uncalibrated_supervisor_checks),
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
        "latest_bar": (
            {
                "open_time_utc": state.latest_bar.bar.open_time_utc.isoformat(),
                "open": str(state.latest_bar.bar.open),
                "high": str(state.latest_bar.bar.high),
                "low": str(state.latest_bar.bar.low),
                "close": str(state.latest_bar.bar.close),
                "tick_volume": state.latest_bar.bar.tick_volume,
                "data_quality": state.latest_bar.data_quality.value,
            }
            if state.latest_bar is not None
            else None
        ),
    }
    for key in ("latest_signal", "latest_risk_decision", "latest_supervisor_decision"):
        summary = getattr(state, key)
        payload[key] = (
            {**asdict(summary), "occurred_at_utc": summary.occurred_at_utc.isoformat()}
            if summary is not None
            else None
        )
    return payload


def create_app(
    *,
    engine: Engine,
    guard: AccountGuardConfig,
    environment: Environment,
    canonical_symbol: str = "EUR/USD",
    timeframe: str = "M5",
    reader_health_path: Path,
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

    def _current_state() -> DashboardState:
        return build_state(
            engine=engine,
            guard=guard,
            environment=environment,
            canonical_symbol=canonical_symbol,
            timeframe=timeframe,
            reader_health_path=reader_health_path,
        )

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        state = _current_state()
        return templates.TemplateResponse(
            request, "dashboard.html", {"state": state, "state_json": _state_to_json(state)}
        )

    @app.get("/api/state", response_class=JSONResponse)
    def api_state() -> dict[str, Any]:
        return _state_to_json(_current_state())

    return app
