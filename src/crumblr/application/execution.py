"""The Execution Service (Phase 4, non-sending) — preflight, never submit.

    LiveReader              = observe/persist real broker + market state
    LiveDecisionOrchestrator = decide (stays MT5-free)
    Execution Service        = preflight / (later) execute        <- here

`live_decision.py`'s own module docstring draws this boundary and this class
is the third tier it names, built separately rather than folded into either
of the other two (review 1.16 §9's reasoning, extended). Unlike
`LiveDecisionOrchestrator`, this class *is* allowed to touch MT5 — it owns
an `OrderCheckMt5Gateway` — but it never imports or reaches `order_send`;
that method always raises on the adapter it holds, unconditionally.

Target flow (`review/PHASE4_PLAN_REVIEW_GO_WITH_TWEAKS.md`), one sealed,
intent-time-approved capsule at a time:

    sealed capsule (risk PASS, supervisor APPROVE)
            |
    derive order_request_id (uuid5 of decision_hash, same as orchestration.py)
            |
    claim (the winning INSERT into execution_requests IS the claim)
            |
    eligibility (activation watermark, version match, not expired, in window)
            |
    fresh broker + market observation, persisted, then reconciled
            |
    ExecutionPreflightGate
            |
    FINAL Risk (same volume, or BLOCK)  -> FINAL_RISK_PASSED/BLOCKED event,
            |                               carrying the full FINAL RiskDecision
            |                               (review 1.22 F-057 — never by
            |                               mutating the sealed DecisionCapsule)
    ApprovedOrder -> order_check   (never order_send)
            |
    append the terminal event

Every refusal along the way appends exactly one `ExecutionEventType` to
`ExecutionEventStore` and moves on to the next capsule — nothing here halts
the whole system on an ordinary refusal. The one exception is a genuinely
unreadable risk-session record, which `LiveDecisionOrchestrator` already
treats as halt-worthy for the same reason; this class does the same, since
both processes read the same durable, shared safety state.

**Known v0 gap, recorded rather than hidden.**

- `CapsuleStore.read_all()` reads every capsule ever sealed in this
  environment, every call — fine at today's scale (the activation watermark
  is unset in every shipped config, so the answer is always "nothing
  eligible"), not fine forever. Worth an index-backed "unclaimed since X"
  query before this runs against real history at volume (`review/DEVIATIONS.md`
  D-047's first gap; its second gap — two separate live reads instead of
  one coherent observation — closed 2026-08-27, review 1.22 F-058).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from crumblr.application.broker_state import BrokerStateObservation, capture_broker_state
from crumblr.application.execution_outcome import close_result_fully_closed
from crumblr.application.expected_state import derive_expected_exposure
from crumblr.application.flatten_plan import build_flatten_plan
from crumblr.application.reconciliation import (
    BrokerStateSource,
    ExpectedState,
    InstrumentSpecSource,
    ProtectiveStopIssue,
    reconcile,
    verify_protective_stops,
)
from crumblr.config import PlatformConfig
from crumblr.domain.enums import (
    Environment,
    ExecutionEventType,
    FlattenEventType,
    ReasonCode,
    ReconciliationStatus,
    RiskVerdict,
    SessionState,
    SnapshotCompleteness,
    SupervisorVerdict,
)
from crumblr.domain.hashing import fingerprint, mt5_magic_number
from crumblr.domain.models import (
    ApprovedOrder,
    DecisionCapsule,
    ExecutionResult,
    FlattenInstruction,
    MarketSnapshot,
    MarketTick,
    PositionState,
)
from crumblr.domain.money import price_to_points
from crumblr.domain.timeutils import UtcDatetime, utc_now
from crumblr.market_data.synthetic import snapshot_id_for
from crumblr.mt5_gateway.execution import OrderCheckMt5Gateway
from crumblr.observability.logging import get_logger
from crumblr.persistence.execution import (
    ExecutionEventStore,
    ExecutionRequestConflictError,
    ExecutionRequestStore,
)
from crumblr.persistence.flatten import (
    FlattenEventRecord,
    FlattenEventStore,
    FlattenRequestConflictError,
    FlattenRequestStore,
    flatten_request_id_for,
)
from crumblr.risk import policies, trading_window
from crumblr.risk.execution_eligibility import evaluate_execution_eligibility
from crumblr.risk.execution_preflight_gate import evaluate_preflight_gate
from crumblr.risk.flatten_gate import FlattenGateContext, evaluate_flatten_gate
from crumblr.risk.kill_switch import KillSwitch
from crumblr.risk.portfolio_risk import assess_open_risk
from crumblr.risk.session import RiskLedgerLock, RiskSessionStore, recover_session
from crumblr.risk.submission_gate import SubmissionGateContext, evaluate_submission_gate
from crumblr.trading_agent.sessions import trading_day, weekly_close

_log = get_logger("execution_orchestrator")

_FRESH_TICK_LOOKBACK = timedelta(seconds=30)
"""How far back to ask for ticks when reading "the current price" — MT5's

`copy_ticks_from` returns the *oldest* `count` ticks at or after `since`,
not the newest, so a window this wide with a generous `count` is what makes
`[-1]` genuinely the most recent tick rather than an arbitrarily old one."""


class CapsuleSource(Protocol):
    """The slice of `persistence.journal.CapsuleStore` this reads."""

    def read_all(
        self, *, environment: Environment | None = None
    ) -> tuple[DecisionCapsule, ...]: ...


class BrokerStateSink(BrokerStateSource, Protocol):
    """`BrokerStateSource` (read, for reconciliation) plus the one write this

    class needs — the slice of `persistence.broker_state.BrokerStateStore`
    used here.
    """

    def record(self, observation: BrokerStateObservation) -> None: ...


class FlattenCloseSink(Protocol):
    """The one real capability the flatten driver needs (Phase B item B5,

    `review/adr/ADR-020-real-flatten-close.md`) — deliberately a narrow
    structural Protocol, not the concrete real demo-execution gateway class
    in `mt5_gateway/demo_execution.py`. This module never names that class
    by name (`tests/unit/test_demo_order_send_gateway.py
    ::TestNotWiredIntoTheOrchestrator` mechanically asserts as much) —
    entries remain genuinely unwired pending Phase C/AG-012's shared
    execution/Risk authority; only this one close capability is real, and
    only reachable via this Protocol.
    """

    def close_position(self, instruction: FlattenInstruction) -> ExecutionResult: ...


@dataclass(frozen=True)
class ExecutionAttemptOutcome:
    """What happened to one capsule during one `run_once()` pass."""

    order_request_id: UUID
    capsule_id: UUID
    event_type: ExecutionEventType
    reason_codes: tuple[ReasonCode, ...] = ()


@dataclass(frozen=True)
class FlattenAttemptOutcome:
    """What happened to one flatten occurrence during one `flatten_once()`

    pass (core critical path item 7). Deliberately not
    `ExecutionAttemptOutcome`: that type's `capsule_id` is non-optional,
    and a flatten has no capsule — see `persistence/flatten.py`'s module
    docstring."""

    flatten_request_id: UUID
    event_type: FlattenEventType
    reason_codes: tuple[ReasonCode, ...] = ()
    target_count: int = 0
    closed_count: int = 0
    """How many of `target_count` positions were confirmed closed on the

    freshest broker observation this pass took (Phase B item B5) — `0` for
    every outcome that never attempted a real close (unchanged pre-B5
    behaviour)."""


@dataclass(frozen=True)
class ReconciliationAttemptOutcome:
    """What happened to one request during one `reconcile_once()` pass

    (core critical path item 8). Deliberately not
    `ExecutionAttemptOutcome`: `reconcile_once()` never reads
    `CapsuleStore` at all (it walks requests, not capsules — see
    `_evaluate_reconciliation_candidates`'s own docstring), so there is
    no `capsule_id` to carry."""

    order_request_id: UUID
    event_type: ExecutionEventType = ExecutionEventType.RECONCILED
    book_status: ReconciliationStatus = ReconciliationStatus.UNKNOWN
    accounted_ticket_count: int = 0


class ExecutionOrchestrator:
    """Runs the non-sending preflight chain against every claimable, eligible

    sealed capsule it finds, once per `run_once()` call — a script drives
    this on a timer, the same shape `LiveReader.poll_once()` and
    `LiveDecisionOrchestrator.decide_once()` already use.
    """

    def __init__(
        self,
        config: PlatformConfig,
        *,
        capsules: CapsuleSource,
        requests: ExecutionRequestStore,
        events: ExecutionEventStore,
        flatten_requests: FlattenRequestStore,
        flatten_events: FlattenEventStore,
        broker_state: BrokerStateSink,
        instrument_specs: InstrumentSpecSource,
        session_store: RiskSessionStore,
        risk_ledger_lock: RiskLedgerLock,
        kill_switch: KillSwitch,
        adapter: OrderCheckMt5Gateway,
        canonical_symbol: str = "EUR/USD",
        activation_watermark: UtcDatetime | None = None,
        worker_id: str = "execution-orchestrator",
        clock: Callable[[], UtcDatetime] = utc_now,
        flatten_close_adapter: FlattenCloseSink | None = None,
    ) -> None:
        self._config = config
        self._capsules = capsules
        self._requests = requests
        self._events = events
        self._flatten_requests = flatten_requests
        self._flatten_events = flatten_events
        self._broker_state = broker_state
        self._instrument_specs = instrument_specs
        self._session_store = session_store
        self._risk_ledger_lock = risk_ledger_lock
        """ADR-021 (AG-012/Phase C): lock-for-read-consistency only on this

        side — `self._session_store` has no `.save()` call anywhere in
        this class, confirmed by grep before this change."""
        self._kill_switch = kill_switch
        self._adapter = adapter
        self._canonical_symbol = canonical_symbol
        self._activation_watermark = activation_watermark
        self._worker_id = worker_id
        self._clock = clock
        self._flatten_close_adapter = flatten_close_adapter
        """Phase B item B5. `None` in every existing caller/test — the exact

        pre-B5 behaviour (no close is ever attempted) is preserved by
        this default alone, independent of `flatten_submission_enabled`.
        A real caller must construct a `FlattenCloseSink` and pass it
        explicitly; nothing here ever constructs one itself."""

    def run_once(self) -> tuple[ExecutionAttemptOutcome, ...]:
        # Core critical path item 7: a flatten is policy-driven (a
        # deadline plus observed exposure), not proposal-driven, so it
        # must fire every cycle independent of whether any capsule
        # exists — the loop below does nothing when there are none.
        # `flatten_once()`'s own outcome type (`FlattenAttemptOutcome`)
        # is deliberately not merged into this method's return value: a
        # flatten has no `capsule_id`, and every existing assertion on
        # `run_once()`'s return tuple stays untouched by this call.
        self.flatten_once()

        now = self._clock()
        outcomes: list[ExecutionAttemptOutcome] = []
        for capsule in self._capsules.read_all(environment=self._config.environment):
            if not _is_intent_time_approved(capsule):
                continue
            outcome = self._process(capsule, now)
            if outcome is not None:
                outcomes.append(outcome)

        # Core critical path item 8 (ADR-010): reconciliation is the last
        # stage of build.md's pipeline ("Execution service executes.
        # Reconciliation verifies."). Running it *after* the capsule loop
        # — not at the top, unlike `flatten_once()` — lets
        # `_recover_ambiguous_submission` (which runs inside the loop
        # above) resolve a `SUBMISSION_STARTED` ambiguity in the same
        # pass before reconciliation asks about it; running it first
        # would report a request as undetermined that this very pass was
        # about to resolve. Its own outcome type
        # (`ReconciliationAttemptOutcome`) is deliberately not merged
        # into this method's return value, same reasoning as
        # `flatten_once()`'s.
        self.reconcile_once()
        return tuple(outcomes)

    def _process(
        self, capsule: DecisionCapsule, now: UtcDatetime
    ) -> ExecutionAttemptOutcome | None:
        assert capsule.trade_intent is not None
        assert capsule.risk_decision is not None
        assert capsule.supervisor_decision is not None
        intent = capsule.trade_intent
        prior_decision = capsule.risk_decision

        order_request_id = uuid5(NAMESPACE_URL, f"crumblr:order:{intent.decision_hash}")

        try:
            claim = self._requests.claim(
                order_request_id=order_request_id,
                capsule_id=capsule.capsule_id,
                intent_id=intent.intent_id,
                fingerprint=_approval_chain_fingerprint(capsule),
                claimed_by=self._worker_id,
                now=now,
            )
        except ExecutionRequestConflictError:
            _log.error("execution.claim_conflict", order_request_id=str(order_request_id))
            raise

        if not claim.claimed:
            # Already claimed by an earlier pass (this worker or another).
            # The claim's whole purpose is to make this a no-op, not a
            # blind retry — but "no-op" must not mean "never revisit a
            # request a crash left ambiguous". Core critical path item 6.
            return self._recover_ambiguous_submission(order_request_id, capsule)

        self._append(order_request_id, ExecutionEventType.REQUEST_CLAIMED, now)

        risk_context = self._risk_context()

        eligibility = evaluate_execution_eligibility(
            capsule,
            activation_watermark=self._activation_watermark,
            now=now,
            current_strategy_version=self._config.trading_agent.strategy_version,
            current_risk_config_version=self._config.config_version,
            intraday=risk_context.intraday,
        )
        if not eligibility.eligible:
            return self._refuse(
                order_request_id,
                capsule,
                ExecutionEventType.INELIGIBLE,
                eligibility.reason_codes,
                now,
            )

        gate = evaluate_preflight_gate(
            environment=capsule.environment,
            canonical_symbol=self._canonical_symbol,
            allowed_symbols=frozenset(self._config.enabled_symbols()),
            kill_switch=self._kill_switch,
        )
        if not gate.open:
            return self._refuse(
                order_request_id, capsule, ExecutionEventType.GATE_CLOSED, gate.reason_codes, now
            )

        # One coherent, current observation (review 1.22 F-058) — the same
        # capture serves reconciliation (its persistence-shaped fields) and
        # FINAL Risk (its raw domain fields), rather than two independent
        # live reads that could disagree about the broker's actual state.
        observation = capture_broker_state(
            self._adapter.reader,
            environment=capsule.environment,
            canonical_symbol=self._canonical_symbol,
            clock=self._clock,
        )
        self._broker_state.record(observation)
        assert observation.account_state is not None

        market = self._config.market_for(self._canonical_symbol)
        expectation = ExpectedState.flat(
            self._config.account_guard,
            canonical_symbol=self._canonical_symbol,
            expected_spec_version=market.expected_spec_version if market is not None else None,
        )
        reconciliation = reconcile(
            self._broker_state, expectation, instrument_specs=self._instrument_specs, now=now
        )
        if reconciliation.status is not ReconciliationStatus.MATCHED:
            reason = (
                ReasonCode.RECONCILIATION_MISMATCH
                if reconciliation.status is ReconciliationStatus.MISMATCHED
                else ReasonCode.RECONCILIATION_UNKNOWN
            )
            return self._refuse(
                order_request_id,
                capsule,
                ExecutionEventType.RECONCILIATION_BLOCKED,
                (reason,),
                now,
                detail="; ".join(reconciliation.reasons) or None,
            )

        spec = self._instrument_specs.latest(canonical_symbol=self._canonical_symbol)
        if spec is None:
            return self._refuse(
                order_request_id,
                capsule,
                ExecutionEventType.FINAL_RISK_BLOCKED,
                (ReasonCode.SYMBOL_NOT_ALLOWED,),
                now,
                detail="no instrument spec has been observed",
            )

        fresh_ticks = self._adapter.reader.ticks(
            self._canonical_symbol,
            since=now - _FRESH_TICK_LOOKBACK,
            count=100,
            source="execution_orchestrator",
        )
        if not fresh_ticks:
            return self._refuse(
                order_request_id,
                capsule,
                ExecutionEventType.FINAL_RISK_BLOCKED,
                (ReasonCode.STALE_MARKET_DATA,),
                now,
                detail="no fresh tick was available",
            )
        tick = fresh_ticks[-1]
        fresh_snapshot = MarketSnapshot(
            snapshot_id=snapshot_id_for(self._canonical_symbol, tick.event_time_utc),
            symbol=self._canonical_symbol,
            event_time_utc=tick.event_time_utc,
            received_time_utc=tick.received_time_utc,
            bid=tick.bid,
            ask=tick.ask,
            spread_points=price_to_points(tick.ask - tick.bid, spec.point),
            timeframe="M5",
            bars=(),
            session_state=SessionState.OPEN,
            symbol_spec_version=spec.spec_version,
            data_quality=tick.data_quality,
        )

        # A fresh timestamp, taken immediately before the final-stage
        # authorities that must all share one clock boundary (review 1.23
        # §3, F-058's remaining gap): recovering which trading day's risk
        # session applies, FINAL Risk's own now-driven checks, the
        # execution events, and `ApprovedOrder.created_at_utc`. Taken here
        # — after every read, right before the checks that consume it —
        # not at `run_once()`'s start, which a slow broker call could have
        # left stale enough to straddle a session/expiry boundary.
        final_now = self._clock()

        # ADR-021 (AG-012/Phase C): this read was already fresh every pass
        # (no in-memory caching) before ADR-021 — it was never part of the
        # race that ADR fixes. Included under the same lock anyway: it is
        # the literal closest thing to the owner work order's own
        # "final-Risk-to-broker-side-effect critical section" wording, and
        # the marginal cost is small — this call site has no write-back
        # either, so it is lock-for-read-consistency only, the same as
        # `agent_gateway/decision_path.py`'s side (ADR-021 §5).
        # AG-024 (ADR-021 §8): `load_latest()` already has its own internal
        # try/except (any read failure becomes `record.unreadable`, which
        # `recover_session()`'s own `if not record.is_known` branch already
        # turns into `must_halt`) — the one thing that was never protected
        # is *acquiring the lock itself*. A failure there previously had no
        # handling anywhere in the call chain.
        try:
            with self._risk_ledger_lock.held(self._canonical_symbol) as connection:
                session_recovery = recover_session(
                    self._session_store.load_latest(connection=connection),
                    live_equity=observation.account_state.equity,
                    live_open_positions=len(observation.position_states),
                    market_day=trading_day(final_now),
                    max_daily_loss=self._config.risk.max_daily_loss,
                    max_drawdown=self._config.risk.max_drawdown,
                )
        except Exception as error:
            _log.error("execution.risk_ledger_lock_failed", error=str(error))
            if not self._kill_switch.is_halted:
                self._kill_switch.trip(
                    reason_codes=(ReasonCode.RISK_LEDGER_LOCK_UNAVAILABLE,),
                    tripped_by="execution_orchestrator",
                    occurred_at_utc=final_now,
                    detail=f"risk-ledger lock unavailable: {error}",
                )
            return self._refuse(
                order_request_id,
                capsule,
                ExecutionEventType.FINAL_RISK_BLOCKED,
                (ReasonCode.RISK_LEDGER_LOCK_UNAVAILABLE,),
                final_now,
                detail=f"risk-ledger lock unavailable: {error}",
            )
        if session_recovery.must_halt:
            if not self._kill_switch.is_halted:
                self._kill_switch.trip(
                    reason_codes=session_recovery.reason_codes,
                    tripped_by="execution_orchestrator",
                    occurred_at_utc=final_now,
                    detail=session_recovery.detail,
                )
            return self._refuse(
                order_request_id,
                capsule,
                ExecutionEventType.FINAL_RISK_BLOCKED,
                session_recovery.reason_codes,
                final_now,
                detail=session_recovery.detail,
            )

        # Real, durable order-frequency history (review 1.23 F-060 —
        # reopened after the first fix counted claimed *requests*, which
        # include every refusal outcome, not actual submission attempts).
        # `SUBMISSION_STARTED` is the durable authority for "the platform
        # committed to attempting one broker submission"; Phase 4
        # structurally never emits one, so this is honestly `0` today, not
        # a placeholder.
        orders_in_last_hour = self._events.count_events_since(
            ExecutionEventType.SUBMISSION_STARTED, final_now - timedelta(hours=1)
        )

        # Owner risk policy v1 (D1.4): real portfolio risk, never a
        # count-based approximation. One coherent observation already in
        # scope (`observation`, F-058) — no second broker read.
        open_risk = assess_open_risk(
            observation.position_states,
            specs={spec.broker_symbol: spec},
            equity=observation.account_state.equity,
        )
        fresh_portfolio = policies.PortfolioState(
            account=observation.account_state,
            open_positions=observation.position_states,
            ledger=session_recovery.ledger,
            orders_in_last_hour=orders_in_last_hour,
            seen_decision_hashes=frozenset(),
            open_risk_fraction=open_risk.fraction,
        )

        final_risk = policies.revalidate_fixed_volume_at_execution_time(
            intent,
            prior_decision,
            fresh_snapshot,
            spec,
            fresh_portfolio,
            risk_context,
            self._kill_switch,
            now=final_now,
        )
        if final_risk.verdict is not RiskVerdict.PASS:
            return self._refuse(
                order_request_id,
                capsule,
                ExecutionEventType.FINAL_RISK_BLOCKED,
                final_risk.reason_codes,
                final_now,
                payload=final_risk.model_dump(mode="json"),
            )

        assert final_risk.approved_volume is not None
        assert intent.stop_loss_price is not None
        order = ApprovedOrder(
            order_request_id=order_request_id,
            intent_id=intent.intent_id,
            intent_risk_decision_id=prior_decision.decision_id,
            final_risk_decision_id=final_risk.decision_id,
            supervisor_decision_id=capsule.supervisor_decision.decision_id,
            broker_symbol=spec.broker_symbol,
            side=intent.side,
            entry_type=intent.entry_type,
            volume=final_risk.approved_volume,
            price=None,
            stop_loss_price=intent.stop_loss_price,
            take_profit_price=intent.take_profit_price,
            max_slippage_points=self._config.execution.max_slippage_points,
            created_at_utc=final_now,
            expires_at_utc=intent.expires_at_utc,
            environment=capsule.environment,
        )

        # Review 1.22 F-057: the durable link ADR-001 requires, appended
        # before `order_check` — never by mutating the sealed
        # `DecisionCapsule`. Carries the complete serialized FINAL
        # RiskDecision plus a fingerprint binding it to the exact
        # `ApprovedOrder` it authorized (F-059's second binding). Review
        # 1.23 F-059: the fingerprint covers the *complete* serialized
        # FINAL `RiskDecision`, not only its `decision_id` — the same
        # "complete content, not a hand-picked field" fix as
        # `_approval_chain_fingerprint`, for the same reason.
        order_fingerprint = fingerprint(
            {
                "order_request_id": str(order_request_id),
                "final_risk_decision": final_risk.model_dump(mode="json"),
                "approved_order": order.model_dump(mode="json"),
            }
        )
        self._append(
            order_request_id,
            ExecutionEventType.FINAL_RISK_PASSED,
            final_now,
            payload={
                "final_risk_decision": final_risk.model_dump(mode="json"),
                "order_fingerprint": order_fingerprint,
            },
        )

        check = self._adapter.order_check(order)
        event_type = (
            ExecutionEventType.ORDER_CHECKED
            if check.accepted
            else ExecutionEventType.ORDER_CHECK_REJECTED
        )
        self._append(order_request_id, event_type, final_now, payload=check.model_dump(mode="json"))
        if not check.accepted:
            return ExecutionAttemptOutcome(
                order_request_id=order_request_id,
                capsule_id=capsule.capsule_id,
                event_type=event_type,
            )

        # Durable execution-activation wiring (Dev-1 core critical path,
        # item 2): whether real submission would currently be authorized,
        # evaluated and recorded — never acted on. `order_send` stays
        # unreachable regardless of this decision; see
        # `risk/submission_gate.py`'s own module docstring.
        gate_event_type, gate_reason_codes = self._evaluate_submission_readiness(
            order_request_id,
            observation=observation,
            tick=tick,
            final_now=final_now,
        )
        if gate_event_type is not ExecutionEventType.SUBMISSION_GATE_PASSED:
            return ExecutionAttemptOutcome(
                order_request_id=order_request_id,
                capsule_id=capsule.capsule_id,
                event_type=gate_event_type,
                reason_codes=gate_reason_codes,
            )

        # Core critical path item 3 (review 1.26 §6 / review 1.27 §8):
        # the gate opened, so the platform now durably commits to
        # attempting one broker submission. `order_send` is still not
        # called — see `_start_submission`'s own docstring.
        submission_event_type = self._start_submission(order_request_id, order, final_now)
        return ExecutionAttemptOutcome(
            order_request_id=order_request_id,
            capsule_id=capsule.capsule_id,
            event_type=submission_event_type,
        )

    def _start_submission(
        self, order_request_id: UUID, order: ApprovedOrder, now: UtcDatetime
    ) -> ExecutionEventType:
        """Core critical path item 3 (review 1.26 §6 / review 1.27 §8):

        `SUBMISSION_STARTED`, the durable pre-side-effect commitment
        point — ADR-003 §6's "write to the journal before acting,
        acknowledge after" rule, applied to the one action this platform
        has never yet taken. Deliberately does not call `order_send`:
        both reviews' explicit ordering rule is that completing this
        item is not authorization to add a real `order_send` call.
        `OrderCheckMt5Gateway.order_send` stays unconditionally disabled
        regardless — building this caller and building `order_send`'s
        real capability are separate, later items (submission
        idempotence, ambiguous-outcome recovery).
        """
        self._append(
            order_request_id,
            ExecutionEventType.SUBMISSION_STARTED,
            now,
            payload=order.model_dump(mode="json"),
        )
        return ExecutionEventType.SUBMISSION_STARTED

    def _recover_ambiguous_submission(
        self, order_request_id: UUID, capsule: DecisionCapsule
    ) -> ExecutionAttemptOutcome | None:
        """Core critical path item 6 (review 1.20 §10 / review 1.21 §12):

        "query durable request state -> reconcile broker state ->
        determine whether the request already took effect." Runs only
        when a claimed request's last durable event is
        `SUBMISSION_STARTED` with nothing after it — the one state a
        process crash between that commitment and a real broker
        response could leave behind. Every other already-claimed state
        is a genuine terminal outcome, not an ambiguity, and needs no
        recovery.

        Never resubmits — `order_send` is not called here, and nothing
        in this method decides to attempt one. It only reads broker
        state (already-reachable, read-only) and durably records what
        it found. Idempotent: once this appends
        `AMBIGUOUS_OUTCOME_RESOLVED`, the next pass's event-history
        check no longer sees `SUBMISSION_STARTED` as the last event, so
        recovery never re-runs or re-reads the broker for an
        already-resolved request.

        Scoped to open positions only, not pending orders — `magic` is
        not tracked for pending orders at any layer today
        (`review/DEVIATIONS.md`); a submitted `EntryType.LIMIT` order
        sitting pending, not yet filled, would not be found by this
        check. Named as a real, separate gap, not silently ignored.

        Phase B item B4: more than one matching position is an integrity
        ambiguity, not a stronger form of "submitted" — see the `>1`
        branch below and `_trip_submission_integrity_ambiguous`.
        """
        events = self._events.events_for(order_request_id)
        if not events or events[-1].event_type is not ExecutionEventType.SUBMISSION_STARTED:
            return None

        magic = mt5_magic_number(order_request_id)
        matches = tuple(
            position for position in self._adapter.positions() if position.magic == magic
        )
        now = self._clock()

        if len(matches) > 1:
            self._append(
                order_request_id,
                ExecutionEventType.AMBIGUOUS_OUTCOME_RESOLVED,
                now,
                payload={
                    "magic_number": magic,
                    "integrity_ambiguity": True,
                    "matching_position_count": len(matches),
                    "matching_tickets": [position.ticket for position in matches],
                },
            )
            self._trip_submission_integrity_ambiguous(order_request_id, matches, now)
            return ExecutionAttemptOutcome(
                order_request_id=order_request_id,
                capsule_id=capsule.capsule_id,
                event_type=ExecutionEventType.AMBIGUOUS_OUTCOME_RESOLVED,
            )

        submitted = len(matches) == 1
        self._append(
            order_request_id,
            ExecutionEventType.AMBIGUOUS_OUTCOME_RESOLVED,
            now,
            payload={
                "magic_number": magic,
                "submitted": submitted,
                "matching_position_count": len(matches),
                "matching_tickets": [position.ticket for position in matches],
            },
        )
        return ExecutionAttemptOutcome(
            order_request_id=order_request_id,
            capsule_id=capsule.capsule_id,
            event_type=ExecutionEventType.AMBIGUOUS_OUTCOME_RESOLVED,
        )

    def _trip_submission_integrity_ambiguous(
        self,
        order_request_id: UUID,
        matches: tuple[PositionState, ...],
        now: UtcDatetime,
    ) -> None:
        """Phase B item B4: more than one broker position shares this

        request's magic number — a magic-number collision or corrupted
        broker/platform state, never a legitimate outcome of one MARKET
        order. Same idempotent-trip shape as `_trip_overnight_exposure`/
        `_trip_protective_stop_issue` — a no-op once already halted.
        """
        if not self._kill_switch.is_halted:
            tickets = [position.ticket for position in matches]
            self._kill_switch.trip(
                reason_codes=(ReasonCode.SUBMISSION_INTEGRITY_AMBIGUOUS,),
                tripped_by="ambiguous_recovery_driver",
                occurred_at_utc=now,
                detail=(
                    f"order_request_id {order_request_id}: {len(matches)} broker "
                    f"positions share magic number {mt5_magic_number(order_request_id)} "
                    f"(tickets={tickets}) -- cannot safely attribute any of them"
                ),
            )

    def flatten_once(self) -> FlattenAttemptOutcome | None:
        """Core critical path item 7 (ADR-009): the durable

        commitment/record half of the automatic weekly flatten (owner
        risk policy v1, D1.5). Called from the top of `run_once()`,
        independent of the capsule loop — see that call site's own
        comment for why.

        Early-returns before any broker read when the session policy is
        disabled (`intraday.enabled=False` — every test config). **Not**
        every shipped config's default: `config/paper.yaml` ships
        `enabled: true`, so this path is live under the shipped paper
        config, not merely a dormant one (ADR-009 §2.7 previously
        overstated this — corrected in `review/adr
        /ADR-012-owner-session-policy-v1.md`).

        Never calls `order_send` or `OrderCheckMt5Gateway.close_all_positions`
        — those stay unconditionally disabled. The gate opening still only
        appends `FLATTEN_SUBMISSION_STARTED` and stops, unchanged — Phase B
        item B5's real per-ticket close attempt
        (`review/adr/ADR-020-real-flatten-close.md`,
        `_attempt_and_resolve_flatten`) happens on the *next* pass instead,
        through the recovery branch below, and only when a real
        `FlattenCloseSink` was explicitly constructed and injected
        (`self._flatten_close_adapter`, `None` in every existing
        caller/test) *and* `flatten_submission_enabled` reads `True` right
        now — false in every shipped config today, so this remains inert
        in practice for the same reason every other Phase-B slice is. The
        `OVERNIGHT_EXPOSURE`/`FLATTEN_STATE_UNKNOWN` halt trips happen
        *after* the gate decision on every path they can reach from (the
        one exception is `FLATTEN_STATE_UNKNOWN`'s own trip immediately
        after the fresh broker read, before the emptiness shortcut — see
        below), so a halt this very pass causes can never be the halt
        that tolerates itself on `risk/flatten_gate.py`'s own
        `SYSTEM_HALTED` leg — that tolerance only ever applies starting
        the *next* pass.

        Durable state is checked *before* any broker read, exactly the
        order item 6's own docstring names ("query durable request state
        -> reconcile broker state"): today's flatten occurrence's
        identity is fully determined by the clock and config alone (no
        broker read needed to derive it), so a pass that already reached
        a terminal outcome for it — blocked, or resolved — returns
        immediately without touching the broker at all. Only a still-open
        `FLATTEN_SUBMISSION_STARTED` commitment, or no occurrence claimed
        yet, needs one.
        """
        now = self._clock()
        policy = trading_window.policy_from_config(self._config.intraday)
        if not policy.enabled:
            return None

        day = trading_day(now)
        # `session_close_utc` names the *weekly* close (owner risk policy
        # v1, D1.5) - coherent on every trading day, not only Friday's own.
        session_close_utc = weekly_close(now)
        flatten_deadline_utc = session_close_utc - policy.flatten_offset
        flatten_request_id = flatten_request_id_for(
            environment=self._config.environment,
            canonical_symbol=self._canonical_symbol,
            trading_day=day,
        )

        prior_events = self._flatten_events.events_for(flatten_request_id)
        if prior_events:
            if prior_events[-1].event_type is not FlattenEventType.FLATTEN_SUBMISSION_STARTED:
                # Already blocked or resolved for today's occurrence —
                # nothing to recover, no broker read needed.
                return None
            observation = capture_broker_state(
                self._adapter.reader,
                environment=self._config.environment,
                canonical_symbol=self._canonical_symbol,
                clock=self._clock,
            )
            self._broker_state.record(observation)
            positions = observation.position_states
            # Tripped from *this* pass's own fresh-before-any-attempt read,
            # not after: Phase B item B5's real close (inside
            # `_resolve_flatten_outcome`) may itself close every remaining
            # position this same pass, and re-tripping OVERNIGHT_EXPOSURE
            # from a now-stale `positions` afterward would misreport
            # exposure that a successful close just resolved. `KillSwitch
            # .trip` is idempotent, so ordering this first changes nothing
            # for the pre-B5 case where nothing ever closes.
            self._trip_overnight_exposure(positions, now)
            return self._resolve_flatten_outcome(flatten_request_id, positions, prior_events, now)

        # No occurrence claimed yet today — observe the broker and decide
        # whether one is needed. Same coherent-observation reasoning as
        # `_process()`'s own broker read (review 1.22 F-058).
        observation = capture_broker_state(
            self._adapter.reader,
            environment=self._config.environment,
            canonical_symbol=self._canonical_symbol,
            clock=self._clock,
        )
        self._broker_state.record(observation)
        positions = observation.position_states

        if (
            trading_window.phase_at(now, policy) is trading_window.SessionPhase.FLATTEN_REQUIRED
            and observation.account.position_set_state is not SnapshotCompleteness.COMPLETE
        ):
            # Owner risk policy v1 (D1.5): flat state cannot be confirmed by
            # the mandatory Friday deadline. An incomplete read that happens
            # to yield an empty `positions` tuple would otherwise be
            # indistinguishable from genuinely flat at the `if not positions`
            # shortcut below — under the weekly policy that gap is a whole
            # unmonitored weekend, not a day that self-corrects tomorrow.
            self._trip_flatten_state_unknown(now)

        if not positions:
            return None

        past_deadline = trading_window.requires_flat(now, policy)
        crossed_weekly_close = any(
            trading_window.has_crossed_weekly_close(position.opened_at_utc, now)
            for position in positions
        )
        if not (past_deadline or crossed_weekly_close):
            return None

        request_fingerprint = fingerprint(
            {
                "environment": self._config.environment.value,
                "canonical_symbol": self._canonical_symbol,
                "trading_day": day.isoformat(),
                "session_close_utc": session_close_utc.isoformat(),
                "flatten_deadline_utc": flatten_deadline_utc.isoformat(),
                "intraday_policy": {
                    "enabled": policy.enabled,
                    "last_entry_offset_seconds": int(policy.last_entry_offset.total_seconds()),
                    "flatten_offset_seconds": int(policy.flatten_offset.total_seconds()),
                },
            }
        )

        try:
            claim = self._flatten_requests.claim(
                flatten_request_id=flatten_request_id,
                environment=self._config.environment,
                canonical_symbol=self._canonical_symbol,
                trading_day=day,
                session_close_utc=session_close_utc,
                flatten_deadline_utc=flatten_deadline_utc,
                fingerprint=request_fingerprint,
                claimed_by=self._worker_id,
                now=now,
            )
        except FlattenRequestConflictError:
            _log.error("flatten.claim_conflict", flatten_request_id=str(flatten_request_id))
            raise

        # Tripped from this pass's own fresh-before-any-attempt read, not
        # after — see the recovery branch above's identical comment for why
        # (Phase B item B5's real close can resolve every remaining
        # position in the same call this trip would otherwise follow).
        self._trip_overnight_exposure(positions, now)

        if not claim.claimed:
            # Lost a race against another worker between the events_for()
            # read above and this claim — recover from whatever it
            # committed, exactly like the fresh-read path above.
            return self._resolve_flatten_outcome(
                flatten_request_id,
                positions,
                self._flatten_events.events_for(flatten_request_id),
                now,
            )
        return self._commit_flatten(
            flatten_request_id,
            positions=positions,
            observation=observation,
            day=day,
            session_close_utc=session_close_utc,
            flatten_deadline_utc=flatten_deadline_utc,
            past_deadline=past_deadline,
            crossed_weekly_close=crossed_weekly_close,
            now=now,
        )

    def _commit_flatten(
        self,
        flatten_request_id: UUID,
        *,
        positions: tuple[PositionState, ...],
        observation: BrokerStateObservation,
        day: date,
        session_close_utc: UtcDatetime,
        flatten_deadline_utc: UtcDatetime,
        past_deadline: bool,
        crossed_weekly_close: bool,
        now: UtcDatetime,
    ) -> FlattenAttemptOutcome:
        """The just-claimed branch: gate, then commit or refuse. Never

        reached for an already-claimed occurrence — see
        `_resolve_flatten_outcome` for that path.
        """
        self._append_flatten(flatten_request_id, FlattenEventType.FLATTEN_REQUEST_CLAIMED, now)

        assert observation.account_state is not None
        market = self._config.market_for(self._canonical_symbol)
        expectation = ExpectedState.flat(
            self._config.account_guard,
            canonical_symbol=self._canonical_symbol,
            expected_spec_version=market.expected_spec_version if market is not None else None,
        )
        reconciliation = reconcile(
            self._broker_state, expectation, instrument_specs=self._instrument_specs, now=now
        )

        context = FlattenGateContext(
            environment=self._config.environment,
            account=observation.account_state,
            terminal_trade_allowed=bool(observation.account.terminal_trade_allowed),
            position_book_complete=observation.account.position_set_state
            is SnapshotCompleteness.COMPLETE,
            reconciliation_status=reconciliation.status,
            kill_switch=self._kill_switch,
            flatten_required=past_deadline or crossed_weekly_close,
            risk_config_version=self._config.config_version,
            approved_risk_config_version=self._config.risk.approved_config_version,
            flatten_submission_enabled=self._config.execution.flatten_submission_enabled,
            feedback_2_0_approved=self._config.execution.feedback_2_0_approved,
            now=now,
        )
        decision = evaluate_flatten_gate(context)
        context_payload: dict[str, object] = {
            "environment": context.environment.value,
            "terminal_trade_allowed": context.terminal_trade_allowed,
            "position_book_complete": context.position_book_complete,
            "reconciliation_status": context.reconciliation_status.value,
            "flatten_required": context.flatten_required,
            "risk_config_version": context.risk_config_version,
            "approved_risk_config_version": context.approved_risk_config_version,
            "flatten_submission_enabled": context.flatten_submission_enabled,
            "feedback_2_0_approved": context.feedback_2_0_approved,
        }

        if not decision.open:
            self._append_flatten(
                flatten_request_id,
                FlattenEventType.FLATTEN_GATE_BLOCKED,
                now,
                reason_codes=decision.reason_codes,
                payload=context_payload,
            )
            return FlattenAttemptOutcome(
                flatten_request_id=flatten_request_id,
                event_type=FlattenEventType.FLATTEN_GATE_BLOCKED,
                reason_codes=decision.reason_codes,
            )

        self._append_flatten(
            flatten_request_id, FlattenEventType.FLATTEN_GATE_PASSED, now, payload=context_payload
        )

        plan = build_flatten_plan(
            positions,
            flatten_request_id=flatten_request_id,
            environment=self._config.environment,
            canonical_symbol=self._canonical_symbol,
            trading_day=day,
            session_close_utc=session_close_utc,
            flatten_deadline_utc=flatten_deadline_utc,
            past_deadline=past_deadline,
            broker_state_snapshot_id=observation.account.snapshot_id,
            now=now,
        )
        self._append_flatten(
            flatten_request_id,
            FlattenEventType.FLATTEN_SUBMISSION_STARTED,
            now,
            payload=plan.model_dump(mode="json"),
        )
        # Phase B item B5: the real close attempt is deliberately *not*
        # made inline here — it happens on the next `flatten_once()` pass,
        # via the same `_attempt_and_resolve_flatten` this method's own
        # commitment feeds (through the recovery branch's read of this
        # exact event's payload). One pass commits, the next pass acts —
        # the same two-step shape `_recover_ambiguous_submission` already
        # established for entries (item 6), kept here rather than
        # collapsed into one call: it keeps "durable commitment" and "the
        # one place a real broker write can happen" on two sides of a
        # full pass boundary, never sharing a call stack with the gate
        # decision that just authorized this commitment.
        return FlattenAttemptOutcome(
            flatten_request_id=flatten_request_id,
            event_type=FlattenEventType.FLATTEN_SUBMISSION_STARTED,
            target_count=len(plan.instructions),
        )

    def _resolve_flatten_outcome(
        self,
        flatten_request_id: UUID,
        positions: tuple[PositionState, ...],
        events: tuple[FlattenEventRecord, ...],
        now: UtcDatetime,
    ) -> FlattenAttemptOutcome | None:
        """The item-6-shaped idempotent recovery for a flatten (ADR-009 §2,

        extended by Phase B item B5). `events` is passed in rather than
        re-read here: both call sites in `flatten_once()` already have it
        (one from the durable-state-first check, one from the
        post-claim-loss re-read), and re-fetching a third time would be a
        redundant query with no new information.

        Runs only when the occurrence's last durable event is
        `FLATTEN_SUBMISSION_STARTED` with nothing after it — the one state
        a crash between commitment and a real close attempt (or between
        attempts, across passes, once B5's real close exists) could leave
        behind. Reconstructs the committed `FlattenInstruction`s from that
        event's own persisted payload — the targets were already named at
        commit time — and delegates to `_attempt_and_resolve_flatten` for
        the actual (possibly real, possibly still inert) resolution,
        exactly as `_commit_flatten` does on the pass that first commits.
        """
        if not events or events[-1].event_type is not FlattenEventType.FLATTEN_SUBMISSION_STARTED:
            return None

        commitment_payload = events[-1].payload or {}
        instructions = tuple(
            FlattenInstruction.model_validate(instruction)
            for instruction in commitment_payload.get("instructions", [])
        )
        return self._attempt_and_resolve_flatten(flatten_request_id, positions, instructions, now)

    def _attempt_and_resolve_flatten(
        self,
        flatten_request_id: UUID,
        positions: tuple[PositionState, ...],
        instructions: tuple[FlattenInstruction, ...],
        now: UtcDatetime,
    ) -> FlattenAttemptOutcome:
        """Phase B item B5 (`review/adr/ADR-020-real-flatten-close.md`): the

        one place a real close is attempted, and the one place a flatten
        occurrence's outcome is durably resolved. Called only from
        `_resolve_flatten_outcome`, itself only reached on a pass *after*
        the one that committed (`_commit_flatten` appends
        `FLATTEN_SUBMISSION_STARTED` and stops, deliberately — see that
        method's own comment) — a full `run_once()` pass boundary always
        separates "durably commit to a flatten" from "the one place a
        real broker write can happen," the same two-step shape
        `_recover_ambiguous_submission` already established for entries
        (item 6).

        `FlattenEventType` events are append-once per `(flatten_request_id,
        event_type)` (`persistence/flatten.py`) — a second, differently-
        content append of the same type raises. This is why a still-open
        residual after a genuine attempt does **not** append anything here:
        `FLATTEN_OUTCOME_RESOLVED` is appended only once the outcome is
        actually known (fully closed, or no attempt was currently
        possible), so a failed attempt simply leaves `FLATTEN_SUBMISSION_STARTED`
        as the last event and lets the *next* `run_once()` pass retry with
        a fresh observation — no artificial retry counter, no blind
        resubmission of an already-closed ticket (only currently-still-open
        targets are ever attempted).
        """
        open_tickets = {position.ticket for position in positions}
        still_open = [
            instruction for instruction in instructions if instruction.ticket in open_tickets
        ]

        attempted = False
        if (
            still_open
            and self._flatten_close_adapter is not None
            and self._config.execution.flatten_submission_enabled
        ):
            attempted = True
            for instruction in still_open:
                try:
                    result = self._flatten_close_adapter.close_position(instruction)
                except Exception:
                    _log.error(
                        "flatten.close_attempt_failed",
                        flatten_request_id=str(flatten_request_id),
                        ticket=instruction.ticket,
                    )
                    continue
                if not close_result_fully_closed(result):
                    _log.error(
                        "flatten.close_attempt_not_filled",
                        flatten_request_id=str(flatten_request_id),
                        ticket=instruction.ticket,
                        state=result.state.value,
                    )

            # Never trust the raw close response alone — confirm from a
            # fresh broker observation, the same discipline `_process()`'s
            # own FINAL Risk read already uses (review 1.22 F-058).
            observation = capture_broker_state(
                self._adapter.reader,
                environment=self._config.environment,
                canonical_symbol=self._canonical_symbol,
                clock=self._clock,
            )
            self._broker_state.record(observation)
            open_tickets = {position.ticket for position in observation.position_states}
            still_open = [
                instruction for instruction in instructions if instruction.ticket in open_tickets
            ]
            now = self._clock()

        target_tickets = [instruction.ticket for instruction in instructions]
        still_open_tickets = [instruction.ticket for instruction in still_open]
        closed_tickets = [ticket for ticket in target_tickets if ticket not in still_open_tickets]

        if not still_open:
            self._append_flatten(
                flatten_request_id,
                FlattenEventType.FLATTEN_OUTCOME_RESOLVED,
                now,
                payload={
                    "target_tickets": target_tickets,
                    "still_open_tickets": [],
                    "closed_tickets": closed_tickets,
                    "flattened": True,
                },
            )
            return FlattenAttemptOutcome(
                flatten_request_id=flatten_request_id,
                event_type=FlattenEventType.FLATTEN_OUTCOME_RESOLVED,
                target_count=len(target_tickets),
                closed_count=len(closed_tickets),
            )

        if attempted:
            # A genuine attempt was made and a residual remains — retry
            # next pass, never blind-resubmit now. No terminal event is
            # appended (see this method's own docstring): FLATTEN_SUBMISSION_STARTED
            # stays the last durable event on purpose.
            self._trip_flatten_close_failed(flatten_request_id, still_open_tickets, now)
            return FlattenAttemptOutcome(
                flatten_request_id=flatten_request_id,
                event_type=FlattenEventType.FLATTEN_SUBMISSION_STARTED,
                reason_codes=(ReasonCode.FLATTEN_CLOSE_FAILED,),
                target_count=len(target_tickets),
                closed_count=len(closed_tickets),
            )

        # No attempt was currently possible (no adapter, or the flag reads
        # false right now) — the same terminal call this method's pre-B5
        # predecessor always made, unchanged.
        self._append_flatten(
            flatten_request_id,
            FlattenEventType.FLATTEN_OUTCOME_RESOLVED,
            now,
            payload={
                "target_tickets": target_tickets,
                "still_open_tickets": still_open_tickets,
                "closed_tickets": closed_tickets,
                "flattened": False,
            },
        )
        return FlattenAttemptOutcome(
            flatten_request_id=flatten_request_id,
            event_type=FlattenEventType.FLATTEN_OUTCOME_RESOLVED,
            target_count=len(target_tickets),
            closed_count=len(closed_tickets),
        )

    def _trip_flatten_close_failed(
        self, flatten_request_id: UUID, still_open_tickets: list[int], now: UtcDatetime
    ) -> None:
        """Phase B item B5: a real close attempt did not fully resolve this

        occurrence. Same idempotent-trip shape as `_trip_overnight_exposure`/
        `_trip_flatten_state_unknown`/`_trip_protective_stop_issue` — a
        no-op once already halted, and tolerated in
        `risk/flatten_gate.py::_TOLERATED_HALT_REASONS` so the flatten
        mechanism can keep retrying past a halt it caused itself."""
        if not self._kill_switch.is_halted:
            self._kill_switch.trip(
                reason_codes=(ReasonCode.FLATTEN_CLOSE_FAILED,),
                tripped_by="flatten_driver",
                occurred_at_utc=now,
                detail=(
                    f"flatten_request_id {flatten_request_id}: a real close attempt left "
                    f"{len(still_open_tickets)} position(s) still open (tickets="
                    f"{still_open_tickets})"
                ),
            )

    def _trip_overnight_exposure(
        self, positions: tuple[PositionState, ...], now: UtcDatetime
    ) -> None:
        """Same halt `_check_session_boundary` already trips in the

        decision tier (`application/orchestration.py`/`live_decision.py`)
        — a fourth producer, in the one tier that can actually see the
        broker. `KillSwitch.trip` is idempotent, so a double-trip across
        tiers is harmless. `tripped_by="flatten_driver"` distinguishes
        this producer from `"risk_engine"` in the audit log. Nothing here
        clears, downgrades, or shortens any halt — it stays in force
        until an operator resets it, exactly as before this item. A
        no-op when `positions` is empty: this is called from the
        already-committed resolution path too, where the position(s) may
        have genuinely closed between passes (an operator's manual
        flatten, say) — that is not a fresh breach to trip on.
        """
        if not positions:
            return
        if not self._kill_switch.is_halted:
            self._kill_switch.trip(
                reason_codes=(ReasonCode.OVERNIGHT_EXPOSURE,),
                tripped_by="flatten_driver",
                occurred_at_utc=now,
                detail=(
                    f"{len(positions)} position(s) still open past the flatten "
                    "deadline or the weekly close"
                ),
            )

    def _trip_flatten_state_unknown(self, now: UtcDatetime) -> None:
        """Owner risk policy v1 (D1.5): the position book could not be

        confirmed flat by the mandatory Friday deadline — the broker read
        was incomplete, so an empty `positions` tuple cannot be trusted.
        Tripped *before* `flatten_once()`'s own emptiness shortcut can
        silently treat that as genuinely flat. Same idempotent-trip shape
        as `_trip_overnight_exposure` — a no-op once already halted.
        """
        if not self._kill_switch.is_halted:
            self._kill_switch.trip(
                reason_codes=(ReasonCode.FLATTEN_STATE_UNKNOWN,),
                tripped_by="flatten_driver",
                occurred_at_utc=now,
                detail="position book could not be confirmed flat by the Friday deadline",
            )

    def _trip_protective_stop_issue(
        self,
        order_request_id: UUID,
        issues: tuple[ProtectiveStopIssue, ...],
        now: UtcDatetime,
    ) -> None:
        """Core critical path item 9: broker truth disagrees with, or

        cannot confirm, this platform's own intended protective stop for
        a position it attributes to itself. Same idempotent-trip shape as
        `_trip_overnight_exposure`/`_trip_flatten_state_unknown` — a
        no-op once already halted. `tripped_by="reconciliation_driver"`
        distinguishes this producer in the audit log. Deliberately does
        not touch `reconcile()`'s own MATCHED/MISMATCHED/UNKNOWN verdict
        (`review/DEVIATIONS.md` D-051 gap 3's scope boundary) — this is a
        dedicated, narrowly-scoped escalation, not a re-derivation of the
        book-level status.
        """
        if not self._kill_switch.is_halted:
            reason_codes = tuple(sorted({issue.reason for issue in issues}, key=lambda r: r.value))
            detail = "; ".join(
                f"ticket={issue.ticket} {issue.reason.value} "
                f"expected={issue.expected} observed={issue.observed}"
                for issue in issues
            )
            self._kill_switch.trip(
                reason_codes=reason_codes,
                tripped_by="reconciliation_driver",
                occurred_at_utc=now,
                detail=(
                    f"order_request_id {order_request_id} protective-stop verification "
                    f"failed: {detail}"
                ),
            )

    def _append_flatten(
        self,
        flatten_request_id: UUID,
        event_type: FlattenEventType,
        now: UtcDatetime,
        *,
        reason_codes: tuple[ReasonCode, ...] = (),
        detail: str | None = None,
        payload: dict[str, object] | None = None,
    ) -> None:
        self._flatten_events.append(
            flatten_request_id=flatten_request_id,
            event_type=event_type,
            occurred_at_utc=now,
            reason_codes=reason_codes,
            detail=detail,
            payload=payload,
        )

    def reconcile_once(self) -> tuple[ReconciliationAttemptOutcome, ...]:
        """Core critical path item 8 (ADR-010): "derive post-fill expected

        state from durable platform execution history and reconcile it
        against broker truth" (review 1.26 §6 item 8). Walks requests, not
        capsules — see `ReconciliationAttemptOutcome`'s own docstring for
        why its outcome type has no `capsule_id`.

        Durable state is checked *before* any broker read, the same
        ordering ADR-008 established: `request_ids_with_event
        (SUBMISSION_STARTED)` is the complete, exhaustively-proven
        candidate set (`application/expected_state.py`'s own mapping over
        every `ExecutionEventType`) — provably empty in every shipped
        config today, so this returns before touching the broker in every
        real deployment and the entire pre-existing test suite. A second,
        narrower check follows the same discipline one step further: if
        every candidate already carries a `RECONCILED` event, there is
        nothing left this pass could possibly determine, and this returns
        before reading the broker for that reason too — mirroring
        `flatten_once()`'s own "already resolved, no broker read" branch.

        `RECONCILED` is appended at most once per request, ever
        (`event_id_for` derives from `(order_request_id, event_type)`
        alone) — a terminal determination, not a per-pass heartbeat.
        Appended only when: the request has no `RECONCILED` yet; its own
        exposure is durably `DETERMINED` (never for a request still
        `UNDETERMINED`); the whole-book verdict is not `UNKNOWN`; and
        every one of its attributed tickets is individually accounted for
        (observed open at the broker, or already removed by a resolved
        flatten). A request failing any of these stays unreconciled and
        is re-examined next pass.
        """
        candidates = self._events.request_ids_with_event(ExecutionEventType.SUBMISSION_STARTED)
        if not candidates:
            return ()

        request_histories = tuple((rid, self._events.events_for(rid)) for rid in candidates)
        pending_ids = frozenset(
            rid
            for rid, events in request_histories
            if not any(event.event_type is ExecutionEventType.RECONCILED for event in events)
        )
        if not pending_ids:
            return ()

        # Full history feeds the derivation — including already-reconciled
        # requests — because reconciling one request does not remove its
        # exposure; the whole-book expectation must still account for it.
        # `pending_ids` alone decides which requests may *receive* a new
        # `RECONCILED` this pass.
        flatten_histories = self._flatten_events.occurrence_histories(
            environment=self._config.environment, canonical_symbol=self._canonical_symbol
        )
        exposure = derive_expected_exposure(request_histories, flatten_histories=flatten_histories)

        market = self._config.market_for(self._canonical_symbol)
        expectation = ExpectedState.from_durable_exposure(
            self._config.account_guard,
            exposure,
            canonical_symbol=self._canonical_symbol,
            expected_spec_version=market.expected_spec_version if market is not None else None,
        )

        now = self._clock()
        observation = capture_broker_state(
            self._adapter.reader,
            environment=self._config.environment,
            canonical_symbol=self._canonical_symbol,
            clock=self._clock,
        )
        self._broker_state.record(observation)

        result = reconcile(
            self._broker_state, expectation, instrument_specs=self._instrument_specs, now=now
        )
        if result.status is ReconciliationStatus.UNKNOWN:
            return ()

        open_tickets = {position.ticket for position in observation.position_states}
        outcomes: list[ReconciliationAttemptOutcome] = []
        for order_request_id, events in request_histories:
            if order_request_id not in pending_ids:
                continue
            if order_request_id not in exposure.determined_request_ids:
                continue
            attributed = exposure.tickets_by_request.get(order_request_id, frozenset())
            if attributed - open_tickets:
                # A ticket the platform believes is still its own is not
                # currently observed open, and no resolved flatten
                # already accounted for its closure — this request stays
                # unreconciled rather than recording a false "accounted
                # for" verdict. The whole-book `result.status` above
                # already independently surfaces this as MISMATCHED.
                continue

            raw_matching: frozenset[int] = frozenset()
            for event in events:
                if event.event_type is ExecutionEventType.AMBIGUOUS_OUTCOME_RESOLVED and (
                    event.payload
                ):
                    raw_matching = frozenset(
                        int(ticket) for ticket in event.payload.get("matching_tickets", [])
                    )
            closed_by_flatten = raw_matching - attributed

            protective_stop_issues = verify_protective_stops(
                observation.position_states,
                attributed=attributed,
                expected_stop_loss_price=exposure.expected_stop_loss_by_request.get(
                    order_request_id
                ),
            )
            if protective_stop_issues:
                self._trip_protective_stop_issue(order_request_id, protective_stop_issues, now)

            self._append(
                order_request_id,
                ExecutionEventType.RECONCILED,
                now,
                payload={
                    "expected_position_tickets": sorted(attributed),
                    "observed_open_tickets": sorted(attributed & open_tickets),
                    "closed_tickets": sorted(closed_by_flatten),
                    "expected_pending_order_ids": [],
                    "book_status": result.status.value,
                    "protective_stop_issues": [
                        {
                            "ticket": issue.ticket,
                            "reason": issue.reason.value,
                            "expected": str(issue.expected) if issue.expected is not None else None,
                            "observed": str(issue.observed) if issue.observed is not None else None,
                        }
                        for issue in protective_stop_issues
                    ],
                },
            )
            outcomes.append(
                ReconciliationAttemptOutcome(
                    order_request_id=order_request_id,
                    book_status=result.status,
                    accounted_ticket_count=len(attributed),
                )
            )
        return tuple(outcomes)

    def _evaluate_submission_readiness(
        self,
        order_request_id: UUID,
        *,
        observation: BrokerStateObservation,
        tick: MarketTick,
        final_now: UtcDatetime,
    ) -> tuple[ExecutionEventType, tuple[ReasonCode, ...]]:
        """F-049's `SubmissionGate`, evaluated for real (ADR-006/Dev-1

        core critical path item 2). Every signal is already in scope from
        `_process()`'s own preceding reads — no new MT5 call, including
        terminal AlgoTrading state, which `capture_broker_state()` already
        captured into `observation.account.terminal_trade_allowed`.
        """
        assert observation.account_state is not None
        context = SubmissionGateContext(
            environment=self._config.environment,
            account=observation.account_state,
            reconciliation_status=ReconciliationStatus.MATCHED,
            fresh_tick=tick,
            max_market_data_age_ms=self._config.execution.max_market_data_age_ms,
            kill_switch=self._kill_switch,
            risk_config_version=self._config.config_version,
            approved_risk_config_version=self._config.risk.approved_config_version,
            submission_enabled=self._config.execution.submission_enabled,
            terminal_trade_allowed=bool(observation.account.terminal_trade_allowed),
            feedback_2_0_approved=self._config.execution.feedback_2_0_approved,
            approved_account_ref=self._config.execution.approved_canary_account_ref,
            now=final_now,
        )
        decision = evaluate_submission_gate(context)
        event_type = (
            ExecutionEventType.SUBMISSION_GATE_PASSED
            if decision.open
            else ExecutionEventType.SUBMISSION_GATE_BLOCKED
        )
        self._append(
            order_request_id,
            event_type,
            final_now,
            reason_codes=decision.reason_codes,
            payload={
                "environment": context.environment.value,
                "reconciliation_status": context.reconciliation_status.value,
                "max_market_data_age_ms": context.max_market_data_age_ms,
                "risk_config_version": context.risk_config_version,
                "approved_risk_config_version": context.approved_risk_config_version,
                "submission_enabled": context.submission_enabled,
                "terminal_trade_allowed": context.terminal_trade_allowed,
                "feedback_2_0_approved": context.feedback_2_0_approved,
                "approved_account_ref": context.approved_account_ref,
                "observed_account_ref": context.account.login_hash,
            },
        )
        return event_type, decision.reason_codes

    def _risk_context(self) -> policies.RiskContext:
        config = self._config
        return policies.RiskContext(
            risk=config.risk,
            execution=config.execution,
            allowed_symbols=frozenset(config.enabled_symbols()),
            require_demo_account=config.account_guard.require_demo_account,
            expected_server=config.account_guard.expected_server,
            expected_login=None,
            expected_currency=config.account_guard.expected_currency,
            expected_leverage=config.account_guard.expected_leverage,
            risk_config_version=config.config_version,
            intraday=trading_window.policy_from_config(config.intraday),
        )

    def _refuse(
        self,
        order_request_id: UUID,
        capsule: DecisionCapsule,
        event_type: ExecutionEventType,
        reason_codes: tuple[ReasonCode, ...],
        now: UtcDatetime,
        *,
        detail: str | None = None,
        payload: dict[str, object] | None = None,
    ) -> ExecutionAttemptOutcome:
        self._append(
            order_request_id,
            event_type,
            now,
            reason_codes=reason_codes,
            detail=detail,
            payload=payload,
        )
        return ExecutionAttemptOutcome(
            order_request_id=order_request_id,
            capsule_id=capsule.capsule_id,
            event_type=event_type,
            reason_codes=reason_codes,
        )

    def _append(
        self,
        order_request_id: UUID,
        event_type: ExecutionEventType,
        now: UtcDatetime,
        *,
        reason_codes: tuple[ReasonCode, ...] = (),
        detail: str | None = None,
        payload: dict[str, object] | None = None,
    ) -> None:
        self._events.append(
            order_request_id=order_request_id,
            event_type=event_type,
            occurred_at_utc=now,
            reason_codes=reason_codes,
            detail=detail,
            payload=payload,
        )


def _approval_chain_fingerprint(capsule: DecisionCapsule) -> str:
    """Bind the whole pre-execution approval chain, not only the intent.

    Review 1.22 F-059: `intent.decision_hash` alone proves two different
    `TradeIntent`s cannot collide, but not that two *differently-approved*
    executions of the same intent (same decision hash, different
    intent-time `RiskDecision`/`SupervisorDecision` content — genuinely
    possible, since `decision_hash` is computed from `TradeIntent`'s own
    fields only) cannot silently collapse into "already claimed, harmless
    retry." This is that binding, used as `ExecutionRequestStore.claim()`'s
    fingerprint.

    Review 1.23 F-059 (reopened as "partly closed"): an earlier version of
    this function hand-selected specific fields off `RiskDecision`/
    `SupervisorDecision` — a list that silently stops covering a field the
    moment it is added to either contract without this function being
    updated too (`SupervisorDecision.uncalibrated_checks` named as the
    concrete example: it "explicitly changes what a Supervisor approval
    means" and was not in the original hand-picked list). Fingerprinting
    each contract's *complete* serialized content instead means this
    function never needs to change again as those contracts grow.
    """
    assert capsule.trade_intent is not None
    assert capsule.risk_decision is not None
    assert capsule.supervisor_decision is not None
    return fingerprint(
        {
            "provenance_fingerprint": capsule.provenance_fingerprint,
            # `TradeIntent.decision_hash` *is* that model's complete-content
            # fingerprint (every field but `intent_id`, already used
            # throughout this codebase as the canonical identity for "this
            # intent's content") — reused rather than a second
            # `model_dump(mode="json")` of the same model, which would also
            # need to survive `TradeIntent.confidence` being a genuine
            # `float` field (`_canonical()` rejects raw floats on purpose;
            # `decision_hash` already handles this the same way via `repr()`).
            "trade_intent_decision_hash": capsule.trade_intent.decision_hash,
            "intent_risk_decision": capsule.risk_decision.model_dump(mode="json"),
            "supervisor_decision": capsule.supervisor_decision.model_dump(mode="json"),
        }
    )


def _is_intent_time_approved(capsule: DecisionCapsule) -> bool:
    return (
        capsule.trade_intent is not None
        and capsule.risk_decision is not None
        and capsule.risk_decision.verdict is RiskVerdict.PASS
        and capsule.supervisor_decision is not None
        and capsule.supervisor_decision.verdict is SupervisorVerdict.APPROVE
    )
