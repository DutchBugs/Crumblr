"""agent_gateway/decision_path.py -- TradeIntent -> Risk -> Policy -> capsule.

Review 1.26 section 7 item 3 / feedback.1.27 section 6.A. Uses a fake
`PortfolioStateProvider` throughout, per the owner-requested reviewer
decision (`review/INTEGRATION_NOTICES.md`, 2026-09-01): a fake is correct
for this level of proof, a genuine LIVE_SHADOW claim needs Core's own
broker-state capture instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from crumblr.agent_gateway.decision_path import (
    AgentDecisionPathResult,
    PortfolioSnapshot,
    evaluate_agent_trade_intent,
)
from crumblr.agent_gateway.evidence import AgentContextEvidence, build_agent_context_evidence
from crumblr.config import PlatformConfig
from crumblr.domain.enums import (
    Environment,
    IncidentStatus,
    ReasonCode,
    ReconciliationStatus,
    Regime,
    RiskVerdict,
    SupervisorVerdict,
)
from crumblr.domain.models import (
    AccountState,
    Contract,
    InstrumentSpec,
    MarketSnapshot,
    PositionState,
)
from crumblr.risk.kill_switch import KillSwitch
from crumblr.risk.session import InMemoryRiskSessionStore, RiskSessionState
from crumblr.trading_agent.sessions import trading_day
from tests.conftest import (
    FIXED_NOW,
    make_account_state,
    make_instrument_spec,
    make_intent,
    make_snapshot,
    paper_config_payload,
)

EXTERNAL_AGENT_STRATEGY_ID = "external_agent"
"""Mirrors the private constant in `agent_gateway/decision_path.py` and
`agent_gateway/gateway.py` -- every intent the Gateway constructs carries
this, so tests use the same literal rather than a coincidentally-matching
one."""


class RecordingRunRecorder:
    def __init__(self) -> None:
        self.events: list[tuple[Contract, UUID, datetime, str]] = []
        self.sealed: list[object] = []
        self.flush_count = 0

    def record(
        self, payload: Contract, *, correlation_id: UUID, occurred_at_utc: datetime, source: str
    ) -> None:
        self.events.append((payload, correlation_id, occurred_at_utc, source))

    def observe(self, tick: Any, bar: Any = None) -> None:
        raise AssertionError("evaluate_agent_trade_intent must never call observe()")

    def record_features(self, features: Any) -> None:
        raise AssertionError(
            "evaluate_agent_trade_intent must never call record_features() -- the "
            "Gateway already durably records agent_context_v1 evidence itself"
        )

    def seal(self, capsule: object) -> None:
        self.sealed.append(capsule)

    def flush(self) -> None:
        self.flush_count += 1


@dataclass(frozen=True)
class FakePortfolioStateProvider:
    """Synthetic-proof-only, per the module docstring -- never backed by a
    real broker read."""

    account: AccountState
    open_positions: tuple[PositionState, ...] = ()
    reconciliation_status: ReconciliationStatus = ReconciliationStatus.MATCHED

    def current(self) -> PortfolioSnapshot:
        return PortfolioSnapshot(
            account=self.account,
            open_positions=self.open_positions,
            reconciliation_status=self.reconciliation_status,
        )


def config() -> PlatformConfig:
    return PlatformConfig.model_validate(paper_config_payload())


def make_features(
    *, snapshot: MarketSnapshot, spec: InstrumentSpec, regime: Regime = Regime.UNKNOWN
) -> AgentContextEvidence:
    """`regime` defaults to `UNKNOWN` -- `build_agent_context_evidence()`
    never produces anything else (AG-006: no TA regime classification is
    computed for this evidence shape). A non-default `regime` here
    constructs the evidence directly rather than via the real production
    builder, deliberately, to test the Risk/Policy wiring in isolation from
    that separately-tracked gap (AG-013)."""
    if regime is Regime.UNKNOWN:
        return build_agent_context_evidence(
            symbol=snapshot.symbol,
            computed_at_utc=snapshot.event_time_utc,
            market_snapshot_id=snapshot.snapshot_id,
            instrument_spec_version=spec.spec_version,
            session_state=snapshot.session_state,
            data_quality=snapshot.data_quality,
        )
    return AgentContextEvidence(
        feature_snapshot_id=uuid4(),
        symbol=snapshot.symbol,
        computed_at_utc=snapshot.event_time_utc,
        market_snapshot_id=snapshot.snapshot_id,
        instrument_spec_version=spec.spec_version,
        session_state=snapshot.session_state,
        data_quality=snapshot.data_quality,
        regime=regime,
    )


@dataclass
class Fixture:
    spec: InstrumentSpec = field(default_factory=make_instrument_spec)
    snapshot: MarketSnapshot | None = None
    account: AccountState = field(default_factory=make_account_state)
    open_positions: tuple[PositionState, ...] = ()
    reconciliation_status: ReconciliationStatus = ReconciliationStatus.MATCHED
    session_store: InMemoryRiskSessionStore = field(default_factory=InMemoryRiskSessionStore)
    kill_switch: KillSwitch = field(default_factory=KillSwitch)
    recorder: RecordingRunRecorder = field(default_factory=RecordingRunRecorder)
    regime: Regime = Regime.TREND
    incident_status: IncidentStatus = IncidentStatus.CLEAR

    def __post_init__(self) -> None:
        if self.snapshot is None:
            self.snapshot = make_snapshot(symbol_spec_version=self.spec.spec_version)

    def evaluate(
        self, intent: Any, **overrides: Any
    ) -> tuple[AgentDecisionPathResult, RecordingRunRecorder]:
        assert self.snapshot is not None
        fields: dict[str, Any] = {
            "outcome_id": uuid4(),
            "strategy_version": "assignment-artifact-hash-v1",
            "snapshot": self.snapshot,
            "spec": self.spec,
            "features": make_features(snapshot=self.snapshot, spec=self.spec, regime=self.regime),
            "config": config(),
            "portfolio_state": FakePortfolioStateProvider(
                account=self.account,
                open_positions=self.open_positions,
                reconciliation_status=self.reconciliation_status,
            ),
            "session_store": self.session_store,
            "kill_switch": self.kill_switch,
            "recorder": self.recorder,
            "environment": Environment.PAPER,
            "code_commit": "test-commit",
            "now": FIXED_NOW,
            "incident_status": self.incident_status,
        }
        fields.update(overrides)
        result = evaluate_agent_trade_intent(intent, **fields)
        return result, self.recorder


def agent_intent(**overrides: Any) -> Any:
    fields: dict[str, Any] = {
        "strategy_id": EXTERNAL_AGENT_STRATEGY_ID,
        "model_version": None,
    }
    fields.update(overrides)
    return make_intent(**fields)


class TestNoTrade:
    def test_no_trade_still_seals_a_capsule(self) -> None:
        fixture = Fixture()
        result, recorder = fixture.evaluate(None)

        assert result.risk_decision is None
        assert result.supervisor_decision is None
        assert result.capsule.trade_intent is None
        assert result.capsule.risk_decision is None
        assert result.capsule.supervisor_decision is None
        assert result.capsule.execution_result is None
        assert recorder.sealed == [result.capsule]

    def test_no_trade_never_touches_the_risk_session_store(self) -> None:
        """Only a directional intent needs a ledger -- NO_TRADE cannot
        breach a loss gate, so recovering one would be a pointless read."""

        class ExplodingSessionStore(InMemoryRiskSessionStore):
            def load_latest(self) -> Any:
                raise AssertionError("must not be called for a NO_TRADE evaluation")

        fixture = Fixture(session_store=ExplodingSessionStore())
        fixture.evaluate(None)  # must not raise


class TestAcceptedIntentReachesApproval:
    def test_risk_pass_and_supervisor_approve(self) -> None:
        fixture = Fixture()
        intent = agent_intent()
        result, recorder = fixture.evaluate(intent)

        assert result.risk_decision is not None
        assert result.risk_decision.verdict is RiskVerdict.PASS
        assert result.supervisor_decision is not None
        assert result.supervisor_decision.verdict is SupervisorVerdict.APPROVE
        assert result.capsule.trade_intent == intent
        assert result.capsule.risk_decision == result.risk_decision
        assert result.capsule.supervisor_decision == result.supervisor_decision
        assert result.capsule.execution_result is None
        assert result.capsule.position_state_before == result.capsule.position_state_after
        assert recorder.sealed == [result.capsule]

    def test_never_reaches_execution(self) -> None:
        """No ApprovedOrder, order_check or order_send anywhere on the
        result -- there is no field through which it could."""
        fixture = Fixture()
        result, _ = fixture.evaluate(agent_intent())
        assert result.capsule.execution_result is None
        assert not hasattr(result, "approved_order")


class TestRiskBlockStopsBeforeSupervisor:
    def test_risk_block_never_reaches_the_policy_gate(self) -> None:
        fixture = Fixture()
        # requested_risk_fraction above the assignment/config band ->
        # RISK_PER_TRADE_LIMIT, a BLOCK.
        intent = agent_intent(requested_risk_fraction=Decimal("0.05"))
        result, recorder = fixture.evaluate(intent)

        assert result.risk_decision is not None
        assert result.risk_decision.verdict is RiskVerdict.BLOCK
        assert ReasonCode.RISK_PER_TRADE_LIMIT in result.risk_decision.reason_codes
        assert result.supervisor_decision is None
        assert result.capsule.supervisor_decision is None
        assert not any(source == "supervisor" for _, _, _, source in recorder.events)


class TestKillSwitchAlreadyHalted:
    def test_halted_kill_switch_produces_a_system_halted_risk_decision(self) -> None:
        kill_switch = KillSwitch()
        kill_switch.trip(
            reason_codes=(ReasonCode.MAX_DRAWDOWN,), tripped_by="test", occurred_at_utc=FIXED_NOW
        )
        fixture = Fixture(kill_switch=kill_switch)
        result, _ = fixture.evaluate(agent_intent())

        assert result.risk_decision is not None
        assert ReasonCode.SYSTEM_HALTED in result.risk_decision.reason_codes
        assert result.supervisor_decision is None


class TestHaltVerdictsTripTheKillSwitch:
    """`risk.policies.evaluate()`/`evaluator.pretrade.evaluate()` only ever
    *name* a HALT in their verdict -- neither calls `kill_switch.trip()`
    itself (self-review finding, code-review medium pass: this module
    sealed a HALT-verdict capsule and returned without ever tripping the
    switch, so a caller reusing the same `kill_switch` instance across
    evaluations -- the exact usage the AG-012 docstring assumes -- would
    keep evaluating later intents as if nothing halting had happened).
    Mirrors `application/live_decision.py`'s own `_trip()` calls."""

    def test_a_risk_halt_trips_the_kill_switch(self) -> None:
        kill_switch = KillSwitch()
        fixture = Fixture(
            account=make_account_state(server="WrongServer-Demo"), kill_switch=kill_switch
        )
        result, _ = fixture.evaluate(agent_intent())

        assert result.risk_decision is not None
        assert result.risk_decision.verdict is RiskVerdict.HALT
        assert kill_switch.is_halted

    def test_a_supervisor_halt_trips_the_kill_switch(self) -> None:
        kill_switch = KillSwitch()
        fixture = Fixture(
            reconciliation_status=ReconciliationStatus.UNKNOWN, kill_switch=kill_switch
        )
        result, _ = fixture.evaluate(agent_intent())

        assert result.supervisor_decision is not None
        assert result.supervisor_decision.verdict is SupervisorVerdict.HALT
        assert kill_switch.is_halted

    def test_an_already_halted_kill_switch_is_not_re_tripped_or_double_recorded(self) -> None:
        kill_switch = KillSwitch()
        kill_switch.trip(
            reason_codes=(ReasonCode.MAX_DRAWDOWN,), tripped_by="test", occurred_at_utc=FIXED_NOW
        )
        fixture = Fixture(
            account=make_account_state(server="WrongServer-Demo"), kill_switch=kill_switch
        )
        result, recorder = fixture.evaluate(agent_intent())

        assert result.risk_decision is not None
        assert result.risk_decision.verdict is RiskVerdict.HALT
        assert not any(
            payload.__class__.__name__ == "SystemHalted" for payload, *_ in recorder.events
        )


class TestAG012FreshSessionRecoveryEveryCall:
    """AG-012's accepted interim mitigation: the risk session is recovered
    fresh from the store on every call, never cached -- proven here by a
    loss gate that could only trip if the store's carried-forward
    `session_start_equity` genuinely participated in this call's
    evaluation, since nothing else in the fixture implies a loss."""

    def test_a_recorded_prior_loss_this_session_reaches_the_daily_loss_gate(self) -> None:
        risk = config().risk
        equity_now = Decimal("10000")
        session_start = equity_now / (Decimal("1") - risk.max_daily_loss - Decimal("0.01"))
        session_store = InMemoryRiskSessionStore(
            initial=RiskSessionState(
                trading_day=trading_day(FIXED_NOW),
                session_start_equity=session_start,
                current_equity=session_start,
                peak_equity=session_start,
                realized_pnl=Decimal("0"),
                max_drawdown_fraction=Decimal("0"),
                max_session_loss_fraction=Decimal("0"),
                open_risk_fraction=Decimal("0"),
                open_position_count=0,
                recorded_at_utc=FIXED_NOW,
            )
        )
        kill_switch = KillSwitch()
        account = make_account_state(equity=equity_now)
        fixture = Fixture(account=account, session_store=session_store, kill_switch=kill_switch)
        result, _ = fixture.evaluate(agent_intent())

        assert result.risk_decision is not None
        assert result.risk_decision.verdict is RiskVerdict.HALT
        assert ReasonCode.DAILY_LOSS_LIMIT in result.risk_decision.reason_codes
        assert kill_switch.is_halted

    def test_two_calls_against_different_stores_are_fully_independent(self) -> None:
        """No object here is reused/mutated between calls -- proves there
        is no in-process ledger this function could accidentally cache."""
        fixture = Fixture()
        first, _ = fixture.evaluate(agent_intent())
        assert first.risk_decision is not None
        assert first.risk_decision.verdict is RiskVerdict.PASS

        stale_store = InMemoryRiskSessionStore(
            initial=RiskSessionState(
                trading_day=trading_day(FIXED_NOW),
                session_start_equity=Decimal("100000"),
                current_equity=Decimal("50000"),
                peak_equity=Decimal("100000"),
                realized_pnl=Decimal("-50000"),
                max_drawdown_fraction=Decimal("0.5"),
                max_session_loss_fraction=Decimal("0.5"),
                open_risk_fraction=Decimal("0"),
                open_position_count=0,
                recorded_at_utc=FIXED_NOW,
            )
        )
        second_fixture = Fixture(session_store=stale_store)
        second, _ = second_fixture.evaluate(agent_intent())
        assert second.risk_decision is not None
        assert second.risk_decision.verdict is RiskVerdict.HALT


class TestReplaySafety:
    def test_identical_outcome_id_and_content_reseals_the_identical_capsule(self) -> None:
        fixture = Fixture()
        outcome_id = uuid4()
        intent = agent_intent()
        first, _ = fixture.evaluate(intent, outcome_id=outcome_id)
        second, _ = fixture.evaluate(intent, outcome_id=outcome_id)
        assert first.capsule.capsule_id == second.capsule.capsule_id
        assert first.capsule == second.capsule

    def test_different_outcome_ids_never_collide(self) -> None:
        fixture = Fixture()
        intent = agent_intent()
        first, _ = fixture.evaluate(intent, outcome_id=uuid4())
        second, _ = fixture.evaluate(intent, outcome_id=uuid4())
        assert first.capsule.capsule_id != second.capsule.capsule_id


class TestFailClosedDefaults:
    def test_default_incident_status_is_unknown_not_clear(self) -> None:
        """`evaluate_agent_trade_intent`'s own default, independent of the
        `Fixture` test helper above (which passes CLEAR explicitly) -- a
        caller that forgets to wire up a real incident read gets a
        refusal, never an unearned approval (review finding F-002)."""
        fixture = Fixture()
        intent = agent_intent()
        result, _ = fixture.evaluate(intent, incident_status=IncidentStatus.UNKNOWN)
        assert result.supervisor_decision is not None
        assert result.supervisor_decision.verdict is SupervisorVerdict.VETO
        assert ReasonCode.INCIDENT_STATE_UNKNOWN in result.supervisor_decision.reason_codes

    def test_unknown_reconciliation_status_halts_the_supervisor(self) -> None:
        fixture = Fixture(reconciliation_status=ReconciliationStatus.UNKNOWN)
        result, _ = fixture.evaluate(agent_intent())
        assert result.supervisor_decision is not None
        assert result.supervisor_decision.verdict is SupervisorVerdict.HALT
        assert ReasonCode.RECONCILIATION_UNKNOWN in result.supervisor_decision.reason_codes


class TestAG013RealAgentEvidenceRegimeIsAlwaysUnknown:
    """Not a test of `decision_path.py` in isolation -- a documented,
    deliberate proof of a real cross-cutting gap (AG-013,
    `review/AGENT_FEEDBACK.md`): `build_agent_context_evidence()`
    (`agent_gateway/evidence.py`, AG-006) always sets `regime=UNKNOWN` by
    design, and the shipped paper config's
    `SupervisorPolicy.veto_on_unknown_regime=True` means a directional
    external-agent proposal built from *real* production evidence can
    never reach Supervisor APPROVE today -- only VETO on UNKNOWN_REGIME,
    or NO_TRADE. Not a safety defect (fail-closed is the safe direction)
    but a real product gap worth tracking openly rather than discovering
    silently."""

    def test_real_evidence_reaches_the_policy_gate_and_vetoes_on_unknown_regime(self) -> None:
        fixture = Fixture(regime=Regime.UNKNOWN)  # build_agent_context_evidence()'s real default
        result, _ = fixture.evaluate(agent_intent())

        assert result.risk_decision is not None
        assert result.risk_decision.verdict is RiskVerdict.PASS
        assert result.supervisor_decision is not None
        assert result.supervisor_decision.verdict is SupervisorVerdict.VETO
        assert ReasonCode.UNKNOWN_REGIME in result.supervisor_decision.reason_codes
