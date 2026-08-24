"""Exact numeric primitives for prices, volumes and risk fractions.

Everything monetary is `Decimal`. Binary floats are rejected at the model
boundary rather than tolerated: `Decimal(1.1)` is 1.100000000000000088817841970
012523233890533447265625, and an error of that shape in a stop-loss price is
both silent and expensive.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal
from typing import Annotated, Any

from pydantic import BeforeValidator, Field

ZERO = Decimal(0)


def _reject_binary_float(value: Any) -> Any:
    """Refuse `float` input so no model silently absorbs a binary rounding error."""
    if isinstance(value, float):
        raise ValueError("float is not accepted for exact numeric fields; pass Decimal, int or str")
    return value


ExactDecimal = Annotated[Decimal, BeforeValidator(_reject_binary_float)]
"""A Decimal that cannot be constructed from a binary float."""

Price = Annotated[Decimal, BeforeValidator(_reject_binary_float), Field(gt=ZERO)]
"""A tradeable price. Strictly positive: FX quotes are never zero or negative."""

Volume = Annotated[Decimal, BeforeValidator(_reject_binary_float), Field(gt=ZERO)]
"""A lot size, in broker volume units."""

RiskFraction = Annotated[
    Decimal, BeforeValidator(_reject_binary_float), Field(gt=ZERO, le=Decimal(1))
]
"""A fraction of account equity placed at risk, in (0, 1]."""


def tick_size_for_digits(digits: int) -> Decimal:
    """Smallest representable price increment for a symbol quoted to `digits`."""
    if digits < 0:
        raise ValueError(f"digits must be non-negative, got {digits}")
    return Decimal(1).scaleb(-digits)


def quantize_price(price: Decimal, digits: int, rounding: str = ROUND_HALF_EVEN) -> Decimal:
    """Snap `price` to the symbol's quoting precision.

    The caller passes `rounding` explicitly when direction matters — a stop must
    be rounded away from the entry so quantization can never tighten it.
    """
    return price.quantize(tick_size_for_digits(digits), rounding=rounding)


def price_to_points(distance: Decimal, point: Decimal) -> int:
    """Convert a price distance to whole broker points.

    `point` is the symbol's point size as reported by MT5 (1e-5 for a 5-digit
    EUR/USD feed). The result is signed; callers wanting a magnitude take abs().
    """
    if point <= ZERO:
        raise ValueError(f"point size must be positive, got {point}")
    return int((distance / point).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def points_to_price(points: int, point: Decimal) -> Decimal:
    """Inverse of `price_to_points`."""
    if point <= ZERO:
        raise ValueError(f"point size must be positive, got {point}")
    return Decimal(points) * point
