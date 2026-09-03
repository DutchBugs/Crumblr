"""The deterministic risk gateway (build.md §8).

Every check here can block. None of them can be skipped, and none of them
consult a model. The gateway runs the full checklist and collects *all* failing
reason codes rather than short-circuiting on the first, because an incident
report listing one of four problems sends the operator down one of four paths.

The distinction between BLOCK and HALT is deliberate: BLOCK refuses this trade,
HALT refuses all trading until an operator intervenes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from crumblr.config import ExecutionConfig, RiskConfig
from crumblr.domain.enums import (
    TRADEABLE_DATA_QUALITY,
    ReasonCode,
    RiskVerdict,
    Side,
)
from crumblr.domain.models import (
    AccountState,
    InstrumentSpec,
    MarketSnapshot,
    PositionState,
    RiskDecision,
    TradeIntent,
)
from crumblr.domain.money import ZERO, price_to_points
from crumblr.domain.timeutils import UtcDatetime, age_ms
from crumblr.risk.kill_switch import EquityLedger, KillSwitch
from crumblr.risk.sizing import realised_risk, size_position
from crumblr.risk.trading_window import (
    IntradayPolicy,
    has_crossed_weekly_close,
    permits_new_entry,
    requires_flat,
)

HALT_REASONS: frozenset[ReasonCode] = frozenset(
    {
        ReasonCode.LIVE_ACCOUNT_IN_PAPER_MODE,
        ReasonCode.WRONG_ACCOUNT,
        ReasonCode.RECONCILIATION_MISMATCH,
        ReasonCode.MAX_DRAWDOWN,
        ReasonCode.DAILY_LOSS_LIMIT,
        ReasonCode.OVERNIGHT_EXPOSURE,
        ReasonCode.FLATTEN_STATE_UNKNOWN,
    }
)
"""Conditions that mean the *system* is unsafe, not just this trade."""


@dataclass(frozen=True)
class PortfolioState:
    """Everything the gateway needs to know about current exposure."""

    account: AccountState
    open_positions: tuple[PositionState, ...]
    ledger: EquityLedger
    orders_in_last_hour: int
    seen_decision_hashes: frozenset[str]
    open_risk_fraction: Decimal | None
    """Real portfolio open risk (owner risk policy v1, D1.4), from

    `risk/portfolio_risk.py::assess_open_risk` — never a count-based
    approximation. `None` means the platform could not establish it (an
    open position with untrustworthy stop geometry) and must never be
    treated as zero; `evaluate()` fails this closed via
    `ReasonCode.OPEN_RISK_UNKNOWN`. Deliberately no default: a
    `PortfolioState` that does not state an open-risk answer is exactly
    the fail-open hazard this field's own default (`= ZERO`) used to
    be — every construction site must now say something."""


@dataclass(frozen=True)
class RiskContext:
    """Configuration and environment the checks are evaluated against."""

    risk: RiskConfig
    execution: ExecutionConfig
    allowed_symbols: frozenset[str]
    require_demo_account: bool
    expected_server: str
    expected_login: int | None
    risk_config_version: str
    expected_currency: str | None = None
    expected_leverage: int | None = None
    intraday: IntradayPolicy = field(default_factory=IntradayPolicy.disabled)
    """The weekly session policy (owner risk policy v1, D1.5; supersedes

    O-003's original daily rule). Defaults to imposing nothing, which is
    safe only because refusing *more* entries is never the unsafe
    direction — a context built without it blocks nothing extra rather
    than permitting something."""


def _stop_distance(intent: TradeIntent) -> Decimal:
    assert intent.stop_loss_price is not None  # guaranteed by the TradeIntent contract
    return abs(intent.reference_price - intent.stop_loss_price)


def evaluate(
    intent: TradeIntent,
    snapshot: MarketSnapshot,
    spec: InstrumentSpec,
    portfolio: PortfolioState,
    context: RiskContext,
    kill_switch: KillSwitch,
    *,
    now: UtcDatetime,
) -> RiskDecision:
    """Run the full pre-trade checklist and produce a verdict.

    Sizing happens last: there is no point computing a volume for an order that
    six other checks have already refused.
    """
    reasons: list[ReasonCode] = []

    # --- System and account state -----------------------------------------
    if kill_switch.is_halted:
        reasons.append(ReasonCode.SYSTEM_HALTED)
    if not portfolio.account.connected:
        reasons.append(ReasonCode.ACCOUNT_NOT_CONNECTED)
    if not portfolio.account.trade_allowed:
        reasons.append(ReasonCode.MARKET_DISABLED)
    if not portfolio.account.expert_allowed:
        reasons.append(ReasonCode.EXPERT_TRADING_DISABLED)
    if context.require_demo_account and not portfolio.account.is_demo:
        reasons.append(ReasonCode.LIVE_ACCOUNT_IN_PAPER_MODE)
    if portfolio.account.server != context.expected_server:
        reasons.append(ReasonCode.WRONG_ACCOUNT)
    if context.expected_login is not None and portfolio.account.login != context.expected_login:
        reasons.append(ReasonCode.WRONG_ACCOUNT)
    # Currency and leverage are checked, not assumed. Both change what a risk
    # budget means without changing anything the strategy can see, so a silent
    # difference between the configured account and the connected one is a
    # wrong account whatever the server name says.
    if (
        context.expected_currency is not None
        and portfolio.account.currency != context.expected_currency
    ):
        reasons.append(ReasonCode.WRONG_ACCOUNT)
    if (
        context.expected_leverage is not None
        and portfolio.account.leverage != context.expected_leverage
    ):
        reasons.append(ReasonCode.WRONG_ACCOUNT)

    # --- Instrument -------------------------------------------------------
    if intent.symbol not in context.allowed_symbols:
        reasons.append(ReasonCode.SYMBOL_NOT_ALLOWED)

    # --- Market data ------------------------------------------------------
    if snapshot.data_quality not in TRADEABLE_DATA_QUALITY:
        reasons.append(ReasonCode.STALE_MARKET_DATA)
    data_age = age_ms(snapshot.event_time_utc, now=now)
    if data_age > context.execution.max_market_data_age_ms or data_age < 0:
        reasons.append(ReasonCode.STALE_MARKET_DATA)
    if snapshot.ask < snapshot.bid or snapshot.bid <= ZERO:
        reasons.append(ReasonCode.INVALID_QUOTE)
    if snapshot.spread_points > context.execution.max_spread_points:
        reasons.append(ReasonCode.SPREAD_TOO_WIDE)

    # --- Trading session (owner risk policy v1, D1.5) ----------------------
    # Judged on market time, not on when this process got round to deciding.
    if not permits_new_entry(snapshot.event_time_utc, context.intraday):
        reasons.append(ReasonCode.SESSION_BLACKOUT)

    # --- Intent validity --------------------------------------------------
    if intent.is_expired(at=now):
        reasons.append(ReasonCode.INTENT_EXPIRED)
    if intent.decision_hash in portfolio.seen_decision_hashes:
        reasons.append(ReasonCode.DUPLICATE_INTENT)
    if intent.side is Side.FLAT:
        reasons.append(ReasonCode.INVALID_STOP)

    # --- Stop validity ----------------------------------------------------
    stop_distance = _stop_distance(intent)
    stop_distance_points = price_to_points(stop_distance, spec.point)
    if stop_distance <= ZERO or stop_distance_points < context.risk.min_stop_distance_points:
        reasons.append(ReasonCode.INVALID_STOP)
    elif stop_distance_points < spec.stops_level:
        reasons.append(ReasonCode.STOP_DISTANCE_VIOLATION)

    # --- Exposure ---------------------------------------------------------
    # O-004 (one exposure per symbol) withdrawn 2026-09-02: see
    # OWNER_POLICY_V1.md §2. Multiple positions are permitted; the real
    # portfolio budget is enforced below via `open_risk_fraction`.
    if overnight_breach(portfolio.open_positions, snapshot.event_time_utc, context.intraday):
        # Past the Friday flatten deadline with the book still open, or
        # holding a position that has already crossed the weekly close.
        # A block would leave it there; only a halt brings a person in.
        reasons.append(ReasonCode.OVERNIGHT_EXPOSURE)
    if len(portfolio.open_positions) >= context.risk.max_open_positions:
        reasons.append(ReasonCode.MAX_OPEN_POSITIONS)
    if portfolio.orders_in_last_hour >= context.risk.max_orders_per_hour:
        reasons.append(ReasonCode.ORDER_FREQUENCY_LIMIT)
    if intent.requested_risk_fraction is None:
        reasons.append(ReasonCode.INVALID_STOP)
    else:
        # Two distinct budgets: what one trade may risk, and what the book may
        # carry in total. They fail for different reasons and are reported so.
        if intent.requested_risk_fraction > context.risk.max_risk_per_trade:
            reasons.append(ReasonCode.RISK_PER_TRADE_LIMIT)
        # Owner risk policy v1 (D1.4): `open_risk_fraction` is `None` when
        # the platform could not establish it (an open position with
        # untrustworthy stop geometry) — never treated as zero. A BLOCK,
        # not a HALT: see `ReasonCode.OPEN_RISK_UNKNOWN`'s own docstring.
        if portfolio.open_risk_fraction is None:
            reasons.append(ReasonCode.OPEN_RISK_UNKNOWN)
        else:
            projected_open_risk = portfolio.open_risk_fraction + intent.requested_risk_fraction
            if projected_open_risk > context.risk.max_open_risk:
                reasons.append(ReasonCode.OPEN_RISK_LIMIT)

    # --- Loss gates -------------------------------------------------------
    if portfolio.ledger.session_loss_fraction >= context.risk.max_daily_loss:
        reasons.append(ReasonCode.DAILY_LOSS_LIMIT)
    if portfolio.ledger.drawdown_fraction >= context.risk.max_drawdown:
        reasons.append(ReasonCode.MAX_DRAWDOWN)

    if reasons:
        return _refuse(intent, reasons, context, now)

    # --- Sizing -----------------------------------------------------------
    assert intent.requested_risk_fraction is not None
    sizing = size_position(
        equity=portfolio.account.equity,
        risk_fraction=intent.requested_risk_fraction,
        stop_distance_price=stop_distance,
        spec=spec,
    )
    if not sizing.is_tradeable or sizing.volume is None:
        return _refuse(intent, [ReasonCode.VOLUME_OUT_OF_RANGE], context, now)

    # The invariant sizing exists to guarantee: never more than authorised.
    carried = realised_risk(sizing.volume, stop_distance, spec)
    if carried > sizing.risk_amount:
        return _refuse(intent, [ReasonCode.OPEN_RISK_LIMIT], context, now)

    return RiskDecision(
        decision_id=_decision_id(intent, "pass"),
        intent_id=intent.intent_id,
        verdict=RiskVerdict.PASS,
        reason_codes=(),
        decided_at_utc=now,
        risk_config_version=context.risk_config_version,
        approved_volume=sizing.volume,
        account_equity=portfolio.account.equity,
        stop_distance_points=stop_distance_points,
        risk_amount=carried,
    )


def revalidate_fixed_volume_at_execution_time(
    intent: TradeIntent,
    prior_decision: RiskDecision,
    fresh_snapshot: MarketSnapshot,
    spec: InstrumentSpec,
    fresh_portfolio: PortfolioState,
    context: RiskContext,
    kill_switch: KillSwitch,
    *,
    now: UtcDatetime,
) -> RiskDecision:
    """ADR-001 — the FINAL, execution-time risk check, immediately before
    `order_check`/`order_send`.

    Reuses `evaluate()` verbatim for every check it already performs against
    freshly observed inputs — system/account state, market data quality and
    spread, session window, intent expiry, exposure, order frequency, and
    loss gates — rather than reimplementing any of them (ADR-001's own
    requirement). `evaluate()`'s sizing decision is used only as a safety
    *ceiling* below; it is never adopted as the answer.

    **This function never resizes.** The outcome is always exactly one of:
    PASS with `prior_decision.approved_volume` unchanged, or BLOCK/HALT
    (`review/PHASE4_PLAN_REVIEW_GO_WITH_TWEAKS.md` point 1). A fresh
    evaluation that would size a *smaller* volume than the one already
    approved is exactly the situation this function must refuse into, not
    silently shrink into.

    `evaluate()` derives stop distance from `intent.reference_price`, which
    is intent-time and may now be stale. This function prices the stop
    against the current executable side of the book instead (point 7): a BUY
    fills at the ask, a SELL at the bid.

    **Caller responsibility.** `fresh_portfolio.seen_decision_hashes` must
    exclude `intent.decision_hash` itself — this call is deliberately
    re-evaluating the one decision that hash already names, not treating it
    as a duplicate of itself. `prior_decision` must be a `PASS`.
    """
    assert prior_decision.verdict is RiskVerdict.PASS
    assert prior_decision.approved_volume is not None
    assert intent.stop_loss_price is not None  # guaranteed for a PASSed directional intent

    fresh = evaluate(intent, fresh_snapshot, spec, fresh_portfolio, context, kill_switch, now=now)

    executable_price = fresh_snapshot.ask if intent.side is Side.BUY else fresh_snapshot.bid
    fresh_stop_distance = abs(executable_price - intent.stop_loss_price)
    fresh_stop_distance_points = price_to_points(fresh_stop_distance, spec.point)

    execution_time_reasons: list[ReasonCode] = []
    if (
        fresh_stop_distance <= ZERO
        or fresh_stop_distance_points < context.risk.min_stop_distance_points
    ):
        execution_time_reasons.append(ReasonCode.INVALID_STOP)
    elif fresh_stop_distance_points < spec.stops_level:
        execution_time_reasons.append(ReasonCode.STOP_DISTANCE_VIOLATION)

    if fresh.verdict is not RiskVerdict.PASS or execution_time_reasons:
        reasons = [
            *fresh.reason_codes,
            *execution_time_reasons,
            ReasonCode.EXECUTION_TIME_RISK_BLOCK,
        ]
        return _refuse_at_execution_time(intent, reasons, context, now)

    assert fresh.risk_amount is not None
    carried = realised_risk(prior_decision.approved_volume, fresh_stop_distance, spec)
    if carried > fresh.risk_amount:
        # The fixed volume now carries more risk than a fresh sizing would
        # currently allow (equity dropped, the executable price moved
        # against the stop, or both). Refuse — never resize into a smaller
        # volume.
        return _refuse_at_execution_time(
            intent,
            [ReasonCode.RISK_PER_TRADE_LIMIT, ReasonCode.EXECUTION_TIME_RISK_BLOCK],
            context,
            now,
        )

    return RiskDecision(
        decision_id=_decision_id(intent, "execution-pass"),
        intent_id=intent.intent_id,
        verdict=RiskVerdict.PASS,
        reason_codes=(),
        decided_at_utc=now,
        risk_config_version=context.risk_config_version,
        approved_volume=prior_decision.approved_volume,
        account_equity=fresh_portfolio.account.equity,
        stop_distance_points=fresh_stop_distance_points,
        risk_amount=carried,
    )


def _refuse_at_execution_time(
    intent: TradeIntent,
    reasons: list[ReasonCode],
    context: RiskContext,
    now: UtcDatetime,
) -> RiskDecision:
    """`_refuse()`, with a `decision_id` distinct from an intent-time refusal.

    `_decision_id` is derived from `decision_hash` plus a discriminator; an
    intent-time BLOCK and an execution-time BLOCK for the same intent would
    otherwise collide on the same id, which would look like the same
    decision recorded twice rather than two different judgements made at two
    different times.
    """
    decision = _refuse(intent, reasons, context, now)
    return decision.model_copy(
        update={"decision_id": _decision_id(intent, f"execution-{decision.verdict.value}")}
    )


def overnight_breach(
    positions: tuple[PositionState, ...], moment: UtcDatetime, policy: IntradayPolicy
) -> bool:
    """Whether owner risk policy v1's weekly session policy (D1.5) is being

    violated right now.

    Two ways it can be: the Friday flatten deadline has passed with the
    book still open, or a position has already survived the weekly
    close. The second matters because the first stops being true the
    moment the week rolls — see `trading_window.has_crossed_weekly_close`.

    Public (not `_overnight_breach`) so `application/orchestration.py`
    and `application/live_decision.py` can call this one implementation
    instead of each re-inlining the same two-legged condition — the
    duplication `review/adr/ADR-009-automatic-flatten-submission.md` §1
    already named as a known fact, now four sites instead of three
    inline copies plus this one.
    """
    if not policy.enabled or not positions:
        return False
    if requires_flat(moment, policy):
        return True
    return any(has_crossed_weekly_close(position.opened_at_utc, moment) for position in positions)


def _refuse(
    intent: TradeIntent,
    reasons: list[ReasonCode],
    context: RiskContext,
    now: UtcDatetime,
) -> RiskDecision:
    """Turn a list of failures into BLOCK, or HALT when the system itself is unsafe."""
    unique = tuple(dict.fromkeys(reasons))
    verdict = (
        RiskVerdict.HALT if any(reason in HALT_REASONS for reason in unique) else RiskVerdict.BLOCK
    )
    return RiskDecision(
        decision_id=_decision_id(intent, verdict.value),
        intent_id=intent.intent_id,
        verdict=verdict,
        reason_codes=unique,
        decided_at_utc=now,
        risk_config_version=context.risk_config_version,
    )


def _decision_id(intent: TradeIntent, discriminator: str) -> UUID:
    """Derived, not random, so a replay reproduces identical decision records."""
    return uuid5(NAMESPACE_URL, f"crumblr:risk:{intent.decision_hash}:{discriminator}")
