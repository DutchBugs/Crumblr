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


class TestBarGapsAndAnomalies:
    def test_a_real_gap_between_stored_bars_is_counted(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        store = MarketDataStore(engine)
        first = MarketBar(
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
        # Ten minutes later, not five — a real gap for M5.
        gap_open = NOW.replace(minute=NOW.minute + 10) if NOW.minute < 50 else NOW
        second = first.model_copy(
            update={
                "bar_id": bar_identity(
                    source="test", canonical_symbol=SYMBOL, timeframe="M5", open_time_utc=gap_open
                ),
                "bar": first.bar.model_copy(update={"open_time_utc": gap_open}),
            }
        )
        store.record_bars([first, second])

        body = client(engine, tmp_path / "health.json").get("/api/state").json()

        assert body["bar_gap_count"] == 1


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


class TestF043PresentationStates:
    """Review 1.13 F-043: the state model must distinguish fresh, stale,

    disconnected, missing-snapshot and database-unavailable, not only expose
    raw numbers a template has to interpret.
    """

    def _write_health(self, path: Path, **fields: object) -> None:
        path.write_text(json.dumps(fields), encoding="utf-8")

    def test_a_missing_snapshot_reads_as_unknown_not_healthy(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        body = client(engine, tmp_path / "missing.json").get("/api/state").json()
        assert body["mt5_connectivity"] == "UNKNOWN"
        assert body["data_feed_state"] == "UNKNOWN"

    def test_a_healthy_snapshot_reads_as_connected_and_healthy(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        path = tmp_path / "health.json"
        self._write_health(path, status="HEALTHY", connected=True, reconnect_count=1)

        body = client(engine, path).get("/api/state").json()

        assert body["mt5_connectivity"] == "CONNECTED"
        assert body["data_feed_state"] == "HEALTHY"

    def test_a_stale_snapshot_reads_as_stale_not_healthy(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        path = tmp_path / "health.json"
        self._write_health(path, status="STALE", connected=True, reconnect_count=1)

        body = client(engine, path).get("/api/state").json()

        assert body["data_feed_state"] == "STALE"
        assert body["data_feed_state"] != "HEALTHY"

    def test_a_disconnected_snapshot_reads_as_disconnected_and_down(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        path = tmp_path / "health.json"
        self._write_health(path, status="DISCONNECTED", connected=False, reconnect_count=1)

        body = client(engine, path).get("/api/state").json()

        assert body["mt5_connectivity"] == "DISCONNECTED"
        assert body["data_feed_state"] == "DOWN"

    def test_an_unreachable_database_is_reported_not_silently_empty(self, tmp_path: Path) -> None:
        from crumblr.persistence.engine import create_db_engine

        # Port 1 refuses connections on any reachable host; this must fail
        # fast rather than the test suite hanging on a real timeout.
        unreachable = create_db_engine(
            "postgresql+psycopg://baduser:badpass@localhost:1/nonexistent?connect_timeout=1"
        )
        app = create_app(
            engine=unreachable,
            guard=GUARD,
            environment=Environment.PAPER,
            canonical_symbol=SYMBOL,
            timeframe="M5",
            reader_health_path=tmp_path / "health.json",
        )
        test_client = TestClient(app)

        html_response = test_client.get("/")
        json_response = test_client.get("/api/state")

        assert html_response.status_code == 503
        assert "DATABASE UNAVAILABLE" in html_response.text
        assert json_response.status_code == 503
        assert json_response.json()["error"] == "database_unavailable"
        unreachable.dispose()


class TestF045EnvironmentBadgeIsNotMisreadAsACampaign:
    """Review 1.14 F-045: the top-bar badge must not say `PAPER` while no

    paper-execution campaign has started — this build has no order path at
    all (F-035), so the raw `Environment.PAPER` value implied more than is
    true. `DEMO DATA` is the badge for that config; every other environment
    already says what it means.
    """

    def test_the_paper_environment_badges_as_demo_data_not_paper(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        body = client(engine, tmp_path / "health.json").get("/api/state").json()

        assert body["environment_badge_label"] == "DEMO DATA"
        assert body["environment"] == "paper"

    def test_the_paper_badge_never_appears_on_the_rendered_page(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        response = client(engine, tmp_path / "health.json").get("/")

        assert "DEMO DATA" in response.text
        assert ">PAPER<" not in response.text

    def test_a_non_paper_environment_badges_as_its_own_name(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        app = create_app(
            engine=engine,
            guard=GUARD,
            environment=Environment.SHADOW,
            canonical_symbol=SYMBOL,
            timeframe="M5",
            reader_health_path=tmp_path / "health.json",
        )
        body = TestClient(app).get("/api/state").json()

        assert body["environment_badge_label"] == "SHADOW"


class TestF046HistoricalDataIsNeverMistakenForLive:
    """Review 1.14 F-046: once the data feed is not `HEALTHY`, the EUR/USD

    hero/chart must visibly say so rather than keep rendering old prices as
    though they were current — the chart itself stays visible (historical
    evidence is useful), only its "this is live" implication is withdrawn.
    """

    def test_a_healthy_feed_hides_the_historical_banner(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        path = tmp_path / "health.json"
        path.write_text(
            json.dumps({"status": "HEALTHY", "connected": True, "reconnect_count": 1}),
            encoding="utf-8",
        )

        response = client(engine, path).get("/")

        banner_start = response.text.index('id="hero-historical-banner"')
        banner_tag = response.text[banner_start : banner_start + 200]
        assert "display:none" in banner_tag

    def test_a_missing_snapshot_shows_the_historical_banner_unhidden(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        response = client(engine, tmp_path / "does_not_exist.json").get("/")

        assert response.status_code == 200
        assert "no active live data session" in response.text.lower()
        # An UNKNOWN feed must not carry `style="display:none;"` on the banner.
        banner_start = response.text.index('id="hero-historical-banner"')
        banner_tag = response.text[banner_start : banner_start + 200]
        assert "display:none" not in banner_tag

    def test_a_stale_feed_still_shows_the_last_known_data_with_a_banner(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        path = tmp_path / "health.json"
        path.write_text(
            json.dumps({"status": "STALE", "connected": True, "reconnect_count": 1}),
            encoding="utf-8",
        )
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
        MarketDataStore(engine).record_ticks([tick])

        response = client(engine, path).get("/")

        # The stale price is still rendered (historical evidence is useful) ...
        assert "1.16700" in response.text
        # ... but the banner is visible, not suppressed.
        banner_start = response.text.index('id="hero-historical-banner"')
        banner_tag = response.text[banner_start : banner_start + 200]
        assert "display:none" not in banner_tag


class TestF044DecisionContextIsNeverAmbiguous:
    """Review 1.13 F-044: a journalled decision must never be presented as

    though it belongs to the live MT5 feed shown next to it — nothing in
    this codebase connects LiveReader's real ticks to the replay decision
    pipeline, so any decision found is a replay decision, always labelled so.
    """

    def test_no_decisions_yet_says_no_live_pipeline_active(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        body = client(engine, tmp_path / "health.json").get("/api/state").json()

        assert body["decision_pipeline_label"] == "NO LIVE DECISION PIPELINE ACTIVE"

    def test_a_journalled_decision_is_labelled_as_replay_with_full_context(
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
            environment=Environment.REPLAY,
            source="trading_agent",
        )
        journal.append(signal_event)

        body = client(engine, tmp_path / "health.json").get("/api/state").json()

        assert body["decision_pipeline_label"] == "LATEST REPLAY DECISION"
        signal = body["latest_signal"]
        assert signal is not None
        assert signal["environment"] == "replay"
        assert signal["source"] == "trading_agent"
        assert signal["correlation_id"] == str(correlation_id)
        assert signal["version_label"] == "1"
