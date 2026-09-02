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
from decimal import Decimal
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from crumblr.application.broker_state import BrokerStateObservation, capture_broker_state
from crumblr.application.flatten_plan import build_flatten_plan
from crumblr.application.reconciliation import (
    BrokerStateSource,
    ExpectedState,
    InstrumentSpecSource,
    reconcile,
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
from crumblr.risk.session import RiskSessionStore, recover_session
from crumblr.risk.submission_gate import SubmissionGateContext, evaluate_submission_gate
from crumblr.trading_agent.sessions import trading_day

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
        kill_switch: KillSwitch,
        adapter: OrderCheckMt5Gateway,
        canonical_symbol: str = "EUR/USD",
        activation_watermark: UtcDatetime | None = None,
        worker_id: str = "execution-orchestrator",
        clock: Callable[[], UtcDatetime] = utc_now,
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
        self._kill_switch = kill_switch
        self._adapter = adapter
        self._canonical_symbol = canonical_symbol
        self._activation_watermark = activation_watermark
        self._worker_id = worker_id
        self._clock = clock

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

        session_recovery = recover_session(
            self._session_store.load_latest(),
            live_equity=observation.account_state.equity,
            live_open_positions=len(observation.position_states),
            market_day=trading_day(final_now),
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

        fresh_portfolio = policies.PortfolioState(
            account=observation.account_state,
            open_positions=observation.position_states,
            ledger=session_recovery.ledger,
            orders_in_last_hour=orders_in_last_hour,
            seen_decision_hashes=frozenset(),
            open_risk_fraction=self._config.risk.max_risk_per_trade
            * Decimal(len(observation.position_states)),
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
        """
        events = self._events.events_for(order_request_id)
        if not events or events[-1].event_type is not ExecutionEventType.SUBMISSION_STARTED:
            return None

        magic = mt5_magic_number(order_request_id)
        matches = tuple(
            position for position in self._adapter.positions() if position.magic == magic
        )
        submitted = len(matches) > 0

        now = self._clock()
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

    def flatten_once(self) -> FlattenAttemptOutcome | None:
        """Core critical path item 7 (ADR-009): the durable

        commitment/record half of the automatic intraday flatten. Called
        from the top of `run_once()`, independent of the capsule loop —
        see that call site's own comment for why.

        Early-returns before any broker read when the intraday policy is
        disabled (`intraday.enabled=False` — every test config, and every
        shipped config's default), which is what keeps this change
        provably inert for the existing capsule-focused test suite: no
        extra broker read, no extra table row, for any of them.

        Never calls `close_all_positions` or `order_send` — the gate
        opening only appends `FLATTEN_SUBMISSION_STARTED` and stops. The
        `OVERNIGHT_EXPOSURE` halt trip happens *after* the gate decision
        on every path, so a halt this very pass causes can never be the
        halt that tolerates itself on `risk/flatten_gate.py`'s own
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
        session_close_utc = trading_window.session_close(now)
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
            outcome = self._resolve_flatten_outcome(
                flatten_request_id, positions, prior_events, now
            )
            self._trip_overnight_exposure(positions, now)
            return outcome

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
        if not positions:
            return None

        past_deadline = trading_window.requires_flat(now, policy)
        crossed_rollover = any(
            trading_window.has_crossed_rollover(position.opened_at_utc, now)
            for position in positions
        )
        if not (past_deadline or crossed_rollover):
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

        if not claim.claimed:
            # Lost a race against another worker between the events_for()
            # read above and this claim — recover from whatever it
            # committed, exactly like the fresh-read path above.
            outcome = self._resolve_flatten_outcome(
                flatten_request_id,
                positions,
                self._flatten_events.events_for(flatten_request_id),
                now,
            )
        else:
            outcome = self._commit_flatten(
                flatten_request_id,
                positions=positions,
                observation=observation,
                day=day,
                session_close_utc=session_close_utc,
                flatten_deadline_utc=flatten_deadline_utc,
                past_deadline=past_deadline,
                crossed_rollover=crossed_rollover,
                now=now,
            )

        self._trip_overnight_exposure(positions, now)
        return outcome

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
        crossed_rollover: bool,
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
            flatten_required=past_deadline or crossed_rollover,
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
        """The item-6-shaped idempotent recovery for a flatten (ADR-009 §2).

        `events` is passed in rather than re-read here: both call sites in
        `flatten_once()` already have it (one from the durable-state-first
        check, one from the post-claim-loss re-read), and re-fetching a
        third time would be a redundant query with no new information.

        Runs only when the occurrence's last durable event is
        `FLATTEN_SUBMISSION_STARTED` with nothing after it. Unlike
        `_recover_ambiguous_submission` (which searches broker positions
        by `mt5_magic_number`), this reads the target tickets recorded in
        the commitment event's own payload and checks which are still
        open — a simpler, more direct determination, since the targets
        were already named. Because `close_all_positions` stays
        unreachable, this will provably always conclude every target is
        still open today — the same honest inertness ADR-008 documents
        for its own positive branch.
        """
        if not events or events[-1].event_type is not FlattenEventType.FLATTEN_SUBMISSION_STARTED:
            return None

        commitment_payload = events[-1].payload or {}
        target_tickets = [
            instruction["ticket"] for instruction in commitment_payload.get("instructions", [])
        ]
        open_tickets = {position.ticket for position in positions}
        still_open = [ticket for ticket in target_tickets if ticket in open_tickets]
        closed = [ticket for ticket in target_tickets if ticket not in open_tickets]

        self._append_flatten(
            flatten_request_id,
            FlattenEventType.FLATTEN_OUTCOME_RESOLVED,
            now,
            payload={
                "target_tickets": target_tickets,
                "still_open_tickets": still_open,
                "closed_tickets": closed,
                "flattened": len(still_open) == 0,
            },
        )
        return FlattenAttemptOutcome(
            flatten_request_id=flatten_request_id,
            event_type=FlattenEventType.FLATTEN_OUTCOME_RESOLVED,
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
                    "deadline or a rollover"
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
