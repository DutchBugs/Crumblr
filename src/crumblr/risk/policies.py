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
    has_crossed_rollover,
    permits_new_entry,
    requires_flat,
)

MAX_EXPOSURES_PER_SYMBOL = 1
"""Owner decision O-004: one EUR/USD exposure at a time, and no more.

Deliberately a constant rather than a configuration field. It is a business
rule the owner approved for v1, not a budget to be tuned, and a YAML key would
invite someone to raise it without the decision that should accompany doing so.
Raising it is a code change, a review and a status.md decision row.

It holds regardless of whether the account turns out to be hedging or netting
(Q2 — answered 2026-08-24 by `account_info()` on the real demo account:
`RETAIL_HEDGING`, status.md §13). A netting account would net a second order
into the first position and a hedging account would open a parallel one;
neither is what v1 is permitted to do, so the rule sits above the account
model rather than depending on it — deliberately, so it did not need to change
once the answer was known.
"""

HALT_REASONS: frozenset[ReasonCode] = frozenset(
    {
        ReasonCode.LIVE_ACCOUNT_IN_PAPER_MODE,
        ReasonCode.WRONG_ACCOUNT,
        ReasonCode.RECONCILIATION_MISMATCH,
        ReasonCode.MAX_DRAWDOWN,
        ReasonCode.DAILY_LOSS_LIMIT,
        ReasonCode.OVERNIGHT_EXPOSURE,
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
    open_risk_fraction: Decimal = ZERO


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
    """Owner decision O-003. Defaults to imposing nothing, which is safe only
    because refusing *more* entries is never the unsafe direction — a context
    built without it blocks nothing extra rather than permitting something."""


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

    # --- Trading session (O-003) ------------------------------------------
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
    # O-004 first, because it is the more specific refusal and the one an
    # operator will want named. Both can fire; both are reported.
    exposures = sum(
        1 for position in portfolio.open_positions if position.broker_symbol == spec.broker_symbol
    )
    if exposures >= MAX_EXPOSURES_PER_SYMBOL:
        reasons.append(ReasonCode.SYMBOL_EXPOSURE_EXISTS)
    if exposures and _overnight_breach(
        portfolio.open_positions, snapshot.event_time_utc, context.intraday
    ):
        # Past the flatten deadline with the book still open, or holding a
        # position that has already crossed a rollover. A block would leave it
        # there; only a halt brings a person in.
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


def _overnight_breach(
    positions: tuple[PositionState, ...], moment: UtcDatetime, policy: IntradayPolicy
) -> bool:
    """Whether O-003 is being violated right now.

    Two ways it can be: the flatten deadline has passed with the book still
    open, or a position has already survived a rollover. The second matters
    because the first stops being true the moment the day rolls — see
    `trading_window.has_crossed_rollover`.
    """
    if not policy.enabled or not positions:
        return False
    if requires_flat(moment, policy):
        return True
    return any(has_crossed_rollover(position.opened_at_utc, moment) for position in positions)


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
