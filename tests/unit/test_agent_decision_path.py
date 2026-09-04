"""agent_gateway/decision_path.py -- TradeIntent -> Risk -> Policy -> capsule.

Review 1.26 section 7 item 3 / feedback.1.27 section 6.A. Uses a fake
`PortfolioStateProvider` throughout, per the owner-requested reviewer
decision (`review/INTEGRATION_NOTICES.md`, 2026-09-01): a fake is correct
for this level of proof, a genuine LIVE_SHADOW claim needs Core's own
broker-state capture instead.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from crumblr.agent_gateway.contracts import (
    AgentIdentity,
    AgentRole,
    AgentStatus,
    ChampionShadowStatus,
    ExternalSupervisorReviewRecord,
    ExternalSupervisorVerdict,
    SupervisorReview,
    TradeProposal,
    TradingAssignment,
)
from crumblr.agent_gateway.decision_path import (
    AgentDecisionPathResult,
    PortfolioSnapshot,
    evaluate_agent_trade_intent,
)
from crumblr.agent_gateway.evidence import build_agent_context_evidence
from crumblr.agent_gateway.gateway import AgentGateway
from crumblr.agent_gateway.reference_supervisor import (
    LOW_CONFIDENCE,
    ReferenceSupervisor,
    ReferenceSupervisorConfig,
)
from crumblr.agent_gateway.stores import (
    InMemoryAgentCredentialStore,
    InMemoryAgentDecisionOutcomeStore,
    InMemoryAgentIdentityStore,
    InMemoryDecisionContextBundleStore,
    InMemoryFeatureEvidenceStore,
    InMemoryTradingAssignmentStore,
)
from crumblr.config import PlatformConfig
from crumblr.domain.enums import (
    DataQuality,
    EntryType,
    Environment,
    IncidentStatus,
    ReasonCode,
    ReconciliationStatus,
    Regime,
    RiskVerdict,
    SessionState,
    Side,
    SupervisorVerdict,
)
from crumblr.domain.models import (
    AccountState,
    Contract,
    InstrumentSpec,
    MarketSnapshot,
    PositionState,
    TradeIntent,
)
from crumblr.risk.kill_switch import KillSwitch
from crumblr.risk.session import (
    InMemoryRiskLedgerLock,
    InMemoryRiskSessionStore,
    RiskLedgerLock,
    RiskSessionState,
)
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
    risk_ledger_lock: RiskLedgerLock = field(default_factory=InMemoryRiskLedgerLock)
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
            "risk_ledger_lock": self.risk_ledger_lock,
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


def agent_proposal(**overrides: Any) -> TradeProposal:
    """A `TradeProposal` for the external-Supervisor wiring tests below --
    content need not match any particular `agent_intent()`, since the
    wiring binds proposal and intent explicitly by id/hash, not by
    field-for-field agreement."""
    fields: dict[str, Any] = {
        "proposal_id": uuid4(),
        "agent_id": uuid4(),
        "assignment_id": uuid4(),
        "context_hash": "context-hash-abc",
        "strategy_artifact_hash": "abc123",
        "side": Side.BUY,
        "entry_type": EntryType.MARKET,
        "reference_price": Decimal("1.08500"),
        "stop_loss_price": Decimal("1.08000"),
        "take_profit_price": Decimal("1.09000"),
        "confidence": 0.8,
        "requested_risk_fraction": Decimal("0.005"),
        "reason_codes": ("sweep_and_shift",),
        "evidence_refs": (),
        "submitted_at_utc": FIXED_NOW,
        "expires_at_utc": FIXED_NOW + timedelta(minutes=5),
    }
    fields.update(overrides)
    return TradeProposal.model_validate(fields)


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


class SpyRiskLedgerLock:
    """Records every `held()` call and the sentinel connection it yields --
    proves this module actually acquires ADR-021's lock around its
    `session_store.load_latest()` read, not merely that a fresh read still
    happens (`TestAG012FreshSessionRecoveryEveryCall` above already proved
    that part; this proves the lock is the mechanism now, not just a
    fresh-every-call habit)."""

    def __init__(self) -> None:
        self.held_for: list[str] = []
        self.connection = object()

    @contextmanager
    def held(self, canonical_symbol: str) -> Iterator[Any]:
        self.held_for.append(canonical_symbol)
        yield self.connection


class ConnectionCapturingSessionStore(InMemoryRiskSessionStore):
    """Records the `connection` it was called with, so a test can assert
    it is exactly the one the lock yielded -- not merely that some
    connection was passed."""

    def __init__(self) -> None:
        super().__init__()
        self.load_latest_connections: list[Any] = []

    def load_latest(self, *, connection: Any = None) -> Any:
        self.load_latest_connections.append(connection)
        return super().load_latest(connection=connection)


class TestAG012RiskLedgerLockAcquired:
    """ADR-021: the fresh-every-call recovery above now runs inside
    `RiskLedgerLock.held(canonical_symbol)`, serializing this read against
    `LiveDecisionOrchestrator`'s own locked recover-update-persist cycle
    and `ExecutionOrchestrator`'s FINAL Risk read -- not merely re-reading
    the store, which alone is not race-free against a concurrent writer's
    read-modify-write (AG-012's original finding)."""

    def test_the_lock_is_acquired_for_the_snapshot_canonical_symbol(self) -> None:
        lock = SpyRiskLedgerLock()
        fixture = Fixture(risk_ledger_lock=lock)
        assert fixture.snapshot is not None

        fixture.evaluate(agent_intent())

        assert lock.held_for == [fixture.snapshot.symbol]

    def test_the_store_read_runs_inside_the_lock_yielded_connection(self) -> None:
        lock = SpyRiskLedgerLock()
        store = ConnectionCapturingSessionStore()
        fixture = Fixture(risk_ledger_lock=lock, session_store=store)

        fixture.evaluate(agent_intent())

        assert store.load_latest_connections == [lock.connection]

    def test_a_no_trade_evaluation_never_acquires_the_lock(self) -> None:
        """Mirrors `TestNoTrade::test_no_trade_never_touches_the_risk_session_store`
        -- NO_TRADE cannot breach a loss gate, so there is nothing here to
        serialize against a concurrent writer for."""
        lock = SpyRiskLedgerLock()
        fixture = Fixture(risk_ledger_lock=lock)

        fixture.evaluate(None)

        assert lock.held_for == []

    def test_the_lock_is_released_before_policy_evaluation_runs(self) -> None:
        """The lock's scope is `recover` only, matching `live_decision.py`'s
        own choice to release before the CPU-bound Risk/Policy evaluation
        -- proven here by a lock whose `held()` raises if entered twice
        (would happen if some later step tried to re-acquire it while
        still "held", the way a non-reentrant real Postgres advisory lock
        held twice by the same session would just silently stack rather
        than deadlock, masking a scoping bug)."""

        class SingleEntryLock(SpyRiskLedgerLock):
            def __init__(self) -> None:
                super().__init__()
                self._entered = False

            @contextmanager
            def held(self, canonical_symbol: str) -> Iterator[Any]:
                assert not self._entered, "lock re-entered while still held"
                self._entered = True
                try:
                    with super().held(canonical_symbol) as connection:
                        yield connection
                finally:
                    self._entered = False

        fixture = Fixture(risk_ledger_lock=SingleEntryLock())
        result, _ = fixture.evaluate(agent_intent())

        assert result.risk_decision is not None
        assert result.risk_decision.verdict is RiskVerdict.PASS


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


class TestStrategyNeutralityThroughTheRealGateway:
    """F-066 item 8, taken all the way through `AgentGateway` itself --
    `TestStrategyNeutrality` above proves this module's own arguments are
    not special-cased, but a `TradeIntent` built by hand does not prove
    the *boundary* is strategy-neutral, only this one function. Two
    independently-registered agents -- different identity, assignment,
    `StrategyArtifact` hash, and a completely unrelated reason-code
    vocabulary each -- are onboarded and submit a proposal through the
    real, unmodified `AgentGateway.submit_trade_proposal`, and the
    resulting real `TradeIntent`s (not hand-built ones) reach an
    identical `RiskVerdict.PASS`/`SupervisorVerdict.APPROVE` through this
    module. Zero code in either `AgentGateway` or `decision_path.py`
    branches on which agent produced the intent -- the regression proof
    `feedback.1.28.md` section 10 item 8 calls "a platform was built, not
    another single-strategy integration.\""""

    def _onboard_and_submit(
        self,
        gateway: AgentGateway,
        snapshot: MarketSnapshot,
        spec: InstrumentSpec,
        *,
        agent_suffix: str,
        reason_codes: tuple[str, ...],
        strategy_artifact_hash: str,
    ) -> TradeIntent:
        agent_id = uuid4()
        assignment_id = uuid4()
        credential_secret = f"secret-{agent_suffix}"
        gateway.register_identity(
            AgentIdentity(
                agent_id=agent_id,
                role=AgentRole.TRADER,
                runtime_version=f"{agent_suffix}-v1",
                service_identity=f"spiffe://crumblr/agents/{agent_suffix}",
                status=AgentStatus.ACTIVE,
                registered_at_utc=FIXED_NOW,
            ),
            credential_secret=credential_secret,
        )
        gateway.issue_assignment(
            TradingAssignment(
                assignment_id=assignment_id,
                assignment_version="assignment-v1",
                allowed_agent_id=agent_id,
                canonical_symbol="EUR/USD",
                timeframe="M5",
                strategy_artifact_id=uuid4(),
                strategy_artifact_hash=strategy_artifact_hash,
                valid_from_utc=FIXED_NOW - timedelta(days=1),
                valid_until_utc=FIXED_NOW + timedelta(days=30),
                max_proposals_per_hour=10,
                allowed_risk_fraction_min=Decimal("0.001"),
                allowed_risk_fraction_max=Decimal("0.01"),
                required_evidence_fields=(),
                supervisor_policy_version="supervisor-policy-v1",
                environment=Environment.PAPER,
                champion_shadow_status=ChampionShadowStatus.SHADOW,
            )
        )
        bundle = gateway.publish_context(
            assignment_id=assignment_id,
            symbol="EUR/USD",
            market_snapshot_id=snapshot.snapshot_id,
            instrument_spec_version=spec.spec_version,
            portfolio_summary_hash=f"portfolio-{agent_suffix}",
            session_state=SessionState.OPEN,
            data_quality=DataQuality.GOOD,
            now=FIXED_NOW,
        )
        proposal = TradeProposal(
            proposal_id=uuid4(),
            agent_id=agent_id,
            assignment_id=assignment_id,
            context_hash=bundle.content_hash,
            strategy_artifact_hash=strategy_artifact_hash,
            side=Side.BUY,
            entry_type=EntryType.MARKET,
            reference_price=Decimal("1.08500"),
            stop_loss_price=Decimal("1.08000"),
            take_profit_price=Decimal("1.09000"),
            confidence=0.8,
            requested_risk_fraction=Decimal("0.005"),
            reason_codes=reason_codes,
            evidence_refs=(),
            submitted_at_utc=FIXED_NOW,
            expires_at_utc=FIXED_NOW + timedelta(minutes=5),
        )
        result = gateway.submit_trade_proposal(
            agent_id=agent_id,
            credential_secret=credential_secret,
            proposal=proposal,
            now=FIXED_NOW,
        )
        assert result.accepted, f"{agent_suffix} proposal rejected: {result.reason}"
        assert result.trade_intent is not None
        return result.trade_intent

    def test_two_unrelated_agents_reach_identical_approval_through_the_real_gateway(
        self,
    ) -> None:
        gateway = AgentGateway(
            identities=InMemoryAgentIdentityStore(),
            credentials=InMemoryAgentCredentialStore(),
            assignments=InMemoryTradingAssignmentStore(),
            contexts=InMemoryDecisionContextBundleStore(),
            outcomes=InMemoryAgentDecisionOutcomeStore(),
            feature_evidence=InMemoryFeatureEvidenceStore(),
        )
        spec = make_instrument_spec()
        snapshot = make_snapshot(symbol_spec_version=spec.spec_version)

        alpha_intent = self._onboard_and_submit(
            gateway,
            snapshot,
            spec,
            agent_suffix="alpha-momentum",
            reason_codes=("ALPHA_MOMENTUM_BREAK", "ALPHA_VOLUME_CONFIRM"),
            strategy_artifact_hash="alpha-artifact-v1",
        )
        beta_intent = self._onboard_and_submit(
            gateway,
            snapshot,
            spec,
            agent_suffix="beta-meanrevert",
            reason_codes=("BETA_MEAN_REVERT_SETUP", "BETA_RSI_DIVERGENCE"),
            strategy_artifact_hash="beta-artifact-v9",
        )

        # The Gateway itself already produced genuinely different intents --
        # not a test artifact, proof the two agents were never merged into
        # one identity/assignment/artifact along the way.
        assert alpha_intent.strategy_version != beta_intent.strategy_version
        assert alpha_intent.reason_codes != beta_intent.reason_codes

        alpha_result, _ = Fixture(spec=spec, snapshot=snapshot).evaluate(
            alpha_intent, strategy_version=alpha_intent.strategy_version
        )
        beta_result, _ = Fixture(spec=spec, snapshot=snapshot).evaluate(
            beta_intent, strategy_version=beta_intent.strategy_version
        )

        for label, result, intent in (
            ("alpha", alpha_result, alpha_intent),
            ("beta", beta_result, beta_intent),
        ):
            assert result.risk_decision is not None, label
            assert result.risk_decision.verdict is RiskVerdict.PASS, label
            assert result.supervisor_decision is not None, label
            assert result.supervisor_decision.verdict is SupervisorVerdict.APPROVE, label
            # The sealed capsule carries each agent's own real identity
            # through unmodified -- not collapsed onto a shared/default one.
            assert result.capsule.trade_intent is intent, label
            assert result.capsule.strategy_version == intent.strategy_version, label


class CountingSupervisor:
    """Wraps a real `ExternalSupervisorProvider` and counts calls -- used
    to prove the external Supervisor is never asked about a proposal the
    Policy Gate already refused."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.call_count = 0

    def review(self, **kwargs: Any) -> Any:
        self.call_count += 1
        return self._inner.review(**kwargs)


class NeverReturnsSupervisor:
    """Simulates a transport-backed Supervisor that never got a response
    (a timeout) -- returns `None`, exactly like a real HTTP client would
    on a failed call."""

    def review(self, **kwargs: Any) -> None:
        return None


class TestExternalSupervisorWiring:
    """AG-003 (`review/AGENT_FEEDBACK.md`), Phase C
    (`review/OWNER_WORK_ORDERS_DEMO_CANARY_2026-09-03.md`): the external
    Supervisor is a second, distinct veto layer from the strategy-neutral
    Policy Gate, wired in via an injected `ExternalSupervisorProvider` --
    never called via HTTP from inside this module."""

    def test_omitting_proposal_and_provider_skips_the_step_entirely(self) -> None:
        """Backward compatibility: every existing caller (PAPER_LITE
        included) that does not pass `proposal`/`external_supervisor`
        must see zero behaviour change."""
        fixture = Fixture()
        result, recorder = fixture.evaluate(agent_intent())

        assert result.external_supervisor_outcome is None
        assert result.external_supervisor_record is None
        assert not any(source == "external_supervisor" for _, _, _, source in recorder.events)

    def test_a_provider_without_a_proposal_is_also_skipped(self) -> None:
        supervisor = ReferenceSupervisor(
            ReferenceSupervisorConfig(supervisor_agent_id=uuid4(), min_confidence=0.0)
        )
        fixture = Fixture()
        result, _ = fixture.evaluate(agent_intent(), external_supervisor=supervisor)
        assert result.external_supervisor_outcome is None

    def test_an_approving_reference_supervisor_reaches_approve(self) -> None:
        supervisor = ReferenceSupervisor(
            ReferenceSupervisorConfig(supervisor_agent_id=uuid4(), min_confidence=0.0)
        )
        fixture = Fixture()
        result, recorder = fixture.evaluate(
            agent_intent(), proposal=agent_proposal(confidence=0.9), external_supervisor=supervisor
        )

        assert result.external_supervisor_outcome is not None
        assert result.external_supervisor_outcome.verdict is ExternalSupervisorVerdict.APPROVE
        assert result.external_supervisor_record is not None
        assert result.external_supervisor_record.verdict is ExternalSupervisorVerdict.APPROVE
        # Not durably recorded by decision_path.py itself -- see the
        # module docstring's "External Supervisor" section for why.
        assert not any(
            isinstance(payload, ExternalSupervisorReviewRecord) for payload, *_ in recorder.events
        )

    def test_a_vetoing_reference_supervisor_does_not_change_risk_or_policy(self) -> None:
        """The external Supervisor's own VETO is recorded and exposed, but
        never overwrites/relabels the Policy Gate's own APPROVE -- "two
        different veto layers" (Phase C's own instruction), never merged
        into one."""
        supervisor = ReferenceSupervisor(
            ReferenceSupervisorConfig(supervisor_agent_id=uuid4(), min_confidence=0.99)
        )
        fixture = Fixture()
        result, _ = fixture.evaluate(
            agent_intent(), proposal=agent_proposal(confidence=0.1), external_supervisor=supervisor
        )

        assert result.external_supervisor_outcome is not None
        assert result.external_supervisor_outcome.verdict is ExternalSupervisorVerdict.VETO
        assert LOW_CONFIDENCE in result.external_supervisor_outcome.reason_codes
        assert result.risk_decision is not None
        assert result.risk_decision.verdict is RiskVerdict.PASS
        assert result.supervisor_decision is not None
        assert result.supervisor_decision.verdict is SupervisorVerdict.APPROVE

    def test_a_missing_supervisor_response_is_unknown_not_a_silent_approval(self) -> None:
        fixture = Fixture()
        result, _ = fixture.evaluate(
            agent_intent(), proposal=agent_proposal(), external_supervisor=NeverReturnsSupervisor()
        )

        assert result.external_supervisor_outcome is not None
        assert result.external_supervisor_outcome.verdict is ExternalSupervisorVerdict.UNKNOWN
        assert result.external_supervisor_record is not None
        assert result.external_supervisor_record.verdict is ExternalSupervisorVerdict.UNKNOWN
        assert result.external_supervisor_record.review_id is None

    def test_two_different_outcomes_for_the_same_proposal_get_different_record_ids(self) -> None:
        """Self-review regression: `record_id` must not collide when the
        same proposal/intent pair produces two different outcomes over
        time (e.g. a timeout on one attempt, a real APPROVE on a retry) --
        keyed on outcome content, not only `trade_intent_decision_hash`."""
        intent = agent_intent()
        the_proposal = agent_proposal()
        fixture = Fixture()

        unknown_result, _ = fixture.evaluate(
            intent, proposal=the_proposal, external_supervisor=NeverReturnsSupervisor()
        )
        approve_result, _ = fixture.evaluate(
            intent,
            proposal=the_proposal,
            external_supervisor=ReferenceSupervisor(
                ReferenceSupervisorConfig(supervisor_agent_id=uuid4(), min_confidence=0.0)
            ),
        )

        assert unknown_result.external_supervisor_record is not None
        assert approve_result.external_supervisor_record is not None
        assert (
            unknown_result.external_supervisor_record.record_id
            != approve_result.external_supervisor_record.record_id
        )

    def test_two_different_unknown_reasons_also_get_different_record_ids(self) -> None:
        """Self-review regression, second pass: the first fix keyed
        `record_id` on `verdict`/`review_id` alone, which still collides
        for two different `UNKNOWN` outcomes -- both have `verdict=
        "UNKNOWN"` and `review_id=None` regardless of *why* they are
        `UNKNOWN`. `reason_codes` has to be part of the key too."""

        class WrongProposalSupervisor:
            def review(self, *, proposal: Any, intent: Any, **kwargs: Any) -> SupervisorReview:
                return ReferenceSupervisor(
                    ReferenceSupervisorConfig(supervisor_agent_id=uuid4(), min_confidence=0.0)
                ).review(proposal=agent_proposal(proposal_id=uuid4()), intent=intent, **kwargs)

        intent = agent_intent()
        the_proposal = agent_proposal()
        fixture = Fixture()

        no_response_result, _ = fixture.evaluate(
            intent, proposal=the_proposal, external_supervisor=NeverReturnsSupervisor()
        )
        mismatch_result, _ = fixture.evaluate(
            intent, proposal=the_proposal, external_supervisor=WrongProposalSupervisor()
        )

        no_response_outcome = no_response_result.external_supervisor_outcome
        mismatch_outcome = mismatch_result.external_supervisor_outcome
        assert no_response_outcome is not None
        assert mismatch_outcome is not None
        assert no_response_outcome.verdict is ExternalSupervisorVerdict.UNKNOWN
        assert mismatch_outcome.verdict is ExternalSupervisorVerdict.UNKNOWN
        assert no_response_outcome.reason_codes != mismatch_outcome.reason_codes
        assert no_response_result.external_supervisor_record is not None
        assert mismatch_result.external_supervisor_record is not None
        assert (
            no_response_result.external_supervisor_record.record_id
            != mismatch_result.external_supervisor_record.record_id
        )

    def test_the_supervisor_is_never_asked_when_the_policy_gate_does_not_approve(self) -> None:
        supervisor = CountingSupervisor(
            ReferenceSupervisor(
                ReferenceSupervisorConfig(supervisor_agent_id=uuid4(), min_confidence=0.0)
            )
        )
        fixture = Fixture(reconciliation_status=ReconciliationStatus.UNKNOWN)
        result, _ = fixture.evaluate(
            agent_intent(), proposal=agent_proposal(), external_supervisor=supervisor
        )

        assert result.supervisor_decision is not None
        assert result.supervisor_decision.verdict is SupervisorVerdict.HALT
        assert supervisor.call_count == 0
        assert result.external_supervisor_outcome is None

    def test_the_supervisor_is_never_asked_when_risk_blocks(self) -> None:
        supervisor = CountingSupervisor(
            ReferenceSupervisor(
                ReferenceSupervisorConfig(supervisor_agent_id=uuid4(), min_confidence=0.0)
            )
        )
        fixture = Fixture()
        intent = agent_intent(requested_risk_fraction=Decimal("0.05"))
        result, _ = fixture.evaluate(
            intent, proposal=agent_proposal(), external_supervisor=supervisor
        )

        assert result.risk_decision is not None
        assert result.risk_decision.verdict is RiskVerdict.BLOCK
        assert supervisor.call_count == 0

    def test_the_provider_is_passed_the_exact_risk_and_policy_decision_ids(self) -> None:
        """Proves `evaluate_agent_trade_intent` threads the real
        `risk_decision.decision_id`/`supervisor_decision.decision_id`
        into the `ExternalSupervisorProvider.review()` call, and that
        `ReferenceSupervisor` correctly echoes them into the
        `SupervisorReview` it builds -- **not** that
        `evaluate_supervisor_review()` would reject a review carrying the
        wrong ones. It does not check `risk_decision_id`/
        `policy_gate_decision_id` at all (self-review finding, tracked as
        a known gap in `review/AGENT_FEEDBACK.md` AG-003 rather than
        silently expanded here): those two fields are documented as
        optional/audit-completing on `SupervisorReview`, not part of the
        binding `evaluate_supervisor_review()` enforces today
        (`proposal_id`/`trade_intent_id`/`trade_intent_decision_hash`/
        expiry only)."""
        supervisor = ReferenceSupervisor(
            ReferenceSupervisorConfig(supervisor_agent_id=uuid4(), min_confidence=0.0)
        )
        fixture = Fixture()
        result, _ = fixture.evaluate(
            agent_intent(), proposal=agent_proposal(), external_supervisor=supervisor
        )

        assert result.external_supervisor_outcome is not None
        review = result.external_supervisor_outcome.review
        assert review is not None
        assert result.risk_decision is not None
        assert result.supervisor_decision is not None
        assert result.capsule.trade_intent is not None
        assert review.risk_decision_id == result.risk_decision.decision_id
        assert review.policy_gate_decision_id == result.supervisor_decision.decision_id
        assert review.trade_intent_id == result.capsule.trade_intent.intent_id
