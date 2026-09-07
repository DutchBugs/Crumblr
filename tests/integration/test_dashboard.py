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
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from crumblr.agent_gateway.contracts import (
    AgentIdentity,
    AgentRole,
    AgentStatus,
    ChampionShadowStatus,
    NoTradeDecision,
    TradingAssignment,
)
from crumblr.agent_gateway.events import AgentDecisionEventType
from crumblr.application.broker_state import BrokerStateObservation
from crumblr.config import AccountGuardConfig, ExecutionConfig, RiskConfig
from crumblr.dashboard.app import create_app
from crumblr.domain.enums import (
    BarOrigin,
    Environment,
    KillSwitchState,
    ReasonCode,
    RiskVerdict,
    Side,
    SnapshotCompleteness,
)
from crumblr.domain.events import SignalGenerated, build_event
from crumblr.domain.models import (
    Bar,
    MarketBar,
    MarketTick,
    RiskDecision,
)
from crumblr.persistence.agent_gateway import (
    PostgresAgentDecisionOutcomeStore,
    PostgresAgentIdentityStore,
    PostgresTradingAssignmentStore,
)
from crumblr.persistence.broker_state import BrokerStateStore
from crumblr.persistence.journal import EventJournal
from crumblr.persistence.market_data import MarketDataStore, bar_identity, tick_identity
from crumblr.persistence.safety_state import PostgresSafetyStateStore
from crumblr.risk.safety_state import SafetyState
from tests.conftest import (
    make_broker_account_snapshot,
    make_broker_pending_order_snapshot,
    make_broker_position_snapshot,
)

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
RISK_CONFIG = RiskConfig.model_validate(
    {
        "max_risk_per_trade": "0.02",
        "max_open_risk": "0.03",
        "max_daily_loss": "0.04",
        "max_drawdown": "0.08",
        "max_orders_per_hour": 6,
        "max_open_positions": 10,
        "min_stop_distance_points": 50,
    }
)
# All four named execution gates default `False` -- matches every shipped config.
EXECUTION_CONFIG = ExecutionConfig.model_validate(
    {
        "max_spread_points": 30,
        "max_market_data_age_ms": 5000,
        "order_timeout_ms": 5000,
        "max_slippage_points": 20,
    }
)
SYMBOL = "EUR/USD"
NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)


def client(
    engine: Engine,
    health_path: Path,
    *,
    agent_assignment_id: UUID | None = None,
    paper_lite_journal_path: Path | None = None,
    execution_config: ExecutionConfig = EXECUTION_CONFIG,
    live_trading_acknowledged: bool = False,
) -> TestClient:
    app = create_app(
        engine=engine,
        guard=GUARD,
        risk_config=RISK_CONFIG,
        execution_config=execution_config,
        live_trading_acknowledged=live_trading_acknowledged,
        environment=Environment.PAPER,
        canonical_symbol=SYMBOL,
        timeframe="M5",
        reader_health_path=health_path,
        agent_assignment_id=agent_assignment_id,
        paper_lite_journal_path=paper_lite_journal_path,
    )
    return TestClient(app)


class TestReadOnlyBoundary:
    """Review 1.9 F-035's hard boundary, checked structurally, not by intent."""

    def test_no_route_accepts_a_mutation(self, engine: Engine, tmp_path: Path) -> None:
        app = create_app(
            engine=engine,
            guard=GUARD,
            risk_config=RISK_CONFIG,
            execution_config=EXECUTION_CONFIG,
            live_trading_acknowledged=False,
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
            risk_config=RISK_CONFIG,
            execution_config=EXECUTION_CONFIG,
            live_trading_acknowledged=False,
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
        # No secrets: the connection string above carries a fake but
        # credential-shaped user/password — neither must ever reach a client,
        # only the fixed, generic message may.
        assert "baduser" not in html_response.text
        assert "badpass" not in html_response.text
        assert "baduser" not in json_response.text
        assert "badpass" not in json_response.text
        assert json_response.json()["detail"] == "database unavailable — see server logs"
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
            risk_config=RISK_CONFIG,
            execution_config=EXECUTION_CONFIG,
            live_trading_acknowledged=False,
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

    though it belongs to the live MT5 feed shown next to it. The dashboard
    refresh (2026-09-06) replaced the old fixed `decision_pipeline_label`
    ("LATEST REPLAY DECISION" / "NO LIVE DECISION PIPELINE ACTIVE") with the
    real current pipeline (`agent_health`/`last_decision`) — this class now
    checks the one part of the original concern that still applies: a
    journalled decision must still carry its own environment/source context,
    never presented as though it were unqualified live state.
    """

    def test_a_journalled_decision_still_carries_its_full_context(
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

        signal = body["latest_signal"]
        assert signal is not None
        assert signal["environment"] == "replay"
        assert signal["source"] == "trading_agent"
        assert signal["correlation_id"] == str(correlation_id)
        assert signal["version_label"] == "1"


def _register_active_assignment(engine: Engine) -> UUID:
    """Registers a real `TradingAssignment` + `AgentIdentity`, valid right

    now (real wall-clock time — `build_state()`'s own `now` defaults to
    `utc_now()`, not the fixed `NOW` constant this file otherwise uses),
    and returns its `assignment_id` for `client(..., agent_assignment_id=...)`.
    """
    real_now = datetime.now(UTC)
    assignment_id = uuid4()
    agent_id = uuid4()
    PostgresTradingAssignmentStore(engine).register(
        TradingAssignment(
            assignment_id=assignment_id,
            assignment_version="assignment-v1",
            allowed_agent_id=agent_id,
            canonical_symbol=SYMBOL,
            timeframe="M5",
            strategy_artifact_id=uuid4(),
            strategy_artifact_hash="artifact-hash-v1",
            valid_from_utc=real_now - timedelta(days=1),
            valid_until_utc=real_now + timedelta(days=30),
            max_proposals_per_hour=10,
            allowed_risk_fraction_min=Decimal("0.001"),
            allowed_risk_fraction_max=Decimal("0.01"),
            required_evidence_fields=(),
            supervisor_policy_version="supervisor-policy-v1",
            environment=Environment.PAPER,
            champion_shadow_status=ChampionShadowStatus.SHADOW,
        )
    )
    PostgresAgentIdentityStore(engine).register(
        AgentIdentity(
            agent_id=agent_id,
            role=AgentRole.TRADER,
            runtime_version="toy-agent-v1",
            service_identity="spiffe://crumblr/agents/toy",
            status=AgentStatus.ACTIVE,
            registered_at_utc=real_now,
        )
    )
    return assignment_id


class TestAgentPanelRendersInTheUi:
    def test_no_assignment_configured_shows_not_provisioned(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        response = client(engine, tmp_path / "health.json").get("/")
        body = client(engine, tmp_path / "health.json").get("/api/state").json()

        assert "NOT PROVISIONED" in response.text
        assert body["agent_health"] == "NOT PROVISIONED"
        assert body["agent_panel"] is None

    def test_a_provisioned_assignment_renders_its_real_fields(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        assignment_id = _register_active_assignment(engine)

        response = client(engine, tmp_path / "health.json", agent_assignment_id=assignment_id).get(
            "/"
        )
        body = (
            client(engine, tmp_path / "health.json", agent_assignment_id=assignment_id)
            .get("/api/state")
            .json()
        )

        assert body["agent_panel"] is not None
        assert body["agent_panel"]["assignment_id"] == str(assignment_id)
        assert body["agent_panel"]["assignment_status"] == "ACTIVE"
        assert body["agent_panel"]["strategy_artifact_hash"] == "artifact-hash-v1"
        assert body["agent_panel"]["runtime_version"] == "toy-agent-v1"
        assert body["agent_health"] == "WAITING"
        # Excludes the trailing <script> block: the browser's stateClass()
        # (added in commit 1d0687e) legitimately contains "NOT PROVISIONED"
        # as a literal JS object key mirroring app.py's own BAD-state set,
        # present in every page regardless of the server-rendered state --
        # this assertion is about the *rendered* markup only.
        rendered_markup = response.text.split("<script", 1)[0]
        assert "NOT PROVISIONED" not in rendered_markup
        assert "ACTIVE" in response.text


class TestRiskPanelRendersInTheUi:
    def test_configured_limits_reflect_the_real_config(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        body = client(engine, tmp_path / "health.json").get("/api/state").json()

        assert body["risk_panel"]["max_risk_per_trade"] == "0.02"
        assert body["risk_panel"]["max_open_risk"] == "0.03"
        assert body["risk_panel"]["max_daily_loss"] == "0.04"
        assert body["risk_panel"]["max_drawdown"] == "0.08"

    def test_no_session_recorded_yet_shows_dashes_not_zero(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        body = client(engine, tmp_path / "health.json").get("/api/state").json()

        assert body["risk_panel"]["current_open_risk"] == "—"
        assert body["risk_panel"]["current_daily_loss"] == "—"
        assert body["risk_panel"]["current_drawdown"] == "—"


class TestExecutionGateIsReallyDerivedNotHardcoded:
    """Review feedback, second pass: the Execution card must be derived

    from the real execution-gate config, not a fixed template literal."""

    def test_default_config_shows_disabled_with_the_real_closed_gates(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        response = client(engine, tmp_path / "health.json").get("/")
        body = client(engine, tmp_path / "health.json").get("/api/state").json()

        assert "no order_send path exists in this build" in response.text
        assert body["execution_gate"]["disabled"] is True
        assert set(body["execution_gate"]["closed_gates"]) == {
            "submission_enabled",
            "feedback_2_0_approved",
            "flatten_submission_enabled",
            "live_trading_acknowledged",
        }
        assert "DISABLED" in response.text

    def test_all_four_gates_open_no_longer_says_disabled(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        all_open = ExecutionConfig.model_validate(
            {
                "max_spread_points": 30,
                "max_market_data_age_ms": 5000,
                "order_timeout_ms": 5000,
                "max_slippage_points": 20,
                "submission_enabled": True,
                "feedback_2_0_approved": True,
                "flatten_submission_enabled": True,
            }
        )

        body = (
            client(
                engine,
                tmp_path / "health.json",
                execution_config=all_open,
                live_trading_acknowledged=True,
            )
            .get("/api/state")
            .json()
        )

        assert body["execution_gate"]["disabled"] is False
        assert body["execution_gate"]["closed_gates"] == []

    def test_all_four_gates_open_renders_neutral_not_green(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        """Review feedback (third pass): all four config gates reading

        `True` is not proof real `order_send` is reachable (the execution
        adapter's own wiring is a separate, structural fact) — the card
        must render as `warn`, never `good`/green."""
        all_open = ExecutionConfig.model_validate(
            {
                "max_spread_points": 30,
                "max_market_data_age_ms": 5000,
                "order_timeout_ms": 5000,
                "max_slippage_points": 20,
                "submission_enabled": True,
                "feedback_2_0_approved": True,
                "flatten_submission_enabled": True,
            }
        )

        response = client(
            engine,
            tmp_path / "health.json",
            execution_config=all_open,
            live_trading_acknowledged=True,
        ).get("/")

        assert "CONFIG GATES OPEN" in response.text
        card_start = response.text.index('<div class="label">Execution</div>')
        card = response.text[card_start : card_start + 300]
        assert "state good" not in card
        assert "dot good" not in card
        assert "state warn" in card
        assert "dot warn" in card

    def test_state_class_never_maps_config_gates_open_to_good(self) -> None:
        from crumblr.dashboard.app import state_class

        assert state_class("CONFIG GATES OPEN") == "warn"
        assert state_class("DISABLED") == "bad"


class TestPaperLiteJournalActivity:
    def test_no_journal_path_configured_shows_empty_not_an_error(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        response = client(engine, tmp_path / "health.json")

        body = response.get("/api/state").json()

        assert body["paper_lite_activity"] == []

    def test_an_audit_fact_in_the_journal_appears_in_the_api_state(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        journal_path = tmp_path / "paper_lite.journal.jsonl"
        journal_path.write_text(
            json.dumps(
                {
                    "sequence": 0,
                    "event_type": "AUDIT_FACT",
                    "payload": {
                        "fact": "PAPER_LITE_SESSION_BLOCKED",
                        "correlation_id": str(uuid4()),
                        "detail": "CLOSED",
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )

        body = (
            client(engine, tmp_path / "health.json", paper_lite_journal_path=journal_path)
            .get("/api/state")
            .json()
        )

        assert len(body["paper_lite_activity"]) == 1
        assert body["paper_lite_activity"][0]["event_type"] == "PAPER_LITE_SESSION_BLOCKED"
        assert body["paper_lite_activity"][0]["detail"] == "CLOSED"


class TestCorruptJournalDegradesVisibly:
    """Review feedback, second pass: an unreadable PAPER_LITE journal line

    must never be silently skipped and then presented as a confident
    outcome — it must render `DEGRADED`, visibly, even when a real,
    otherwise-complete outcome exists underneath it."""

    def test_a_corrupt_line_reports_degraded_even_with_a_real_outcome_present(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        assignment_id = _register_active_assignment(engine)
        journal_path = tmp_path / "paper_lite.journal.jsonl"
        journal_path.write_text(
            '{"sequence": 0, "event_type": "PORTFOLIO_CREATED", "payload": {}}\n'
            '{"sequence": 1, "event_type": "AUDIT_FACT", "payl',
            encoding="utf-8",
        )

        body = (
            client(
                engine,
                tmp_path / "health.json",
                agent_assignment_id=assignment_id,
                paper_lite_journal_path=journal_path,
            )
            .get("/api/state")
            .json()
        )

        assert body["last_decision"] is not None
        assert body["last_decision"]["platform_outcome"] == "DEGRADED"
        assert body["agent_health"] == "UNKNOWN"


class TestNoSecretsAnywhereInTheResponse:
    """The work order's own explicit acceptance test: no secret-shaped

    string anywhere in `/api/state` or the rendered HTML, even once real
    Agent/PAPER_LITE data is present."""

    # "credential" is deliberately excluded from the HTML check: the page's
    # own footer honestly states "no credentials" as a reassurance, which
    # would otherwise false-positive this check — the API response (pure
    # data, no prose) is held to the stricter standard instead.
    _API_FORBIDDEN_SUBSTRINGS = ("password", "credential", "bearer ", "secret")
    _PAGE_FORBIDDEN_SUBSTRINGS = ("password", "bearer ", "secret")

    def test_with_a_real_provisioned_agent(self, engine: Engine, tmp_path: Path) -> None:
        assignment_id = _register_active_assignment(engine)

        api_response = client(
            engine, tmp_path / "health.json", agent_assignment_id=assignment_id
        ).get("/api/state")
        page_response = client(
            engine, tmp_path / "health.json", agent_assignment_id=assignment_id
        ).get("/")

        api_text_lower = api_response.text.lower()
        page_text_lower = page_response.text.lower()
        for forbidden in self._API_FORBIDDEN_SUBSTRINGS:
            assert forbidden not in api_text_lower, f"{forbidden!r} leaked into /api/state"
        for forbidden in self._PAGE_FORBIDDEN_SUBSTRINGS:
            assert forbidden not in page_text_lower, f"{forbidden!r} leaked into the rendered page"


class TestBrokerAccountPositionsAndPendingOrdersRenderInTheUi:
    """Work order §43 items 2-5: real PostgreSQL, only the latest snapshot's
    own rows ever render, and an incomplete set never renders as confirmed
    empty."""

    def test_no_broker_observation_yet_reads_as_no_evidence(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        response = client(engine, tmp_path / "health.json").get("/api/state")

        assert response.json()["broker"]["account"] is None
        assert response.json()["broker"]["positions"] == []
        assert response.json()["broker"]["pending_orders"] == []

    def test_account_and_terminal_trade_allowed_are_shown_as_two_independent_facts(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        """Owner review of commit 1d0687e: `account_trade_allowed` and
        `terminal_trade_allowed` are two genuinely different broker facts
        and must never be collapsed into one "Trade allowed" line -- an
        account that can trade on a terminal that itself cannot must show
        that distinction, not silently agree with only one of the two."""
        store = BrokerStateStore(engine)
        account = make_broker_account_snapshot(
            account_trade_allowed=True, terminal_trade_allowed=False
        )
        store.record(BrokerStateObservation(account=account, positions=(), pending_orders=()))

        api = client(engine, tmp_path / "health.json").get("/api/state").json()
        page = client(engine, tmp_path / "health.json").get("/").text
        rendered_markup = page.split("<script", 1)[0]

        assert api["broker"]["account"]["account_trade_allowed"] is True
        assert api["broker"]["account"]["terminal_trade_allowed"] is False
        assert "Account trade allowed" in rendered_markup
        assert "Terminal trade allowed" in rendered_markup
        # Both rows present with their own distinct YES/NO -- not merged
        # into a single "Trade allowed: YES" that would hide the mismatch.
        account_idx = rendered_markup.index("Account trade allowed")
        terminal_idx = rendered_markup.index("Terminal trade allowed")
        account_row = rendered_markup[account_idx : account_idx + 200]
        terminal_row = rendered_markup[terminal_idx : terminal_idx + 200]
        assert ">YES<" in account_row
        assert ">NO<" in terminal_row

    def test_a_terminal_trade_allowed_of_none_reads_as_unknown_never_yes(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        store = BrokerStateStore(engine)
        account = make_broker_account_snapshot(
            account_trade_allowed=True, terminal_trade_allowed=None
        )
        store.record(BrokerStateObservation(account=account, positions=(), pending_orders=()))

        api = client(engine, tmp_path / "health.json").get("/api/state").json()
        page = client(engine, tmp_path / "health.json").get("/").text
        rendered_markup = page.split("<script", 1)[0]

        assert api["broker"]["account"]["terminal_trade_allowed"] is None
        terminal_idx = rendered_markup.index("Terminal trade allowed")
        terminal_row = rendered_markup[terminal_idx : terminal_idx + 200]
        assert ">UNKNOWN<" in terminal_row
        assert ">YES<" not in terminal_row

    def test_only_the_latest_of_two_snapshots_is_current(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        store = BrokerStateStore(engine)
        first = make_broker_account_snapshot(balance=Decimal("1000"))
        store.record(BrokerStateObservation(account=first, positions=(), pending_orders=()))
        second = make_broker_account_snapshot(balance=Decimal("2000"))
        store.record(BrokerStateObservation(account=second, positions=(), pending_orders=()))

        response = client(engine, tmp_path / "health.json").get("/api/state")

        assert response.json()["broker"]["account"]["balance"] == "2000"

    def test_a_complete_snapshot_with_positions_and_pending_orders_renders_both(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        store = BrokerStateStore(engine)
        account = make_broker_account_snapshot(
            position_set_state=SnapshotCompleteness.COMPLETE,
            pending_order_set_state=SnapshotCompleteness.COMPLETE,
        )
        position = make_broker_position_snapshot(account.snapshot_id, ticket=555)
        order = make_broker_pending_order_snapshot(account.snapshot_id, order_id=777)
        store.record(
            BrokerStateObservation(account=account, positions=(position,), pending_orders=(order,))
        )

        api = client(engine, tmp_path / "health.json").get("/api/state").json()
        page = client(engine, tmp_path / "health.json").get("/").text

        assert len(api["broker"]["positions"]) == 1
        assert len(api["broker"]["pending_orders"]) == 1
        assert "EUR/USD" in page
        assert "555" not in page  # ticket is not itself a displayed field
        assert "777" in page  # order_id is displayed

    def test_zero_rows_with_a_complete_set_is_confirmed_empty_not_hidden(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        store = BrokerStateStore(engine)
        account = make_broker_account_snapshot(
            position_set_state=SnapshotCompleteness.COMPLETE,
            pending_order_set_state=SnapshotCompleteness.COMPLETE,
        )
        store.record(BrokerStateObservation(account=account, positions=(), pending_orders=()))

        page = client(engine, tmp_path / "health.json").get("/").text

        assert "0 open positions" in page
        assert "No pending orders observed" in page

    def test_a_failed_position_set_never_renders_as_confirmed_empty_in_the_page(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        store = BrokerStateStore(engine)
        account = make_broker_account_snapshot(
            position_set_state=SnapshotCompleteness.FAILED,
            pending_order_set_state=SnapshotCompleteness.COMPLETE,
        )
        store.record(BrokerStateObservation(account=account, positions=(), pending_orders=()))

        api = client(engine, tmp_path / "health.json").get("/api/state").json()
        page = client(engine, tmp_path / "health.json").get("/").text
        # The JS refresh logic (tests/js/dashboard_broker_refresh_test.mjs)
        # legitimately contains "0 open positions" as a string literal for
        # the COMPLETE-and-empty case, in every page regardless of the
        # server-rendered state -- excluding the trailing <script> block
        # keeps this assertion about the *rendered* markup only.
        rendered_markup = page.split("<script", 1)[0]

        assert api["broker"]["account"]["position_set_state"] == "FAILED"
        assert api["broker"]["positions"] == []
        assert "absence cannot be trusted" in rendered_markup


class TestDecisionPipelineRestagingRendersRealEvidence:
    """Work order §16, Slice 5: the 8-stage decision pipeline is restaged
    from `dashboard.pipeline.build_pipeline_view`, driven by the same
    `agent_panel`/`last_decision` evidence as the Agent panel and the Last
    Decision card -- never a second, independently-computed read of that
    evidence. The pre-restaging pipeline section was rendered once
    server-side and never refreshed on poll; `/api/state` must now carry the
    same `pipeline` object the page itself was built from.
    """

    def test_no_assignment_reads_unknown_everywhere_in_the_api_and_the_page(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        body = client(engine, tmp_path / "health.json").get("/api/state").json()
        page = client(engine, tmp_path / "health.json").get("/").text
        rendered_markup = page.split("<script", 1)[0]

        assert body["pipeline"]["market"] == "UNKNOWN"
        assert body["pipeline"]["agent"] == "UNKNOWN"
        assert body["pipeline"]["risk"] == "N/A"
        assert "Decision pipeline" in rendered_markup

    def test_a_real_session_blocked_audit_fact_places_the_block_before_core_risk(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        # `build_last_decision` never infers an outcome from the journal
        # alone -- it only reports one for the outcome_id this exact
        # assignment actually claimed (see agent_state.py's identity-binding
        # discipline), so the fixture must claim a real NO_TRADE outcome,
        # accept it, and correlate the audit fact to that same outcome_id --
        # a bare journal line with an unrelated correlation_id (as
        # TestPaperLiteJournalActivity uses, which only checks the raw
        # activity feed, never `last_decision`) is not enough here.
        assignment_id = _register_active_assignment(engine)
        registered_assignment = PostgresTradingAssignmentStore(engine).current(assignment_id)
        assert registered_assignment is not None
        decision = NoTradeDecision(
            decision_id=uuid4(),
            agent_id=registered_assignment.allowed_agent_id,
            assignment_id=assignment_id,
            context_hash="context-hash-abc",
            reason_codes=(),
            decided_at_utc=NOW,
        )
        outcome_store = PostgresAgentDecisionOutcomeStore(engine)
        outcome_store.claim_no_trade(decision, now=NOW)
        outcome_store.append_event(
            outcome_id=decision.decision_id,
            event_type=AgentDecisionEventType.ACCEPTED,
            occurred_at_utc=NOW,
        )

        journal_path = tmp_path / "paper_lite.journal.jsonl"
        journal_path.write_text(
            json.dumps(
                {
                    "sequence": 0,
                    "event_type": "AUDIT_FACT",
                    "payload": {
                        "fact": "PAPER_LITE_SESSION_BLOCKED",
                        "correlation_id": str(decision.decision_id),
                        "detail": "CLOSED",
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )

        api_client = client(
            engine,
            tmp_path / "health.json",
            agent_assignment_id=assignment_id,
            paper_lite_journal_path=journal_path,
        )
        body = api_client.get("/api/state").json()
        page = api_client.get("/").text
        rendered_markup = page.split("<script", 1)[0]

        assert body["last_decision"]["platform_outcome"] == "SESSION_BLOCKED"
        assert body["pipeline"]["agent"] == "RESPONSE RECEIVED"
        assert body["pipeline"]["gateway"] == "ACCEPTED"
        assert body["pipeline"]["risk"] == "NOT REACHED"
        assert body["pipeline"]["policy"] == "NOT REACHED"
        assert body["pipeline"]["supervisor"] == "NOT REACHED"
        assert body["pipeline"]["paper"] == "NOT REACHED"
        assert "RESPONSE RECEIVED" in rendered_markup
        assert "NOT REACHED" in rendered_markup

    def test_a_corrupt_journal_line_reaches_the_pipeline_as_degraded_too(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        # The DEGRADED-blanks-market/context branch itself is already
        # covered precisely at the unit level (test_dashboard_pipeline.py::
        # test_degraded_journal_reads_as_unknown_everywhere_not_a_confident_answer,
        # with a context genuinely present to isolate the override). This
        # only checks the wiring: a real DEGRADED `last_decision` from
        # `build_state()` actually reaches `/api/state`'s `pipeline` object,
        # not a second, independent read of the journal.
        assignment_id = _register_active_assignment(engine)
        journal_path = tmp_path / "paper_lite.journal.jsonl"
        journal_path.write_text(
            '{"sequence": 0, "event_type": "PORTFOLIO_CREATED", "payload": {}}\n'
            '{"sequence": 1, "event_type": "AUDIT_FACT", "payl',
            encoding="utf-8",
        )

        body = (
            client(
                engine,
                tmp_path / "health.json",
                agent_assignment_id=assignment_id,
                paper_lite_journal_path=journal_path,
            )
            .get("/api/state")
            .json()
        )

        assert body["last_decision"]["platform_outcome"] == "DEGRADED"
        assert body["pipeline"]["market"] == "UNKNOWN"
        assert body["pipeline"]["context"] == "UNKNOWN"
        assert body["pipeline"]["agent"] == "UNKNOWN"
