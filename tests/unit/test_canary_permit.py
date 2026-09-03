"""`CanaryPermit` domain-model validators (Phase B item B8,

`review/adr/ADR-018-canary-permit.md`). Pure contract tests — no
database. `tests/integration/test_canary_permit_store.py` covers the
durable atomic-consumption mechanism.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from crumblr.domain.enums import EntryType
from crumblr.domain.models import CanaryPermit
from tests.conftest import FIXED_NOW


def permit(**overrides: Any) -> CanaryPermit:
    fields: dict[str, Any] = {
        "permit_id": uuid4(),
        "approved_account_ref": "d9ac869767271225",
        "expected_server": "PepperstoneUK-Demo",
        "canonical_symbol": "EUR/USD",
        "entry_type": EntryType.MARKET,
        "max_requested_risk_fraction": Decimal("0.0025"),
        "issued_by": "levi",
        "reason": "first constrained DEMO canary",
        "issued_at_utc": FIXED_NOW,
        "valid_until_utc": FIXED_NOW + timedelta(hours=1),
    }
    fields.update(overrides)
    return CanaryPermit(**fields)


class TestCanaryPermitValidation:
    def test_a_well_formed_permit_constructs(self) -> None:
        assert permit().canonical_symbol == "EUR/USD"

    def test_a_non_eur_usd_symbol_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="EUR/USD"):
            permit(canonical_symbol="GBP/USD")

    def test_a_non_market_entry_type_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="MARKET"):
            permit(entry_type=EntryType.LIMIT)

    def test_a_fully_agent_driven_permit_constructs(self) -> None:
        result = permit(
            agent_id=uuid4(), assignment_id=uuid4(), strategy_artifact_hash="pivot-2-2-v1"
        )
        assert result.agent_id is not None

    def test_a_fully_internal_strategy_permit_constructs(self) -> None:
        result = permit()
        assert result.agent_id is None
        assert result.assignment_id is None
        assert result.strategy_artifact_hash is None

    def test_a_partial_agent_identity_binding_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="all-set"):
            permit(agent_id=uuid4())

    def test_a_partial_agent_identity_binding_via_assignment_only_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="all-set"):
            permit(assignment_id=uuid4())

    def test_a_permit_valid_until_before_it_was_issued_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="after issued_at_utc"):
            permit(valid_until_utc=FIXED_NOW - timedelta(minutes=1))

    def test_a_permit_valid_until_equal_to_issued_at_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="after issued_at_utc"):
            permit(valid_until_utc=FIXED_NOW)

    def test_a_validity_window_longer_than_24_hours_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="24h"):
            permit(valid_until_utc=FIXED_NOW + timedelta(hours=24, minutes=1))

    def test_a_validity_window_of_exactly_24_hours_is_accepted(self) -> None:
        result = permit(valid_until_utc=FIXED_NOW + timedelta(hours=24))
        assert result.valid_until_utc == FIXED_NOW + timedelta(hours=24)

    def test_max_requested_risk_fraction_rejects_a_binary_float(self) -> None:
        with pytest.raises(ValidationError):
            permit(max_requested_risk_fraction=0.0025)

    def test_max_requested_risk_fraction_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            permit(max_requested_risk_fraction=Decimal("0"))

    def test_max_requested_risk_fraction_cannot_exceed_one(self) -> None:
        with pytest.raises(ValidationError):
            permit(max_requested_risk_fraction=Decimal("1.5"))
