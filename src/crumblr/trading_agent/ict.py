"""The ICT entry model, expressed as a set of required conditions.

The setup this implements is the common one: price sweeps a liquidity pool,
displaces back through structure leaving a fair value gap, then retraces into
that gap at a discount before continuing. Each of those clauses is a separate,
individually enforceable condition rather than one fused rule, so a run can
report *which* condition failed rather than only that no trade was taken.

Every condition can be switched off in `IctConditions`. That is not a way to
loosen the model in production — it is what makes the model measurable: turning
one condition off and re-running tells you what that condition was contributing.
Production configuration should require all of them.

**On evidence.** This strategy rests on a premise about how institutional order
flow interacts with resting stops. Synthetic replay data has no order flow and
no participants, so a run against it demonstrates that the detection logic works
and nothing whatsoever about whether the premise holds. That question needs real
data and the walk-forward protocol in build.md §13.2.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from decimal import ROUND_DOWN, ROUND_UP, Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from crumblr.domain.enums import EntryType, Regime, Side
from crumblr.domain.hashing import fingerprint
from crumblr.domain.models import (
    Bar,
    Contract,
    InstrumentSpec,
    MarketSnapshot,
    Symbol,
    TradeIntent,
    VersionTag,
)
from crumblr.domain.money import ZERO, ExactDecimal, quantize_price
from crumblr.domain.timeutils import UtcDatetime
from crumblr.trading_agent.base import AgentContext, StrategyDecision, StrategyOutcome
from crumblr.trading_agent.imbalance import (
    FairValueGap,
    OrderBlock,
    average_true_range,
    find_fair_value_gaps,
    find_order_block,
)
from crumblr.trading_agent.liquidity import (
    LiquiditySweep,
    detect_sweep,
    find_liquidity_pools,
    nearest_target,
)
from crumblr.trading_agent.sessions import (
    TRADING_KILLZONES,
    Killzone,
    is_market_open,
    killzone_at,
)
from crumblr.trading_agent.structure import (
    DealingRange,
    RangePosition,
    StructureState,
    read_structure,
)

STRATEGY_ID = "ict_v1"
STRATEGY_VERSION = "1.0.0"
FEATURE_SET_VERSION = "ict-features-v1"

MINIMUM_BARS = 120
"""Enough history for swing detection, a dealing range and an ATR baseline."""


@dataclass(frozen=True)
class IctConditions:
    """Which clauses of the model are mandatory.

    Defaults require the complete setup. Relaxing one is an experiment, and the
    resulting strategy is a different version with its own evaluation record.
    """

    require_market_open: bool = True
    require_killzone: bool = True
    require_liquidity_sweep: bool = True
    require_structure_shift: bool = True
    require_fair_value_gap: bool = True

    require_price_in_zone: bool = True
    """Price must currently be trading inside the gap.

    This is the entry trigger, and leaving it out was a real error in the first
    version of this model: the other conditions all become true at the moment of
    displacement, when price is at the *extreme* of the move. Entering there is
    the opposite of the intent — the model is supposed to wait for price to
    retrace back into the imbalance, which is also what puts it back at a
    discount. Without this condition the location filter rejected every setup,
    and correctly so.
    """

    require_order_block: bool = False
    """Off by default: a valid displacement leg does not always leave an
    opposing candle behind it, and demanding one rejects otherwise complete
    setups."""

    require_discount_premium: bool = True
    require_optimal_trade_entry: bool = True
    require_liquidity_target: bool = False
    """Off by default: when no opposing pool exists the model falls back to a
    reward multiple rather than declining the trade."""


@dataclass(frozen=True)
class IctConfig:
    """Parameters of the entry model. Versioned with the strategy."""

    conditions: IctConditions = field(default_factory=IctConditions)
    swing_strength: int = 2

    sweep_lookback_bars: int = 24
    structure_lookback_bars: int = 24
    """How long a setup stays armed.

    The sweep and the shift form the setup; the entry comes later, when price
    retraces into the gap. These windows must therefore be long enough to still
    cover the setup while that retracement plays out — an eight-bar window
    expires before price has come back.
    """

    min_displacement_ratio: Decimal = Decimal("1.5")
    fvg_max_age_bars: int = 30

    ote_low: Decimal = Decimal("0.62")
    ote_high: Decimal = Decimal("0.79")
    """The optimal-trade-entry band, as a retracement of the dealing range."""

    equilibrium_tolerance: Decimal = Decimal("0.02")
    stop_buffer_atr: Decimal = Decimal("0.25")
    """Stops sit beyond the swept extreme by this multiple of ATR, so the stop
    is not resting exactly where the sweep already reached."""

    fallback_reward_multiple: Decimal = Decimal("2.0")
    min_reward_multiple: Decimal = Decimal("1.5")
    """A liquidity target closer than this is not worth the risk taken."""

    intent_lifetime_seconds: int = 300
    tradeable_killzones: frozenset[Killzone] = TRADING_KILLZONES


class IctFeatureSnapshot(Contract):
    """The structural evidence behind one ICT decision.

    Persisted with the decision capsule so a setup can be re-examined later
    against what the model actually saw, rather than against a chart redrawn
    from memory.
    """

    feature_snapshot_id: UUID
    feature_set_version: VersionTag
    symbol: Symbol
    computed_at_utc: UtcDatetime
    bars_used: int

    killzone: Killzone
    market_open: bool
    structure_state: StructureState
    range_position: RangePosition
    retracement: ExactDecimal
    atr: ExactDecimal

    swept_level: ExactDecimal | None = None
    sweep_direction: Side | None = None
    shifted_up: bool = False
    shifted_down: bool = False
    fvg_lower: ExactDecimal | None = None
    fvg_upper: ExactDecimal | None = None
    displacement_ratio: ExactDecimal | None = None
    order_block_low: ExactDecimal | None = None
    order_block_high: ExactDecimal | None = None
    liquidity_target: ExactDecimal | None = None

    conditions_met: tuple[str, ...] = ()
    conditions_failed: tuple[str, ...] = ()

    @property
    def feature_values_hash(self) -> str:
        return fingerprint(
            {
                "feature_set_version": self.feature_set_version,
                "killzone": self.killzone,
                "structure_state": self.structure_state,
                "range_position": self.range_position,
                "retracement": self.retracement,
                "atr": self.atr,
                "swept_level": self.swept_level,
                "sweep_direction": self.sweep_direction,
                "shifted_up": self.shifted_up,
                "shifted_down": self.shifted_down,
                "fvg_lower": self.fvg_lower,
                "fvg_upper": self.fvg_upper,
                "displacement_ratio": self.displacement_ratio,
                "conditions_met": list(self.conditions_met),
                "conditions_failed": list(self.conditions_failed),
            }
        )

    @property
    def regime(self) -> Regime:
        """Map structure onto the platform's regime vocabulary.

        A confirmed structure shift counts as a directional read even though
        the *prior* swing sequence is, by then, no longer clean — a shift is
        precisely the moment the old structure breaks. Reporting that as
        UNKNOWN would have the supervisor veto every valid setup this model
        exists to take, since it enters on shifts by design.

        Unclear structure with no shift is genuinely unknown, and the
        supervisor should still veto on it.
        """
        if self.shifted_up or self.shifted_down:
            return Regime.TREND
        if self.structure_state in (StructureState.BULLISH, StructureState.BEARISH):
            return Regime.TREND
        return Regime.UNKNOWN


@dataclass
class _Evaluation:
    """Working state while the conditions are checked in order."""

    met: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    def check(self, name: str, passed: bool, *, required: bool) -> bool:
        """Record a condition. Returns whether evaluation may continue."""
        if passed:
            self.met.append(name)
            return True
        self.failed.append(name)
        return not required


def _evaluate(
    snapshot: MarketSnapshot,
    bars: tuple[Bar, ...],
    spec: InstrumentSpec,
    context: AgentContext,
    config: IctConfig | None = None,
) -> tuple[StrategyDecision, IctFeatureSnapshot]:
    """Run the entry model and return the decision plus its evidence.

    Conditions are evaluated in the order the model narrates them — session,
    sweep, shift, imbalance, location — and evaluation stops at the first
    required condition that fails. The feature snapshot records both the
    conditions met and the one that stopped it.
    """
    config = config or IctConfig()
    conditions = config.conditions
    evaluation = _Evaluation()

    now = snapshot.event_time_utc
    atr = average_true_range(bars)
    killzone = killzone_at(now)
    market_open = is_market_open(now)
    structure = read_structure(
        bars, strength=config.swing_strength, shift_lookback=config.structure_lookback_bars
    )

    def snapshot_of(**overrides: object) -> IctFeatureSnapshot:
        base: dict[str, object] = {
            "feature_snapshot_id": _feature_id(snapshot),
            "feature_set_version": FEATURE_SET_VERSION,
            "symbol": snapshot.symbol,
            "computed_at_utc": now,
            "bars_used": len(bars),
            "killzone": killzone,
            "market_open": market_open,
            "structure_state": structure.state,
            "range_position": RangePosition.EQUILIBRIUM,
            "retracement": ZERO,
            "atr": atr,
            "shifted_up": structure.shifted_up,
            "shifted_down": structure.shifted_down,
            "conditions_met": tuple(evaluation.met),
            "conditions_failed": tuple(evaluation.failed),
        }
        base.update(overrides)
        return IctFeatureSnapshot(**base)  # type: ignore[arg-type]

    def no_trade(reason: str) -> tuple[StrategyDecision, IctFeatureSnapshot]:
        return (
            StrategyDecision(side=Side.FLAT, confidence=0.0, reason_codes=(reason,), intent=None),
            snapshot_of(),
        )

    # --- Session ----------------------------------------------------------
    if not evaluation.check("market_open", market_open, required=conditions.require_market_open):
        return no_trade("market_closed")

    in_killzone = killzone in config.tradeable_killzones
    if not evaluation.check("killzone", in_killzone, required=conditions.require_killzone):
        return no_trade(f"outside_killzone:{killzone.value.lower()}")

    if atr <= ZERO:
        return no_trade("atr_unavailable")

    # --- Liquidity sweep --------------------------------------------------
    buy_side = find_liquidity_pools(structure.swing_highs, is_buy_side=True)
    sell_side = find_liquidity_pools(structure.swing_lows, is_buy_side=False)
    sweep = detect_sweep(bars, buy_side + sell_side, lookback=config.sweep_lookback_bars)

    if not evaluation.check(
        "liquidity_sweep", sweep is not None, required=conditions.require_liquidity_sweep
    ):
        return no_trade("no_liquidity_sweep")

    # The sweep names the direction: a low taken and rejected argues for longs.
    direction = sweep.direction if sweep is not None else None
    if direction is None:
        return no_trade("no_direction")

    if direction in context.open_position_sides:
        return no_trade("already_positioned")

    # --- Structure shift --------------------------------------------------
    shift_agrees = structure.shifted_up if direction is Side.BUY else structure.shifted_down
    if not evaluation.check(
        "structure_shift", shift_agrees, required=conditions.require_structure_shift
    ):
        return no_trade("no_structure_shift")

    # --- Imbalance --------------------------------------------------------
    gaps = find_fair_value_gaps(
        bars,
        min_displacement_ratio=config.min_displacement_ratio,
        max_age_bars=config.fvg_max_age_bars,
    )
    aligned = tuple(gap for gap in gaps if gap.direction is direction)
    gap: FairValueGap | None = aligned[-1] if aligned else None

    if not evaluation.check(
        "fair_value_gap", gap is not None, required=conditions.require_fair_value_gap
    ):
        return no_trade("no_fair_value_gap")

    # --- Entry trigger ----------------------------------------------------
    # Price must have come back into the imbalance. This is the moment the
    # model is waiting for, not the displacement that created it.
    entry = snapshot.ask if direction is Side.BUY else snapshot.bid
    in_zone = gap is not None and gap.contains(entry)
    if not evaluation.check("price_in_zone", in_zone, required=conditions.require_price_in_zone):
        return no_trade("price_not_in_zone")

    order_block: OrderBlock | None = None
    if gap is not None:
        order_block = find_order_block(
            bars, displacement_index=gap.created_index, direction=direction
        )
    if not evaluation.check(
        "order_block", order_block is not None, required=conditions.require_order_block
    ):
        return no_trade("no_order_block")

    # --- Location ---------------------------------------------------------
    # Premium and discount are measured against the *impulse leg* — from the
    # level that was swept to the extreme the displacement reached — not
    # against an older swing range. That leg is the move price is retracing,
    # and it is the range the optimal-trade-entry band is defined on. Measuring
    # against an unrelated range makes both filters arbitrary.
    if sweep is None:
        return no_trade("no_liquidity_sweep")
    dealing_range = _impulse_leg(bars, sweep=sweep, direction=direction)
    if dealing_range is None:
        return no_trade("no_dealing_range")

    position = dealing_range.position_of(entry, tolerance=config.equilibrium_tolerance)
    retracement = dealing_range.retracement_of(entry)

    wants = RangePosition.DISCOUNT if direction is Side.BUY else RangePosition.PREMIUM
    if not evaluation.check(
        "discount_premium", position is wants, required=conditions.require_discount_premium
    ):
        return no_trade(f"wrong_range_half:{position.value.lower()}")

    in_ote = config.ote_low <= retracement <= config.ote_high
    if direction is Side.SELL:
        # Retracement is measured from the range high, so a short entering at
        # the same depth of pullback sits on the mirrored band.
        in_ote = config.ote_low <= (Decimal(1) - retracement) <= config.ote_high

    if not evaluation.check(
        "optimal_trade_entry", in_ote, required=conditions.require_optimal_trade_entry
    ):
        return no_trade("outside_ote")

    # --- Stop and target --------------------------------------------------
    buffer_distance = atr * config.stop_buffer_atr
    digits = spec.digits

    if direction is Side.BUY:
        raw_stop = sweep.extreme - buffer_distance
        stop = quantize_price(raw_stop, digits, ROUND_DOWN)
    else:
        raw_stop = sweep.extreme + buffer_distance
        stop = quantize_price(raw_stop, digits, ROUND_UP)

    stop_distance = abs(entry - stop)
    policy_floor = spec.point * Decimal(max(context.min_stop_distance_points, spec.stops_level))
    if stop_distance < policy_floor:
        # Widen to policy rather than propose a stop the risk engine must reject
        # (build.md §9.1: the agent proposes within policy).
        stop = (
            quantize_price(entry - policy_floor, digits, ROUND_DOWN)
            if direction is Side.BUY
            else quantize_price(entry + policy_floor, digits, ROUND_UP)
        )
        stop_distance = abs(entry - stop)

    if stop_distance <= ZERO:
        return no_trade("degenerate_stop")

    opposing = buy_side if direction is Side.BUY else sell_side
    target_level = nearest_target(opposing, from_price=entry, direction=direction)

    reward = None
    if target_level is not None:
        reward = abs(target_level - entry) / stop_distance
        if reward < config.min_reward_multiple:
            target_level = None

    if not evaluation.check(
        "liquidity_target", target_level is not None, required=conditions.require_liquidity_target
    ):
        return no_trade("no_liquidity_target")

    if target_level is None:
        distance = stop_distance * config.fallback_reward_multiple
        target_level = entry + distance if direction is Side.BUY else entry - distance

    target = quantize_price(target_level, digits, ROUND_UP if direction is Side.BUY else ROUND_DOWN)
    if target <= ZERO or stop <= ZERO:
        return no_trade("degenerate_price_levels")

    features = snapshot_of(
        range_position=position,
        retracement=retracement,
        swept_level=sweep.swept_level,
        sweep_direction=sweep.direction,
        fvg_lower=gap.lower if gap else None,
        fvg_upper=gap.upper if gap else None,
        displacement_ratio=gap.displacement_ratio if gap else None,
        order_block_low=order_block.low if order_block else None,
        order_block_high=order_block.high if order_block else None,
        liquidity_target=target,
    )

    reason_codes = (
        f"killzone:{killzone.value.lower()}",
        f"swept:{sweep.swept_level}",
        f"structure:{structure.state.value.lower()}",
        f"range:{position.value.lower()}",
        f"retracement:{retracement.quantize(Decimal('0.01'))}",
    )

    intent = TradeIntent(
        intent_id=uuid5(
            NAMESPACE_URL,
            f"crumblr:intent:{STRATEGY_ID}:{STRATEGY_VERSION}:{features.feature_snapshot_id}",
        ),
        strategy_id=STRATEGY_ID,
        strategy_version=STRATEGY_VERSION,
        model_version=None,
        symbol=snapshot.symbol,
        side=direction,
        created_at_utc=now,
        expires_at_utc=now + timedelta(seconds=config.intent_lifetime_seconds),
        entry_type=EntryType.MARKET,
        reference_price=entry,
        stop_loss_price=stop,
        take_profit_price=target,
        confidence=_confidence(len(evaluation.met), gap),
        reason_codes=reason_codes,
        requested_risk_fraction=context.requested_risk_fraction,
        feature_snapshot_id=features.feature_snapshot_id,
    )

    return (
        StrategyDecision(
            side=direction,
            confidence=intent.confidence,
            reason_codes=reason_codes,
            intent=intent,
        ),
        features,
    )


def _impulse_leg(
    bars: tuple[Bar, ...], *, sweep: LiquiditySweep, direction: Side
) -> DealingRange | None:
    """The leg from the swept level to the extreme the displacement reached.

    This is the range a retracement is measured against. For a long it runs
    from the low that was swept up to the highest point reached since; for a
    short, the mirror.
    """
    leg = bars[sweep.bar_index :]
    if not leg:
        return None

    if direction is Side.BUY:
        low = sweep.extreme
        high = max(bar.high for bar in leg)
        low_time, high_time = sweep.time_utc, leg[-1].open_time_utc
    else:
        high = sweep.extreme
        low = min(bar.low for bar in leg)
        low_time, high_time = leg[-1].open_time_utc, sweep.time_utc

    if high <= low:
        return None
    return DealingRange(low=low, high=high, low_time_utc=low_time, high_time_utc=high_time)


def _confidence(conditions_met: int, gap: FairValueGap | None) -> float:
    """Confidence from how much of the model actually lined up.

    Deliberately coarse. A number derived from a handful of boolean conditions
    is an ordering, not a probability, and calling it one would invite the
    supervisor's calibration checks to be read as meaningful before any
    calibration has been done.
    """
    base = min(conditions_met / 8.0, 1.0)
    if gap is not None and gap.displacement_ratio > Decimal("2.5"):
        base = min(base + 0.1, 1.0)
    return round(base, 4)


def _feature_id(snapshot: MarketSnapshot) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        f"crumblr:ict-features:{snapshot.symbol}:{snapshot.event_time_utc.isoformat()}",
    )


def evaluate(
    snapshot: MarketSnapshot,
    bars: tuple[Bar, ...],
    spec: InstrumentSpec,
    context: AgentContext,
    config: IctConfig | None = None,
) -> StrategyOutcome:
    """Run the entry model, in the shape the orchestrator expects."""
    if len(bars) < MINIMUM_BARS:
        return StrategyOutcome(
            decision=StrategyDecision(
                side=Side.FLAT, confidence=0.0, reason_codes=("warming_up",), intent=None
            ),
            features=None,
        )
    decision, features = _evaluate(snapshot, bars, spec, context, config)
    return StrategyOutcome(decision=decision, features=features)
