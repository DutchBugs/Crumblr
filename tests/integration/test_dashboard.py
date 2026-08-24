"""Dashboard v0 (review 1.9 F-035, review 1.12 §8): read-only, by construction.

Two kinds of claim are under test. The functional one — the page and the JSON
endpoint actually reflect what is in PostgreSQL and in the reader-health
snapshot — and the boundary one, which matters more: nothing registered on
this app can mutate anything, and nothing in the dashboard package reaches
MT5 or a credential. A dashboard that merely happens not to have a HALT
button today is not the same claim as one that cannot grow one by accident.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from crumblr.config import AccountGuardConfig
from crumblr.dashboard.app import create_app
from crumblr.domain.enums import (
    BarOrigin,
    Environment,
    KillSwitchState,
    ReasonCode,
    RiskVerdict,
    Side,
)
from crumblr.domain.events import SignalGenerated, build_event
from crumblr.domain.models import Bar, MarketBar, MarketTick, RiskDecision
from crumblr.persistence.journal import EventJournal
from crumblr.persistence.market_data import MarketDataStore, bar_identity, tick_identity
from crumblr.persistence.safety_state import PostgresSafetyStateStore
from crumblr.risk.safety_state import SafetyState

pytestmark = pytest.mark.integration

GUARD = AccountGuardConfig.model_validate(
    {
        "expected_server": "PepperstoneUK-Demo",
        "expected_login": None,
        "require_demo_account": True,
        "expected_currency": "EUR",
        "expected_leverage": 30,
    }
)
SYMBOL = "EUR/USD"
NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)


def client(engine: Engine, health_path: Path) -> TestClient:
    app = create_app(
        engine=engine,
        guard=GUARD,
        environment=Environment.PAPER,
        canonical_symbol=SYMBOL,
        timeframe="M5",
        reader_health_path=health_path,
    )
    return TestClient(app)


class TestReadOnlyBoundary:
    """Review 1.9 F-035's hard boundary, checked structurally, not by intent."""

    def test_no_route_accepts_a_mutation(self, engine: Engine, tmp_path: Path) -> None:
        app = create_app(
            engine=engine,
            guard=GUARD,
            environment=Environment.PAPER,
            canonical_symbol=SYMBOL,
            timeframe="M5",
            reader_health_path=tmp_path / "health.json",
        )
        mutating = {"POST", "PUT", "PATCH", "DELETE"}
        for route in app.routes:
            methods = getattr(route, "methods", None) or set()
            assert not (methods & mutating), (
                f"route {getattr(route, 'path', route)!r} accepts {methods & mutating}"
            )

    def test_the_dashboard_package_never_imports_metatrader5(self) -> None:
        import ast

        package_dir = Path(__file__).resolve().parents[2] / "src" / "crumblr" / "dashboard"
        for path in package_dir.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                assert not any(name.startswith("MetaTrader5") for name in names), (
                    f"{path} imports MetaTrader5"
                )
                assert not any(name.startswith("crumblr.mt5_gateway") for name in names), (
                    f"{path} imports the MT5 gateway"
                )

    def test_a_post_to_the_index_route_is_refused(self, engine: Engine, tmp_path: Path) -> None:
        response = client(engine, tmp_path / "health.json").post("/")
        assert response.status_code == 405


class TestPageAndApiReflectRealState:
    def test_the_page_carries_the_execution_disabled_banner(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        response = client(engine, tmp_path / "health.json").get("/")
        assert response.status_code == 200
        assert "EXECUTION DISABLED" in response.text
        assert "READ ONLY" in response.text

    def test_no_ticks_or_bars_yet_reads_as_absence_not_an_error(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        response = client(engine, tmp_path / "health.json").get("/api/state")
        assert response.status_code == 200
        body = response.json()
        assert body["latest_tick"] is None
        assert body["latest_bar"] is None
        assert body["tick_count"] == 0

    def test_a_stored_tick_and_bar_appear_in_the_api_state(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        tick = MarketTick(
            tick_id=tick_identity(
                source="test",
                canonical_symbol=SYMBOL,
                event_time_utc=NOW,
                bid=Decimal("1.16700"),
                ask=Decimal("1.16706"),
            ),
            source="test",
            canonical_symbol=SYMBOL,
            broker_symbol="EURUSD",
            event_time_utc=NOW,
            received_time_utc=NOW,
            bid=Decimal("1.16700"),
            ask=Decimal("1.16706"),
        )
        bar = MarketBar(
            bar_id=bar_identity(
                source="test", canonical_symbol=SYMBOL, timeframe="M5", open_time_utc=NOW
            ),
            source="test",
            canonical_symbol=SYMBOL,
            broker_symbol="EURUSD",
            timeframe="M5",
            bar=Bar(
                open_time_utc=NOW,
                open=Decimal("1.16700"),
                high=Decimal("1.16750"),
                low=Decimal("1.16680"),
                close=Decimal("1.16720"),
                tick_volume=42,
            ),
            origin=BarOrigin.BROKER,
            received_time_utc=NOW,
        )
        store = MarketDataStore(engine)
        store.record_ticks([tick])
        store.record_bars([bar])

        body = client(engine, tmp_path / "health.json").get("/api/state").json()

        assert body["latest_tick"]["bid"] == "1.16700"
        assert body["latest_bar"]["close"] == "1.16720"
        assert body["tick_count"] == 1
        assert body["bar_count"] == 1

    def test_a_halted_state_is_reported_not_hidden(self, engine: Engine, tmp_path: Path) -> None:
        PostgresSafetyStateStore(engine).save(
            SafetyState(
                state=KillSwitchState.HALTED,
                reason_codes=(ReasonCode.DAILY_LOSS_LIMIT,),
                recorded_at_utc=NOW,
                tripped_by="risk_engine",
                detail="daily loss limit reached",
            )
        )

        body = client(engine, tmp_path / "health.json").get("/api/state").json()

        assert body["halt"]["state"] == "HALTED"
        assert "daily loss" in body["halt"]["detail"]

    def test_the_latest_decisions_are_read_from_the_journal(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        journal = EventJournal(engine)
        correlation_id = uuid4()
        signal_event = build_event(
            SignalGenerated(
                signal_id=uuid4(),
                snapshot_id=uuid4(),
                symbol=SYMBOL,
                strategy_id="baseline_v1",
                strategy_version="1",
                proposed_side=Side.BUY,
                confidence=0.8,
                feature_snapshot_id=uuid4(),
                feature_set_version="1",
            ),
            correlation_id=correlation_id,
            environment=Environment.PAPER,
            source="trading_agent",
        )
        risk_event = build_event(
            RiskDecision(
                decision_id=uuid4(),
                intent_id=uuid4(),
                verdict=RiskVerdict.BLOCK,
                reason_codes=(ReasonCode.DAILY_LOSS_LIMIT,),
                decided_at_utc=NOW,
                risk_config_version="1",
            ),
            correlation_id=correlation_id,
            environment=Environment.PAPER,
            source="risk_engine",
        )
        journal.append_many([signal_event, risk_event])

        body = client(engine, tmp_path / "health.json").get("/api/state").json()

        assert body["latest_signal"] is not None
        assert "BUY" in body["latest_signal"]["summary"]
        assert body["latest_risk_decision"] is not None
        assert body["latest_risk_decision"]["summary"].startswith("BLOCK")


class TestReaderHealthSnapshot:
    def test_a_missing_snapshot_file_reads_as_absent_not_an_error(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        body = client(engine, tmp_path / "does_not_exist.json").get("/api/state").json()
        assert body["reader_health"] is None

    def test_an_existing_snapshot_is_surfaced_verbatim(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        health_path = tmp_path / "health.json"
        health_path.write_text(
            json.dumps({"status": "HEALTHY", "connected": True, "reconnect_count": 3}),
            encoding="utf-8",
        )

        body = client(engine, health_path).get("/api/state").json()

        assert body["reader_health"]["status"] == "HEALTHY"
        assert body["reader_health"]["reconnect_count"] == 3
