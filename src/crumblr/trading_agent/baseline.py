"""Deterministic baseline strategy (build.md §9.3 Stage A).

Its purpose is to validate infrastructure, not to claim an edge. It trades a
moving-average separation in a trending regime with an ATR-derived stop, and it
returns NO_TRADE far more often than it trades — which build.md §30.4 treats as
a first-class and often desirable outcome.

The agent proposes a *risk fraction*, never a lot size. It also never touches
the broker: it receives a snapshot and returns an intent, and that is the whole
of its authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import ROUND_DOWN, ROUND_UP, Decimal
from uuid import NAMESPACE_URL, uuid5

from crumblr.domain.enums import EntryType, Regime, Side
from crumblr.domain.models import Bar, InstrumentSpec, MarketSnapshot, TradeIntent
from crumblr.domain.money import ZERO, quantize_price
from crumblr.trading_agent.base import (
    AgentContext,
    StrategyDecision,
    StrategyOutcome,
)
from crumblr.trading_agent.features import (
    MINIMUM_BARS as FEATURE_MINIMUM_BARS,
)
from crumblr.trading_agent.features import FeatureSnapshot, compute_features

STRATEGY_ID = "baseline_v1"
STRATEGY_VERSION = "1.0.0"

MINIMUM_BARS = FEATURE_MINIMUM_BARS
"""History this strategy needs before it can compute anything."""

TRADEABLE_REGIMES: frozenset[Regime] = frozenset({Regime.TREND})
"""Regimes this strategy claims to understand. Everything else is NO_TRADE."""


@dataclass(frozen=True)
class StrategyConfig:
    """Strategy parameters. Versioned with the strategy, never tuned in place."""

    stop_atr_multiple: Decimal = Decimal("1.5")
    reward_multiple: Decimal = Decimal("2.0")
    intent_lifetime_seconds: int = 60
    min_trend_score: Decimal = Decimal("0.50")
    max_trend_score: Decimal = Decimal("6.0")
    """Beyond this the move is treated as already extended, not as confirmation."""


def _confidence_from(trend_score: Decimal, config: StrategyConfig) -> float:
    """Map trend separation onto [0, 1], saturating at `max_trend_score`."""
    magnitude = min(abs(trend_score), config.max_trend_score)
    span = config.max_trend_score - config.min_trend_score
    if span <= ZERO:
        return 0.0
    scaled = (magnitude - config.min_trend_score) / span
    return float(max(Decimal(0), min(Decimal(1), scaled)))


def _decide(
    snapshot: MarketSnapshot,
    features: FeatureSnapshot,
    spec: InstrumentSpec,
    context: AgentContext,
    config: StrategyConfig | None = None,
) -> StrategyDecision:
    """Map market state onto a TradeIntent or NO_TRADE.

    Stops are rounded *away* from the entry so that quantising to the symbol's
    precision can never silently tighten the risk the sizing calculation was
    based on.
    """
    config = config or StrategyConfig()

    if features.regime not in TRADEABLE_REGIMES:
        return StrategyDecision(
            side=Side.FLAT,
            confidence=0.0,
            reason_codes=(f"regime_not_traded:{features.regime.value.lower()}",),
            intent=None,
        )

    direction = Side.BUY if features.trend_score > ZERO else Side.SELL

    if direction in context.open_position_sides:
        return StrategyDecision(
            side=Side.FLAT,
            confidence=0.0,
            reason_codes=("already_positioned",),
            intent=None,
        )

    if abs(features.trend_score) > config.max_trend_score:
        return StrategyDecision(
            side=Side.FLAT,
            confidence=0.0,
            reason_codes=("trend_overextended",),
            intent=None,
        )

    if features.atr <= ZERO:
        return StrategyDecision(
            side=Side.FLAT,
            confidence=0.0,
            reason_codes=("atr_unavailable",),
            intent=None,
        )

    # Volatility sets the stop, but policy sets the floor: the wider of the two
    # wins. The broker's own stops_level is a hard constraint on top of that.
    volatility_stop = features.atr * config.stop_atr_multiple
    policy_floor = spec.point * Decimal(max(context.min_stop_distance_points, spec.stops_level))
    stop_distance = max(volatility_stop, policy_floor)

    target_distance = stop_distance * config.reward_multiple
    digits = spec.digits

    if direction is Side.BUY:
        entry = snapshot.ask
        stop = quantize_price(entry - stop_distance, digits, ROUND_DOWN)
        target = quantize_price(entry + target_distance, digits, ROUND_UP)
    else:
        entry = snapshot.bid
        stop = quantize_price(entry + stop_distance, digits, ROUND_UP)
        target = quantize_price(entry - target_distance, digits, ROUND_DOWN)

    if stop <= ZERO or target <= ZERO:
        return StrategyDecision(
            side=Side.FLAT,
            confidence=0.0,
            reason_codes=("degenerate_price_levels",),
            intent=None,
        )

    confidence = _confidence_from(features.trend_score, config)
    reason_codes = (
        f"regime:{features.regime.value.lower()}",
        "ema_separation",
        f"trend_score:{features.trend_score.quantize(Decimal('0.01'))}",
    )

    # Derived from the decision inputs rather than random, so replaying the
    # same series reproduces the same intent identifiers.
    intent_id = uuid5(
        NAMESPACE_URL,
        f"crumblr:intent:{STRATEGY_ID}:{STRATEGY_VERSION}:{features.feature_snapshot_id}",
    )

    intent = TradeIntent(
        intent_id=intent_id,
        strategy_id=STRATEGY_ID,
        strategy_version=STRATEGY_VERSION,
        model_version=None,
        symbol=snapshot.symbol,
        side=direction,
        created_at_utc=snapshot.event_time_utc,
        expires_at_utc=snapshot.event_time_utc + timedelta(seconds=config.intent_lifetime_seconds),
        entry_type=EntryType.MARKET,
        reference_price=entry,
        stop_loss_price=stop,
        take_profit_price=target,
        confidence=confidence,
        reason_codes=reason_codes,
        requested_risk_fraction=context.requested_risk_fraction,
        feature_snapshot_id=features.feature_snapshot_id,
    )
    return StrategyDecision(
        side=direction,
        confidence=confidence,
        reason_codes=reason_codes,
        intent=intent,
    )


def evaluate(
    snapshot: MarketSnapshot,
    bars: tuple[Bar, ...],
    spec: InstrumentSpec,
    context: AgentContext,
    config: StrategyConfig | None = None,
) -> StrategyOutcome:
    """Compute features and decide, in the shape the orchestrator expects."""
    if len(bars) < MINIMUM_BARS:
        return StrategyOutcome(
            decision=StrategyDecision(
                side=Side.FLAT, confidence=0.0, reason_codes=("warming_up",), intent=None
            ),
            features=None,
        )

    features = compute_features(
        bars, symbol=snapshot.symbol, computed_at_utc=snapshot.event_time_utc
    )
    if features is None:
        return StrategyOutcome(
            decision=StrategyDecision(
                side=Side.FLAT, confidence=0.0, reason_codes=("warming_up",), intent=None
            ),
            features=None,
        )

    return StrategyOutcome(
        decision=_decide(snapshot, features, spec, context, config), features=features
    )
