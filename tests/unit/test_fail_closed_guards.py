"""The paths that refuse rather than guess.

build.md §20: "No new exposure. Reconcile first." Every guard below turns an
ambiguous input into a loud failure. They are cheap to write and they are the
ones that matter when something upstream goes wrong at 03:00.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import yaml
from pydantic import ValidationError

from crumblr.config import load_config
from crumblr.domain.enums import Environment, Side
from crumblr.domain.hashing import canonical_json, fingerprint
from crumblr.domain.models import DecisionCapsule, PositionState
from crumblr.domain.money import points_to_price
from tests.conftest import (
    FIXED_NOW,
    make_instrument_spec,
    make_intent,
    make_risk_decision,
    make_supervisor_decision,
    paper_config_payload,
)


class TestFingerprintRefusesAmbiguousInput:
    """A fingerprint that silently accepts an unstable encoding proves nothing."""

    def test_float_cannot_be_fingerprinted(self) -> None:
        with pytest.raises(TypeError, match="float cannot be fingerprinted"):
            fingerprint({"price": 1.085})

    def test_naive_datetime_cannot_be_fingerprinted(self) -> None:
        with pytest.raises(TypeError, match="naive datetime cannot be fingerprinted"):
            fingerprint({"t": datetime(2026, 8, 17, 12, 0)})  # noqa: DTZ001

    def test_unsupported_type_is_refused(self) -> None:
        with pytest.raises(TypeError, match="no canonical encoding"):
            fingerprint({"thing": object()})

    def test_enums_encode_by_value(self) -> None:
        class Colour(Enum):
            RED = "red"

        assert canonical_json({"c": Colour.RED}) == canonical_json({"c": "red"})

    def test_nested_structures_are_encoded(self) -> None:
        payload = {"outer": {"inner": [1, Decimal("2.50"), None, True]}}
        assert fingerprint(payload) == fingerprint(
            {"outer": {"inner": [1, Decimal("2.5"), None, True]}}
        )

    def test_aware_datetimes_encode_by_instant(self) -> None:
        utc = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)
        assert fingerprint({"t": utc}) == fingerprint({"t": utc})


class TestInstrumentSpecBounds:
    def test_volume_step_larger_than_max_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="volume_step exceeds volume_max"):
            make_instrument_spec(volume_max=Decimal("1"), volume_step=Decimal("5"))


class TestPositionState:
    def test_an_open_position_cannot_be_flat(self) -> None:
        with pytest.raises(ValidationError, match="cannot have side FLAT"):
            PositionState(
                ticket=1,
                broker_symbol="EURUSD",
                side=Side.FLAT,
                volume=Decimal("0.05"),
                open_price=Decimal("1.08500"),
                opened_at_utc=FIXED_NOW,
                profit=Decimal("0"),
                swap=Decimal("0"),
                observed_at_utc=FIXED_NOW,
            )


class TestPointConversionGuards:
    def test_zero_point_size_is_refused(self) -> None:
        with pytest.raises(ValueError, match="point size must be positive"):
            points_to_price(100, Decimal("0"))

    def test_negative_point_size_is_refused(self) -> None:
        with pytest.raises(ValueError, match="point size must be positive"):
            points_to_price(100, Decimal("-0.00001"))


class TestConfigFileGuards:
    def test_an_empty_overlay_is_treated_as_no_overrides(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "base.yaml").write_text(
            yaml.safe_dump(paper_config_payload()), encoding="utf-8"
        )
        (config_dir / "paper.yaml").write_text("", encoding="utf-8")
        assert (
            load_config(Environment.PAPER, config_dir=config_dir).environment is Environment.PAPER
        )

    def test_a_non_mapping_config_is_refused(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "base.yaml").write_text("- just\n- a\n- list\n", encoding="utf-8")
        (config_dir / "paper.yaml").write_text("{}", encoding="utf-8")
        with pytest.raises(ValueError, match="must contain a YAML mapping"):
            load_config(Environment.PAPER, config_dir=config_dir)

    def test_a_missing_base_file_is_refused(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        with pytest.raises(FileNotFoundError, match=r"base\.yaml"):
            load_config(Environment.PAPER, config_dir=config_dir)


class TestDecisionCapsuleProvenance:
    """build.md §25.2: prove exactly what created a decision."""

    def _capsule(self, **overrides: Any) -> DecisionCapsule:
        intent = make_intent()
        fields: dict[str, Any] = {
            "capsule_id": uuid4(),
            "occurred_at_utc": FIXED_NOW,
            "correlation_id": uuid4(),
            "canonical_symbol": "EUR/USD",
            "broker_symbol": "EURUSD",
            "market_snapshot_id": uuid4(),
            "feature_set_version": "features-v1",
            "feature_values_hash": "abc123",
            "strategy_version": "0.1.0",
            "model_version": None,
            "trade_intent": intent,
            "risk_config_version": "cfg-v1",
            "risk_decision": make_risk_decision(intent.intent_id),
            "supervisor_decision": make_supervisor_decision(intent.intent_id),
            "code_commit": "deadbeef",
            "environment": Environment.PAPER,
        }
        fields.update(overrides)
        return DecisionCapsule(**fields)

    def test_identical_provenance_produces_one_fingerprint(self) -> None:
        snapshot_id = uuid4()
        first = self._capsule(market_snapshot_id=snapshot_id, occurred_at_utc=FIXED_NOW)
        second = self._capsule(
            market_snapshot_id=snapshot_id,
            occurred_at_utc=FIXED_NOW + timedelta(seconds=1),
            trade_intent=first.trade_intent,
        )
        assert first.provenance_fingerprint == second.provenance_fingerprint

    def test_a_different_code_commit_changes_the_fingerprint(self) -> None:
        baseline = self._capsule()
        changed = self._capsule(
            code_commit="cafebabe",
            market_snapshot_id=baseline.market_snapshot_id,
            trade_intent=baseline.trade_intent,
        )
        assert changed.provenance_fingerprint != baseline.provenance_fingerprint

    def test_a_different_risk_config_changes_the_fingerprint(self) -> None:
        baseline = self._capsule()
        changed = self._capsule(
            risk_config_version="cfg-v2",
            market_snapshot_id=baseline.market_snapshot_id,
            trade_intent=baseline.trade_intent,
        )
        assert changed.provenance_fingerprint != baseline.provenance_fingerprint

    def test_a_capsule_without_an_intent_is_valid(self) -> None:
        """A NO_TRADE window is still a decision worth recording."""
        capsule = self._capsule(trade_intent=None, risk_decision=None, supervisor_decision=None)
        assert capsule.trade_intent is None
        assert capsule.provenance_fingerprint

    def test_a_capsule_survives_a_json_round_trip(self) -> None:
        capsule = self._capsule()
        restored = DecisionCapsule.model_validate(capsule.model_dump(mode="json"))
        assert restored == capsule
        assert restored.provenance_fingerprint == capsule.provenance_fingerprint

    def test_a_capsule_is_immutable(self) -> None:
        capsule = self._capsule()
        with pytest.raises(ValidationError):
            capsule.code_commit = "tampered"
