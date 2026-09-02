"""agent_gateway/market_context.py -- the strategy-neutral outbound
context payload (feedback.1.28 section 3, F-066).
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from crumblr.agent_gateway.market_context import (
    DEFAULT_MAX_BARS,
    AgentInstrumentFacts,
    AgentMarketContextProvenance,
    AgentMarketContextV1,
    AgentMarketData,
    AgentPlatformState,
    build_agent_market_context_v1,
)
from crumblr.domain.enums import (
    KillSwitchState,
    ReconciliationStatus,
    SessionState,
)
from tests.conftest import FIXED_NOW, make_bar, make_instrument_spec, make_snapshot


def build(**overrides: Any) -> AgentMarketContextV1:
    fields: dict[str, Any] = {
        "context_id": uuid4(),
        "content_hash": "content-hash-abc",
        "assignment_id": uuid4(),
        "strategy_artifact_id": uuid4(),
        "strategy_artifact_hash": "artifact-hash-v1",
        "issued_at_utc": FIXED_NOW,
        "expires_at_utc": FIXED_NOW + timedelta(minutes=5),
        "snapshot": make_snapshot(),
        "spec": make_instrument_spec(),
        "session_state": SessionState.OPEN,
        "safety_state": KillSwitchState.RUNNING,
        "reconciliation_status": ReconciliationStatus.MATCHED,
        "feature_snapshot_id": uuid4(),
        "open_position_count": 0,
    }
    fields.update(overrides)
    return build_agent_market_context_v1(**fields)


class TestBinding:
    def test_provenance_carries_the_exact_binding_fields(self) -> None:
        context_id = uuid4()
        assignment_id = uuid4()
        strategy_artifact_id = uuid4()
        context = build(
            context_id=context_id,
            content_hash="hash-xyz",
            assignment_id=assignment_id,
            strategy_artifact_id=strategy_artifact_id,
            strategy_artifact_hash="artifact-hash-v2",
        )
        assert context.provenance == AgentMarketContextProvenance(
            context_id=context_id,
            content_hash="hash-xyz",
            assignment_id=assignment_id,
            strategy_artifact_id=strategy_artifact_id,
            strategy_artifact_hash="artifact-hash-v2",
            issued_at_utc=FIXED_NOW,
            expires_at_utc=FIXED_NOW + timedelta(minutes=5),
        )


class TestMarket:
    def test_market_data_is_forwarded_from_the_snapshot_unaltered(self) -> None:
        snapshot = make_snapshot(bid=Decimal("1.08500"), ask=Decimal("1.08512"))
        context = build(snapshot=snapshot)
        assert context.market.bid == Decimal("1.08500")
        assert context.market.ask == Decimal("1.08512")
        assert context.market.canonical_symbol == snapshot.symbol
        assert context.market.market_snapshot_id == snapshot.snapshot_id
        assert context.market.data_quality == snapshot.data_quality

    def test_bars_are_bounded_to_max_bars(self) -> None:
        many_bars = tuple(
            make_bar(open_time_utc=FIXED_NOW - timedelta(minutes=5 * i)) for i in range(10)
        )
        snapshot = make_snapshot(bars=many_bars)
        context = build(snapshot=snapshot, max_bars=3)
        assert context.market.bars_count == 3
        assert context.market.bars == many_bars[-3:]

    def test_default_max_bars_matches_the_internal_strategy_history_window(self) -> None:
        assert DEFAULT_MAX_BARS == 400

    def test_max_bars_zero_yields_zero_bars_not_all_of_them(self) -> None:
        """Manual review finding (the automated self-review pass could not
        run this session): `snapshot.bars[-0:]` is `snapshot.bars[0:]` --
        ALL bars, not zero -- since `-0 == 0` in Python slicing."""
        many_bars = tuple(
            make_bar(open_time_utc=FIXED_NOW - timedelta(minutes=5 * i)) for i in range(10)
        )
        snapshot = make_snapshot(bars=many_bars)
        context = build(snapshot=snapshot, max_bars=0)
        assert context.market.bars == ()
        assert context.market.source_bar_ids == ()

    def test_a_negative_max_bars_is_refused(self) -> None:
        with pytest.raises(ValueError, match="max_bars"):
            build(max_bars=-1)

    def test_source_bar_ids_are_derived_and_stable(self) -> None:
        bar = make_bar(open_time_utc=FIXED_NOW)
        snapshot = make_snapshot(bars=(bar,), symbol="EUR/USD", timeframe="M5")
        context = build(snapshot=snapshot)
        assert context.market.source_bar_ids == (f"EUR/USD:M5:{FIXED_NOW.isoformat()}",)

    def test_source_bar_ids_match_bars_one_to_one(self) -> None:
        bars = tuple(make_bar(open_time_utc=FIXED_NOW - timedelta(minutes=5 * i)) for i in range(5))
        snapshot = make_snapshot(bars=bars)
        context = build(snapshot=snapshot, max_bars=5)
        assert len(context.market.source_bar_ids) == len(context.market.bars)


class TestInstrument:
    def test_instrument_facts_are_forwarded_from_the_spec_unaltered(self) -> None:
        spec = make_instrument_spec(digits=5, stops_level=10)
        context = build(spec=spec)
        assert context.instrument.digits == 5
        assert context.instrument.stops_level == 10
        assert context.instrument.broker_symbol == spec.broker_symbol
        assert context.instrument.spec_version == spec.spec_version


class TestPlatformState:
    def test_platform_state_is_forwarded_unaltered(self) -> None:
        feature_snapshot_id = uuid4()
        context = build(
            session_state=SessionState.CLOSED,
            safety_state=KillSwitchState.HALTED,
            reconciliation_status=ReconciliationStatus.UNKNOWN,
            feature_snapshot_id=feature_snapshot_id,
            open_position_count=2,
            open_risk_fraction=Decimal("0.01"),
        )
        assert context.platform_state == AgentPlatformState(
            session_state=SessionState.CLOSED,
            safety_state=KillSwitchState.HALTED,
            reconciliation_status=ReconciliationStatus.UNKNOWN,
            feature_snapshot_id=feature_snapshot_id,
            open_position_count=2,
            open_risk_fraction=Decimal("0.01"),
        )

    def test_open_risk_fraction_defaults_to_none_not_zero(self) -> None:
        """A caller that has not supplied a real exposure figure gets an
        honest "unknown", never a silently-permissive zero."""
        context = build()
        assert context.platform_state.open_risk_fraction is None


class TestStrategyNeutrality:
    """The exact negative list `feedback.1.28.md` section 3 names: none of
    these tokens may ever appear as a field name anywhere in this schema.
    A future edit that reintroduces strategy-specific concepts (exactly
    the AG-015 mistake) fails this test immediately, structurally, rather
    than depending on a reviewer noticing."""

    FORBIDDEN_TOKENS = (
        "sweep",
        "fvg",
        "fair_value_gap",
        "mss",
        "pivot",
        "ote",
        "setup",
        "regime",
        "liquidity",
    )

    def test_no_schema_field_name_names_a_strategy_concept(self) -> None:
        field_names: set[str] = set()
        for model in (
            AgentMarketContextV1,
            AgentMarketContextProvenance,
            AgentMarketData,
            AgentInstrumentFacts,
            AgentPlatformState,
        ):
            field_names.update(model.model_fields.keys())

        for token in self.FORBIDDEN_TOKENS:
            offenders = [name for name in field_names if token in name.lower()]
            assert offenders == [], f"strategy-specific token {token!r} found in {offenders}"

    def test_the_contract_rejects_an_unknown_extra_field(self) -> None:
        """`Contract`'s `extra="forbid"` -- nothing can be smuggled onto
        this schema informally, including a strategy-specific field
        someone adds without updating the negative-list test above."""
        with pytest.raises(ValidationError):
            AgentPlatformState(
                session_state=SessionState.OPEN,
                safety_state=KillSwitchState.RUNNING,
                reconciliation_status=ReconciliationStatus.MATCHED,
                feature_snapshot_id=uuid4(),
                open_position_count=0,
                liquidity_sweep_detected=True,  # type: ignore[call-arg]
            )


class TestImmutability:
    def test_the_context_is_frozen(self) -> None:
        context = build()
        with pytest.raises(ValidationError):
            context.schema_version = "2.0"  # type: ignore[assignment]
