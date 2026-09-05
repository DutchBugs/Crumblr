from __future__ import annotations

import inspect
import json
from collections.abc import Iterator
from contextlib import contextmanager
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
    PaperLiteIncidentClearAssertion,
    PaperLiteOrchestrator,
    PaperLiteOutcomeType,
    PaperLiteSafetyError,
    PaperLiteSettings,
    require_paper_lite_database_url,
)
from crumblr.application.recording import NullRecorder
from crumblr.config import PlatformConfig
from crumblr.domain.enums import (
    EntryType,
    Environment,
    IncidentStatus,
    ReasonCode,
    RiskVerdict,
    Side,
)
from crumblr.domain.models import InstrumentSpec, MarketSnapshot
from crumblr.persistence.paper_lite import (
    PAPER_LITE_INCIDENT_CLEAR_ASSERTED,
    SUPERVISOR_SKIPPED_PAPER_MODE,
    DurablePaperBroker,
    PaperJournalEventType,
)
from crumblr.risk import trading_window
from crumblr.risk.kill_switch import KillSwitch
from crumblr.risk.session import (
    InMemoryRiskLedgerLock,
    InMemoryRiskSessionStore,
    RiskLedgerLock,
    RiskSessionState,
)
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
    requested_risk_fraction: Decimal = Decimal("0.02")

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
            requested_risk_fraction=self.requested_risk_fraction,
            reason_codes=("TOY_BREAKOUT_VOCABULARY",),
            evidence_refs=(),
            submitted_at_utc=context.provenance.issued_at_utc,
            expires_at_utc=context.provenance.expires_at_utc,
        )


@dataclass
class Fixture:
    path: Path
    directional: bool = True
    requested_risk_fraction: Decimal = Decimal("0.02")
    session_store: InMemoryRiskSessionStore = field(default_factory=InMemoryRiskSessionStore)
    risk_ledger_lock: RiskLedgerLock = field(default_factory=InMemoryRiskLedgerLock)

    def build(self) -> tuple[PaperLiteOrchestrator, DurablePaperBroker]:
        lite_settings = settings(self.path)
        base = PlatformConfig.model_validate(paper_config_payload())
        config = lite_settings.platform_config(base)
        agent = ToyAgent(
            directional=self.directional, requested_risk_fraction=self.requested_risk_fraction
        )
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
            risk_ledger_lock=self.risk_ledger_lock,
            kill_switch=KillSwitch(),
            code_commit="test-commit",
            incident_clear_assertion=PaperLiteIncidentClearAssertion(
                operator="paper-lite-test",
                note="unit-test assertion for the isolated paper path",
                asserted_at_utc=FIXED_NOW,
            ),
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


class SpyRiskLedgerLock:
    """Records every `held()` call -- proves `_recover_risk_session()`/
    `_persist_risk_session()` (AG-023) actually acquire the lock, not
    merely that the parameter exists on the constructor."""

    def __init__(self) -> None:
        self.held_for: list[str] = []

    @contextmanager
    def held(self, canonical_symbol: str) -> Iterator[None]:
        self.held_for.append(canonical_symbol)
        yield None


class TestAG023RiskLedgerLockAcquired:
    """`_recover_risk_session()`/`_persist_risk_session()` used to read and
    write `risk_session_states` with no lock at all -- a fourth,
    unaccounted party against the same table `LiveDecisionOrchestrator`/
    `decision_path.py` already lock-protect (ADR-021). Each method now
    acquires `RiskLedgerLock.held(canonical_symbol)` around its own store
    call -- proven here, not merely asserted in a docstring. This does
    not make the two calls one atomic critical section (see
    `_persist_risk_session()`'s own docstring for the honest remaining
    gap) -- only that each individual read/write is itself lock-protected."""

    def test_one_full_cycle_acquires_the_lock_for_both_recover_and_persist(
        self, tmp_path: Path
    ) -> None:
        lock = SpyRiskLedgerLock()
        orchestrator, _ = Fixture(
            tmp_path / "paper.jsonl", directional=False, risk_ledger_lock=lock
        ).build()
        snapshot, spec = compatible_snapshot()

        process_clear(orchestrator, snapshot, spec)

        assert lock.held_for
        assert set(lock.held_for) == {"EUR/USD"}
        # At least one recover (before the decision) and one persist
        # (after) -- proven by count, not just presence, so a future
        # regression that drops one of the two calls is caught.
        assert len(lock.held_for) >= 2


class RaisingRiskLedgerLock:
    """AG-024 (ADR-021 section 8, mirrors `application/live_decision.py`'s
    own test): simulates the lock's own acquisition failing -- e.g. a
    transient database outage. Before AG-024, this had no handling
    anywhere in `_recover_risk_session()`/`_persist_risk_session()`'s own
    call chain and would have propagated uncaught."""

    def held(self, canonical_symbol: str) -> Any:
        raise RuntimeError(f"simulated database outage acquiring the lock for {canonical_symbol}")


class TestAG024RiskLedgerLockFailureFailsClosed:
    """A lock/recover/persist failure must halt, never crash the process
    or silently proceed on unknown risk-session state (ADR-021 section
    8) -- mirrors `application/live_decision.py`'s own AG-024 test."""

    def test_a_lock_failure_trips_the_kill_switch_instead_of_raising(self, tmp_path: Path) -> None:
        orchestrator, _ = Fixture(
            tmp_path / "paper.jsonl", directional=False, risk_ledger_lock=RaisingRiskLedgerLock()
        ).build()
        snapshot, spec = compatible_snapshot()

        # Must not raise -- the whole point of AG-024.
        process_clear(orchestrator, snapshot, spec)

        assert orchestrator._kill_switch.is_halted
        assert ReasonCode.RISK_LEDGER_LOCK_UNAVAILABLE in orchestrator._kill_switch.active_reasons


REPO_ROOT = Path(__file__).resolve().parents[2]


class TestTypedPaperOnlyBoundary:
    def test_module_has_no_real_execution_adapter_import(self) -> None:
        source = inspect.getsource(paper_lite_module)
        assert "mt5_gateway.execution" not in source
        assert "OrderCheckMt5Gateway" not in source

    @pytest.mark.parametrize(
        "path",
        [
            "src/crumblr/application/paper_lite.py",
            "src/crumblr/application/paper_lite_toy_agent.py",
            "scripts/paper_lite.py",
        ],
    )
    def test_no_real_demo_order_send_reference_anywhere_in_the_paper_path(self, path: str) -> None:
        """Section 7 regression: none of PAPER_LITE's own files may
        reference the real DEMO order_send adapter (`mt5_gateway
        /demo_execution.py::DemoOrderSendMt5Gateway`, Dev-1's Phase-B B1+B2
        slice) -- a static source check, not only a runtime assertion, so a
        future import cannot slip in unnoticed."""
        source = (REPO_ROOT / path).read_text(encoding="utf-8")
        assert "demo_execution" not in source
        assert "DemoOrderSendMt5Gateway" not in source
        assert "order_send" not in source

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
                risk_ledger_lock=InMemoryRiskLedgerLock(),
                kill_switch=KillSwitch(),
                code_commit="test",
            )

    def test_the_supervisor_skip_cannot_activate_outside_paper_mode(self, tmp_path: Path) -> None:
        """Section 5/7: `SUPERVISOR_SKIPPED_PAPER_MODE` is only ever reached
        through `PaperLiteOrchestrator.process()`, which hardcodes
        `environment=Environment.PAPER` in its call into the shared
        decision path -- the real guarantee this can never activate outside
        paper mode is that the orchestrator itself refuses to construct
        against anything else, checked at both the config and the
        assignment level."""
        lite_settings = settings(tmp_path / "paper.jsonl")
        base = PlatformConfig.model_validate(paper_config_payload())
        config = lite_settings.platform_config(base)
        orchestrator, broker = Fixture(tmp_path / "unused.jsonl").build()

        non_paper_config = config.model_copy(update={"environment": Environment.SHADOW})
        with pytest.raises(PaperLiteConfigurationError, match="requires Environment"):
            PaperLiteOrchestrator(
                non_paper_config,
                settings=lite_settings,
                assignment=assignment(),
                agent=ToyAgent(),
                gateway=orchestrator._gateway,
                broker=broker,
                recorder=NullRecorder(),
                session_store=InMemoryRiskSessionStore(),
                risk_ledger_lock=InMemoryRiskLedgerLock(),
                kill_switch=KillSwitch(),
                code_commit="test",
            )

        non_paper_assignment = assignment().model_copy(update={"environment": Environment.SHADOW})
        with pytest.raises(PaperLiteConfigurationError, match="assignment must target PAPER"):
            PaperLiteOrchestrator(
                config,
                settings=lite_settings,
                assignment=non_paper_assignment,
                agent=ToyAgent(),
                gateway=orchestrator._gateway,
                broker=broker,
                recorder=NullRecorder(),
                session_store=InMemoryRiskSessionStore(),
                risk_ledger_lock=InMemoryRiskLedgerLock(),
                kill_switch=KillSwitch(),
                code_commit="test",
            )

    def test_database_guard_accepts_only_the_dedicated_paper_database(self) -> None:
        dedicated = "postgresql+psycopg://user:secret@localhost:5432/crumblr_test_dev3"

        assert require_paper_lite_database_url(dedicated) == dedicated
        with pytest.raises(PaperLiteConfigurationError, match="dedicated"):
            require_paper_lite_database_url(
                "postgresql+psycopg://user:secret@localhost:5432/crumblr"
            )

    def test_clear_requires_operator_assertion_and_journals_it_durably(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "paper.jsonl"
        orchestrator, broker = Fixture(path).build()
        assertion_entries = [
            entry
            for entry in broker.audit_entries
            if entry.payload.get("fact") == PAPER_LITE_INCIDENT_CLEAR_ASSERTED
        ]
        assert len(assertion_entries) == 1
        assert json.loads(assertion_entries[0].payload["detail"]) == {
            "asserted_at_utc": FIXED_NOW.isoformat(),
            "note": "unit-test assertion for the isolated paper path",
            "operator": "paper-lite-test",
        }

        restarted = DurablePaperBroker(
            path,
            make_instrument_spec(),
            starting_balance=Decimal("10000"),
        )
        assert any(
            entry.payload.get("fact") == PAPER_LITE_INCIDENT_CLEAR_ASSERTED
            for entry in restarted.audit_entries
        )

        orchestrator._incident_clear_assertion = None
        snapshot, spec = compatible_snapshot()
        with pytest.raises(PaperLiteSafetyError, match="operator-bound"):
            process_clear(orchestrator, snapshot, spec)


class TestSessionPolicy:
    """PAPER_LITE no longer owns a session-phase calendar -- Section 1 of
    the Dev-3 Phase-A convergence work order removed `PaperLiteSessionPhase`/
    `paper_lite_session_phase()` in favor of Core's own shared
    `risk.trading_window` authority (D1.5/ADR-012), the one calendar every
    other pipeline (`LiveDecisionOrchestrator`, `ExecutionOrchestrator`)
    already uses. `trading_window.phase_at()`'s own arithmetic is already
    proven in `tests/unit/test_trading_window.py` -- these tests instead
    prove PAPER_LITE's *wiring* is correct: `PaperLiteSettings.platform_config()`
    must produce an `IntradayConfig` that, once turned into a policy and
    evaluated, reproduces the exact same four phases at the exact same
    15/5-minute Friday boundaries this track's own tests always required."""

    def _policy(self, path: Path) -> trading_window.IntradayPolicy:
        base = PlatformConfig.model_validate(paper_config_payload())
        config = settings(path).platform_config(base)
        assert config.intraday.enabled, "PAPER_LITE must enable Core's session policy, not opt out"
        return trading_window.policy_from_config(config.intraday)

    def test_weekday_overnight_has_no_daily_cutoff(self, tmp_path: Path) -> None:
        thursday_1659_ny = datetime(2026, 9, 3, 20, 59, tzinfo=UTC)
        policy = self._policy(tmp_path / "paper.jsonl")
        assert trading_window.phase_at(thursday_1659_ny, policy) is trading_window.SessionPhase.OPEN

    def test_friday_entry_cutoff_and_flatten_deadline(self, tmp_path: Path) -> None:
        friday = datetime(2026, 9, 4, tzinfo=UTC)
        policy = self._policy(tmp_path / "paper.jsonl")
        assert (
            trading_window.phase_at(friday.replace(hour=20, minute=44), policy)
            is trading_window.SessionPhase.OPEN
        )
        assert (
            trading_window.phase_at(friday.replace(hour=20, minute=45), policy)
            is trading_window.SessionPhase.NO_NEW_ENTRIES
        )
        assert (
            trading_window.phase_at(friday.replace(hour=20, minute=55), policy)
            is trading_window.SessionPhase.FLATTEN_REQUIRED
        )

    def test_weekend_is_closed(self, tmp_path: Path) -> None:
        saturday = datetime(2026, 9, 5, 12, tzinfo=UTC)
        policy = self._policy(tmp_path / "paper.jsonl")
        assert trading_window.phase_at(saturday, policy) is trading_window.SessionPhase.CLOSED


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
        assert risk_state.open_risk_fraction == broker.open_risk_assessment().fraction

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

    def test_a_second_full_size_position_exceeding_the_open_risk_budget_is_blocked(
        self, tmp_path: Path
    ) -> None:
        """Section 2 of the Dev-3 Phase-A convergence work order: the
        temporary single-position guard is gone -- PAPER_LITE now routes
        every proposal through the same Core `assess_open_risk()` every
        other pipeline uses. Two full-size (owner-default 2%) positions
        would together request ~4% against the owner's 3% total open-risk
        budget, so the second must BLOCK on the real budget, not on a
        position-count proxy."""
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

        assert second.outcome_type is PaperLiteOutcomeType.RISK_BLOCKED
        assert second.decision_path is not None
        assert second.decision_path.risk_decision is not None
        assert ReasonCode.OPEN_RISK_LIMIT in second.decision_path.risk_decision.reason_codes
        assert len(broker.positions()) == 1

    def test_several_small_positions_under_the_open_risk_budget_are_not_blocked_by_count(
        self, tmp_path: Path
    ) -> None:
        """Section 7 regression: several positions whose combined risk
        stays under the owner's 3% total budget must not be refused merely
        because a position already exists -- both entries here request 1%
        each, ~2% combined, comfortably under budget."""
        orchestrator, broker = Fixture(
            tmp_path / "paper.jsonl", requested_risk_fraction=Decimal("0.01")
        ).build()
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

        assert second.outcome_type is PaperLiteOutcomeType.PAPER_FILLED
        assert len(broker.positions()) == 2

    def test_flat_portfolio_open_risk_is_exact_zero_not_unknown(self, tmp_path: Path) -> None:
        """Section 7 regression: a flat book must reach Core Risk as an
        exact, known `0`, never as `OPEN_RISK_UNKNOWN` -- a directional
        proposal against a flat portfolio must be able to PASS on its own
        merits, with no open-risk-derived refusal at all."""
        orchestrator, _ = Fixture(tmp_path / "paper.jsonl").build()
        snapshot, spec = compatible_snapshot()

        outcome = process_clear(orchestrator, snapshot, spec)

        assert outcome.outcome_type is PaperLiteOutcomeType.PAPER_FILLED
        assert outcome.decision_path is not None
        assert outcome.decision_path.risk_decision is not None
        assert outcome.decision_path.risk_decision.verdict is RiskVerdict.PASS
        assert ReasonCode.OPEN_RISK_UNKNOWN not in outcome.decision_path.risk_decision.reason_codes

    def test_weekday_overnight_position_remains_open(self, tmp_path: Path) -> None:
        """Open-risk budget arithmetic is covered separately above -- this
        test is only about overnight retention, so the second cycle asks
        for NO_TRADE rather than a second entry, keeping the two concerns
        apart."""
        orchestrator, broker = Fixture(tmp_path / "paper.jsonl").build()
        snapshot, spec = compatible_snapshot()
        assert (
            process_clear(orchestrator, snapshot, spec).outcome_type
            is PaperLiteOutcomeType.PAPER_FILLED
        )
        orchestrator._agent.directional = False  # type: ignore[attr-defined]

        tuesday = FIXED_NOW + timedelta(days=1)
        later_snapshot, _ = compatible_snapshot(
            snapshot_id=uuid4(),
            event_time_utc=tuesday,
            received_time_utc=tuesday,
            bars=(snapshot.bars[-1].model_copy(update={"open_time_utc": tuesday}),),
        )
        orchestrator._clock = lambda: tuesday

        outcome = process_clear(orchestrator, later_snapshot, spec)

        assert outcome.outcome_type is PaperLiteOutcomeType.NO_TRADE
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
        assert len(outcome.closed_trades) == 1
        assert outcome.closed_trades[0].exit_reason == "owner_policy_v1_friday_flatten"

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
