"""The transaction flow (build.md §3).

    OBSERVE → SIGNAL → INTENT → RISK → SUPERVISOR → ORDER BUILD
    → order_check → order_send → RECONCILE → EVALUATE → AUDIT

No stage may be skipped, so the loop below runs them in order and records the
outcome of each one into a decision capsule — including the windows where
nothing was traded, which are the majority and are still evidence.

The orchestrator holds no broker credentials and makes no trading judgement of
its own. It moves objects between components that do.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from crumblr.application.recording import NullRecorder, RunRecorder
from crumblr.config import PlatformConfig
from crumblr.domain.enums import (
    IncidentStatus,
    ReasonCode,
    ReconciliationStatus,
    RiskVerdict,
    SupervisorVerdict,
)
from crumblr.domain.events import (
    OrderResultReceived,
    OrderSubmitted,
    PositionChanged,
    SignalGenerated,
    SystemHalted,
)
from crumblr.domain.models import (
    ApprovedOrder,
    Bar,
    DecisionCapsule,
    ExecutionResult,
    InstrumentSpec,
    MarketSnapshot,
    PositionState,
    RiskDecision,
    SupervisorDecision,
    TradeIntent,
)
from crumblr.domain.money import ZERO
from crumblr.evaluator import pretrade
from crumblr.market_data.synthetic import (
    GeneratedTick,
    as_market_bar,
    as_market_tick,
    build_snapshot,
    snapshot_id_for,
)
from crumblr.mt5_gateway.simulated import ClosedTrade, SimulatedBroker
from crumblr.observability.logging import get_logger
from crumblr.risk import policies, session, trading_window
from crumblr.risk.kill_switch import EquityLedger, KillSwitch
from crumblr.risk.session import InMemoryRiskSessionStore, RiskSessionStore
from crumblr.trading_agent import registry
from crumblr.trading_agent.base import AgentContext, FeatureEvidence
from crumblr.trading_agent.sessions import trading_day

MAX_HISTORY_BARS = 400
"""Rolling window handed to the feature pipeline."""

CODE_COMMIT = "uncommitted-prototype"

_log = get_logger("orchestration")


@dataclass
class RunTally:
    """Counters for the run report. Every outcome is counted somewhere."""

    windows: int = 0
    features_unavailable: int = 0
    no_trade: int = 0
    intents: int = 0
    risk_passed: int = 0
    risk_blocked: int = 0
    risk_halted: int = 0
    supervisor_approved: int = 0
    supervisor_vetoed: int = 0
    supervisor_halted: int = 0
    order_check_rejected: int = 0
    orders_filled: int = 0
    no_trade_reasons: dict[str, int] = field(default_factory=dict)
    risk_reasons: dict[str, int] = field(default_factory=dict)
    supervisor_reasons: dict[str, int] = field(default_factory=dict)
    injected_faults: dict[str, int] = field(default_factory=dict)

    def count(self, bucket: dict[str, int], key: str) -> None:
        bucket[key] = bucket.get(key, 0) + 1


@dataclass
class RunResult:
    """Everything a run produced, for reporting and for assertions in tests."""

    tally: RunTally
    capsules: list[DecisionCapsule]
    closed_trades: list[ClosedTrade]
    starting_equity: Decimal
    final_equity: Decimal
    peak_equity: Decimal
    max_drawdown_fraction: Decimal
    halted: bool
    halt_reasons: tuple[ReasonCode, ...]

    session_start_equity: Decimal = ZERO
    """The baseline the daily-loss gate measured against.

    Not the same as `starting_equity` once a run resumes a session another
    process began — which is the observable difference F-019 is about."""

    session_resumed: bool = False
    """Whether this run picked up a persisted risk session."""

    uncalibrated_checks: tuple[str, ...] = ()
    """Supervisor checks that could not fail during this run (F-024).

    On the report rather than only in the capsules, because a run that made no
    decisions would otherwise show an empty list and read as full coverage."""

    halt_detail: str | None = None
    """What the halt said, in words.

    A reason code names the rule; the detail names the situation. An operator
    clearing a halt needs both, and the report is where they will look first."""

    @property
    def net_profit(self) -> Decimal:
        return self.final_equity - self.starting_equity


class ReplayOrchestrator:
    """Drives one replay from end to end."""

    def __init__(
        self,
        config: PlatformConfig,
        spec: InstrumentSpec,
        broker: SimulatedBroker,
        *,
        starting_equity: Decimal,
        recorder: RunRecorder | None = None,
        kill_switch: KillSwitch | None = None,
        session_store: RiskSessionStore | None = None,
    ) -> None:
        self._config = config
        self._spec = spec
        self._broker = broker
        # An unknown strategy id fails here rather than silently defaulting —
        # which trade was taken, and by what, is the basis of the audit trail.
        self._strategy = registry.resolve(config.trading_agent.strategy_id)

        # Both dependencies default to their forgetful implementations, which
        # is what a replay with no database behind it needs. A durable run is
        # assembled in `application.bootstrap` and injected here: the
        # orchestrator moves objects between components and does not decide
        # whether the system has a memory.
        self._recorder: RunRecorder = recorder if recorder is not None else NullRecorder()
        self._kill_switch = kill_switch if kill_switch is not None else KillSwitch()
        self._session_store: RiskSessionStore = (
            session_store if session_store is not None else InMemoryRiskSessionStore()
        )
        # Replaced by whatever the recorded session recovers to, on the first
        # tick — the market day is not known until then. Until that happens
        # this is what a first start would use anyway.
        self._ledger = EquityLedger(starting_equity=starting_equity)
        self._session_key: tuple[object, ...] | None = None
        self._session_resumed = False
        self._history: deque[Bar] = deque(maxlen=MAX_HISTORY_BARS)
        self._seen_hashes: set[str] = set()
        self._order_times: deque[datetime] = deque()
        self._recent_intents: deque[datetime] = deque()
        self._current_trading_day: date | None = None

        self._risk_context = policies.RiskContext(
            risk=config.risk,
            execution=config.execution,
            allowed_symbols=frozenset(config.enabled_symbols()),
            require_demo_account=config.account_guard.require_demo_account,
            expected_server=config.account_guard.expected_server,
            expected_login=config.account_guard.expected_login,
            expected_currency=config.account_guard.expected_currency,
            expected_leverage=config.account_guard.expected_leverage,
            risk_config_version=config.config_version,
            intraday=trading_window.policy_from_config(config.intraday),
        )
        self._policy = pretrade.SupervisorPolicy(
            enabled=config.supervisor.enabled,
            veto_on_unknown_regime=config.supervisor.veto_on_unknown_regime,
            allowed_strategy_ids=frozenset({config.trading_agent.strategy_id}),
            allowed_model_versions=None,
            # `None` here is "not calibrated", not "no limit" — see F-024 and
            # the comment in config/base.yaml.
            max_intents_per_hour=config.supervisor.max_intents_per_hour,
        )

    def run(self, ticks: list[GeneratedTick]) -> RunResult:
        tally = RunTally()
        capsules: list[DecisionCapsule] = []
        starting_equity = self._ledger.starting_equity
        _log.info(
            "replay.started",
            bars=len(ticks),
            strategy_id=self._strategy.strategy_id,
            strategy_version=self._strategy.version,
            environment=self._config.environment.value,
            config_version=self._config.config_version,
            starting_equity=str(starting_equity),
        )

        for index, tick in enumerate(ticks):
            self._broker.advance(tick)
            if index == 0:
                # Recovery needs two things that do not exist until the first
                # tick has landed: the market day, and a broker with a clock.
                self._recover_session(tick)
                starting_equity = self._ledger.starting_equity
            self._roll_session(tick)
            self._ledger.update(self._broker.equity)
            if tick.injected_fault:
                tally.count(tally.injected_faults, tick.injected_fault)

            # Recorded before anything is decided about it, and recorded for
            # every window — including the warm-up ones, which produce no event
            # and would otherwise leave no trace that the system ever saw them
            # (review 1.6 F-022).
            self._recorder.observe(
                as_market_tick(tick, self._spec),
                as_market_bar(tick, self._spec, timeframe=self._config_timeframe()),
            )

            self._history.append(tick.bar)
            tally.windows += 1

            capsule = self._process_window(tick, tally)
            if capsule is not None:
                capsules.append(capsule)

            self._check_loss_gates(tick)
            self._check_session_boundary(tick)
            self._persist_session(tick)

        self._ledger.update(self._broker.equity)
        # Anything buffered by a window that never sealed — the tail of a run
        # that ended mid-window, or a halt raised after the last capsule.
        self._recorder.flush()
        _log.info(
            "replay.finished",
            windows=tally.windows,
            intents=tally.intents,
            orders_filled=tally.orders_filled,
            halted=self._kill_switch.is_halted,
            final_equity=str(self._broker.equity),
            max_drawdown=str(self._ledger.max_drawdown_fraction),
        )
        return RunResult(
            tally=tally,
            capsules=capsules,
            closed_trades=list(self._broker.closed_trades),
            starting_equity=starting_equity,
            final_equity=self._broker.equity,
            peak_equity=self._ledger.peak_equity,
            max_drawdown_fraction=self._ledger.max_drawdown_fraction,
            halted=self._kill_switch.is_halted,
            halt_reasons=self._kill_switch.active_reasons,
            session_start_equity=self._ledger.session_start_equity,
            session_resumed=self._session_resumed,
            uncalibrated_checks=pretrade.uncalibrated_checks(self._policy),
            halt_detail=(
                self._kill_switch.history[-1].detail
                if self._kill_switch.is_halted and self._kill_switch.history
                else None
            ),
        )

    # ------------------------------------------------------------------ #

    def _process_window(self, tick: GeneratedTick, tally: RunTally) -> DecisionCapsule | None:
        """One decision window, from observation to audit."""
        history = tuple(self._history)
        if len(history) < self._strategy.minimum_bars:
            tally.features_unavailable += 1
            return None

        snapshot = build_snapshot(
            tick,
            history=history,
            spec=self._spec,
            timeframe=self._config_timeframe(),
        )

        positions_before = self._broker.positions()
        outcome = self._strategy.evaluate(
            snapshot,
            history,
            self._spec,
            AgentContext(
                open_position_sides=tuple(p.side for p in positions_before),
                requested_risk_fraction=self._config.risk.max_risk_per_trade,
                min_stop_distance_points=self._config.risk.min_stop_distance_points,
            ),
        )

        if outcome.features is None:
            tally.features_unavailable += 1
            return None

        features = outcome.features
        decision = outcome.decision

        self._recorder.record(
            SignalGenerated(
                signal_id=uuid5(
                    NAMESPACE_URL,
                    f"crumblr:signal:{snapshot.snapshot_id}:{self._strategy.strategy_id}",
                ),
                snapshot_id=snapshot.snapshot_id,
                symbol=snapshot.symbol,
                strategy_id=self._strategy.strategy_id,
                strategy_version=self._strategy.version,
                model_version=self._config.trading_agent.model_version,
                proposed_side=decision.side,
                confidence=decision.confidence,
                regime=features.regime,
                feature_snapshot_id=features.feature_snapshot_id,
                feature_set_version=features.feature_set_version,
                reason_codes=decision.reason_codes,
            ),
            correlation_id=snapshot.snapshot_id,
            occurred_at_utc=snapshot.event_time_utc,
            source="trading_agent",
        )

        if decision.intent is None:
            tally.no_trade += 1
            for reason in decision.reason_codes:
                tally.count(tally.no_trade_reasons, reason)
            return self._seal(snapshot, features, None, None, None, None, positions_before)

        tally.intents += 1
        intent = decision.intent
        self._recorder.record(
            intent,
            correlation_id=snapshot.snapshot_id,
            occurred_at_utc=snapshot.event_time_utc,
            source="trading_agent",
        )
        self._recent_intents.append(snapshot.event_time_utc)
        self._trim_window(self._recent_intents, snapshot.event_time_utc, timedelta(hours=1))

        risk_decision = policies.evaluate(
            intent,
            snapshot,
            self._spec,
            self._portfolio(positions_before),
            self._risk_context,
            self._kill_switch,
            now=snapshot.received_time_utc,
        )
        self._recorder.record(
            risk_decision,
            correlation_id=snapshot.snapshot_id,
            occurred_at_utc=snapshot.event_time_utc,
            source="risk_engine",
        )
        self._record_risk(risk_decision, tally, snapshot)

        if risk_decision.verdict is not RiskVerdict.PASS:
            return self._seal(
                snapshot, features, intent, risk_decision, None, None, positions_before
            )

        supervisor_decision = pretrade.evaluate(
            intent,
            features,
            self._policy,
            pretrade.SupervisorContext(
                intents_in_last_hour=len(self._recent_intents),
                # Replay runs against a simulated broker whose position book is
                # the same object the orchestrator reads, so local and broker
                # state cannot diverge — reconciliation is genuinely MATCHED
                # here rather than assumed. Against a real broker this must
                # come from the reconciliation loop (M5); until that exists it
                # will report UNKNOWN, and UNKNOWN halts.
                reconciliation_status=self._reconciliation_status(),
                incident_status=self._incident_status(),
            ),
            now=snapshot.received_time_utc,
        )
        self._recorder.record(
            supervisor_decision,
            correlation_id=snapshot.snapshot_id,
            occurred_at_utc=snapshot.event_time_utc,
            source="supervisor",
        )
        self._record_supervisor(supervisor_decision, tally, snapshot)

        if supervisor_decision.verdict is not SupervisorVerdict.APPROVE:
            return self._seal(
                snapshot,
                features,
                intent,
                risk_decision,
                supervisor_decision,
                None,
                positions_before,
            )

        execution = self._execute(intent, risk_decision, supervisor_decision, snapshot, tally)
        self._seen_hashes.add(intent.decision_hash)
        return self._seal(
            snapshot,
            features,
            intent,
            risk_decision,
            supervisor_decision,
            execution,
            positions_before,
        )

    def _execute(
        self,
        intent: TradeIntent,
        risk_decision: RiskDecision,
        supervisor_decision: SupervisorDecision,
        snapshot: MarketSnapshot,
        tally: RunTally,
    ) -> ExecutionResult | None:
        """Build the order, pre-flight it, then submit it."""
        assert risk_decision.approved_volume is not None
        assert intent.stop_loss_price is not None

        order = ApprovedOrder(
            order_request_id=uuid5(NAMESPACE_URL, f"crumblr:order:{intent.decision_hash}"),
            intent_id=intent.intent_id,
            risk_decision_id=risk_decision.decision_id,
            supervisor_decision_id=supervisor_decision.decision_id,
            broker_symbol=self._spec.broker_symbol,
            side=intent.side,
            entry_type=intent.entry_type,
            volume=risk_decision.approved_volume,
            price=None,
            stop_loss_price=intent.stop_loss_price,
            take_profit_price=intent.take_profit_price,
            max_slippage_points=self._config.execution.max_slippage_points,
            created_at_utc=snapshot.received_time_utc,
            expires_at_utc=intent.expires_at_utc,
            environment=self._config.environment,
        )

        check = self._broker.order_check(order)
        self._recorder.record(
            check,
            correlation_id=snapshot.snapshot_id,
            occurred_at_utc=snapshot.event_time_utc,
            source="mt5_gateway",
        )
        if not check.accepted:
            tally.order_check_rejected += 1
            return None

        positions_before = self._broker.positions()
        self._recorder.record(
            OrderSubmitted(
                order_request_id=order.order_request_id,
                intent_id=order.intent_id,
                broker_symbol=order.broker_symbol,
                side=order.side,
                submitted_at_utc=order.created_at_utc,
                # The order request id is itself derived from the decision
                # hash, so resubmitting the same decision carries the same
                # key — which is what makes a retry after an ambiguous
                # outcome safe rather than a second position.
                idempotency_key=order.order_request_id,
            ),
            correlation_id=snapshot.snapshot_id,
            occurred_at_utc=snapshot.event_time_utc,
            source="mt5_gateway",
        )

        result = self._broker.order_send(order)
        self._recorder.record(
            OrderResultReceived(result=result, state=result.state),
            correlation_id=snapshot.snapshot_id,
            occurred_at_utc=snapshot.event_time_utc,
            source="mt5_gateway",
        )
        self._recorder.record(
            PositionChanged(
                before=positions_before,
                after=self._broker.positions(),
                trigger="order_send",
            ),
            correlation_id=snapshot.snapshot_id,
            occurred_at_utc=snapshot.event_time_utc,
            source="mt5_gateway",
        )

        tally.orders_filled += 1
        self._order_times.append(snapshot.event_time_utc)
        self._trim_window(self._order_times, snapshot.event_time_utc, timedelta(hours=1))
        return result

    # ------------------------------------------------------------------ #

    def _portfolio(self, positions: tuple[PositionState, ...]) -> policies.PortfolioState:
        # Each open position was sized to the per-trade budget, so total open
        # risk is that budget times the number of positions.
        open_risk = self._config.risk.max_risk_per_trade * Decimal(len(positions))
        return policies.PortfolioState(
            account=self._broker.account(),
            open_positions=positions,
            ledger=self._ledger,
            orders_in_last_hour=len(self._order_times),
            seen_decision_hashes=frozenset(self._seen_hashes),
            open_risk_fraction=open_risk,
        )

    def _reconciliation_status(self) -> ReconciliationStatus:
        """Whether local and broker positions are known to agree.

        In replay the broker's book *is* the only book — there is no second
        record to diverge from — so this is a real MATCHED, not a placeholder.
        The moment a real gateway is introduced this must be replaced by the
        reconciliation loop's actual result, and its absence must report
        UNKNOWN rather than defaulting to agreement (review finding F-002).
        """
        return ReconciliationStatus.MATCHED

    def _incident_status(self) -> IncidentStatus:
        """Whether the incident register is clear.

        No incident register exists yet, and replay raises none. This reports
        CLEAR because nothing in a replay can open an incident — not because
        an absent register is being read as clean.
        """
        return IncidentStatus.CLEAR

    def _recover_session(self, first: GeneratedTick) -> None:
        """Pick the risk budget up where the last process left it (F-019).

        Not doing this made a restart a way to refill a spent daily-loss
        allowance. The recovery itself lives in `risk.session`, which is
        written so that it can only ever come back with *less* headroom than
        the record it read — and halts outright when it cannot tell.
        """
        recovery = session.recover_session(
            self._session_store.load_latest(),
            live_equity=self._broker.equity,
            live_open_positions=len(self._broker.positions()),
            market_day=trading_day(first.event_time_utc),
        )
        self._ledger = recovery.ledger
        self._current_trading_day = recovery.trading_day
        self._session_resumed = recovery.resumed

        if recovery.must_halt:
            self._trip(
                reason_codes=recovery.reason_codes,
                tripped_by="risk_session_recovery",
                occurred_at_utc=first.received_time_utc,
                correlation_id=snapshot_id_for(self._spec.canonical_symbol, first.event_time_utc),
                detail=recovery.detail,
            )

    def _persist_session(self, tick: GeneratedTick) -> None:
        """Record the session whenever it has become more constrained.

        Writing every window would be a database call per bar for a value
        that mostly has not moved. Writing only when the session rolls, a new
        worst case is set, or the position book changes is enough, because
        those are the only transitions that can make a later recovery *more*
        permissive than reality if they are lost.
        """
        if self._current_trading_day is None:
            return
        state = session.snapshot(
            self._ledger,
            trading_day=self._current_trading_day,
            realized_pnl=self._broker.balance - self._ledger.starting_equity,
            open_risk_fraction=(
                self._config.risk.max_risk_per_trade * Decimal(len(self._broker.positions()))
            ),
            open_position_count=len(self._broker.positions()),
            recorded_at_utc=tick.received_time_utc,
        )
        key = (
            state.trading_day,
            state.session_start_equity,
            state.max_drawdown_fraction,
            state.max_session_loss_fraction,
            state.open_position_count,
        )
        if key == self._session_key:
            return
        self._session_key = key
        self._session_store.save(state)

    def _roll_session(self, tick: GeneratedTick) -> None:
        """Reset the daily-loss baseline when the FX trading day rolls.

        Without this the "daily" limit measures loss since the start of the
        run, so it behaves as a total-loss cap: once tripped it can never
        clear, and every later window is refused as SYSTEM_HALTED.
        """
        day = trading_day(tick.event_time_utc)
        if self._current_trading_day is None:
            self._current_trading_day = day
        elif day != self._current_trading_day:
            self._current_trading_day = day
            self._ledger.start_new_session()

    def _check_session_boundary(self, tick: GeneratedTick) -> None:
        """Halt if exposure outlived its flatten deadline (O-003).

        Checked per tick rather than only inside a decision window, because a
        position can sit through the boundary during windows where the
        strategy proposed nothing at all — which is most of them.

        This detects; it does not close. Closing needs the execution path
        (M5), and ADR-004 records what has to be built there. Detecting and
        halting is the part that can be honest today: review 1.6 §4 requires
        that failing to prove flatness never quietly becomes permission to
        hold overnight, and a halt is the only refusal strong enough for that.
        """
        if self._kill_switch.is_halted:
            return
        positions = self._broker.positions()
        policy = self._risk_context.intraday
        if not positions or not policy.enabled:
            return
        # Two ways to breach O-003, and the second is why the first is not
        # enough: at the rollover `requires_flat` goes quiet for the new day,
        # and a position that survived the old one would stop looking like a
        # breach a second after becoming one.
        past_deadline = trading_window.requires_flat(tick.event_time_utc, policy)
        crossed = any(
            trading_window.has_crossed_rollover(position.opened_at_utc, tick.event_time_utc)
            for position in positions
        )
        if not (past_deadline or crossed):
            return
        closes_at = trading_window.session_close(tick.event_time_utc)
        self._trip(
            reason_codes=(ReasonCode.OVERNIGHT_EXPOSURE,),
            tripped_by="risk_engine",
            occurred_at_utc=tick.received_time_utc,
            correlation_id=snapshot_id_for(self._spec.canonical_symbol, tick.event_time_utc),
            detail=(
                f"{len(positions)} position(s) still open at "
                f"{tick.event_time_utc.isoformat()}, past the flatten deadline for the "
                f"trading day ending {closes_at.isoformat()}"
            ),
        )

    def _check_loss_gates(self, tick: GeneratedTick) -> None:
        """Trip the kill switch when a loss gate is breached (build.md §8.2)."""
        if self._kill_switch.is_halted:
            return
        breached: list[ReasonCode] = []
        if self._ledger.drawdown_fraction >= self._config.risk.max_drawdown:
            breached.append(ReasonCode.MAX_DRAWDOWN)
        if self._ledger.session_loss_fraction >= self._config.risk.max_daily_loss:
            breached.append(ReasonCode.DAILY_LOSS_LIMIT)
        if breached:
            self._trip(
                reason_codes=tuple(breached),
                tripped_by="risk_engine",
                occurred_at_utc=tick.received_time_utc,
                correlation_id=snapshot_id_for(self._spec.canonical_symbol, tick.event_time_utc),
                detail=(
                    f"drawdown={self._ledger.drawdown_fraction:.4f} "
                    f"session_loss={self._ledger.session_loss_fraction:.4f}"
                ),
            )

    def _trip(
        self,
        *,
        reason_codes: tuple[ReasonCode, ...],
        tripped_by: str,
        occurred_at_utc: datetime,
        correlation_id: UUID,
        detail: str | None = None,
    ) -> None:
        """Halt, and make the halt durable before doing anything else.

        The kill switch writes its own state to the safety store first; this
        adds the journal's account of it. The event is flushed immediately
        rather than waiting for the window to seal — a window that commits as
        one unit is worth having, but not at the price of losing the record of
        a halt to whatever crash follows it.
        """
        if self._kill_switch.is_halted:
            return
        state_before = self._kill_switch.state
        self._kill_switch.trip(
            reason_codes=reason_codes,
            tripped_by=tripped_by,
            occurred_at_utc=occurred_at_utc,
            detail=detail,
        )
        self._recorder.record(
            SystemHalted(
                state_before=state_before,
                state_after=self._kill_switch.state,
                reason_codes=reason_codes,
                tripped_by=tripped_by,
                detail=detail,
            ),
            correlation_id=correlation_id,
            occurred_at_utc=occurred_at_utc,
            source="risk_engine",
        )
        self._recorder.flush()

    def _record_risk(
        self, decision: RiskDecision, tally: RunTally, snapshot: MarketSnapshot
    ) -> None:
        for reason in decision.reason_codes:
            tally.count(tally.risk_reasons, reason.value)
        if decision.verdict is RiskVerdict.PASS:
            tally.risk_passed += 1
        elif decision.verdict is RiskVerdict.BLOCK:
            tally.risk_blocked += 1
            _log.info(
                "risk.blocked",
                correlation_id=str(snapshot.snapshot_id),
                intent_id=str(decision.intent_id),
                reason_codes=[code.value for code in decision.reason_codes],
            )
        else:
            tally.risk_halted += 1
            _log.error(
                "risk.halted",
                correlation_id=str(snapshot.snapshot_id),
                intent_id=str(decision.intent_id),
                reason_codes=[code.value for code in decision.reason_codes],
            )
            self._trip(
                reason_codes=decision.reason_codes,
                tripped_by="risk_engine",
                occurred_at_utc=snapshot.received_time_utc,
                correlation_id=snapshot.snapshot_id,
            )

    def _record_supervisor(
        self, decision: SupervisorDecision, tally: RunTally, snapshot: MarketSnapshot
    ) -> None:
        for reason in decision.reason_codes:
            tally.count(tally.supervisor_reasons, reason.value)
        if decision.verdict is SupervisorVerdict.APPROVE:
            tally.supervisor_approved += 1
        elif decision.verdict is SupervisorVerdict.VETO:
            tally.supervisor_vetoed += 1
            _log.info(
                "supervisor.vetoed",
                correlation_id=str(snapshot.snapshot_id),
                intent_id=str(decision.intent_id),
                reason_codes=[code.value for code in decision.reason_codes],
            )
        else:
            tally.supervisor_halted += 1
            _log.error(
                "supervisor.halted",
                correlation_id=str(snapshot.snapshot_id),
                intent_id=str(decision.intent_id),
                reason_codes=[code.value for code in decision.reason_codes],
            )
            self._trip(
                reason_codes=decision.reason_codes,
                tripped_by="supervisor",
                occurred_at_utc=snapshot.received_time_utc,
                correlation_id=snapshot.snapshot_id,
            )

    def _seal(
        self,
        snapshot: MarketSnapshot,
        features: FeatureEvidence,
        intent: TradeIntent | None,
        risk_decision: RiskDecision | None,
        supervisor_decision: SupervisorDecision | None,
        execution: ExecutionResult | None,
        positions_before: tuple[PositionState, ...],
    ) -> DecisionCapsule:
        """Persist the immutable record of this window (build.md §11)."""
        capsule = DecisionCapsule(
            capsule_id=uuid5(NAMESPACE_URL, f"crumblr:capsule:{snapshot.snapshot_id}"),
            occurred_at_utc=snapshot.event_time_utc,
            correlation_id=snapshot.snapshot_id,
            canonical_symbol=snapshot.symbol,
            broker_symbol=self._spec.broker_symbol,
            market_snapshot_id=snapshot.snapshot_id,
            feature_set_version=features.feature_set_version,
            feature_values_hash=features.feature_values_hash,
            strategy_version=self._strategy.version,
            model_version=None,
            model_output=None,
            trade_intent=intent,
            risk_config_version=self._config.config_version,
            risk_decision=risk_decision,
            supervisor_decision=supervisor_decision,
            execution_result=execution,
            position_state_before=positions_before,
            position_state_after=self._broker.positions(),
            code_commit=CODE_COMMIT,
            environment=self._config.environment,
        )
        self._recorder.seal(capsule)
        return capsule

    def _config_timeframe(self) -> str:
        return "M5"

    @staticmethod
    def _trim_window(entries: deque[datetime], now: datetime, window: timedelta) -> None:
        """Drop entries that have aged out of the rolling rate-limit window."""
        while entries and (now - entries[0]) > window:
            entries.popleft()
