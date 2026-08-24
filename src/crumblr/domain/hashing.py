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
