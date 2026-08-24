"""Position sizing (build.md §6.2, §8.1).

This is the module the Trading Agent is not allowed to be. The agent says how
much of the account it wants at risk; the size that produces is computed here
from account equity, the actual stop distance, and the broker's own symbol
specification.

Rounding is always downward. A size rounded up is a size that risks more than
the budget authorised.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from crumblr.domain.models import InstrumentSpec
from crumblr.domain.money import ZERO


@dataclass(frozen=True)
class SizingResult:
    """Outcome of a sizing attempt, including why it produced nothing."""

    volume: Decimal | None
    risk_amount: Decimal
    loss_per_lot: Decimal
    rejection: str | None = None

    @property
    def is_tradeable(self) -> bool:
        return self.volume is not None


def loss_per_lot(stop_distance_price: Decimal, spec: InstrumentSpec) -> Decimal:
    """Account-currency loss for one lot if the stop is hit.

    Uses tick size and tick value rather than a hard-coded pip value, because
    those are the numbers the broker actually reports for the symbol.
    """
    if stop_distance_price <= ZERO:
        raise ValueError(f"stop distance must be positive, got {stop_distance_price}")
    if spec.tick_size <= ZERO:
        raise ValueError("instrument tick_size must be positive")
    return (stop_distance_price / spec.tick_size) * spec.tick_value


def normalise_volume(volume: Decimal, spec: InstrumentSpec) -> Decimal:
    """Snap a volume down to the broker's volume step and clamp to its maximum."""
    steps = (volume / spec.volume_step).to_integral_value(rounding=ROUND_DOWN)
    snapped = steps * spec.volume_step
    return min(snapped, spec.volume_max)


def size_position(
    *,
    equity: Decimal,
    risk_fraction: Decimal,
    stop_distance_price: Decimal,
    spec: InstrumentSpec,
) -> SizingResult:
    """Convert a risk budget into a broker-legal volume.

    Returns a rejection rather than a clamped-up volume when the budget cannot
    fund the broker's minimum lot: trading a size the account did not authorise
    is worse than not trading.
    """
    if equity <= ZERO:
        return SizingResult(None, ZERO, ZERO, rejection="non_positive_equity")

    risk_amount = equity * risk_fraction
    per_lot = loss_per_lot(stop_distance_price, spec)
    if per_lot <= ZERO:
        return SizingResult(None, risk_amount, per_lot, rejection="degenerate_loss_per_lot")

    raw_volume = risk_amount / per_lot
    volume = normalise_volume(raw_volume, spec)

    if volume < spec.volume_min:
        # Rounding up to volume_min would exceed the authorised risk.
        return SizingResult(
            None, risk_amount, per_lot, rejection="risk_budget_below_broker_minimum"
        )
    if volume <= ZERO:
        return SizingResult(None, risk_amount, per_lot, rejection="volume_rounds_to_zero")

    return SizingResult(volume=volume, risk_amount=risk_amount, loss_per_lot=per_lot)


def realised_risk(volume: Decimal, stop_distance_price: Decimal, spec: InstrumentSpec) -> Decimal:
    """Account-currency risk actually carried by `volume`.

    Because sizing rounds down, this is at most the requested budget — a
    property the risk engine asserts before approving.
    """
    return volume * loss_per_lot(stop_distance_price, spec)
