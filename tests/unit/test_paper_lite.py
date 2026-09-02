from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pytest

import crumblr.application.paper_lite as paper_lite_module
from crumblr.agent_gateway.contracts import (
    AgentIdentity,
    AgentRole,
    AgentStatus,
    ChampionShadowStatus,
    NoTradeDecision,
    TradeProposal,
    TradingAssignment,
)
from crumblr.agent_gateway.gateway import AgentGateway
from crumblr.agent_gateway.market_context import AgentMarketContextV1
from crumblr.agent_gateway.stores import (
    InMemoryAgentCredentialStore,
    InMemoryAgentDecisionOutcomeStore,
    InMemoryAgentIdentityStore,
    InMemoryDecisionContextBundleStore,
    InMemoryFeatureEvidenceStore,
    InMemoryTradingAssignmentStore,
)
from crumblr.application.paper_lite import (
    PaperLiteConfigurationError,
    PaperLiteOrchestrator,
    PaperLiteOutcomeType,
    PaperLiteSafetyError,
    PaperLiteSessionPhase,
    PaperLiteSettings,
    paper_lite_session_phase,
)
from crumblr.application.recording import NullRecorder
from crumblr.config import PlatformConfig
from crumblr.domain.enums import EntryType, Environment, IncidentStatus, ReasonCode, Side
from crumblr.domain.models import InstrumentSpec, MarketSnapshot
from crumblr.persistence.paper_lite import (
    SUPERVISOR_SKIPPED_PAPER_MODE,
    DurablePaperBroker,
    PaperJournalEventType,
)
from crumblr.risk.kill_switch import KillSwitch
from crumblr.risk.session import InMemoryRiskSessionStore, RiskSessionState
from crumblr.trading_agent.sessions import trading_day
from tests.conftest import FIXED_NOW, make_instrument_spec, make_snapshot, paper_config_payload

AGENT_ID = UUID("5c056548-2eaf-4fb3-b41b-6c0dce98d905")
ASSIGNMENT_ID = UUID("7d18f133-f0e4-4d21-a490-11368c6f4daf")
CREDENTIAL = "paper-lite-test-credential"


def settings(path: Path) -> PaperLiteSettings:
    return PaperLiteSettings(
        mode="PAPER_LITE",
        starting_balance=Decimal("10000"),
        journal_path=path,
        safety_latch_path=path.with_suffix(".safety.json"),
        account_currency="EUR",
        leverage=30,
        operational_max_open_positions=20,
        max_risk_per_trade=Decimal("0.02"),
        max_open_risk=Decimal("0.03"),
        max_daily_loss=Decimal("0.04"),
        max_drawdown=Decimal("0.08"),
        friday_last_entry_minutes_before_close=15,
        friday_flatten_minutes_before_close=5,
    )


def assignment() -> TradingAssignment:
    return TradingAssignment(
        assignment_id=ASSIGNMENT_ID,
        assignment_version="paper-lite-v1",
        allowed_agent_id=AGENT_ID,
        canonical_symbol="EUR/USD",
        timeframe="M1",
        strategy_artifact_id=UUID("923fc10b-ae1d-4e02-a407-2bebfceee2e3"),
        strategy_artifact_hash="toy-artifact-v1",
        valid_from_utc=FIXED_NOW - timedelta(days=1),
        valid_until_utc=FIXED_NOW + timedelta(days=30),
        max_proposals_per_hour=20,
        allowed_risk_fraction_min=Decimal("0.001"),
        allowed_risk_fraction_max=Decimal("0.02"),
        required_evidence_fields=(),
        supervisor_policy_version="paper-lite-skip-v1",
        environment=Environment.PAPER,
        champion_shadow_status=ChampionShadowStatus.SHADOW,
    )


@dataclass
class ToyAgent:
    agent_id: UUID = AGENT_ID
    credential_secret: str = CREDENTIAL
    directional: bool = True

    def decide(self, context: AgentMarketContextV1) -> TradeProposal | NoTradeDecision:
        decision_id = uuid5(NAMESPACE_URL, f"toy:{context.provenance.content_hash}")
        if not self.directional:
            return NoTradeDecision(
                decision_id=decision_id,
                agent_id=self.agent_id,
                assignment_id=context.provenance.assignment_id,
                context_hash=context.provenance.content_hash,
                reason_codes=("TOY_NO_TRADE",),
                decided_at_utc=context.provenance.issued_at_utc,
            )
        return TradeProposal(
            proposal_id=decision_id,
            agent_id=self.agent_id,
            assignment_id=context.provenance.assignment_id,
            context_hash=context.provenance.content_hash,
            strategy_artifact_hash=context.provenance.strategy_artifact_hash,
            side=Side.BUY,
            entry_type=EntryType.MARKET,
            reference_price=context.market.ask,
            stop_loss_price=context.market.ask - Decimal("0.00200"),
            take_profit_price=context.market.ask + Decimal("0.00400"),
            confidence=1.0,
            requested_risk_fraction=Decimal("0.02"),
            reason_codes=("TOY_BREAKOUT_VOCABULARY",),
            evidence_refs=(),
            submitted_at_utc=context.provenance.issued_at_utc,
            expires_at_utc=context.provenance.expires_at_utc,
        )


@dataclass
class Fixture:
    path: Path
    directional: bool = True
    session_store: InMemoryRiskSessionStore = field(default_factory=InMemoryRiskSessionStore)

    def build(self) -> tuple[PaperLiteOrchestrator, DurablePaperBroker]:
        lite_settings = settings(self.path)
        base = PlatformConfig.model_validate(paper_config_payload())
        config = lite_settings.platform_config(base)
        agent = ToyAgent(directional=self.directional)
        gateway = AgentGateway(
            identities=InMemoryAgentIdentityStore(),
            credentials=InMemoryAgentCredentialStore(),
            assignments=InMemoryTradingAssignmentStore(),
            contexts=InMemoryDecisionContextBundleStore(),
            outcomes=InMemoryAgentDecisionOutcomeStore(),
            feature_evidence=InMemoryFeatureEvidenceStore(),
        )
        gateway.register_identity(
            AgentIdentity(
                agent_id=AGENT_ID,
                role=AgentRole.TRADER,
                runtime_version="toy-v1",
                service_identity="toy-local",
                status=AgentStatus.ACTIVE,
                registered_at_utc=FIXED_NOW,
            ),
            credential_secret=CREDENTIAL,
        )
        assigned = assignment()
        gateway.issue_assignment(assigned)
        spec = make_instrument_spec()
        broker = DurablePaperBroker(
            self.path,
            spec,
            starting_balance=lite_settings.starting_balance,
        )
        orchestrator = PaperLiteOrchestrator(
            config,
            settings=lite_settings,
            assignment=assigned,
            agent=agent,
            gateway=gateway,
            broker=broker,
            recorder=NullRecorder(),
            session_store=self.session_store,
            kill_switch=KillSwitch(),
            code_commit="test-commit",
            clock=lambda: FIXED_NOW,
        )
        return orchestrator, broker


def compatible_snapshot(**overrides: Any) -> tuple[MarketSnapshot, InstrumentSpec]:
    spec = make_instrument_spec()
    return make_snapshot(symbol_spec_version=spec.spec_version, **overrides), spec


def process_clear(
    orchestrator: PaperLiteOrchestrator,
    snapshot: MarketSnapshot,
    spec: InstrumentSpec,
) -> Any:
    return orchestrator.process(snapshot, spec, incident_status=IncidentStatus.CLEAR)


class TestTypedPaperOnlyBoundary:
    def test_module_has_no_real_execution_adapter_import(self) -> None:
        source = inspect.getsource(paper_lite_module)
        assert "mt5_gateway.execution" not in source
        assert "OrderCheckMt5Gateway" not in source

    def test_any_real_submission_flag_refuses_startup(self, tmp_path: Path) -> None:
        lite_settings = settings(tmp_path / "paper.jsonl")
        base = PlatformConfig.model_validate(paper_config_payload())
        config = lite_settings.platform_config(base)
        config = config.model_copy(
            update={"execution": config.execution.model_copy(update={"submission_enabled": True})}
        )
        orchestrator, broker = Fixture(tmp_path / "unused.jsonl").build()

        with pytest.raises(PaperLiteConfigurationError, match="submission flag"):
            PaperLiteOrchestrator(
                config,
                settings=lite_settings,
                assignment=assignment(),
                agent=ToyAgent(),
                gateway=orchestrator._gateway,
                broker=broker,
                recorder=NullRecorder(),
                session_store=InMemoryRiskSessionStore(),
                kill_switch=KillSwitch(),
                code_commit="test",
            )


class TestSessionPolicy:
    def test_weekday_overnight_has_no_daily_cutoff(self) -> None:
        thursday_1659_ny = datetime(2026, 9, 3, 20, 59, tzinfo=UTC)
        assert paper_lite_session_phase(thursday_1659_ny) is PaperLiteSessionPhase.OPEN

    def test_friday_entry_cutoff_and_flatten_deadline(self) -> None:
        friday = datetime(2026, 9, 4, tzinfo=UTC)
        assert (
            paper_lite_session_phase(friday.replace(hour=20, minute=44))
            is PaperLiteSessionPhase.OPEN
        )
        assert (
            paper_lite_session_phase(friday.replace(hour=20, minute=45))
            is PaperLiteSessionPhase.NO_NEW_ENTRIES
        )
        assert (
            paper_lite_session_phase(friday.replace(hour=20, minute=55))
            is PaperLiteSessionPhase.FLATTEN_REQUIRED
        )

    def test_weekend_is_closed(self) -> None:
        saturday = datetime(2026, 9, 5, 12, tzinfo=UTC)
        assert paper_lite_session_phase(saturday) is PaperLiteSessionPhase.CLOSED


class TestPaperLiteFlow:
    def test_no_trade_traverses_gateway_and_seals_without_a_position(self, tmp_path: Path) -> None:
        orchestrator, broker = Fixture(tmp_path / "paper.jsonl", directional=False).build()
        snapshot, spec = compatible_snapshot()

        outcome = process_clear(orchestrator, snapshot, spec)

        assert outcome.outcome_type is PaperLiteOutcomeType.NO_TRADE
        assert outcome.gateway_result is not None
        assert outcome.gateway_result.accepted
        assert outcome.decision_path is not None
        assert outcome.portfolio.open_position_count == 0
        assert broker.positions() == ()

    def test_risk_block_never_creates_a_paper_position(self, tmp_path: Path) -> None:
        orchestrator, broker = Fixture(tmp_path / "paper.jsonl").build()
        snapshot, spec = compatible_snapshot(spread_points=100)

        outcome = process_clear(orchestrator, snapshot, spec)

        assert outcome.outcome_type is PaperLiteOutcomeType.RISK_BLOCKED
        assert outcome.decision_path is not None
        assert outcome.decision_path.risk_decision is not None
        assert outcome.decision_path.risk_decision.verdict.value == "BLOCK"
        assert broker.positions() == ()

    @pytest.mark.parametrize(
        ("daily_loss", "drawdown", "expected_reason"),
        [
            (Decimal("0.04"), Decimal("0"), ReasonCode.DAILY_LOSS_LIMIT),
            (Decimal("0"), Decimal("0.08"), ReasonCode.MAX_DRAWDOWN),
        ],
    )
    def test_owner_loss_gates_survive_in_the_lite_risk_session(
        self,
        tmp_path: Path,
        daily_loss: Decimal,
        drawdown: Decimal,
        expected_reason: ReasonCode,
    ) -> None:
        state = RiskSessionState(
            trading_day=trading_day(FIXED_NOW),
            session_start_equity=Decimal("10000"),
            current_equity=Decimal("10000"),
            peak_equity=Decimal("10000"),
            realized_pnl=Decimal("0"),
            max_drawdown_fraction=drawdown,
            max_session_loss_fraction=daily_loss,
            open_risk_fraction=Decimal("0"),
            open_position_count=0,
            recorded_at_utc=FIXED_NOW,
        )
        fixture = Fixture(
            tmp_path / "paper.jsonl",
            session_store=InMemoryRiskSessionStore(initial=state),
        )
        orchestrator, broker = fixture.build()
        snapshot, spec = compatible_snapshot()

        outcome = process_clear(orchestrator, snapshot, spec)

        assert outcome.outcome_type is PaperLiteOutcomeType.RISK_BLOCKED
        assert expected_reason in orchestrator._kill_switch.active_reasons
        assert broker.positions() == ()

    def test_platform_policy_veto_never_creates_a_paper_position(self, tmp_path: Path) -> None:
        orchestrator, broker = Fixture(tmp_path / "paper.jsonl").build()
        snapshot, spec = compatible_snapshot()

        outcome = orchestrator.process(snapshot, spec, incident_status=IncidentStatus.ACTIVE)

        assert outcome.outcome_type is PaperLiteOutcomeType.POLICY_BLOCKED
        assert broker.positions() == ()

    def test_unknown_incident_state_vetoes_by_default(self, tmp_path: Path) -> None:
        orchestrator, broker = Fixture(tmp_path / "paper.jsonl").build()
        snapshot, spec = compatible_snapshot()

        outcome = orchestrator.process(snapshot, spec)

        assert outcome.outcome_type is PaperLiteOutcomeType.POLICY_BLOCKED
        assert broker.positions() == ()

    def test_approved_path_records_skip_and_fills_only_the_simulator(self, tmp_path: Path) -> None:
        fixture = Fixture(tmp_path / "paper.jsonl")
        orchestrator, broker = fixture.build()
        snapshot, spec = compatible_snapshot()

        outcome = process_clear(orchestrator, snapshot, spec)

        assert outcome.outcome_type is PaperLiteOutcomeType.PAPER_FILLED
        assert outcome.execution_result is not None
        assert outcome.execution_result.retcode_comment == "simulated fill"
        assert len(broker.positions()) == 1
        assert any(
            entry.event_type is PaperJournalEventType.AUDIT_FACT
            and entry.payload["fact"] == SUPERVISOR_SKIPPED_PAPER_MODE
            for entry in broker.audit_entries
        )
        risk_state = fixture.session_store.load_latest().state
        assert risk_state is not None
        assert risk_state.open_position_count == 1
        assert risk_state.open_risk_fraction == broker.exact_open_risk_fraction()

    def test_existing_exposure_without_risk_session_halts_on_restart(self, tmp_path: Path) -> None:
        path = tmp_path / "paper.jsonl"
        first, _ = Fixture(path).build()
        snapshot, spec = compatible_snapshot()
        assert (
            process_clear(first, snapshot, spec).outcome_type is PaperLiteOutcomeType.PAPER_FILLED
        )

        restarted, _ = Fixture(path).build()
        later = FIXED_NOW + timedelta(minutes=1)
        later_snapshot, _ = compatible_snapshot(
            snapshot_id=uuid4(),
            event_time_utc=later,
            received_time_utc=later,
            bars=(snapshot.bars[-1].model_copy(update={"open_time_utc": later}),),
        )
        restarted._clock = lambda: later

        outcome = process_clear(restarted, later_snapshot, spec)

        assert outcome.outcome_type is PaperLiteOutcomeType.RISK_BLOCKED
        assert restarted._kill_switch.is_halted
        assert restarted._kill_switch.active_reasons[0].value == "SAFETY_STATE_UNKNOWN"

    def test_second_directional_position_fails_closed_until_exact_core_seam_lands(
        self, tmp_path: Path
    ) -> None:
        orchestrator, broker = Fixture(tmp_path / "paper.jsonl").build()
        snapshot, spec = compatible_snapshot()
        first = process_clear(orchestrator, snapshot, spec)
        assert first.outcome_type is PaperLiteOutcomeType.PAPER_FILLED

        later = FIXED_NOW + timedelta(minutes=1)
        second_snapshot, _ = compatible_snapshot(
            snapshot_id=uuid4(),
            event_time_utc=later,
            received_time_utc=later,
            bars=(make_snapshot().bars[-1].model_copy(update={"open_time_utc": later}),),
        )
        orchestrator._clock = lambda: later
        second = process_clear(orchestrator, second_snapshot, spec)

        assert second.outcome_type is PaperLiteOutcomeType.EXACT_OPEN_RISK_UNAVAILABLE
        assert len(broker.positions()) == 1

    def test_weekday_overnight_position_remains_open(self, tmp_path: Path) -> None:
        orchestrator, broker = Fixture(tmp_path / "paper.jsonl").build()
        snapshot, spec = compatible_snapshot()
        assert (
            process_clear(orchestrator, snapshot, spec).outcome_type
            is PaperLiteOutcomeType.PAPER_FILLED
        )

        tuesday = FIXED_NOW + timedelta(days=1)
        later_snapshot, _ = compatible_snapshot(
            snapshot_id=uuid4(),
            event_time_utc=tuesday,
            received_time_utc=tuesday,
            bars=(snapshot.bars[-1].model_copy(update={"open_time_utc": tuesday}),),
        )
        orchestrator._clock = lambda: tuesday

        outcome = process_clear(orchestrator, later_snapshot, spec)

        assert outcome.outcome_type is PaperLiteOutcomeType.EXACT_OPEN_RISK_UNAVAILABLE
        assert len(broker.positions()) == 1

    def test_friday_deadline_flattens_remaining_paper_exposure(self, tmp_path: Path) -> None:
        orchestrator, broker = Fixture(tmp_path / "paper.jsonl").build()
        snapshot, spec = compatible_snapshot()
        assert (
            process_clear(orchestrator, snapshot, spec).outcome_type
            is PaperLiteOutcomeType.PAPER_FILLED
        )

        friday_flatten = datetime(2026, 9, 4, 20, 55, tzinfo=UTC)
        friday_snapshot, _ = compatible_snapshot(
            snapshot_id=uuid4(),
            event_time_utc=friday_flatten,
            received_time_utc=friday_flatten,
            bars=(snapshot.bars[-1].model_copy(update={"open_time_utc": friday_flatten}),),
        )
        orchestrator._clock = lambda: friday_flatten

        outcome = process_clear(orchestrator, friday_snapshot, spec)

        assert outcome.outcome_type is PaperLiteOutcomeType.SESSION_BLOCKED
        assert broker.positions() == ()
        assert broker.portfolio_view().closed_trade_count == 1

    def test_weekend_exposure_raises_instead_of_carrying_silently(self, tmp_path: Path) -> None:
        orchestrator, broker = Fixture(tmp_path / "paper.jsonl").build()
        snapshot, spec = compatible_snapshot()
        assert (
            process_clear(orchestrator, snapshot, spec).outcome_type
            is PaperLiteOutcomeType.PAPER_FILLED
        )

        saturday = datetime(2026, 9, 5, 12, tzinfo=UTC)
        weekend_snapshot, _ = compatible_snapshot(
            snapshot_id=uuid4(),
            event_time_utc=saturday,
            received_time_utc=saturday,
            bars=(snapshot.bars[-1].model_copy(update={"open_time_utc": saturday}),),
        )
        orchestrator._clock = lambda: saturday

        with pytest.raises(PaperLiteSafetyError, match="closed weekend"):
            process_clear(orchestrator, weekend_snapshot, spec)

        assert len(broker.positions()) == 1
        assert orchestrator._kill_switch.is_halted
        assert ReasonCode.OVERNIGHT_EXPOSURE in orchestrator._kill_switch.active_reasons

    def test_same_bar_is_at_most_once_even_when_the_latest_tick_changes(
        self, tmp_path: Path
    ) -> None:
        orchestrator, broker = Fixture(tmp_path / "paper.jsonl").build()
        snapshot, spec = compatible_snapshot()
        first = process_clear(orchestrator, snapshot, spec)
        assert first.outcome_type is PaperLiteOutcomeType.PAPER_FILLED

        same_bar_new_tick, _ = compatible_snapshot(
            snapshot_id=uuid4(),
            event_time_utc=FIXED_NOW + timedelta(seconds=2),
            received_time_utc=FIXED_NOW + timedelta(seconds=2),
            bid=Decimal("1.08501"),
            ask=Decimal("1.08513"),
            bars=snapshot.bars,
        )
        second = process_clear(orchestrator, same_bar_new_tick, spec)

        assert second.outcome_type is PaperLiteOutcomeType.ALREADY_PROCESSED
        assert len(broker.positions()) == 1
