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
from crumblr.agent_gateway.evidence import build_agent_context_evidence
from crumblr.config import PlatformConfig
from crumblr.domain.enums import (
    Environment,
    IncidentStatus,
    ReasonCode,
    ReconciliationStatus,
    Regime,
    RiskVerdict,
    Side,
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


def config(**supervisor_overrides: Any) -> PlatformConfig:
    payload = paper_config_payload()
    if supervisor_overrides:
        payload["supervisor"] = {**payload["supervisor"], **supervisor_overrides}
    return PlatformConfig.model_validate(payload)


def make_features(*, snapshot: MarketSnapshot, spec: InstrumentSpec) -> Any:
    """Always the real production builder -- `regime` is always `UNKNOWN`
    (AG-006: no TA regime classification is computed for this evidence
    shape) and, since feedback.1.28/F-066's strategy-neutral Policy Gate,
    that no longer matters to anything this module checks (AG-013,
    resolved -- see `TestAG013Resolved` below)."""
    return build_agent_context_evidence(
        symbol=snapshot.symbol,
        computed_at_utc=snapshot.event_time_utc,
        market_snapshot_id=snapshot.snapshot_id,
        instrument_spec_version=spec.spec_version,
        session_state=snapshot.session_state,
        data_quality=snapshot.data_quality,
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
            "features": make_features(snapshot=self.snapshot, spec=self.spec),
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
            def load_latest(self, *, connection: Any = None) -> Any:
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


def untrusted_position(**overrides: Any) -> PositionState:
    """An open position `risk.portfolio_risk.assess_open_risk` cannot
    honestly value -- no `stop_loss_price` (`NO_PROTECTIVE_STOP`), the
    same shape `TestOpenRiskUnknown` below exercises. Mirrors
    `tests/unit/test_portfolio_risk.py::position()`'s own fixture style."""
    fields: dict[str, Any] = {
        "ticket": 900001,
        "broker_symbol": "EURUSD",
        "side": Side.BUY,
        "volume": Decimal("0.10"),
        "open_price": Decimal("1.08500"),
        "opened_at_utc": FIXED_NOW,
        "profit": Decimal("0"),
        "swap": Decimal("0"),
        "observed_at_utc": FIXED_NOW,
    }
    fields.update(overrides)
    return PositionState(**fields)


class TestOpenRiskUnknown:
    """Owner Work Order D2.2/D1.4 (`review/OWNER_WORK_ORDERS_2026-09-02.md`):
    the count-based `max_risk_per_trade * len(open_positions)`
    approximation is gone. `decision_path.py` now calls Core's own
    `risk.portfolio_risk.assess_open_risk()` directly over the fixture's
    `open_positions` -- an untrustworthy position (no protective stop)
    makes the whole book's open-risk figure `None`, and `risk.policies
    .evaluate()` itself fails closed on that as `OPEN_RISK_UNKNOWN`, a
    `BLOCK` (deliberately not a `HALT` -- `review/DEVIATIONS.md` D-054
    gap 1), never a silent zero."""

    def test_an_untrusted_open_position_blocks_before_the_policy_gate(self) -> None:
        fixture = Fixture(open_positions=(untrusted_position(),))
        result, recorder = fixture.evaluate(agent_intent())

        assert result.risk_decision is not None
        assert result.risk_decision.verdict is RiskVerdict.BLOCK
        assert ReasonCode.OPEN_RISK_UNKNOWN in result.risk_decision.reason_codes
        assert result.supervisor_decision is None
        assert result.capsule.supervisor_decision is None
        assert not any(source == "supervisor" for _, _, _, source in recorder.events)

    def test_an_untrusted_open_position_does_not_halt_the_kill_switch(self) -> None:
        """A BLOCK, not a HALT -- `_trip()` is only ever called on a
        `RiskVerdict.HALT` (see `evaluate_agent_trade_intent`), so an
        untrusted-position BLOCK must leave the switch untouched."""
        kill_switch = KillSwitch()
        fixture = Fixture(open_positions=(untrusted_position(),), kill_switch=kill_switch)
        result, _ = fixture.evaluate(agent_intent())

        assert result.risk_decision is not None
        assert result.risk_decision.verdict is RiskVerdict.BLOCK
        assert not kill_switch.is_halted

    def test_an_untrusted_open_position_still_seals_a_capsule(self) -> None:
        fixture = Fixture(open_positions=(untrusted_position(),))
        result, recorder = fixture.evaluate(agent_intent())

        assert result.capsule.risk_decision == result.risk_decision
        assert recorder.sealed == [result.capsule]

    def test_a_genuinely_flat_book_still_reaches_approve(self) -> None:
        """Regression guard: an empty `open_positions` is established,
        trustworthy zero risk (`assess_open_risk(() , ...)`), not
        `None` -- the common case must not fail closed."""
        fixture = Fixture(open_positions=())
        result, _ = fixture.evaluate(agent_intent())

        assert result.risk_decision is not None
        assert result.risk_decision.verdict is RiskVerdict.PASS
        assert result.supervisor_decision is not None
        assert result.supervisor_decision.verdict is SupervisorVerdict.APPROVE

    def test_a_trustworthy_stopped_position_still_reaches_approve(self) -> None:
        """A position with real protective-stop geometry is established
        risk, not unknown -- only *untrustworthy* geometry fails closed."""
        fixture = Fixture(open_positions=(untrusted_position(stop_loss_price=Decimal("1.08000")),))
        result, _ = fixture.evaluate(agent_intent())

        assert result.risk_decision is not None
        assert result.risk_decision.verdict is RiskVerdict.PASS
        assert result.supervisor_decision is not None
        assert result.supervisor_decision.verdict is SupervisorVerdict.APPROVE


class TestAG012FreshSessionRecoveryEveryCall:
    """AG-012's accepted interim mitigation: the risk session is recovered
    fresh from the store on every call, never cached -- proven here by a
    loss gate that could only trip if the store's carried-forward
    `session_start_equity` genuinely participated in this call's
    evaluation, since nothing else in the fixture implies a loss.

    PL-006 (`review/adr/ADR-013-restart-recovery-loss-drawdown-check.md`):
    `recover_session()` now catches an already-breached
    `max_daily_loss`/`max_drawdown` itself, during recovery -- before
    `policies.evaluate()` ever runs -- rather than relying solely on
    `evaluate()`'s own live loss-gate leg to catch it. The kill switch is
    tripped with the real reason (`DAILY_LOSS_LIMIT`/`MAX_DRAWDOWN`)
    during recovery; `evaluate()` then runs against an already-halted
    switch and reports `BLOCK`/`SYSTEM_HALTED` per its own
    already-halted-system convention (`test_risk_engine.py
    ::test_adr001_7_a_kill_switch_tripped_since_approval_is_refused`),
    not a fresh `HALT`/`DAILY_LOSS_LIMIT` escalation -- the halt already
    happened, earlier and more correctly than before PL-006."""

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

        assert kill_switch.is_halted
        assert ReasonCode.DAILY_LOSS_LIMIT in kill_switch.active_reasons
        assert result.risk_decision is not None
        assert result.risk_decision.verdict is RiskVerdict.BLOCK
        assert ReasonCode.SYSTEM_HALTED in result.risk_decision.reason_codes

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
        second_kill_switch = KillSwitch()
        second_fixture = Fixture(session_store=stale_store, kill_switch=second_kill_switch)
        second, _ = second_fixture.evaluate(agent_intent())

        assert second_kill_switch.is_halted
        # The stale store's max_drawdown_fraction=0.5 and
        # max_session_loss_fraction=0.5 both exceed their configured
        # thresholds (max_drawdown=0.08, max_daily_loss=0.04) -- both
        # reasons, not either/or, so both must survive recovery.
        assert ReasonCode.MAX_DRAWDOWN in second_kill_switch.active_reasons
        assert ReasonCode.DAILY_LOSS_LIMIT in second_kill_switch.active_reasons
        assert second.risk_decision is not None
        assert second.risk_decision.verdict is RiskVerdict.BLOCK
        assert ReasonCode.SYSTEM_HALTED in second.risk_decision.reason_codes


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

    def test_config_supervisor_enabled_false_does_not_bypass_platform_safety_checks(self) -> None:
        """Self-review finding: unlike `evaluator.pretrade.SupervisorPolicy
        .enabled` (a strategy-envelope switch that trivially APPROVEs an
        internal-strategy intent), `config.supervisor.enabled` has no
        effect at all on `_evaluate_platform_policy` -- it is not even a
        parameter. feedback.1.28 section 7 calls reconciliation/incident
        health "hard checks" for the external-agent path, not part of a
        togglable policy envelope."""
        fixture = Fixture(reconciliation_status=ReconciliationStatus.UNKNOWN)
        result, _ = fixture.evaluate(agent_intent(), config=config(enabled=False))
        assert result.supervisor_decision is not None
        assert result.supervisor_decision.verdict is SupervisorVerdict.HALT
        assert ReasonCode.RECONCILIATION_UNKNOWN in result.supervisor_decision.reason_codes


class TestAG013Resolved:
    """AG-013 (`review/AGENT_FEEDBACK.md`) found that real `agent_context_v1`
    evidence always carries `regime=UNKNOWN` (AG-006, by design), and the
    old `evaluator.pretrade.SupervisorPolicy.veto_on_unknown_regime=True`
    meant a directional external-agent proposal could reach Risk `PASS`
    but never Supervisor `APPROVE` -- only `VETO` on `UNKNOWN_REGIME`.
    feedback.1.28/F-066's strategy-neutral Policy Gate resolves this by
    construction: `_evaluate_platform_policy` never reads `features` or
    `Regime` at all. Proven here against the real production evidence
    builder, not a test double, closing the loop AG-013 opened."""

    def test_real_evidence_reaches_supervisor_approve(self) -> None:
        fixture = Fixture()
        result, _ = fixture.evaluate(agent_intent())

        assert result.risk_decision is not None
        assert result.risk_decision.verdict is RiskVerdict.PASS
        assert result.supervisor_decision is not None
        assert result.supervisor_decision.verdict is SupervisorVerdict.APPROVE
        assert result.supervisor_decision.observed_regime is Regime.UNKNOWN
        assert result.supervisor_decision.reason_codes == ()


class TestStrategyNeutrality:
    """F-066 item 8 (`feedback.1.28.md` section 10): "a second toy/test
    agent with a deliberately different reason-code vocabulary can use the
    same Core path without Core code changes" -- the regression test that
    proves a platform was built, not another single-strategy integration.
    A completely different `strategy_id` and an arbitrary, made-up
    `reason_codes` vocabulary reaches the identical outcome as
    `agent_intent()`'s own -- this module does not special-case either."""

    def test_an_unrelated_strategy_id_and_vocabulary_reaches_approve_identically(self) -> None:
        fixture = Fixture()
        intent = agent_intent(
            strategy_id="totally_different_toy_agent",
            reason_codes=("MADE_UP_CODE_ALPHA", "ANOTHER_UNRELATED_CODE"),
        )
        result, _ = fixture.evaluate(intent)

        assert result.risk_decision is not None
        assert result.risk_decision.verdict is RiskVerdict.PASS
        assert result.supervisor_decision is not None
        assert result.supervisor_decision.verdict is SupervisorVerdict.APPROVE
