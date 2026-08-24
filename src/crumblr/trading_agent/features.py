"""Causal feature computation (build.md §9.3).

Every value here is derived from closed bars only. The pipeline never sees the
bar it is being asked to predict, which is the single property that separates a
backtest from a fantasy. `compute_features` takes its history explicitly rather
than reading a rolling buffer, so replay and live compute identical features
from identical inputs.
"""

from __future__ import annotations

from decimal import Decimal
from itertools import pairwise
from uuid import UUID, uuid5

from crumblr.domain.enums import Regime
from crumblr.domain.hashing import fingerprint
from crumblr.domain.models import Bar, Contract, Symbol, VersionTag
from crumblr.domain.money import ZERO, ExactDecimal
from crumblr.domain.timeutils import UtcDatetime

FEATURE_SET_VERSION = "features-v1"

EMA_FAST_PERIOD = 12
EMA_SLOW_PERIOD = 26
ATR_PERIOD = 14
ATR_BASELINE_PERIOD = 50

HIGH_VOLATILITY_RATIO = Decimal("1.60")
LOW_VOLATILITY_RATIO = Decimal("0.60")
TREND_SCORE_THRESHOLD = Decimal("0.50")

MINIMUM_BARS = ATR_BASELINE_PERIOD + ATR_PERIOD + 1
"""History required before any regime other than UNKNOWN can be claimed."""

_FEATURE_NAMESPACE = "crumblr:features"


class FeatureSnapshot(Contract):
    """Feature values for one decision window.

    The content hash lets a decision capsule prove which numbers the strategy
    actually saw, rather than which numbers we later think it should have seen.
    """

    feature_snapshot_id: UUID
    feature_set_version: VersionTag
    symbol: Symbol
    computed_at_utc: UtcDatetime
    bars_used: int

    ema_fast: ExactDecimal
    ema_slow: ExactDecimal
    atr: ExactDecimal
    atr_baseline: ExactDecimal
    volatility_ratio: ExactDecimal
    trend_score: ExactDecimal
    regime: Regime

    @property
    def feature_values_hash(self) -> str:
        return fingerprint(
            {
                "feature_set_version": self.feature_set_version,
                "ema_fast": self.ema_fast,
                "ema_slow": self.ema_slow,
                "atr": self.atr,
                "atr_baseline": self.atr_baseline,
                "volatility_ratio": self.volatility_ratio,
                "trend_score": self.trend_score,
                "regime": self.regime,
            }
        )


def _ema(values: list[Decimal], period: int) -> Decimal | None:
    """Exponential moving average, seeded with the mean of the first window."""
    if len(values) < period:
        return None
    alpha = Decimal(2) / Decimal(period + 1)
    ema = sum(values[:period], ZERO) / Decimal(period)
    for value in values[period:]:
        ema = alpha * value + (Decimal(1) - alpha) * ema
    return ema


def _true_ranges(bars: list[Bar]) -> list[Decimal]:
    """True range needs the previous close, so the first bar yields no value."""
    return [
        max(
            current.high - current.low,
            abs(current.high - previous.close),
            abs(current.low - previous.close),
        )
        for previous, current in pairwise(bars)
    ]


def _mean(values: list[Decimal]) -> Decimal:
    return sum(values, ZERO) / Decimal(len(values))


def _classify(volatility_ratio: Decimal, trend_score: Decimal) -> Regime:
    """Map the two summary statistics onto a single regime label.

    Volatility is checked first: a market moving three times its usual range is
    better described as volatile than as trending, and the supervisor is
    configured to treat unusual conditions with suspicion.
    """
    if volatility_ratio >= HIGH_VOLATILITY_RATIO:
        return Regime.HIGH_VOLATILITY
    if volatility_ratio <= LOW_VOLATILITY_RATIO:
        return Regime.LOW_VOLATILITY
    if abs(trend_score) >= TREND_SCORE_THRESHOLD:
        return Regime.TREND
    return Regime.RANGE


def compute_features(
    bars: tuple[Bar, ...],
    *,
    symbol: str,
    computed_at_utc: UtcDatetime,
) -> FeatureSnapshot | None:
    """Compute features from closed bars, or None when history is too short.

    Returning None rather than a partially-populated snapshot keeps the
    "insufficient evidence" case distinguishable from "computed and flat".
    """
    if len(bars) < MINIMUM_BARS:
        return None

    ordered = list(bars)
    closes = [bar.close for bar in ordered]

    ema_fast = _ema(closes, EMA_FAST_PERIOD)
    ema_slow = _ema(closes, EMA_SLOW_PERIOD)
    if ema_fast is None or ema_slow is None:
        return None

    ranges = _true_ranges(ordered)
    if len(ranges) < ATR_BASELINE_PERIOD:
        return None

    atr = _mean(ranges[-ATR_PERIOD:])
    atr_baseline = _mean(ranges[-ATR_BASELINE_PERIOD:])
    if atr_baseline <= ZERO:
        return None

    volatility_ratio = atr / atr_baseline
    # Separation between the two averages, expressed in units of ATR, so the
    # score is comparable across price levels and volatility regimes.
    trend_score = (ema_fast - ema_slow) / atr if atr > ZERO else ZERO

    snapshot_id = uuid5(
        UUID("6ba7b811-9dad-11d1-80b4-00c04fd430c8"),
        f"{_FEATURE_NAMESPACE}:{symbol}:{computed_at_utc.isoformat()}",
    )
    return FeatureSnapshot(
        feature_snapshot_id=snapshot_id,
        feature_set_version=FEATURE_SET_VERSION,
        symbol=symbol,
        computed_at_utc=computed_at_utc,
        bars_used=len(ordered),
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        atr=atr,
        atr_baseline=atr_baseline,
        volatility_ratio=volatility_ratio,
        trend_score=trend_score,
        regime=_classify(volatility_ratio, trend_score),
    )
