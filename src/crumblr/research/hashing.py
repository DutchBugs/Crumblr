"""Canonical research hashing for external JSON that may contain floats."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from crumblr.domain.hashing import fingerprint


def research_fingerprint(value: Any) -> str:
    """Hash external research JSON after lossless float string normalization.

    Core correctly forbids binary floats in authoritative financial contracts.
    External Trainer metrics arrive as JSON numbers; converting through their
    decimal string representation keeps the research hash deterministic without
    granting those metrics authority in Risk or execution.
    """
    return fingerprint(_normalize(value))


def _normalize(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {str(key): _normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    return value
