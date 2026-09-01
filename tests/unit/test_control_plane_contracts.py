"""Risk gateway and supervisor contracts (build.md §6.3, §8, §10).

The separation of powers in build.md §1 is only real if it is structural. These
tests assert the structure: the supervisor has no field with which to trade,
and the risk engine cannot half-approve anything.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from crumblr.domain.enums import (
    DataQuality,
    EntryType,
    IncidentSeverity,
    ReasonCode,
    RiskVerdict,
    Side,
    SupervisorVerdict,
)
from crumblr.domain.hashing import mt5_magic_number
from crumblr.domain.models import Incident, SupervisorDecision
from tests.conftest import (
    FIXED_NOW,
    make_approved_order,
    make_bar,
    make_incident,
    make_instrument_spec,
    make_risk_decision,
    make_snapshot,
    make_supervisor_decision,
)


class TestRiskDecisionConsistency:
    def test_pass_must_carry_an_approved_volume(self) -> None:
        with pytest.raises(ValidationError, match="must carry an approved_volume"):
            make_risk_decision(approved_volume=None)

    def test_pass_must_record_the_stop_distance_used(self) -> None:
        with pytest.raises(ValidationError, match="must record the stop distance"):
            make_risk_decision(stop_distance_points=None)

    @pytest.mark.parametrize("verdict", [RiskVerdict.BLOCK, RiskVerdict.HALT])
    def test_refusal_must_not_carry_a_volume(self, verdict: RiskVerdict) -> None:
        with pytest.raises(ValidationError, match="must not carry a volume"):
            make_risk_decision(verdict=verdict, reason_codes=(ReasonCode.SPREAD_TOO_WIDE,))

    @pytest.mark.parametrize("verdict", [RiskVerdict.BLOCK, RiskVerdict.HALT])
    def test_refusal_must_state_a_machine_readable_reason(self, verdict: RiskVerdict) -> None:
        with pytest.raises(ValidationError, match="must carry reason codes"):
            make_risk_decision(
                verdict=verdict,
                reason_codes=(),
                approved_volume=None,
                stop_distance_points=None,
            )

    def test_a_blocked_decision_is_valid_with_reasons(self) -> None:
        decision = make_risk_decision(
            verdict=RiskVerdict.BLOCK,
            reason_codes=(ReasonCode.DAILY_LOSS_LIMIT, ReasonCode.SYSTEM_HALTED),
            approved_volume=None,
            stop_distance_points=None,
            risk_amount=None,
        )
        assert decision.approved_volume is None
        assert ReasonCode.DAILY_LOSS_LIMIT in decision.reason_codes

    def test_reason_codes_must_come_from_the_closed_vocabulary(self) -> None:
        with pytest.raises(ValidationError):
            make_risk_decision(
                verdict=RiskVerdict.BLOCK,
                reason_codes=("made_up_reason",),
                approved_volume=None,
                stop_distance_points=None,
            )


class TestSupervisorIsVetoOnly:
    """build.md §10.1: 'the evaluator should not rewrite a BUY into a SELL'."""

    @pytest.mark.parametrize(
        "trading_field",
        ["side", "volume", "approved_volume", "lot_size", "price", "stop_loss_price", "symbol"],
    )
    def test_supervisor_has_no_field_that_could_place_a_trade(self, trading_field: str) -> None:
        assert trading_field not in SupervisorDecision.model_fields

    @pytest.mark.parametrize("verdict", [SupervisorVerdict.VETO, SupervisorVerdict.HALT])
    def test_refusal_must_state_a_reason(self, verdict: SupervisorVerdict) -> None:
        with pytest.raises(ValidationError, match="must carry reason codes"):
            make_supervisor_decision(verdict=verdict, reason_codes=())

    def test_approval_needs_no_reason_codes(self) -> None:
        assert make_supervisor_decision().reason_codes == ()

    def test_veto_is_valid_with_a_reason(self) -> None:
        decision = make_supervisor_decision(
            verdict=SupervisorVerdict.VETO,
            reason_codes=(ReasonCode.UNKNOWN_REGIME,),
        )
        assert decision.verdict is SupervisorVerdict.VETO


class TestMarketSnapshotIntegrity:
    def test_crossed_quote_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="crossed quote"):
            make_snapshot(bid=Decimal("1.08512"), ask=Decimal("1.08500"))

    def test_equal_bid_and_ask_is_allowed(self) -> None:
        """A zero spread is unusual but not impossible; it is not corrupt data."""
        snapshot = make_snapshot(bid=Decimal("1.08500"), ask=Decimal("1.08500"), spread_points=0)
        assert snapshot.spread_points == 0

    def test_negative_price_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            make_snapshot(bid=Decimal("-1.0"))

    def test_mid_sits_between_bid_and_ask(self) -> None:
        snapshot = make_snapshot()
        assert snapshot.bid < snapshot.mid < snapshot.ask

    def test_clock_skew_is_reported_not_hidden(self) -> None:
        snapshot = make_snapshot(received_time_utc=FIXED_NOW - timedelta(milliseconds=40))
        assert snapshot.ingest_latency_ms == -40

    def test_quality_flag_is_from_the_closed_vocabulary(self) -> None:
        assert make_snapshot(data_quality=DataQuality.SUSPECT).data_quality is DataQuality.SUSPECT
        with pytest.raises(ValidationError):
            make_snapshot(data_quality="PROBABLY_FINE")


class TestBarIntegrity:
    def test_high_below_low_is_refused(self) -> None:
        with pytest.raises(ValidationError, match=r"high .* is below low"):
            make_bar(high=Decimal("1.08300"), low=Decimal("1.08400"))

    def test_high_below_close_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="high is below the open/close range"):
            make_bar(high=Decimal("1.08520"), close=Decimal("1.08550"))

    def test_low_above_open_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="low is above the open/close range"):
            make_bar(low=Decimal("1.08520"), open=Decimal("1.08500"))

    def test_doji_bar_is_valid(self) -> None:
        flat = Decimal("1.08500")
        assert make_bar(open=flat, high=flat, low=flat, close=flat).high == flat


class TestInstrumentSpecVersioning:
    """build.md §7: refresh specs at startup and detect changes."""

    def test_identical_specs_share_a_version(self) -> None:
        first = make_instrument_spec()
        second = make_instrument_spec(captured_at_utc=FIXED_NOW + timedelta(days=1))
        assert first.spec_version == second.spec_version

    def test_a_changed_spec_produces_a_new_version(self) -> None:
        baseline = make_instrument_spec()
        widened = make_instrument_spec(stops_level=30)
        assert widened.spec_version != baseline.spec_version

    def test_filling_mode_order_does_not_affect_the_version(self) -> None:
        assert (
            make_instrument_spec(filling_modes=("FOK", "IOC")).spec_version
            == make_instrument_spec(filling_modes=("IOC", "FOK")).spec_version
        )

    def test_a_tick_value_fluctuation_alone_does_not_change_the_version(self) -> None:
        """F-039: tick_value drifts live with the cross-currency rate, not broker policy."""
        first = make_instrument_spec(tick_value=Decimal("0.8568539749455898"))
        second = make_instrument_spec(tick_value=Decimal("0.8571002210000000"))
        assert first.spec_version == second.spec_version

    def test_inverted_volume_bounds_are_refused(self) -> None:
        with pytest.raises(ValidationError, match=r"volume_min .* exceeds volume_max"):
            make_instrument_spec(volume_min=Decimal("10"), volume_max=Decimal("1"))


class TestIncidentPromotionBlocking:
    """status.md §9: a SEV-0 or unresolved SEV-1 blocks promotion."""

    def _incident(self, severity: IncidentSeverity, *, closed: bool) -> Incident:
        return make_incident(
            severity=severity,
            closed_at_utc=FIXED_NOW + timedelta(hours=2) if closed else None,
            root_cause="missing idempotency key" if closed else None,
        )

    def test_open_sev1_blocks_promotion(self) -> None:
        assert self._incident(IncidentSeverity.SEV_1, closed=False).blocks_promotion

    def test_closed_sev1_no_longer_blocks(self) -> None:
        assert not self._incident(IncidentSeverity.SEV_1, closed=True).blocks_promotion

    def test_sev0_blocks_even_once_closed(self) -> None:
        assert self._incident(IncidentSeverity.SEV_0, closed=True).blocks_promotion

    def test_sev3_never_blocks(self) -> None:
        assert not self._incident(IncidentSeverity.SEV_3, closed=False).blocks_promotion

    def test_closing_an_incident_requires_a_root_cause(self) -> None:
        with pytest.raises(ValidationError, match="must record a root cause"):
            make_incident(closed_at_utc=FIXED_NOW + timedelta(hours=1), root_cause=None)

    def test_closing_before_opening_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="closed_at_utc precedes opened_at_utc"):
            make_incident(
                closed_at_utc=FIXED_NOW - timedelta(hours=1),
                root_cause="clock went backwards",
            )


class TestApprovedOrder:
    """The only object the execution engine acts on (build.md §7 invariant 2)."""

    def test_a_flat_side_is_not_an_order(self) -> None:
        with pytest.raises(ValidationError, match="must be directional"):
            make_approved_order(side=Side.FLAT)

    def test_a_limit_order_needs_a_price(self) -> None:
        with pytest.raises(ValidationError, match="requires an explicit price"):
            make_approved_order(entry_type=EntryType.LIMIT, price=None)

    def test_a_limit_order_with_a_price_is_valid(self) -> None:
        order = make_approved_order(entry_type=EntryType.LIMIT, price=Decimal("1.08450"))
        assert order.price == Decimal("1.08450")

    def test_a_market_order_needs_no_price(self) -> None:
        assert make_approved_order().price is None

    def test_an_order_must_carry_a_stop(self) -> None:
        with pytest.raises(ValidationError):
            make_approved_order(stop_loss_price=None)

    def test_expiry_must_follow_creation(self) -> None:
        with pytest.raises(ValidationError, match="expires_at_utc must be after"):
            make_approved_order(expires_at_utc=FIXED_NOW - timedelta(seconds=1))

    def test_the_idempotency_key_is_carried_on_the_order(self) -> None:
        order = make_approved_order()
        assert order.order_request_id is not None

    def test_magic_number_is_derived_from_the_idempotency_key(self) -> None:
        """Core critical path item 5: the same `order_request_id` must

        always produce the same MT5 `magic` — a future `order_send`
        caller and a future reconciliation reader have to agree on it
        independently, without either persisting it separately."""
        request_id = uuid4()
        first = make_approved_order(order_request_id=request_id)
        second = make_approved_order(order_request_id=request_id)

        assert first.magic_number == mt5_magic_number(request_id)
        assert first.magic_number == second.magic_number

    def test_different_orders_get_different_magic_numbers(self) -> None:
        first = make_approved_order()
        second = make_approved_order()
        assert first.magic_number != second.magic_number

    def test_magic_number_is_a_non_negative_31_bit_value(self) -> None:
        """No real Pepperstone/MT5 evidence exists for this field's actual

        constraints (submitting a real order to observe one is exactly
        what this platform must not yet do) — deliberately conservative
        rather than assuming a wider range is safe."""
        order = make_approved_order()
        assert 0 <= order.magic_number <= 0x7FFFFFFF
