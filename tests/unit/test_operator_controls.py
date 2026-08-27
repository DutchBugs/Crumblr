"""The three operator controls (build.md §8.2, review finding F-008).

The controls exist so that an operator can act precisely under pressure. Most
of these tests therefore assert what each action does *not* do — that halting
does not liquidate, that flattening does not silently stop trading. Coupling
them would be the easy mistake, and it is the one that gets somebody the
opposite of what they asked for at the worst moment.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from crumblr.domain.enums import EntryType, Environment, KillSwitchState, ReasonCode, Side
from crumblr.domain.models import ApprovedOrder
from crumblr.domain.timeutils import utc_now
from crumblr.market_data.synthetic import SyntheticMarketConfig, generate_ticks
from crumblr.mt5_gateway.simulated import SimulatedBroker
from crumblr.risk.kill_switch import KillSwitch
from crumblr.risk.operator_controls import OperatorAction, OperatorControls
from crumblr.risk.safety_state import InMemorySafetyStateStore, SafetyState
from tests.conftest import make_instrument_spec

SPEC = make_instrument_spec()


def running_switch() -> KillSwitch:
    """A switch that starts explicitly RUNNING, as after an operator reset."""
    return KillSwitch.on_startup(
        InMemorySafetyStateStore(
            SafetyState(
                state=KillSwitchState.RUNNING,
                reason_codes=(),
                recorded_at_utc=utc_now(),
            )
        )
    )


def broker_with_open_position() -> SimulatedBroker:
    broker = SimulatedBroker(SPEC, starting_balance=Decimal("10000"))
    ticks = list(generate_ticks(SyntheticMarketConfig(bar_count=5), SPEC))
    broker.advance(ticks[0])
    from uuid import uuid4

    broker.order_send(
        ApprovedOrder(
            order_request_id=uuid4(),
            intent_id=uuid4(),
            intent_risk_decision_id=uuid4(),
            supervisor_decision_id=uuid4(),
            broker_symbol=SPEC.broker_symbol,
            side=Side.BUY,
            entry_type=EntryType.MARKET,
            volume=Decimal("0.05"),
            stop_loss_price=ticks[0].bid - Decimal("0.00500"),
            take_profit_price=ticks[0].ask + Decimal("0.01000"),
            max_slippage_points=20,
            created_at_utc=ticks[0].received_time_utc,
            expires_at_utc=ticks[0].received_time_utc.replace(year=2030),
            environment=Environment.PAPER,
        )
    )
    return broker


def controls() -> tuple[OperatorControls, KillSwitch, SimulatedBroker]:
    switch = running_switch()
    broker = broker_with_open_position()
    return OperatorControls(switch, broker), switch, broker


class TestHaltNewOrders:
    def test_halting_stops_new_orders(self) -> None:
        control, switch, _ = controls()
        control.halt_new_orders(operator="levi", reason="spread behaving oddly")
        assert switch.is_halted

    def test_halting_does_not_close_positions(self) -> None:
        """The whole reason these are three controls and not one."""
        control, _, broker = controls()
        assert len(broker.positions()) == 1
        control.halt_new_orders(operator="levi", reason="spread behaving oddly")
        assert len(broker.positions()) == 1, "halting must not liquidate the book"

    def test_the_halt_carries_the_operator_and_reason(self) -> None:
        control, switch, _ = controls()
        control.halt_new_orders(operator="levi", reason="broker feed unstable")
        assert switch.history[-1].tripped_by == "levi"
        assert switch.history[-1].detail == "broker feed unstable"
        assert ReasonCode.MANUAL_HALT in switch.history[-1].reason_codes


class TestFlattenPositions:
    def test_flattening_closes_every_position(self) -> None:
        control, _, broker = controls()
        record = control.flatten_positions(operator="levi", reason="stepping away")
        assert broker.positions() == ()
        assert len(record.affected_tickets) == 1

    def test_flattening_does_not_halt(self) -> None:
        """An operator who wants both must ask for both."""
        control, switch, _ = controls()
        control.flatten_positions(operator="levi", reason="stepping away")
        assert not switch.is_halted, "flattening must not silently stop trading"

    def test_the_closed_trade_records_the_operator_reason(self) -> None:
        control, _, broker = controls()
        control.flatten_positions(operator="levi", reason="stepping away")
        assert broker.closed_trades[-1].exit_reason.startswith("operator flatten")

    def test_flattening_an_empty_book_is_harmless(self) -> None:
        switch = running_switch()
        broker = SimulatedBroker(SPEC, starting_balance=Decimal("10000"))
        broker.advance(next(iter(generate_ticks(SyntheticMarketConfig(bar_count=2), SPEC))))
        record = OperatorControls(switch, broker).flatten_positions(
            operator="levi", reason="precaution"
        )
        assert record.affected_tickets == ()


class TestCancelPendingOrders:
    def test_cancelling_does_not_halt_or_flatten(self) -> None:
        control, switch, broker = controls()
        control.cancel_pending_orders(operator="levi", reason="stale entries")
        assert not switch.is_halted
        assert len(broker.positions()) == 1


class TestEveryActionIsAttributable:
    @pytest.mark.parametrize(
        "action",
        ["halt_new_orders", "cancel_pending_orders", "flatten_positions"],
    )
    def test_an_unnamed_operator_is_refused(self, action: str) -> None:
        control, _, _ = controls()
        with pytest.raises(ValueError, match="identified operator"):
            getattr(control, action)(operator="   ", reason="because")

    @pytest.mark.parametrize(
        "action",
        ["halt_new_orders", "cancel_pending_orders", "flatten_positions"],
    )
    def test_an_unstated_reason_is_refused(self, action: str) -> None:
        control, _, _ = controls()
        with pytest.raises(ValueError, match="stated reason"):
            getattr(control, action)(operator="levi", reason="")


class TestAuditLog:
    def test_each_action_is_logged_separately(self) -> None:
        control, _, _ = controls()
        control.flatten_positions(operator="levi", reason="stepping away")
        control.halt_new_orders(operator="levi", reason="and stopping for the day")

        assert [entry.action for entry in control.audit_log] == [
            OperatorAction.FLATTEN_POSITIONS,
            OperatorAction.HALT_NEW_ORDERS,
        ]

    def test_the_log_records_who_and_why(self) -> None:
        control, _, _ = controls()
        control.halt_new_orders(operator="levi", reason="macro release imminent")
        entry = control.audit_log[-1]
        assert entry.operator == "levi"
        assert entry.reason == "macro release imminent"

    def test_a_reset_is_logged_as_its_own_action(self) -> None:
        control, switch, _ = controls()
        control.halt_new_orders(operator="levi", reason="checking something")
        control.reset_halt(operator="levi", incident_note="INC-9 closed, feed verified")
        assert not switch.is_halted
        assert control.audit_log[-1].action is OperatorAction.RESET_HALT

    def test_a_reset_still_requires_an_incident_note(self) -> None:
        control, _, _ = controls()
        control.halt_new_orders(operator="levi", reason="checking something")
        with pytest.raises(ValueError):
            control.reset_halt(operator="levi", incident_note="  ")
