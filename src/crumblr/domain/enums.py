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
    """The operational circuit-breaker ceiling (`RiskConfig.max_open_positions`)

    has been hit. Not the owner's portfolio budget — that is
    `OPEN_RISK_LIMIT`, measured against real open risk. Owner risk policy
    v1 (D1.4) reclassified this field; see its docstring in `config.py`."""
    SYMBOL_EXPOSURE_EXISTS = "SYMBOL_EXPOSURE_EXISTS"
    """Retired 2026-09-02: O-004 (one exposure per symbol) was withdrawn by

    `OWNER_POLICY_V1.md` §2 — multiple positions are now permitted, subject
    to the real portfolio open-risk budget (`OPEN_RISK_LIMIT`). Kept only
    because `ReasonCode` values are reconstructed from persisted rows
    (`ReasonCode(code)` in `persistence/execution.py`,
    `persistence/flatten.py`, `persistence/safety_state.py`) — deleting the
    member would make any historical row carrying it undecodable. No code
    path emits this any more."""
    MAX_DRAWDOWN = "MAX_DRAWDOWN"
    SESSION_BLACKOUT = "SESSION_BLACKOUT"
    """`risk/trading_window.py::permits_new_entry` refused a new entry —

    either the market is genuinely closed (the weekend gap), or, on the
    Friday trading day only, `moment` is inside the last-entry cutoff
    before the weekly close (owner risk policy v1, D1.5). Monday through
    Thursday this can only mean the market is closed, since the weekly
    close is always too far away to trigger it. A BLOCK, not a HALT — a
    session boundary refuses this one entry; it says nothing about the
    system's own safety."""
    OVERNIGHT_EXPOSURE = "OVERNIGHT_EXPOSURE"
    """Exposure survived the weekly close, or the flatten deadline leading

    up to it (owner risk policy v1, D1.5, `review/adr/ADR-012-owner
    -session-policy-v1.md`; originally O-003, which forbade *any*
    overnight hold — D1.5 narrows this to a weekend-close breach only,
    since weekday overnight is now explicitly permitted).

    A halt rather than a warning. Review 1.6 §4's original requirement
    carries forward unchanged under the weekly policy: failing to prove
    flatness at the boundary must not silently become permission to
    hold, and the only refusal strong enough to prevent that is one that
    stops the system until a person looks.

    The name is kept even though "overnight" now overstates what this
    detects (a normal weekday hold no longer triggers it) —
    `ReasonCode` values are reconstructed from persisted rows
    (`ReasonCode(code)` in `persistence/execution.py`,
    `persistence/flatten.py`, `persistence/safety_state.py`), the same
    constraint that forced `SYMBOL_EXPOSURE_EXISTS` above to be
    retained rather than renamed."""
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

    OPEN_RISK_UNKNOWN = "OPEN_RISK_UNKNOWN"
    """Owner risk policy v1 (D1.4, `review/adr/ADR-011-owner-risk-policy

    -v1.md`): `risk/portfolio_risk.py::assess_open_risk` could not
    establish the book's total open risk — at least one open position
    has no trustworthy protective-stop geometry (no recorded stop, or no
    known instrument spec). Never counted as zero risk: a position this
    platform cannot value is not evidence the book is safe, the same
    F-002 discipline every other member in this group applies.

    **BLOCK, not HALT — deliberately.** The refusal's whole job (stop
    *new* risk stacking on top of unquantifiable risk) is fully achieved
    by a BLOCK. The platform cannot currently close the offending
    position either way (`close_all_positions` stays unbuilt, D-050),
    so a HALT would be a permanent brick with no in-system remediation
    path, and core critical path item 9 (broker-side SL/protection
    verification) is the correctly-scoped future owner of that
    system-level judgement — this code names the gap, item 9 escalates
    it."""

    FLATTEN_STATE_UNKNOWN = "FLATTEN_STATE_UNKNOWN"
    """Owner risk policy v1 (D1.5, `review/adr/ADR-012-owner-session

    -policy-v1.md`): the position book could not be confirmed flat by
    the mandatory Friday flatten deadline — the broker's position read
    was incomplete at or past that point, so an empty result cannot be
    trusted as genuinely flat. Never treated as "no positions, nothing
    to do": the work order is explicit that an unconfirmed flat state
    must HALT and surface the incident rather than assume success, and
    under the weekly policy the cost of getting this wrong is a full
    unmonitored weekend, not a day that self-corrects tomorrow. Same
    F-002 "absence of evidence is not evidence of safety" discipline as
    `OPEN_RISK_UNKNOWN` above, and tolerated in `flatten_gate.py
    ::_TOLERATED_HALT_REASONS` alongside `OVERNIGHT_EXPOSURE` — the
    flatten machinery must still be able to attempt a commitment despite
    this specific halt, or it could never recover once tripped."""

    PROTECTIVE_STOP_MISSING = "PROTECTIVE_STOP_MISSING"
    """Core critical path item 9 (`review/feedback.1.26.md`: *"Verify

    broker-side SL after a fill; absence/mismatch fails closed and
    escalates."*): a position this platform attributes to itself has no
    broker-reported protective stop (`PositionState.stop_loss_price is
    None`), or the platform's own durably-recorded intended stop for the
    owning request could not be established at all. `ApprovedOrder
    .stop_loss_price` is a required field — every order this platform
    ever approves carries a real intended stop — so either condition is
    a fail-closed signal, never treated as "no stop configured, nothing
    to protect." This is the escalation `OPEN_RISK_UNKNOWN` above names
    as its own correctly-scoped future owner: a position whose
    protection cannot be trusted is exactly the case `assess_open_risk`
    could not value either.

    Deliberately a *separate*, narrowly-scoped halt producer from the
    book-level `RECONCILIATION_MISMATCH`/`RECONCILIATION_UNKNOWN`
    verdict above — `review/DEVIATIONS.md` D-051 gap 3 records that
    whether a *generic* reconciliation mismatch should itself halt
    remains a deliberately deferred, separate policy question. This
    reason fulfils `build.md` §8.2's "reconciliation mismatch" HALT
    trigger for this one specific, well-defined case only; the generic
    case stays exactly as deferred as D-051 left it."""

    PROTECTIVE_STOP_MISMATCH = "PROTECTIVE_STOP_MISMATCH"
    """Sibling to `PROTECTIVE_STOP_MISSING` above: the broker-reported

    protective stop on a position this platform attributes to itself
    does not match the stop the platform's own durable
    `SUBMISSION_STARTED` record says it intended. Same escalation, same
    D-051 gap 3 scope boundary, same rationale — the stop that is
    actually protecting the position is not the one this platform
    believes it placed."""

    SUBMISSION_INTEGRITY_AMBIGUOUS = "SUBMISSION_INTEGRITY_AMBIGUOUS"
    """Phase B item B4 (`review/OWNER_WORK_ORDERS_DEMO_CANARY_2026-09-03.md`):

    ambiguous-outcome recovery (`application/execution.py
    ::_recover_ambiguous_submission`) found *more than one* broker
    position sharing this request's computed magic number. A single
    MARKET order this platform ever submits can produce at most one
    resulting position — no retry logic exists that could legitimately
    produce two — so two or more matches signals a magic-number
    collision or corrupted broker/platform state, never a normal
    outcome. Never silently attributed to the request (which of the N
    positions would even be "the" one this platform is responsible
    for?): recovery deliberately does not set `submitted` at all for
    this case, so `expected_state.py::derive_expected_exposure` leaves
    the request undetermined rather than guessing.

    Same "provably inert today, real code for when real submission
    lands" shape as `PROTECTIVE_STOP_MISSING`/`PROTECTIVE_STOP_MISMATCH`
    above (item 9): a real MT5 position bearing this platform's magic
    number cannot exist while `order_send` stays an unconditional raise,
    so this branch cannot fire in any shipped configuration today.
    Tolerated in `flatten_gate.py::_TOLERATED_HALT_REASONS` alongside
    `PROTECTIVE_STOP_MISSING`/`PROTECTIVE_STOP_MISMATCH` — flattening
    closes broker-observed positions regardless of per-request
    attribution, so becoming flat is still the safe resolution of this
    halt too."""

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

    # Core critical path item 7 — `risk/flatten_gate.py`.
    POSITION_BOOK_INCOMPLETE = "POSITION_BOOK_INCOMPLETE"
    """`FlattenGate`: the position snapshot this pass observed is not

    `SnapshotCompleteness.COMPLETE`. Distinct from `RECONCILIATION_UNKNOWN`
    below — a book can be internally complete and freshly read while
    reconciliation itself is unknown for an unrelated reason (a missing
    account snapshot, say). Either alone is sufficient reason not to close
    a position book this platform cannot currently see in full."""

    FLATTEN_NOT_REQUIRED = "FLATTEN_NOT_REQUIRED"
    """`FlattenGate`: neither `trading_window.requires_flat` (unchanged)

    nor `has_crossed_weekly_close` (owner risk policy v1, D1.5 —
    replaces the old daily `has_crossed_rollover`) currently holds.
    Names the precondition the gate exists to check, so a context built
    with a stale or wrong clock fails with a legible reason rather than
    silently doing nothing."""

    FLATTEN_SUBMISSION_NOT_ENABLED = "FLATTEN_SUBMISSION_NOT_ENABLED"
    """`FlattenGate`: `config.ExecutionConfig.flatten_submission_enabled`

    is `False` — the default, and the value in every shipped config.
    Deliberately a fourth, separate flag from `submission_enabled`
    (ADR-004 §5.1, ADR-009 §2): enabling order submission must not
    silently also enable automatic liquidation."""

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

    RECONCILED = "RECONCILED"
    """Core critical path item 8 (review 1.16 §7-8, review 1.26 §6 item

    8): "derive post-fill expected state from durable platform execution
    history and reconcile it against broker truth." Appended at most
    once per request, ever (`event_id_for` derives from
    `(order_request_id, event_type)` alone — a second append with
    different content raises `ExecutionEventConflictError`) — a
    once-per-request terminal determination, not a per-pass heartbeat,
    matching this member's own position in `OrderState`'s build.md §19
    machine (after `FILLED`, before `CLOSED`). Payload carries the
    determination only (`expected_position_tickets`,
    `observed_open_tickets`, `closed_tickets`, `book_status`) — no
    timestamp, no snapshot id, mirroring `AMBIGUOUS_OUTCOME_RESOLVED`'s
    own precedent exactly, so a concurrent double-check converges rather
    than raising a false conflict.

    **The only one of the five reserved-for-M5 members whose literal
    claim this platform can honestly make today.** `SUBMITTED`/
    `BROKER_ACK`/`FILLED`/`CLOSED` each assert a broker fact — a real
    submission, acknowledgement, fill, or lifecycle end — that no code
    path here can produce; emitting any of them would be evidence
    fabrication in the one table whose purpose is auditable provenance
    (the same objection ADR-009 §2.1 raised against fabricating a
    placeholder capsule). `RECONCILED`'s claim — "the platform compared
    its durable expectation against observed broker truth, and here is
    the verdict" — is true of an action the platform actually performs,
    including truthfully about a still-flat expectation. See
    `review/adr/ADR-010-post-fill-reconciliation.md` §2.2.

    **Provably always the `flat()`-equivalent verdict today.** No
    committed request can ever have resulted in a real position, since
    `order_send` stays unreachable — see
    `application/execution.py::ExecutionOrchestrator.reconcile_once()`.
    """

    # Reserved for M5. `SUBMITTED`/`BROKER_ACK`/`FILLED`/`CLOSED` each
    # assert a broker fact (a real submission, acknowledgement, fill, or
    # lifecycle end) that no code path in this platform can produce —
    # never emitted by anything Phase 4 or the core critical path builds.
    SUBMITTED = "SUBMITTED"
    BROKER_ACK = "BROKER_ACK"
    FILLED = "FILLED"
    CLOSED = "CLOSED"


class FlattenEventType(StrEnum):
    """Core critical path item 7 (ADR-009) — the append-only log of what

    happened to one flatten occurrence
    (`persistence/flatten.py::FlattenEventStore`). A **separate** vocabulary
    from `ExecutionEventType` above, on purpose: a flatten has no
    `DecisionCapsule` and no `TradeIntent` (it is policy-driven, not
    proposal-driven — see `persistence/flatten.py`'s module docstring for
    why it lives in its own table pair), so sharing one event enum across
    both identity spaces would let a flatten event carry an order-only
    name, or vice versa, silently.
    """

    FLATTEN_REQUEST_CLAIMED = "FLATTEN_REQUEST_CLAIMED"
    """This pass's claim on today's flatten occurrence for this symbol won

    — the `persistence/flatten.py::FlattenRequestStore.claim()` analogue
    of `REQUEST_CLAIMED` above."""

    FLATTEN_GATE_BLOCKED = "FLATTEN_GATE_BLOCKED"
    """`risk/flatten_gate.py::evaluate_flatten_gate()` closed. Carries the

    gate's `reason_codes` and the complete serialized `FlattenGateContext`
    in its payload — the same shape as `SUBMISSION_GATE_BLOCKED`, and, for
    the same reason, the expected, honest outcome against every shipped
    config today."""

    FLATTEN_GATE_PASSED = "FLATTEN_GATE_PASSED"
    """The gate opened. `FLATTEN_SUBMISSION_STARTED` follows immediately —

    recording that the gate opened is not itself an attempt to close
    anything, mirroring `SUBMISSION_GATE_PASSED`."""

    FLATTEN_SUBMISSION_STARTED = "FLATTEN_SUBMISSION_STARTED"
    """The durable pre-side-effect commitment point for a flatten —

    ADR-003 §6 applied to the one close this platform has never yet made.
    Appended once, carrying the complete serialized `FlattenPlan` (every
    target ticket, side, and the broker-reported volume to be closed) that
    was committed to.

    **Not `CLOSED`** (reserved for M5 below): `CLOSED` answers "this
    position's lifecycle ended", which is post-fill closure and item 8's
    territory (ADR-008 §2 already set this precedent for `RECONCILED`;
    the same reasoning applies here). **Not `RECONCILED`** — item 8's,
    for the same reason. **Not `SUBMISSION_STARTED`** — two concrete
    reasons, not merely a naming preference: (1) `agent_gateway
    ::ProposalWithdrawal` treats `SUBMISSION_STARTED` as the withdrawal-
    cutoff boundary for an agent-proposed order; a policy-driven flatten
    is not a proposal and must never be agent-withdrawable. (2)
    `ExecutionEventStore.count_events_since(SUBMISSION_STARTED, ...)` is
    FINAL Risk's durable order-frequency budget; a flatten is not a new
    order and must not consume it.

    **Emitting this event is not calling `close_all_positions` or
    `order_send`.** Both stay unconditionally disabled regardless of this
    event's existence — see `mt5_gateway/execution.py
    ::OrderCheckMt5Gateway`."""

    FLATTEN_OUTCOME_RESOLVED = "FLATTEN_OUTCOME_RESOLVED"
    """The item-6-shaped idempotent recovery for a flatten: appended when

    a claimed flatten occurrence's last durable event is
    `FLATTEN_SUBMISSION_STARTED` with nothing after it. Unlike
    `AMBIGUOUS_OUTCOME_RESOLVED` (which searches broker positions by
    `mt5_magic_number`), this reads the target tickets recorded in the
    commitment event's own payload and checks which are still open — a
    simpler, more direct determination, since the targets were already
    named. Because `close_all_positions` stays unreachable, this will
    provably always conclude every target is still open today — the same
    honest inertness ADR-008 documents for its own positive branch.
    Idempotent by construction: once appended, `events[-1]` is no longer
    `FLATTEN_SUBMISSION_STARTED`, so recovery never re-runs."""


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
