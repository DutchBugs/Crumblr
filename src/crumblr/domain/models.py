"""Typed boundaries between the platform, the Trading Agent and the Supervisor.

Every model here is frozen and forbids unknown fields. A contract that quietly
accepts an extra key is a contract that lets a renamed field pass as a default,
and defaults are exactly what build.md §17 forbids for anything risk-bearing.

Collections are tuples rather than lists so an "immutable" decision capsule is
actually immutable all the way down.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from crumblr.domain.enums import (
    BarOrigin,
    DataQuality,
    EntryType,
    Environment,
    IncidentSeverity,
    OrderState,
    ReasonCode,
    Regime,
    RiskVerdict,
    SessionState,
    Side,
    SnapshotCompleteness,
    StreamAnomaly,
    SupervisorVerdict,
)
from crumblr.domain.hashing import fingerprint, mt5_magic_number
from crumblr.domain.money import ZERO, ExactDecimal, Price, RiskFraction, Volume
from crumblr.domain.timeutils import UtcDatetime

DIRECTIONAL_SIDES: frozenset[Side] = frozenset({Side.BUY, Side.SELL})

Symbol = Annotated[str, Field(min_length=1, max_length=64)]
VersionTag = Annotated[str, Field(min_length=1, max_length=128)]


class Contract(BaseModel):
    """Base for every persisted contract: immutable and closed to unknown fields."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
        validate_default=True,
    )

    @model_validator(mode="before")
    @classmethod
    def _discard_computed_fields(cls, data: Any) -> Any:
        """Let a serialised contract be loaded back.

        Computed fields are written out by `model_dump` but are derived, not
        input, so `extra="forbid"` would reject them on the way back in. They
        are dropped here and recomputed from the stored fields — which is also
        what makes them useful as integrity checks: the repository layer
        compares the digest it stored against the one recomputed on load, and a
        mismatch means the row was altered underneath us.
        """
        if isinstance(data, dict):
            computed = cls.model_computed_fields
            if computed and not data.keys().isdisjoint(computed):
                return {key: value for key, value in data.items() if key not in computed}
        return data


# --------------------------------------------------------------------------- #
# Market data
# --------------------------------------------------------------------------- #


class Bar(Contract):
    """One OHLC bar. Invariants that make the bar meaningless are rejected here."""

    open_time_utc: UtcDatetime
    open: Price
    high: Price
    low: Price
    close: Price
    tick_volume: int = Field(ge=0)
    real_volume: int | None = Field(default=None, ge=0)
    spread_points: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _check_ohlc(self) -> Self:
        if self.high < self.low:
            raise ValueError(f"high {self.high} is below low {self.low}")
        if self.high < max(self.open, self.close):
            raise ValueError("high is below the open/close range")
        if self.low > min(self.open, self.close):
            raise ValueError("low is above the open/close range")
        return self


class InstrumentSpec(Contract):
    """Broker symbol specification, per build.md §7.

    The canonical symbol is ours; the broker symbol is whatever the venue calls
    it. Nothing downstream may hard-code "EURUSD" — brokers use suffixes.
    """

    canonical_symbol: Symbol
    broker_symbol: Symbol
    currency_base: Annotated[str, Field(min_length=3, max_length=8)]
    currency_profit: Annotated[str, Field(min_length=3, max_length=8)]

    contract_size: ExactDecimal = Field(gt=ZERO)
    digits: int = Field(ge=0, le=12)
    point: ExactDecimal = Field(gt=ZERO)
    tick_size: ExactDecimal = Field(gt=ZERO)
    tick_value: ExactDecimal = Field(gt=ZERO)

    volume_min: Volume
    volume_max: Volume
    volume_step: Volume

    stops_level: int = Field(ge=0)
    freeze_level: int = Field(ge=0)
    filling_modes: tuple[str, ...]
    trade_mode: str

    captured_at_utc: UtcDatetime

    @model_validator(mode="after")
    def _check_volume_bounds(self) -> Self:
        if self.volume_min > self.volume_max:
            raise ValueError(f"volume_min {self.volume_min} exceeds volume_max {self.volume_max}")
        if self.volume_step > self.volume_max:
            raise ValueError("volume_step exceeds volume_max")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def spec_version(self) -> str:
        """Content hash of the specification's *semantic* (broker-policy) fields.

        build.md §7 requires detecting when a broker changes a symbol's specs.
        Comparing this hash against the stored one turns that into an equality
        check instead of a field-by-field diff.

        `captured_at_utc` and `tick_value` are deliberately excluded (review
        F-039). `captured_at_utc` is observation metadata, not a spec field.
        `tick_value` is excluded because MT5 recomputes it live from the
        current cross-currency rate whenever the account currency differs
        from the quote currency (confirmed 2026-08-24 first contact,
        `status.md`: a EUR account against EURUSD produced a non-round
        `tick_value` for exactly this reason) — it drifts tick-to-tick with
        the market, not with broker policy, so hashing it produced a false
        `spec_changed` on every reconnect. `tick_value` is still recorded on
        every `InstrumentSpec`; it just is not part of what "changed" means.
        """
        return fingerprint(
            {
                "broker_symbol": self.broker_symbol,
                "currency_base": self.currency_base,
                "currency_profit": self.currency_profit,
                "contract_size": self.contract_size,
                "digits": self.digits,
                "point": self.point,
                "tick_size": self.tick_size,
                "volume_min": self.volume_min,
                "volume_max": self.volume_max,
                "volume_step": self.volume_step,
                "stops_level": self.stops_level,
                "freeze_level": self.freeze_level,
                "filling_modes": sorted(self.filling_modes),
                "trade_mode": self.trade_mode,
            }
        )


class MarketTick(Contract):
    """One quote exactly as a source delivered it (build.md §12.1, review F-022).

    Raw, and deliberately not normalised: a crossed or stale quote is recorded
    as it arrived and flagged, because the evidence that the feed misbehaved is
    the thing worth keeping. `MarketSnapshot` is the derived view a strategy
    sees; this is what the derivation started from.

    `source` names where it came from — the generator, or a specific broker and
    server — so that two runs against different feeds can never be silently
    pooled.
    """

    tick_id: UUID
    source: VersionTag
    canonical_symbol: Symbol
    broker_symbol: Symbol

    event_time_utc: UtcDatetime
    """When the market says it happened."""

    received_time_utc: UtcDatetime
    """When this platform saw it. Never used for ordering (ADR-003 invariant 4)."""

    bid: Price
    ask: Price
    last: Price | None = None
    volume: int | None = Field(default=None, ge=0)
    flags: int | None = Field(default=None, ge=0)
    """The broker's own tick flags, kept opaque. MT5 packs several facts in here."""

    data_quality: DataQuality = DataQuality.GOOD
    anomalies: tuple[StreamAnomaly, ...] = ()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def spread(self) -> Decimal:
        return self.ask - self.bid

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ingest_latency_ms(self) -> int:
        return int((self.received_time_utc - self.event_time_utc).total_seconds() * 1000)


class MarketBar(Contract):
    """A stored bar together with the account of where it came from.

    `Bar` is the shape; this is the record. Two fields carry the weight:
    `origin` says whether a broker produced it or this platform did, and
    `pipeline_version` names the transformation when the answer is the latter.
    Without them a derived bar is indistinguishable from a delivered one, and
    a change to the aggregation rules silently rewrites history.
    """

    bar_id: UUID
    source: VersionTag
    canonical_symbol: Symbol
    broker_symbol: Symbol
    timeframe: Annotated[str, Field(min_length=1, max_length=16)]

    bar: Bar
    origin: BarOrigin
    pipeline_version: VersionTag | None = None
    tick_count: int | None = Field(default=None, ge=0)
    """How many ticks went into it. `0` is a gap that was filled, not a bar."""

    received_time_utc: UtcDatetime
    data_quality: DataQuality = DataQuality.GOOD
    anomalies: tuple[StreamAnomaly, ...] = ()

    @model_validator(mode="after")
    def _check_derived_bars_name_their_pipeline(self) -> Self:
        """A bar this platform built must say what built it.

        The alternative is a stored bar whose derivation cannot be reproduced,
        which is the same as a bar nobody can check.
        """
        if self.origin is BarOrigin.AGGREGATED_FROM_TICKS and not self.pipeline_version:
            raise ValueError("an aggregated bar must name the pipeline version that produced it")
        if self.origin is not BarOrigin.AGGREGATED_FROM_TICKS and self.tick_count is not None:
            raise ValueError(
                f"tick_count is meaningless for a {self.origin.value} bar; "
                "it describes an aggregation this bar did not go through"
            )
        return self


class MarketSnapshot(Contract):
    """Normalised market state handed to the Trading Agent (build.md §6.1).

    This is derived data. Raw ticks are persisted separately and never
    overwritten (§12.1), so a corrupt quote is still recorded as evidence even
    though it is refused here: normalisation flags it SUSPECT and emits no
    snapshot rather than passing a crossed book to a strategy.
    """

    snapshot_id: UUID
    symbol: Symbol
    event_time_utc: UtcDatetime
    received_time_utc: UtcDatetime

    bid: Price
    ask: Price
    spread_points: int = Field(ge=0)

    timeframe: Annotated[str, Field(min_length=1, max_length=16)]
    bars: tuple[Bar, ...]

    session_state: SessionState
    symbol_spec_version: VersionTag
    data_quality: DataQuality

    @model_validator(mode="after")
    def _check_quote(self) -> Self:
        if self.ask < self.bid:
            raise ValueError(f"crossed quote: ask {self.ask} below bid {self.bid}")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / Decimal(2)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ingest_latency_ms(self) -> int:
        """Receive time minus event time. Negative values indicate clock skew."""
        return int((self.received_time_utc - self.event_time_utc).total_seconds() * 1000)


# --------------------------------------------------------------------------- #
# Trading Agent output
# --------------------------------------------------------------------------- #


class TradeIntent(Contract):
    """The only object a Trading Agent may produce (build.md §6.2).

    Deliberately absent: lot size. The agent proposes risk as a fraction of
    equity; the risk engine turns that into a volume using account state and
    the broker's symbol specification. An agent that could name its own lot
    size would be an agent that could bypass the risk budget.

    `stop_loss_price` is mandatory for BUY and SELL. Position sizing is derived
    from stop distance, so a directional intent without a stop is not merely
    risky — it is unsizeable.
    """

    intent_id: UUID
    strategy_id: VersionTag
    strategy_version: VersionTag
    model_version: VersionTag | None = None

    symbol: Symbol
    side: Side
    created_at_utc: UtcDatetime
    expires_at_utc: UtcDatetime

    entry_type: EntryType
    reference_price: Price

    stop_loss_price: Price | None = None
    take_profit_price: Price | None = None

    confidence: float = Field(ge=0.0, le=1.0)
    reason_codes: tuple[str, ...]

    requested_risk_fraction: RiskFraction | None = None
    feature_snapshot_id: UUID

    @model_validator(mode="after")
    def _check_lifetime(self) -> Self:
        if self.expires_at_utc <= self.created_at_utc:
            raise ValueError("expires_at_utc must be after created_at_utc")
        return self

    @model_validator(mode="after")
    def _check_directional_requirements(self) -> Self:
        if self.side in DIRECTIONAL_SIDES:
            if self.stop_loss_price is None:
                raise ValueError(f"{self.side} intent requires a stop_loss_price")
            if self.requested_risk_fraction is None:
                raise ValueError(f"{self.side} intent requires a requested_risk_fraction")
            if not self.reason_codes:
                raise ValueError(f"{self.side} intent requires at least one reason code")
        else:
            if self.stop_loss_price is not None or self.take_profit_price is not None:
                raise ValueError("FLAT intent must not carry stop or target prices")
            if self.requested_risk_fraction is not None:
                raise ValueError("FLAT intent must not request a risk fraction")
        return self

    @model_validator(mode="after")
    def _check_stop_direction(self) -> Self:
        """A stop below entry on a SELL is an accelerator, not a brake."""
        if self.side is Side.BUY:
            if self.stop_loss_price is not None and self.stop_loss_price >= self.reference_price:
                raise ValueError("BUY stop_loss_price must be below reference_price")
            if (
                self.take_profit_price is not None
                and self.take_profit_price <= self.reference_price
            ):
                raise ValueError("BUY take_profit_price must be above reference_price")
        elif self.side is Side.SELL:
            if self.stop_loss_price is not None and self.stop_loss_price <= self.reference_price:
                raise ValueError("SELL stop_loss_price must be above reference_price")
            if (
                self.take_profit_price is not None
                and self.take_profit_price >= self.reference_price
            ):
                raise ValueError("SELL take_profit_price must be below reference_price")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def decision_hash(self) -> str:
        """Digest of the decision content, excluding `intent_id`.

        Two intents with the same digest are the same decision, whoever
        generated them. That property is what makes a replay verifiable: the
        same inputs must reproduce the same digest.
        """
        return fingerprint(
            {
                "strategy_id": self.strategy_id,
                "strategy_version": self.strategy_version,
                "model_version": self.model_version,
                "symbol": self.symbol,
                "side": self.side,
                "created_at_utc": self.created_at_utc,
                "expires_at_utc": self.expires_at_utc,
                "entry_type": self.entry_type,
                "reference_price": self.reference_price,
                "stop_loss_price": self.stop_loss_price,
                "take_profit_price": self.take_profit_price,
                "confidence": repr(self.confidence),
                "reason_codes": list(self.reason_codes),
                "requested_risk_fraction": self.requested_risk_fraction,
                "feature_snapshot_id": self.feature_snapshot_id,
            }
        )

    def is_expired(self, *, at: datetime) -> bool:
        return at >= self.expires_at_utc


# --------------------------------------------------------------------------- #
# Control plane decisions
# --------------------------------------------------------------------------- #


class RiskDecision(Contract):
    """Deterministic risk gateway verdict (build.md §6.3, §8).

    A PASS carries the approved volume; the risk engine, not the agent, is the
    only component that ever names one. A BLOCK or HALT carries reason codes
    and no volume — there is no partially approved order.
    """

    decision_id: UUID
    intent_id: UUID
    verdict: RiskVerdict
    reason_codes: tuple[ReasonCode, ...]
    decided_at_utc: UtcDatetime
    risk_config_version: VersionTag

    approved_volume: Volume | None = None
    account_equity: ExactDecimal | None = None
    stop_distance_points: int | None = Field(default=None, gt=0)
    risk_amount: ExactDecimal | None = None

    @model_validator(mode="after")
    def _check_verdict_consistency(self) -> Self:
        if self.verdict is RiskVerdict.PASS:
            if self.approved_volume is None:
                raise ValueError("a PASS risk decision must carry an approved_volume")
            if self.stop_distance_points is None:
                raise ValueError("a PASS risk decision must record the stop distance used")
        else:
            if self.approved_volume is not None:
                raise ValueError(f"a {self.verdict} risk decision must not carry a volume")
            if not self.reason_codes:
                raise ValueError(f"a {self.verdict} risk decision must carry reason codes")
        return self


class SupervisorDecision(Contract):
    """Evaluator verdict (build.md §10.1).

    Veto-only by construction: there is no field through which the supervisor
    could alter side, price or size. It judges the intent it was given.
    """

    decision_id: UUID
    intent_id: UUID
    verdict: SupervisorVerdict
    reason_codes: tuple[ReasonCode, ...]
    decided_at_utc: UtcDatetime
    policy_version: VersionTag
    statistical_monitor_version: VersionTag | None = None
    observed_regime: Regime = Regime.UNKNOWN
    notes: str | None = Field(default=None, max_length=2000)

    uncalibrated_checks: tuple[VersionTag, ...] = ()
    """Checks that did not run, named (review 1.6 F-024).

    An approval from a seven-rule control plane reads as though seven rules
    passed. When one of them is configured to a threshold nothing can cross,
    that reading is false, and the falseness is invisible — which is what
    deviation D-028 was about. Naming the checks that were not in force makes
    an approval say what it actually means, in the capsule and in the journal
    rather than in a comment somebody has to go and find."""

    @model_validator(mode="after")
    def _check_reasons(self) -> Self:
        if self.verdict is not SupervisorVerdict.APPROVE and not self.reason_codes:
            raise ValueError(f"a {self.verdict} supervisor decision must carry reason codes")
        return self


# --------------------------------------------------------------------------- #
# Execution
# --------------------------------------------------------------------------- #


class ApprovedOrder(Contract):
    """The only thing the execution engine will act on.

    `order_request_id` is the idempotency key required by build.md §7
    invariant 2: a reconnect or retry carrying the same key must never create
    a second position.
    """

    order_request_id: UUID
    intent_id: UUID
    intent_risk_decision_id: UUID
    """The intent-time `RiskDecision` that first approved this trade
    (`risk/policies.py::evaluate`) — always present."""
    final_risk_decision_id: UUID | None = None
    """The execution-time `RiskDecision` that authorized submission
    (`risk/policies.py::revalidate_fixed_volume_at_execution_time`, ADR-001)
    — review 1.22 F-057. `None` only for the replay/paper path
    (`application/orchestration.py`), which does not yet run a FINAL Risk
    revalidation step; always set by `application/execution.py::
    ExecutionOrchestrator`."""
    supervisor_decision_id: UUID

    broker_symbol: Symbol
    side: Side
    entry_type: EntryType
    volume: Volume
    price: Price | None = None
    stop_loss_price: Price
    take_profit_price: Price | None = None

    max_slippage_points: int = Field(ge=0)
    created_at_utc: UtcDatetime
    expires_at_utc: UtcDatetime
    environment: Environment

    @computed_field  # type: ignore[prop-decorator]
    @property
    def magic_number(self) -> int:
        """The MT5 `magic` a future `order_send` would carry for this order

        — core critical path item 5, `review/adr/ADR-007-order-send-idempotence.md`.
        Derived, not assigned, so it never needs its own persistence and
        always agrees with what a future reconciliation reader computes
        independently for the same `order_request_id`."""
        return mt5_magic_number(self.order_request_id)

    @model_validator(mode="after")
    def _check_order(self) -> Self:
        if self.side is Side.FLAT:
            raise ValueError("an approved order must be directional; FLAT is a close instruction")
        if self.entry_type is not EntryType.MARKET and self.price is None:
            raise ValueError(f"{self.entry_type} order requires an explicit price")
        if self.expires_at_utc <= self.created_at_utc:
            raise ValueError("expires_at_utc must be after created_at_utc")
        return self


class ExecutionResult(Contract):
    """Everything the broker told us, kept for reconciliation (build.md §6.4).

    Both the request and the response are persisted. When the two disagree the
    reconciler needs the original payload, not a summary of it.
    """

    execution_id: UUID
    order_request_id: UUID
    intent_id: UUID
    state: OrderState

    mt5_order_ticket: int | None = None
    mt5_deal_ticket: int | None = None
    mt5_position_ticket: int | None = None

    retcode: int | None = None
    retcode_comment: str | None = Field(default=None, max_length=512)

    requested_price: Price | None = None
    executed_price: Price | None = None
    requested_volume: Volume | None = None
    executed_volume: ExactDecimal | None = Field(default=None, ge=ZERO)
    slippage_points: int | None = None

    submitted_at_utc: UtcDatetime | None = None
    broker_time_utc: UtcDatetime | None = None
    completed_at_utc: UtcDatetime | None = None
    latency_ms: int | None = Field(default=None, ge=0)

    order_check_payload: dict[str, Any] | None = None
    order_send_payload: dict[str, Any] | None = None
    request_payload: dict[str, Any] | None = None
    error_detail: str | None = Field(default=None, max_length=2000)


class FlattenInstruction(Contract):
    """What a future policy-driven close would act on, for exactly one open

    position — the flatten's analogue of `ApprovedOrder`, and deliberately
    not `ApprovedOrder` itself (core critical path item 7, ADR-009 §2).
    `ApprovedOrder` cannot express this: it raises on `Side.FLAT` ("FLAT is
    a close instruction") and requires `intent_id`/`intent_risk_decision_id`/
    `supervisor_decision_id`/`stop_loss_price`/`expires_at_utc` — none of
    which a policy-driven close has an honest value for, since there was no
    proposal behind it. This carries no stop loss (a close does not need
    one) and no expiry (the deadline has already passed).

    `volume` is always the broker's own reported position size, never
    risk-sized — the single largest semantic difference from an entry
    order, worth stating explicitly rather than leaving implicit."""

    flatten_request_id: UUID
    ticket: int
    broker_symbol: Symbol
    position_side: Side
    close_side: Side
    volume: Volume
    open_price: Price
    opened_at_utc: UtcDatetime
    magic: int | None = None
    crossed_rollover: bool
    observed_at_utc: UtcDatetime

    @model_validator(mode="after")
    def _check_close(self) -> Self:
        if self.position_side is Side.FLAT:
            raise ValueError(
                "a flatten instruction must target a directional position; "
                "FLAT cannot itself be closed"
            )
        expected_close = Side.SELL if self.position_side is Side.BUY else Side.BUY
        if self.close_side is not expected_close:
            raise ValueError(
                f"close_side must be the inverse of position_side ({expected_close!r}), "
                f"got {self.close_side!r} -- a close that does not invert the position "
                "would add to it instead of closing it"
            )
        return self


class FlattenPlan(Contract):
    """One flatten occurrence's complete commitment — one `FlattenInstruction`

    per open position this occurrence targets. Per-position instructions
    under a per-book request is deliberate: ADR-004 §7 defers "per-position
    vs per-book deadline, once several instruments exist" as an open owner
    question, and this shape keeps both answers reachable without a future
    schema change."""

    flatten_request_id: UUID
    environment: Environment
    canonical_symbol: Symbol
    trading_day: date
    session_close_utc: UtcDatetime
    flatten_deadline_utc: UtcDatetime
    past_deadline: bool
    crossed_rollover: bool
    observed_at_utc: UtcDatetime
    broker_state_snapshot_id: UUID
    """Ties this commitment to the exact durable `BrokerAccountSnapshot`/

    `BrokerPositionSnapshot` rows it was decided on, so an auditor can
    reproduce the observation, not merely read a summary of it."""
    instructions: tuple[FlattenInstruction, ...]

    @model_validator(mode="after")
    def _check_plan(self) -> Self:
        if not self.instructions:
            raise ValueError("a flatten plan must target at least one position")
        if not (self.past_deadline or self.crossed_rollover):
            raise ValueError(
                "a flatten plan must have a real trigger: past_deadline or crossed_rollover"
            )
        return self


# --------------------------------------------------------------------------- #
# Broker state
# --------------------------------------------------------------------------- #


class AccountState(Contract):
    """Account snapshot used by the environment guard (build.md §8.1).

    `is_demo` is the field that keeps paper mode honest: connecting to a live
    account while configured for paper is a HALT, not a warning.
    """

    login: int
    server: Annotated[str, Field(min_length=1, max_length=128)]
    currency: Annotated[str, Field(min_length=3, max_length=8)]
    is_demo: bool
    trade_allowed: bool
    expert_allowed: bool
    connected: bool

    balance: ExactDecimal
    equity: ExactDecimal
    margin: ExactDecimal = Field(ge=ZERO)
    margin_free: ExactDecimal
    margin_level: ExactDecimal | None = None
    leverage: int = Field(gt=0)

    observed_at_utc: UtcDatetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def login_hash(self) -> str:
        """Account identifier for logs and metrics (build.md §16, §21)."""
        return fingerprint({"login": self.login, "server": self.server})[:16]


class PositionState(Contract):
    """An open position as the broker reports it."""

    ticket: int
    broker_symbol: Symbol
    side: Side
    volume: Volume
    open_price: Price
    current_price: Price | None = None
    stop_loss_price: Price | None = None
    take_profit_price: Price | None = None
    opened_at_utc: UtcDatetime
    profit: ExactDecimal
    swap: ExactDecimal
    magic: int | None = None
    observed_at_utc: UtcDatetime

    @model_validator(mode="after")
    def _check_side(self) -> Self:
        if self.side is Side.FLAT:
            raise ValueError("an open position cannot have side FLAT")
        return self


# --------------------------------------------------------------------------- #
# Broker-state snapshots (review 1.15 F-047)
# --------------------------------------------------------------------------- #

# `AccountState`/`PositionState` above are the *live* reads the account guard
# and (eventually) the risk engine act on for one request; they are never
# durably stored. The contracts below are the audit-shaped record of the same
# observation — never held only in memory — and never carry the raw MT5
# login: `account_ref` is the same non-reversible fingerprint
# `AccountState.login_hash` already computes, so a snapshot and the account it
# was observed from can be correlated without persisting the credential-shaped
# value itself.


class BrokerAccountSnapshot(Contract):
    """A durable observation of the broker's own account/margin state.

    `position_set_state`/`pending_order_set_state` carry forward the
    fail-vs-empty distinction the gateway's `positions()`/`pending_orders()`
    already make (an empty tuple only ever means a genuinely flat book; a
    failed query raises) — see `SnapshotCompleteness`. Child rows in
    `broker_position_snapshots`/`broker_pending_order_snapshots` only ever
    exist when the matching state is `COMPLETE`; a `FAILED`/`UNKNOWN` state
    with no child rows means what it says, not "confirmed empty".
    """

    snapshot_id: UUID
    observed_at_utc: UtcDatetime
    recorded_at_utc: UtcDatetime
    environment: Environment
    server: Annotated[str, Field(min_length=1, max_length=128)]
    account_ref: str
    currency: Annotated[str, Field(min_length=3, max_length=8)]
    leverage: int = Field(gt=0)
    margin_mode: str | None = None

    balance: ExactDecimal
    equity: ExactDecimal
    profit: ExactDecimal
    margin: ExactDecimal = Field(ge=ZERO)
    margin_free: ExactDecimal
    margin_level: ExactDecimal | None = None

    account_trade_allowed: bool
    terminal_trade_allowed: bool | None = None

    position_set_state: SnapshotCompleteness
    pending_order_set_state: SnapshotCompleteness


class BrokerPositionSnapshot(Contract):
    """One open position as observed in a broker-state snapshot.

    Tied to its parent `BrokerAccountSnapshot` by `snapshot_id` — the two are
    always written in the same observation, so `snapshot_id` is enough to
    reconstruct "everything the broker reported at this moment" without a
    separate grouping timestamp that could drift from it.
    """

    snapshot_id: UUID
    observed_at_utc: UtcDatetime
    ticket: int
    canonical_symbol: str
    broker_symbol: Symbol
    side: Side
    volume: Volume
    opened_at_utc: UtcDatetime
    open_price: Price
    current_price: Price | None = None
    stop_loss_price: Price | None = None
    take_profit_price: Price | None = None
    profit: ExactDecimal
    swap: ExactDecimal
    magic: int | None = None
    comment: Annotated[str, Field(max_length=256)] | None = None

    @model_validator(mode="after")
    def _check_side(self) -> Self:
        if self.side is Side.FLAT:
            raise ValueError("an open position cannot have side FLAT")
        return self


class BrokerPendingOrderSnapshot(Contract):
    """One pending (not-yet-filled) broker order observed in a snapshot.

    A flat position book can still carry future exposure through a pending
    order — this exists so that fact is not invisible to reconciliation.
    `order_type`/`state` are decoded MT5 enum names (`mt5_gateway.enums
    .ORDER_TYPES`/`ORDER_STATES`), not this platform's own `EntryType`/
    `OrderState` — those describe an `ApprovedOrder` this platform submitted,
    which a broker-observed pending order need not be.
    """

    snapshot_id: UUID
    observed_at_utc: UtcDatetime
    order_id: int
    canonical_symbol: str
    broker_symbol: Symbol
    order_type: str
    state: str
    volume: Volume
    price: Price
    stop_loss_price: Price | None = None
    take_profit_price: Price | None = None
    expires_at_utc: UtcDatetime | None = None


class PendingOrderState(Contract):
    """A pending broker order as read for one request — see `PositionState`

    for why this is not what gets persisted (`BrokerPendingOrderSnapshot` is).
    `order_type`/`state` are decoded MT5 enum names, not this platform's own
    `EntryType`/`OrderState`.
    """

    order_id: int
    broker_symbol: Symbol
    order_type: str
    state: str
    volume: Volume
    price: Price
    stop_loss_price: Price | None = None
    take_profit_price: Price | None = None
    expires_at_utc: UtcDatetime | None = None
    observed_at_utc: UtcDatetime


class Incident(Contract):
    """status.md §9. A SEV-0 or unresolved SEV-1 blocks promotion."""

    incident_id: UUID
    severity: IncidentSeverity
    component: Annotated[str, Field(min_length=1, max_length=64)]
    summary: Annotated[str, Field(min_length=1, max_length=512)]
    opened_at_utc: UtcDatetime
    closed_at_utc: UtcDatetime | None = None
    reason_codes: tuple[ReasonCode, ...] = ()
    correlation_id: UUID | None = None
    root_cause: str | None = Field(default=None, max_length=4000)
    corrective_action: str | None = Field(default=None, max_length=4000)
    regression_test: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def _check_closure(self) -> Self:
        if self.closed_at_utc is not None:
            if self.closed_at_utc < self.opened_at_utc:
                raise ValueError("closed_at_utc precedes opened_at_utc")
            if self.root_cause is None:
                raise ValueError("a closed incident must record a root cause")
        return self

    @property
    def is_open(self) -> bool:
        return self.closed_at_utc is None

    @property
    def blocks_promotion(self) -> bool:
        if self.severity is IncidentSeverity.SEV_0:
            return True
        return self.severity is IncidentSeverity.SEV_1 and self.is_open


# --------------------------------------------------------------------------- #
# Audit
# --------------------------------------------------------------------------- #


class DecisionCapsule(Contract):
    """Immutable record of one decision window (build.md §11).

    Holds every version identifier that fed the decision, so a trade from six
    months ago can be replayed against the exact code, config and model that
    produced it.
    """

    capsule_id: UUID
    occurred_at_utc: UtcDatetime
    correlation_id: UUID

    canonical_symbol: Symbol
    broker_symbol: Symbol
    market_snapshot_id: UUID

    feature_set_version: VersionTag
    feature_values_hash: VersionTag
    strategy_version: VersionTag
    model_version: VersionTag | None = None
    model_output: dict[str, Any] | None = None

    trade_intent: TradeIntent | None = None
    risk_config_version: VersionTag
    risk_decision: RiskDecision | None = None
    supervisor_decision: SupervisorDecision | None = None
    execution_result: ExecutionResult | None = None

    position_state_before: tuple[PositionState, ...] = ()
    position_state_after: tuple[PositionState, ...] = ()

    code_commit: VersionTag
    environment: Environment

    @computed_field  # type: ignore[prop-decorator]
    @property
    def provenance_fingerprint(self) -> str:
        """build.md §25.2 — proof of what produced this decision.

        Binds input data, feature version, model artifact, config and code
        commit into one digest.
        """
        return fingerprint(
            {
                "market_snapshot_id": self.market_snapshot_id,
                "feature_set_version": self.feature_set_version,
                "feature_values_hash": self.feature_values_hash,
                "strategy_version": self.strategy_version,
                "model_version": self.model_version,
                "risk_config_version": self.risk_config_version,
                "code_commit": self.code_commit,
                "environment": self.environment,
                "decision_hash": (
                    self.trade_intent.decision_hash if self.trade_intent is not None else None
                ),
            }
        )
