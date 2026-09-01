"""Closed vocabularies shared by every component.

Every enum here is part of the persisted contract: values are written to the
event journal and to `decision_capsules`, so renaming a member is a schema
change, not a refactor.
"""

from __future__ import annotations

from enum import StrEnum


class Environment(StrEnum):
    """Execution environment. Gates which accounts and actions are permitted."""

    BACKTEST = "backtest"
    REPLAY = "replay"
    PAPER = "paper"
    SHADOW = "shadow"
    LIVE = "live"


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    FLAT = "FLAT"


class EntryType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"


class DataQuality(StrEnum):
    """build.md §12.3. A strategy must not trade on SUSPECT data."""

    GOOD = "GOOD"
    STALE = "STALE"
    GAPPED = "GAPPED"
    OUT_OF_ORDER = "OUT_OF_ORDER"
    SUSPECT = "SUSPECT"


TRADEABLE_DATA_QUALITY: frozenset[DataQuality] = frozenset({DataQuality.GOOD})
"""Data qualities a TradeIntent may be derived from. Everything else fails closed."""


class BarOrigin(StrEnum):
    """How a stored bar came to exist (review 1.6 F-022).

    A bar the broker sent and a bar this platform built out of ticks are not
    interchangeable evidence, and six months later nobody will remember which
    was which. Storing the origin makes the question answerable instead.
    """

    BROKER = "BROKER"
    """Delivered by the broker's own bar feed — `copy_rates` at M1."""

    AGGREGATED_FROM_TICKS = "AGGREGATED_FROM_TICKS"
    """Built here from a tick stream, by a named and versioned pipeline."""

    SYNTHETIC = "SYNTHETIC"
    """Produced by the replay generator. Never evidence about a real market."""


class StreamAnomaly(StrEnum):
    """What a market-data stream did that it should not have (build.md §12.3).

    Milestone 2 requires gaps and out-of-order data to be *detected*, which is
    a different claim from handled: each of these is recorded against the data
    it was found in so that a decision made on a degraded stream can be
    identified afterwards rather than blending in.
    """

    GAP = "GAP"
    """An expected interval produced nothing."""

    OUT_OF_ORDER = "OUT_OF_ORDER"
    """An observation arrived carrying an earlier timestamp than its predecessor."""

    DUPLICATE = "DUPLICATE"
    """The same instant was delivered more than once."""

    CROSSED_QUOTE = "CROSSED_QUOTE"
    """Ask below bid. Never tradeable, and worth keeping as evidence."""

    STALLED = "STALLED"
    """The stream ran on but the quote stopped changing for longer than expected."""


class SessionState(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    PRE_OPEN = "PRE_OPEN"
    POST_CLOSE = "POST_CLOSE"
    HOLIDAY = "HOLIDAY"
    UNKNOWN = "UNKNOWN"


class Regime(StrEnum):
    """build.md §9.3 stage C. UNKNOWN exists so the supervisor can veto on it."""

    TREND = "TREND"
    RANGE = "RANGE"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    UNKNOWN = "UNKNOWN"


class RiskVerdict(StrEnum):
    """build.md §6.3."""

    PASS = "PASS"
    BLOCK = "BLOCK"
    HALT = "HALT"


class SupervisorVerdict(StrEnum):
    """build.md §10.1. The supervisor may not rewrite an intent, only judge it."""

    APPROVE = "APPROVE"
    VETO = "VETO"
    HALT = "HALT"


class ReasonCode(StrEnum):
    """Machine-readable reasons for a BLOCK, HALT or VETO.

    Seeded from build.md §6.3 and extended with the blocking conditions listed
    in §8.1. Reason codes are persisted and drive alerting, so they must stay
    machine-stable.
    """

    # §6.3 — named explicitly in the specification.
    STALE_MARKET_DATA = "STALE_MARKET_DATA"
    SPREAD_TOO_WIDE = "SPREAD_TOO_WIDE"
    DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
    OPEN_RISK_LIMIT = "OPEN_RISK_LIMIT"
    RISK_PER_TRADE_LIMIT = "RISK_PER_TRADE_LIMIT"
    INVALID_STOP = "INVALID_STOP"
    SYMBOL_NOT_ALLOWED = "SYMBOL_NOT_ALLOWED"
    MARKET_DISABLED = "MARKET_DISABLED"
    RECONCILIATION_MISMATCH = "RECONCILIATION_MISMATCH"
    SUPERVISOR_VETO = "SUPERVISOR_VETO"

    # §8.1 — remaining pre-trade blocking conditions.
    ACCOUNT_NOT_CONNECTED = "ACCOUNT_NOT_CONNECTED"
    WRONG_ACCOUNT = "WRONG_ACCOUNT"
    LIVE_ACCOUNT_IN_PAPER_MODE = "LIVE_ACCOUNT_IN_PAPER_MODE"
    EXPERT_TRADING_DISABLED = "EXPERT_TRADING_DISABLED"
    INVALID_QUOTE = "INVALID_QUOTE"
    VOLUME_OUT_OF_RANGE = "VOLUME_OUT_OF_RANGE"
    VOLUME_STEP_INVALID = "VOLUME_STEP_INVALID"
    STOP_DISTANCE_VIOLATION = "STOP_DISTANCE_VIOLATION"
    MAX_OPEN_POSITIONS = "MAX_OPEN_POSITIONS"
    SYMBOL_EXPOSURE_EXISTS = "SYMBOL_EXPOSURE_EXISTS"
    """O-004: this instrument already has an exposure, and v1 permits one.

    Distinct from MAX_OPEN_POSITIONS on purpose. That is a portfolio budget and
    is configurable; this is an owner-approved business rule about a single
    instrument, and the two would be indistinguishable in an incident report if
    they shared a code."""
    MAX_DRAWDOWN = "MAX_DRAWDOWN"
    SESSION_BLACKOUT = "SESSION_BLACKOUT"
    OVERNIGHT_EXPOSURE = "OVERNIGHT_EXPOSURE"
    """O-003: exposure survived the flatten deadline.

    A halt rather than a warning. Review 1.6 §4 is explicit that failing to
    prove flatness at the boundary must not silently become permission to hold
    overnight, and the only refusal strong enough to prevent that is one that
    stops the system until a person looks."""
    INTENT_EXPIRED = "INTENT_EXPIRED"
    DUPLICATE_INTENT = "DUPLICATE_INTENT"
    ORDER_FREQUENCY_LIMIT = "ORDER_FREQUENCY_LIMIT"
    SYSTEM_HALTED = "SYSTEM_HALTED"

    # F-002 — safety-critical state that could not be established.
    RECONCILIATION_UNKNOWN = "RECONCILIATION_UNKNOWN"
    INCIDENT_STATE_UNKNOWN = "INCIDENT_STATE_UNKNOWN"
    SAFETY_STATE_UNKNOWN = "SAFETY_STATE_UNKNOWN"
    DECISION_STATE_UNKNOWN = "DECISION_STATE_UNKNOWN"
    """Review 1.19 §5 (F-054): the durable decision-window/duplicate-

    protection record exists but could not be read, or failed its own
    schema check. Distinct from a genuinely first-ever start (nothing
    recorded, which is not an error) — collapsing the two would let a
    corrupted idempotence record look identical to a legitimate fresh
    start, which is exactly the failure class F-054 exists to prevent."""

    # §10.1 — supervisor pre-trade envelope checks.
    UNKNOWN_REGIME = "UNKNOWN_REGIME"
    CONFIDENCE_OUT_OF_RANGE = "CONFIDENCE_OUT_OF_RANGE"
    STRATEGY_NOT_ENABLED = "STRATEGY_NOT_ENABLED"
    MODEL_VERSION_NOT_ALLOWED = "MODEL_VERSION_NOT_ALLOWED"
    SIGNAL_FREQUENCY_ANOMALY = "SIGNAL_FREQUENCY_ANOMALY"
    CRITICAL_DRIFT = "CRITICAL_DRIFT"
    ACTIVE_INCIDENT = "ACTIVE_INCIDENT"
    EXCESSIVE_SLIPPAGE = "EXCESSIVE_SLIPPAGE"
    REPEATED_ORDER_REJECTION = "REPEATED_ORDER_REJECTION"
    MT5_CONNECTION_FAILURE = "MT5_CONNECTION_FAILURE"
    CORRUPTED_VERSION_REFERENCE = "CORRUPTED_VERSION_REFERENCE"

    # Phase 4 — ADR-001 execution-time (FINAL) risk revalidation.
    EXECUTION_TIME_RISK_BLOCK = "EXECUTION_TIME_RISK_BLOCK"
    """Appended alongside the specific reason(s) whenever FINAL Risk refuses
    an already intent-time-approved order (`risk/policies.py::
    revalidate_fixed_volume_at_execution_time`). Never the only reason: it
    marks *when* the refusal happened, not *why* — an operator seeing
    `RISK_PER_TRADE_LIMIT` alone cannot tell an intent-time BLOCK from a
    final-gate one, and the two point at different questions (a strategy
    that over-asks, versus market conditions that moved between decision
    and execution)."""

    # Phase 4 — execution eligibility (`risk/execution_eligibility.py`).
    DECISION_PREDATES_EXECUTION_ACTIVATION = "DECISION_PREDATES_EXECUTION_ACTIVATION"
    """A sealed, intent-time-approved capsule was decided before the human-set
    execution-activation watermark existed. Review 1.21's plan review, point
    6: an old shadow-mode approval must never become retroactively
    executable just because a config flag is switched on later — this is
    the reason code that names exactly that refusal."""

    STRATEGY_VERSION_NOT_CURRENT = "STRATEGY_VERSION_NOT_CURRENT"
    """The capsule's `strategy_version`/`risk_config_version` no longer
    matches what is currently running. Executing a decision made under a
    superseded strategy or risk configuration would submit an order nobody
    running today actually approved."""

    LIVE_EXECUTION_NOT_PERMITTED = "LIVE_EXECUTION_NOT_PERMITTED"
    """`Environment.LIVE` reached the execution preflight gate. CLAUDE.md §4:
    `config/live.yaml` does not exist and must not without a recorded human
    promotion decision — this is the same rule enforced one layer further
    in, structurally, rather than trusted to never be reached."""

    RISK_POLICY_NOT_APPROVED = "RISK_POLICY_NOT_APPROVED"
    """`SubmissionGate` (F-049): the risk-config version in force has not

    been explicitly approved by the owner for real submission
    (`config.RiskConfig.approved_config_version`, modeled on F-055's
    instrument-spec pin). `None` — every shipped config today — reads as
    unapproved, never as "assume yes"."""

    EXECUTION_NOT_EXPLICITLY_ENABLED = "EXECUTION_NOT_EXPLICITLY_ENABLED"
    """`SubmissionGate` (F-049): `config.ExecutionConfig.submission_enabled`

    is `False` — the default, and the value in every shipped config."""

    ALGOTRADING_DISABLED = "ALGOTRADING_DISABLED"
    """`SubmissionGate` (F-049): the real terminal does not currently report

    AlgoTrading enabled. Distinct from `EXPERT_TRADING_DISABLED` (the
    account-level flag `evaluate()` already checks at intent time) — this
    is the terminal-level toggle APP-016 explicitly says must never be
    flipped just to make a check pass."""

    FEEDBACK_2_0_NOT_APPROVED = "FEEDBACK_2_0_NOT_APPROVED"
    """`SubmissionGate` (F-049): `config.ExecutionConfig.feedback_2_0_approved`

    is `False` — `feedback.2.0.md` has not given its GO yet."""

    # Operator action.
    MANUAL_HALT = "MANUAL_HALT"


class OrderState(StrEnum):
    """build.md §19. Illegal transitions must fail closed."""

    CREATED = "CREATED"
    RISK_APPROVED = "RISK_APPROVED"
    SUPERVISOR_APPROVED = "SUPERVISOR_APPROVED"
    ORDER_CHECKED = "ORDER_CHECKED"
    SUBMITTED = "SUBMITTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    RECONCILED = "RECONCILED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"


class KillSwitchState(StrEnum):
    """build.md §8.2. The three manual controls stay separately addressable."""

    RUNNING = "RUNNING"
    HALTED = "HALTED"
    UNKNOWN = "UNKNOWN"
    """Prior state could not be established — treated as halted until resolved."""


class ReconciliationStatus(StrEnum):
    """Whether local and broker position state are known to agree.

    `UNKNOWN` is the important member. Reconciliation that has not run yet, or
    whose result could not be read, is not the same as reconciliation that
    passed — and representing it as a boolean forces the two together, with the
    safe-looking value winning by default.

    Review finding F-002: *absence of evidence is not evidence of safety.*
    """

    MATCHED = "MATCHED"
    MISMATCHED = "MISMATCHED"
    UNKNOWN = "UNKNOWN"


class IncidentStatus(StrEnum):
    """Whether the incident register is known to be clear."""

    CLEAR = "CLEAR"
    ACTIVE = "ACTIVE"
    UNKNOWN = "UNKNOWN"


class IncidentSeverity(StrEnum):
    """status.md §9. SEV-0 or an unresolved SEV-1 blocks promotion."""

    SEV_0 = "SEV-0"
    SEV_1 = "SEV-1"
    SEV_2 = "SEV-2"
    SEV_3 = "SEV-3"


class ExecutionEventType(StrEnum):
    """Phase 4 — the append-only log of what happened to one execution

    request (`persistence/execution.py::ExecutionEventStore`). `OrderState`
    above names the full build.md §19 state machine; this is the narrower
    event vocabulary for *how a request got there, or didn't*. The reserved
    values below are never emitted by anything this phase builds — they
    exist so the M5 event log has a named shape to grow into rather than
    being invented from scratch then.
    """

    REQUEST_CLAIMED = "REQUEST_CLAIMED"
    INELIGIBLE = "INELIGIBLE"
    GATE_CLOSED = "GATE_CLOSED"
    RECONCILIATION_BLOCKED = "RECONCILIATION_BLOCKED"
    FINAL_RISK_PASSED = "FINAL_RISK_PASSED"
    """Review 1.22 F-057: the durable link ADR-001 requires. Carries the
    complete serialized FINAL `RiskDecision` (and, once `ApprovedOrder` is
    built from it, an `order_fingerprint` binding the two) in its payload —
    the sealed `DecisionCapsule` is never mutated to hold this instead."""
    FINAL_RISK_BLOCKED = "FINAL_RISK_BLOCKED"
    ORDER_CHECKED = "ORDER_CHECKED"
    ORDER_CHECK_REJECTED = "ORDER_CHECK_REJECTED"
    SUBMISSION_GATE_PASSED = "SUBMISSION_GATE_PASSED"
    """F-049 (`risk/submission_gate.py::evaluate_submission_gate`), evaluated

    immediately after a successful `order_check` — whether real submission
    would currently be authorized. Read-only and durable, same as
    `FINAL_RISK_PASSED`: recording that the gate opened is not itself an
    attempt to submit anything. When it opens, `SUBMISSION_STARTED` (below)
    follows as the platform's durable commitment to attempt one broker
    submission — `order_send` itself is still not called; see that event's
    own docstring."""
    SUBMISSION_GATE_BLOCKED = "SUBMISSION_GATE_BLOCKED"
    """Carries the gate's `reason_codes` and the complete serialized

    `SubmissionGateContext` in its payload — every shipped config today
    closes at least three of the nine legs, so this is the expected,
    honest outcome until an owner explicitly approves submission."""
    SUBMISSION_STARTED = "SUBMISSION_STARTED"
    """Core critical path item 3 (review 1.26 §6 / review 1.27 §8): the

    durable pre-side-effect commitment point — ADR-003 §6's "write to the
    journal before acting, acknowledge after" applied to the one action
    this platform has never yet taken. Appended once, immediately after
    `SUBMISSION_GATE_PASSED`, carrying the complete serialized
    `ApprovedOrder` that was committed to. Reserved for exactly this
    purpose since review 1.15 §14 first named it; ADR-005 already makes it
    a cross-track contract — Dev 2's `agent_gateway/contracts.py
    ::ProposalWithdrawal` treats this event as the withdrawal-cutoff
    boundary (honoured strictly before it, refused at or after).

    **Emitting this event is not calling `order_send`.**
    `OrderCheckMt5Gateway.order_send` stays unconditionally disabled
    regardless of this event's existence — wiring the caller
    (this event) and wiring `order_send`'s real capability are separate,
    later items (submission idempotence, ambiguous-outcome recovery), by
    explicit reviewer instruction. No shipped config can reach
    `SUBMISSION_GATE_PASSED` today, so this stays unreachable in every
    real deployment exactly as that event already is."""
    AMBIGUOUS_OUTCOME_RESOLVED = "AMBIGUOUS_OUTCOME_RESOLVED"
    """Core critical path item 6 (review 1.20 §10 / review 1.21 §12):

    "query durable request state -> reconcile broker state -> determine
    whether the request already took effect." Appended when a claimed
    request's last durable event is `SUBMISSION_STARTED` with nothing
    after it — the one state a process crash between that commitment and
    a real broker response could leave behind. Payload carries the
    complete determination: the `magic_number` searched for
    (`domain/hashing.py::mt5_magic_number`, item 5) and whether a
    matching broker position was found.

    **Not `RECONCILED`, deliberately.** `RECONCILED` (below) stays
    reserved for its original M5 purpose — post-fill reconciliation
    (core critical path item 8), confirming a *known* fill against
    expected state. This event answers a different question: whether an
    *unclear* submission happened at all. Reusing `RECONCILED` here
    would collide with item 8's own later need for it.

    **Never a decision to resubmit.** ADR-003 §6: "an ambiguous outcome
    resolves to reconcile, never to retry the action." This event
    records a determination; `order_send` is not called by it, and
    nothing here decides to attempt one — that stays exactly as
    unreachable as it has always been."""

    # Reserved for M5. Never emitted by anything Phase 4 builds.
    SUBMITTED = "SUBMITTED"
    BROKER_ACK = "BROKER_ACK"
    FILLED = "FILLED"
    RECONCILED = "RECONCILED"
    CLOSED = "CLOSED"


class SnapshotCompleteness(StrEnum):
    """Whether a broker-state collection query actually completed (review

    1.15 F-047, §5 "Complete-set semantics"). MT5's own `positions_get()` and
    `orders_get()` return `None` both when the book is genuinely empty and
    when the call failed — the gateway already resolves that ambiguity
    before this type is ever assigned (an empty tuple only ever means the
    former; a failure raises). This type exists so that resolution survives
    into persistence instead of being flattened back into a bare row count,
    where "0 positions" and "positions unknown" would look identical again.
    """

    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
