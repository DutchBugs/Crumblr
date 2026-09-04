"""The live/shadow decision pipeline (review 1.15 §7, review 1.16 §9).

    real closed M5 bar
            |
      feature pipeline
            |
       Trading Agent
            |
    TradeIntent / NO_TRADE
            |
  intent-time deterministic Risk Engine
            |
         Supervisor
            |
    STOP HERE FOR SHADOW MODE

**Why this is a separate class, not a mode of `LiveReader` or
`ReplayOrchestrator`.** Review 1.16 §9 is explicit: "Build it as a separate
`LiveDecisionOrchestrator`/equivalent rather than turning `LiveReader` into
a trading process." The boundary this keeps:

    LiveReader              = observe/persist real broker + market state
    LiveDecisionOrchestrator = decide, from what LiveReader already persisted
    Execution service (M5)  = later, execute

This class never imports `MetaTrader5` and never opens an MT5 connection —
it reads real market data (`MarketDataStore`), the real broker-state
snapshots F-047 captures (`BrokerStateStore`), and the real instrument spec
LiveReader now persists (`InstrumentSpecStore`), all through PostgreSQL. The
Trading Agent (`trading_agent.registry`) and the deterministic Risk Engine
(`risk.policies`) and Supervisor (`evaluator.pretrade`) are exactly the same
components `ReplayOrchestrator` already uses and this codebase already
tests — nothing about *how a decision is judged* is new here, only *where
its inputs come from*.

**Execution is not reachable from this module.** There is no `ApprovedOrder`
construction, no `order_check`, no `order_send` anywhere in this file.
`decide_once()` returns after the Supervisor's verdict, always — the
"STOP HERE FOR SHADOW MODE" line in the diagram above is not a comment
about intent, it is where the code actually stops.

**Known v0 gaps, recorded rather than hidden:**

- `orders_in_last_hour` is always `0` — no order path exists to count, and
  reporting a real count would imply one does.
- Decision-window idempotence (which bar window was last decided, and which
  `TradeIntent` hashes the risk engine's duplicate-protection check has
  seen) is durable as of F-054 (review 1.17 §8) — restored from
  `DecisionWindowStore` on the first `decide_once()` call and re-saved as
  it progresses, so a restart cannot re-decide an already-decided window
  from a blank slate. Review 1.19 §5 hardened the recovery itself: an
  unreadable/corrupt record trips the kill switch (`_recover_decision_window`)
  rather than being treated as "nothing recorded" — the two must never look
  the same. See `application/decision_window.py`.
- D-031 (feature-value persistence) is closed as of review 1.17 §9 / review
  1.18 §8: `self._recorder.record_features(features)` durably stores the
  full `FeatureEvidence` payload, not only its hash, for every window that
  has features at all. See `persistence/features.py`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import Connection

from crumblr.application.decision_window import (
    DecisionWindowState,
    DecisionWindowStore,
    recover_decision_window,
)
from crumblr.application.reconciliation import (
    BrokerStateSource,
    ExpectedState,
    InstrumentSpecSource,
    reconcile,
)
from crumblr.application.recording import RunRecorder
from crumblr.config import PlatformConfig
from crumblr.domain.enums import (
    IncidentStatus,
    ReasonCode,
    RiskVerdict,
    SessionState,
    SupervisorVerdict,
)
from crumblr.domain.events import SignalGenerated, SystemHalted
from crumblr.domain.models import (
    AccountState,
    Bar,
    BrokerAccountSnapshot,
    BrokerPositionSnapshot,
    DecisionCapsule,
    InstrumentSpec,
    MarketBar,
    MarketSnapshot,
    MarketTick,
    PositionState,
    RiskDecision,
    SupervisorDecision,
    TradeIntent,
)
from crumblr.domain.money import price_to_points
from crumblr.domain.timeutils import UtcDatetime, utc_now
from crumblr.evaluator import pretrade
from crumblr.market_data.synthetic import snapshot_id_for
from crumblr.observability.logging import get_logger
from crumblr.risk import policies, session, trading_window
from crumblr.risk.kill_switch import EquityLedger, KillSwitch
from crumblr.risk.portfolio_risk import OpenRiskAssessment, assess_open_risk
from crumblr.risk.session import RiskLedgerLock, RiskSessionStore
from crumblr.trading_agent import registry
from crumblr.trading_agent.base import AgentContext, FeatureEvidence
from crumblr.trading_agent.sessions import trading_day

_log = get_logger("live_decision")

RECENT_BAR_WINDOW = 400
"""Same rolling window `ReplayOrchestrator` hands the feature pipeline

(`MAX_HISTORY_BARS`) — kept equal so a strategy sees the same amount of
history in both paths."""

CODE_COMMIT = "uncommitted-prototype"


class MarketDataSource(Protocol):
    """The slice of `persistence.market_data.MarketDataStore` this reads."""

    def recent_bars(
        self, *, canonical_symbol: str, timeframe: str, limit: int
    ) -> tuple[MarketBar, ...]: ...
    def latest_tick(self, *, canonical_symbol: str) -> MarketTick | None: ...


@dataclass(frozen=True)
class LiveDecisionOutcome:
    """What one `decide_once()` call produced, for logging and tests."""

    capsule: DecisionCapsule | None
    skipped_reason: str | None = None

    @property
    def skipped(self) -> bool:
        return self.capsule is None and self.skipped_reason is not None


def _account_state_from_snapshot(snapshot: BrokerAccountSnapshot, *, is_demo: bool) -> AccountState:
    """Convert a durable `BrokerAccountSnapshot` (F-047) into the live-read

    `AccountState` shape the risk engine expects.

    `login` has no real value here on purpose: `BrokerAccountSnapshot` never
    carries the raw MT5 login (build.md §21) — only `account_ref`, a
    fingerprint. `0` is a placeholder that can never match a real
    `expected_login`, which is the fail-closed direction if a future config
    ever sets one; account identity for the *live* decision path is verified
    by reconciliation's `account_ref` comparison instead, not by this field
    — see `LiveDecisionOrchestrator.__init__`'s `RiskContext`.
    """
    return AccountState(
        login=0,
        server=snapshot.server,
        currency=snapshot.currency,
        is_demo=is_demo,
        trade_allowed=snapshot.account_trade_allowed,
        expert_allowed=snapshot.account_trade_allowed,
        connected=True,
        balance=snapshot.balance,
        equity=snapshot.equity,
        margin=snapshot.margin,
        margin_free=snapshot.margin_free,
        margin_level=snapshot.margin_level,
        leverage=snapshot.leverage,
        observed_at_utc=snapshot.observed_at_utc,
    )


def _position_state_from_snapshot(position: BrokerPositionSnapshot) -> PositionState:
    return PositionState(
        ticket=position.ticket,
        broker_symbol=position.broker_symbol,
        side=position.side,
        volume=position.volume,
        open_price=position.open_price,
        current_price=position.current_price,
        stop_loss_price=position.stop_loss_price,
        take_profit_price=position.take_profit_price,
        opened_at_utc=position.opened_at_utc,
        profit=position.profit,
        swap=position.swap,
        magic=position.magic,
        observed_at_utc=position.observed_at_utc,
    )


def _spread_points(tick: MarketTick, spec: InstrumentSpec) -> int:
    return price_to_points(tick.ask - tick.bid, spec.point)


def _build_market_snapshot(
    tick: MarketTick, *, history: tuple[Bar, ...], spec: InstrumentSpec, timeframe: str
) -> MarketSnapshot:
    """Assemble the normalised snapshot handed to the Trading Agent, from a

    real persisted tick and real persisted bar history — the live-data
    counterpart of `market_data.synthetic.build_snapshot`, which this
    mirrors field for field except for where its inputs come from.
    """
    return MarketSnapshot(
        snapshot_id=snapshot_id_for(spec.canonical_symbol, tick.event_time_utc),
        symbol=spec.canonical_symbol,
        event_time_utc=tick.event_time_utc,
        received_time_utc=tick.received_time_utc,
        bid=tick.bid,
        ask=tick.ask,
        spread_points=_spread_points(tick, spec),
        timeframe=timeframe,
        bars=history,
        session_state=SessionState.OPEN,
        symbol_spec_version=spec.spec_version,
        data_quality=tick.data_quality,
    )


class LiveDecisionOrchestrator:
    """One decision window at a time, against real persisted state.

    Construct once per process (a script calls `decide_once()` on a timer,
    the same shape `LiveReader.poll_once()` already uses). Every dependency
    is a narrow Protocol read from PostgreSQL — see the module docstring for
    why nothing here ever reaches MT5.
    """

    def __init__(
        self,
        config: PlatformConfig,
        *,
        market_data: MarketDataSource,
        broker_state: BrokerStateSource,
        instrument_specs: InstrumentSpecSource,
        recorder: RunRecorder,
        kill_switch: KillSwitch,
        session_store: RiskSessionStore,
        risk_ledger_lock: RiskLedgerLock,
        decision_window_store: DecisionWindowStore,
        canonical_symbol: str = "EUR/USD",
        timeframe: str = "M5",
        clock: Callable[[], UtcDatetime] = utc_now,
    ) -> None:
        self._config = config
        self._market_data = market_data
        self._broker_state = broker_state
        self._instrument_specs = instrument_specs
        self._recorder = recorder
        self._kill_switch = kill_switch
        self._session_store = session_store
        self._risk_ledger_lock = risk_ledger_lock
        """ADR-021 (AG-012/Phase C): serializes recover→update→persist on

        `risk_session_states` against every other process reading/writing
        it for the same symbol — `agent_gateway/decision_path.py`'s own
        intent-time Risk check, and (for read-consistency completeness)
        `ExecutionOrchestrator`'s FINAL Risk. Required, not optional: this
        is a correctness fix, not an inert-until-wired feature."""
        self._decision_window_store = decision_window_store
        self._canonical_symbol = canonical_symbol
        self._timeframe = timeframe
        self._clock = clock

        self._strategy = registry.resolve(config.trading_agent.strategy_id)
        market = config.market_for(canonical_symbol)
        self._expectation = ExpectedState.flat(
            config.account_guard,
            canonical_symbol=canonical_symbol,
            expected_spec_version=market.expected_spec_version if market is not None else None,
        )
        self._risk_context = policies.RiskContext(
            risk=config.risk,
            execution=config.execution,
            allowed_symbols=frozenset(config.enabled_symbols()),
            require_demo_account=config.account_guard.require_demo_account,
            expected_server=config.account_guard.expected_server,
            # See `_account_state_from_snapshot`: the reconstructed live
            # AccountState has no real login to compare, so this is always
            # None regardless of `config.account_guard.expected_login` —
            # deliberately, so that check can never fire a false BLOCK
            # against a placeholder value. Account identity is verified by
            # reconciliation's `account_ref` comparison instead.
            expected_login=None,
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
            max_intents_per_hour=config.supervisor.max_intents_per_hour,
        )

        self._ledger: EquityLedger | None = None
        self._current_trading_day: date | None = None

        # F-054: restored lazily on the first `decide_once()` call, not
        # here — `_recover_decision_window()` may need to trip the kill
        # switch (review 1.19 §5's fail-closed requirement), and a
        # constructor is not the place for that side effect. Blank until
        # then; `_decision_window_recovered` guards against redoing it.
        self._seen_hashes: set[str] = set()
        self._last_decided_open_time: datetime | None = None
        self._decision_window_recovered = False

    def decide_once(self) -> LiveDecisionOutcome:
        """Evaluate the newest closed real bar, if it has not been judged yet.

        Never raises for an ordinary "nothing new / not enough evidence"
        condition — those are `LiveDecisionOutcome.skipped_reason`, the same
        philosophy `LiveReader.poll_once()` uses for its own health
        transitions: a caller driving this on a timer should not have to
        wrap every call in its own try/except for expected quiet windows.
        """
        if not self._decision_window_recovered:
            self._recover_decision_window()
            self._decision_window_recovered = True

        spec = self._instrument_specs.latest(canonical_symbol=self._canonical_symbol)
        if spec is None:
            return self._skip("no instrument spec has been observed yet")

        bars = self._market_data.recent_bars(
            canonical_symbol=self._canonical_symbol,
            timeframe=self._timeframe,
            limit=RECENT_BAR_WINDOW,
        )
        if len(bars) < self._strategy.minimum_bars:
            return self._skip(
                f"only {len(bars)} bars stored, strategy needs {self._strategy.minimum_bars}"
            )

        latest_open_time = bars[-1].bar.open_time_utc
        if (
            self._last_decided_open_time is not None
            and latest_open_time <= self._last_decided_open_time
        ):
            return self._skip("no new closed bar since the last decision")

        tick = self._market_data.latest_tick(canonical_symbol=self._canonical_symbol)
        if tick is None:
            return self._skip("no tick has been observed yet")

        account_snapshot = self._broker_state.latest_account_snapshot()
        if account_snapshot is None:
            return self._skip("no broker-state snapshot has ever been captured")

        now = self._clock()
        market_day = trading_day(tick.event_time_utc)
        positions = tuple(
            _position_state_from_snapshot(position)
            for position in self._broker_state.positions_for(account_snapshot.snapshot_id)
        )
        account = _account_state_from_snapshot(
            account_snapshot, is_demo=self._config.account_guard.require_demo_account
        )
        # Owner risk policy v1 (D1.4): real portfolio risk, never a
        # count-based approximation. Computed unconditionally, every cycle
        # (not only once an intent exists), since ADR-021's unified
        # persist below needs it regardless of what the strategy decides.
        open_risk = assess_open_risk(
            positions, specs={spec.broker_symbol: spec}, equity=account.equity
        )

        # ADR-021 (AG-012/Phase C): recover→update→persist, every cycle,
        # inside one locked transaction — replaces the old "recover once,
        # cache in memory, persist only on a risk-PASS cycle" design.
        # `session.recover_session()` already derives same-day-vs-new-day
        # session semantics from `(recorded.trading_day, market_day)`
        # itself, so recovering fresh every cycle needs no separate manual
        # day-rollover branch — `EquityLedger.start_new_session()` is no
        # longer called directly here, `recover_session()` is now the only
        # place that decision is made.
        with self._risk_ledger_lock.held(self._canonical_symbol) as connection:
            self._recover_session(account_snapshot, market_day, connection=connection)
            assert self._ledger is not None
            self._ledger.update(account_snapshot.equity)
            self._persist_session(positions, open_risk, now, connection=connection)

        self._check_loss_gates(now)
        self._check_session_boundary(tick.event_time_utc, positions, now)

        history = tuple(bar.bar for bar in bars)
        snapshot = _build_market_snapshot(
            tick, history=history, spec=spec, timeframe=self._timeframe
        )
        self._last_decided_open_time = latest_open_time
        # Durable the instant the window is claimed, not only once a full
        # decision goes through (F-054): an early skip below (too little
        # feature history) must still not re-attempt this same window after
        # a restart, the same as a full decision would not.
        self._save_decision_window(now)

        outcome = self._strategy.evaluate(
            snapshot,
            history,
            spec,
            AgentContext(
                open_position_sides=tuple(position.side for position in positions),
                requested_risk_fraction=self._config.risk.max_risk_per_trade,
                min_stop_distance_points=self._config.risk.min_stop_distance_points,
            ),
        )
        if outcome.features is None:
            return self._skip("strategy has too little history to say anything")

        features = outcome.features
        decision = outcome.decision

        self._recorder.record_features(features)
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
            capsule = self._seal(snapshot, spec, features, None, None, None, positions)
            return LiveDecisionOutcome(capsule=capsule)

        intent = decision.intent
        self._recorder.record(
            intent,
            correlation_id=snapshot.snapshot_id,
            occurred_at_utc=snapshot.event_time_utc,
            source="trading_agent",
        )

        reconciliation_result = reconcile(
            self._broker_state,
            self._expectation,
            instrument_specs=self._instrument_specs,
            now=now,
        )
        # `account`/`open_risk` already computed above (ADR-021) — reused
        # here unchanged, not recomputed, so the figure Risk judges against
        # is provably the same one just persisted.
        portfolio = policies.PortfolioState(
            account=account,
            open_positions=positions,
            ledger=self._ledger,
            orders_in_last_hour=0,
            seen_decision_hashes=frozenset(self._seen_hashes),
            open_risk_fraction=open_risk.fraction,
        )
        risk_decision = policies.evaluate(
            intent, snapshot, spec, portfolio, self._risk_context, self._kill_switch, now=now
        )
        self._recorder.record(
            risk_decision,
            correlation_id=snapshot.snapshot_id,
            occurred_at_utc=snapshot.event_time_utc,
            source="risk_engine",
        )
        if risk_decision.verdict is RiskVerdict.HALT:
            self._trip(risk_decision.reason_codes, "risk_engine", now, snapshot.snapshot_id)
        if risk_decision.verdict is not RiskVerdict.PASS:
            capsule = self._seal(snapshot, spec, features, intent, risk_decision, None, positions)
            return LiveDecisionOutcome(capsule=capsule)

        supervisor_decision = pretrade.evaluate(
            intent,
            features,
            self._policy,
            pretrade.SupervisorContext(
                intents_in_last_hour=0,
                incident_status=IncidentStatus.CLEAR,
                reconciliation_status=reconciliation_result.status,
            ),
            now=now,
        )
        self._recorder.record(
            supervisor_decision,
            correlation_id=snapshot.snapshot_id,
            occurred_at_utc=snapshot.event_time_utc,
            source="supervisor",
        )
        if supervisor_decision.verdict is SupervisorVerdict.HALT:
            self._trip(supervisor_decision.reason_codes, "supervisor", now, snapshot.snapshot_id)

        self._seen_hashes.add(intent.decision_hash)
        self._save_decision_window(now)
        capsule = self._seal(
            snapshot, spec, features, intent, risk_decision, supervisor_decision, positions
        )
        # No `_persist_session()` call here (unlike before ADR-021): the
        # ledger is already durably persisted for this cycle, under lock,
        # above — nothing between there and here mutates it further.
        return LiveDecisionOutcome(capsule=capsule)

    # ------------------------------------------------------------------ #

    def _recover_decision_window(self) -> None:
        """Restore F-054 state, failing closed if it cannot be trusted.

        Review 1.19 §5: an unreadable/corrupt decision-window record must
        not be treated as "nothing recorded" — that would let the exact
        failure this store exists to prevent (re-deciding an
        already-decided window after a restart) hide behind a record that
        merely *looks* empty. `recover_decision_window()` tells the two
        apart; a corrupted record trips the kill switch here, the same way
        `_recover_session()` already does for a corrupted risk-session
        record.
        """
        record = self._decision_window_store.load_latest(
            canonical_symbol=self._canonical_symbol,
            strategy_id=self._strategy.strategy_id,
            config_version=self._config.config_version,
        )
        recovery = recover_decision_window(record)
        self._last_decided_open_time = recovery.last_decided_open_time_utc
        self._seen_hashes = set(recovery.seen_decision_hashes)
        if recovery.must_halt:
            now = self._clock()
            self._trip(
                recovery.reason_codes,
                "decision_window_recovery",
                now,
                uuid5(
                    NAMESPACE_URL,
                    f"crumblr:decision-window-recovery:{self._canonical_symbol}:"
                    f"{self._strategy.strategy_id}:{self._config.config_version}:"
                    f"{now.isoformat()}",
                ),
                detail=recovery.detail,
            )

    def _save_decision_window(self, now: UtcDatetime) -> None:
        """Persist what a restart must not be allowed to forget (F-054).

        Called twice per decided window, not once: right after
        `_last_decided_open_time` is claimed (so an early skip still
        durably marks the window handled) and again after a new decision
        hash is added (so the risk engine's duplicate-protection check
        survives a restart too). `_last_decided_open_time` cannot be `None`
        here — this is only ever called after it has just been set.
        """
        assert self._last_decided_open_time is not None
        self._decision_window_store.save(
            DecisionWindowState(
                canonical_symbol=self._canonical_symbol,
                strategy_id=self._strategy.strategy_id,
                config_version=self._config.config_version,
                last_decided_open_time_utc=self._last_decided_open_time,
                seen_decision_hashes=frozenset(self._seen_hashes),
                recorded_at_utc=now,
            )
        )

    def _skip(self, reason: str) -> LiveDecisionOutcome:
        _log.info("live_decision.skipped", reason=reason)
        return LiveDecisionOutcome(capsule=None, skipped_reason=reason)

    def _recover_session(
        self,
        account_snapshot: BrokerAccountSnapshot,
        market_day: date,
        *,
        connection: Connection | None,
    ) -> None:
        """Called every `decide_once()` cycle since ADR-021 (AG-012/Phase

        C), always inside `self._risk_ledger_lock.held(...)`'s block —
        `connection` threads through to `load_latest()` so this read
        participates in that same locked transaction, never a second one.
        `session.recover_session()` itself already derives same-day-vs-
        new-day semantics from `(recorded.trading_day, market_day)`, so
        calling this every cycle needs no separate manual day-rollover
        branch at the call site."""
        recovery = session.recover_session(
            self._session_store.load_latest(connection=connection),
            live_equity=account_snapshot.equity,
            live_open_positions=len(self._broker_state.positions_for(account_snapshot.snapshot_id)),
            market_day=market_day,
            max_daily_loss=self._config.risk.max_daily_loss,
            max_drawdown=self._config.risk.max_drawdown,
        )
        self._ledger = recovery.ledger
        self._current_trading_day = recovery.trading_day
        if recovery.must_halt:
            self._trip(
                recovery.reason_codes,
                "risk_session_recovery",
                account_snapshot.observed_at_utc,
                uuid5(NAMESPACE_URL, f"crumblr:live-recovery:{account_snapshot.snapshot_id}"),
                detail=recovery.detail,
            )

    def _check_loss_gates(self, now: UtcDatetime) -> None:
        if self._kill_switch.is_halted or self._ledger is None:
            return
        breached: list[ReasonCode] = []
        if self._ledger.drawdown_fraction >= self._config.risk.max_drawdown:
            breached.append(ReasonCode.MAX_DRAWDOWN)
        if self._ledger.session_loss_fraction >= self._config.risk.max_daily_loss:
            breached.append(ReasonCode.DAILY_LOSS_LIMIT)
        if breached:
            self._trip(
                tuple(breached),
                "risk_engine",
                now,
                uuid5(NAMESPACE_URL, f"crumblr:live-loss-gate:{now.isoformat()}"),
                detail=(
                    f"drawdown={self._ledger.drawdown_fraction:.4f} "
                    f"session_loss={self._ledger.session_loss_fraction:.4f}"
                ),
            )

    def _check_session_boundary(
        self, moment: UtcDatetime, positions: tuple[PositionState, ...], now: UtcDatetime
    ) -> None:
        """Halt if exposure outlived its weekly deadline (owner risk

        policy v1, D1.5). Uses `risk/policies.py::overnight_breach` —
        the one shared implementation — rather than re-inlining it, so
        this site and `orchestration.py`'s identical check cannot drift
        from each other or from the risk gateway's own leg.
        """
        if self._kill_switch.is_halted:
            return
        policy = self._risk_context.intraday
        if not policies.overnight_breach(positions, moment, policy):
            return
        self._trip(
            (ReasonCode.OVERNIGHT_EXPOSURE,),
            "risk_engine",
            now,
            uuid5(NAMESPACE_URL, f"crumblr:live-session-boundary:{moment.isoformat()}"),
            detail=(
                f"{len(positions)} position(s) still open at {moment.isoformat()}, "
                "past the flatten deadline"
            ),
        )

    def _trip(
        self,
        reason_codes: tuple[ReasonCode, ...],
        tripped_by: str,
        occurred_at_utc: UtcDatetime,
        correlation_id: UUID,
        *,
        detail: str | None = None,
    ) -> None:
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

    def _persist_session(
        self,
        positions: tuple[PositionState, ...],
        open_risk: OpenRiskAssessment,
        now: UtcDatetime,
        *,
        connection: Connection | None,
    ) -> None:
        """Called every `decide_once()` cycle since ADR-021 (AG-012/Phase

        C), always inside `self._risk_ledger_lock.held(...)`'s block,
        immediately after `_recover_session()`/`self._ledger.update()` —
        `connection` threads through to `save()` so this write
        participates in that same locked transaction. `open_risk` is
        threaded in from `decide_once()`'s own assessment (owner risk
        policy v1, D1.4) rather than recomputed here: this method has no
        `InstrumentSpec` in scope on its own (unlike `ReplayOrchestrator`,
        which holds one fixed spec for the whole run), and its one caller
        already computed the real figure — so the persisted number is
        provably the same one Risk judges against, not a second,
        independently-derived value that could differ if the account
        snapshot moved between them."""
        if self._ledger is None or self._current_trading_day is None:
            return
        state = session.snapshot(
            self._ledger,
            trading_day=self._current_trading_day,
            realized_pnl=self._ledger.current_equity - self._ledger.starting_equity,
            open_risk_fraction=open_risk.fraction,
            open_position_count=len(positions),
            recorded_at_utc=now,
        )
        self._session_store.save(state, connection=connection)

    def _seal(
        self,
        snapshot: MarketSnapshot,
        spec: InstrumentSpec,
        features: FeatureEvidence,
        intent: TradeIntent | None,
        risk_decision: RiskDecision | None,
        supervisor_decision: SupervisorDecision | None,
        positions: tuple[PositionState, ...],
    ) -> DecisionCapsule:
        """Persist the immutable record of this window (build.md §11).

        `spec` is passed in rather than read from `self` because — unlike
        `ReplayOrchestrator`, which holds one fixed spec for the whole run —
        this orchestrator re-reads the latest durable spec on every
        `decide_once()` call, so the broker symbol recorded here must be the
        one the *decided* snapshot actually used, not whatever `latest()`
        would return if called again after the fact.
        """
        capsule = DecisionCapsule(
            capsule_id=uuid5(NAMESPACE_URL, f"crumblr:live-capsule:{snapshot.snapshot_id}"),
            occurred_at_utc=snapshot.event_time_utc,
            correlation_id=snapshot.snapshot_id,
            canonical_symbol=snapshot.symbol,
            broker_symbol=spec.broker_symbol,
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
            execution_result=None,
            position_state_before=positions,
            position_state_after=positions,
            code_commit=CODE_COMMIT,
            environment=self._config.environment,
        )
        self._recorder.seal(capsule)
        return capsule
