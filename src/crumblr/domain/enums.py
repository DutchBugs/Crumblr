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
