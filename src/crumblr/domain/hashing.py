"""Deterministic fingerprinting for decisions and specifications.

build.md §11 and §25.2 require that a stored decision can be proven to match
the inputs that produced it. That only holds if the serialisation is canonical:
the same logical content must always produce the same bytes, on any host, in
any Python process.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID


def _canonical(value: Any) -> Any:
    """Reduce a value to a JSON-safe form with a single unambiguous encoding."""
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, Decimal):
        # Normalised so 1.10 and 1.1 fingerprint identically.
        return format(value.normalize(), "f")
    if isinstance(value, float):
        raise TypeError("float cannot be fingerprinted deterministically; use Decimal")
    if isinstance(value, Enum):
        return _canonical(value.value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise TypeError("naive datetime cannot be fingerprinted")
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _canonical(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    raise TypeError(f"no canonical encoding for {type(value).__name__}")


def canonical_json(payload: dict[str, Any]) -> str:
    """Serialise `payload` to its one canonical JSON representation."""
    return json.dumps(
        _canonical(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def fingerprint(payload: dict[str, Any]) -> str:
    """SHA-256 hex digest of the canonical encoding of `payload`."""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def mt5_magic_number(order_request_id: UUID) -> int:
    """A deterministic MT5 `magic` number for one `order_request_id`.

    Core critical path item 5 (`review/adr/ADR-007-order-send-idempotence.md`):
    MT5 has no native idempotency-key concept, and `order_request_id`
    (a UUID) means nothing to the broker on its own. `magic` is the
    established MT5 mechanism that *does* survive into the broker's own
    position/order records and can be queried back — this derives one
    deterministically, so a future `order_send` caller and a future
    reconciliation reader always agree on the same value for the same
    logical order without either persisting it separately.

    Masked to 31 bits (`0` to `2_147_483_647`): always non-negative,
    fits both signed and unsigned 32-bit interpretations. No real
    Pepperstone/MT5 terminal evidence exists for this field's actual
    constraints — deliberately calling for a conservative, narrower
    width than the schema's `BigInteger` column could hold, rather than
    assuming a wider range is safe (`review/DEVIATIONS.md` D-037's own
    "decode from observation, never hardcode an MT5 assumption" rule,
    applied here to a field no observation has ever been possible for,
    since submitting a real order to generate one is exactly what this
    platform must not yet do). ~2.1 billion possible values — collision
    risk across this platform's realistic order volume is negligible,
    the same acceptance already applied to `AccountState.login_hash`'s
    narrower 64-bit truncation.
    """
    return int(fingerprint({"order_request_id": str(order_request_id)})[:8], 16) & 0x7FFFFFFF
