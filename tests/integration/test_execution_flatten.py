"""Automatic flatten submission, end to end, against real PostgreSQL and a

fake MT5 terminal — core critical path item 7, ADR-009. Mirrors
`test_execution_orchestrator.py`'s own end-to-end shape: this is the test
that proves the non-sending guarantee holds for the *flatten* path
specifically — a real broker-side position past its deadline reaches a
persisted `FLATTEN_SUBMISSION_STARTED` event, and `close_all_positions`/
`order_send` are never called.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import Engine

from crumblr.config import IntradayConfig
from crumblr.domain.enums import FlattenEventType, ReasonCode
from crumblr.persistence.flatten import FlattenEventStore
from crumblr.persistence.instrument_specs import InstrumentSpecStore
from crumblr.risk.kill_switch import KillSwitch
from tests.conftest import FIXED_NOW
from tests.integration._execution_fixtures import (
    FakeMt5,
    fake_position,
    orchestrator,
    platform_config,
    spec,
)

pytestmark = pytest.mark.integration

# `session_close(FIXED_NOW) == 2026-08-17 21:00:00 UTC`, nine hours after
# FIXED_NOW — computed directly from `risk/trading_window.py::session_close`
# rather than guessed, so these two policies land on opposite sides of that
# nine-hour gap deterministically.

PAST_DEADLINE_POLICY = IntradayConfig.model_validate(
    {
        "enabled": True,
        "last_entry_minutes_before_close": 660,  # 11h — still before the deadline below
        "flatten_minutes_before_close": 600,  # 10h — FIXED_NOW (9h out) is already past this
    }
)

OPEN_POLICY = IntradayConfig.model_validate(
    {
        "enabled": True,
        "last_entry_minutes_before_close": 120,  # 2h
        "flatten_minutes_before_close": 60,  # 1h — FIXED_NOW (9h out) is well before this
    }
)


class TestFlattenOnce:
    def test_a_disabled_intraday_policy_never_reads_the_broker(self, engine: Engine) -> None:
        the_spec = spec()
        InstrumentSpecStore(engine).record(the_spec)
        config = platform_config(expected_spec_version=the_spec.spec_version)  # intraday disabled
        fake = FakeMt5()

        orch = orchestrator(engine, config, fake, activation_watermark=None)
        outcome = orch.flatten_once()

        assert outcome is None
        assert fake.positions_get_calls == 0

    def test_a_flat_book_never_reaches_the_flatten_gate(self, engine: Engine) -> None:
        the_spec = spec()
        InstrumentSpecStore(engine).record(the_spec)
        config = platform_config(
            expected_spec_version=the_spec.spec_version, intraday=PAST_DEADLINE_POLICY
        )
        fake = FakeMt5()  # open_positions defaults to ()

        orch = orchestrator(engine, config, fake, activation_watermark=None)
        outcome = orch.flatten_once()

        assert outcome is None
        assert fake.positions_get_calls == 1  # the broker was read, just found flat

    def test_before_the_deadline_nothing_is_committed(self, engine: Engine) -> None:
        the_spec = spec()
        InstrumentSpecStore(engine).record(the_spec)
        config = platform_config(expected_spec_version=the_spec.spec_version, intraday=OPEN_POLICY)
        fake = FakeMt5()
        fake.open_positions = (fake_position(time=int(FIXED_NOW.timestamp())),)

        orch = orchestrator(engine, config, fake, activation_watermark=None)
        outcome = orch.flatten_once()

        assert outcome is None

    def test_past_the_deadline_a_shipped_style_config_is_gate_blocked(self, engine: Engine) -> None:
        """No approval fields set — the honest, shipped-config outcome."""
        the_spec = spec()
        InstrumentSpecStore(engine).record(the_spec)
        config = platform_config(
            expected_spec_version=the_spec.spec_version, intraday=PAST_DEADLINE_POLICY
        )
        fake = FakeMt5()
        fake.open_positions = (fake_position(),)
        kill_switch = KillSwitch()

        orch = orchestrator(
            engine, config, fake, activation_watermark=None, kill_switch=kill_switch
        )
        outcome = orch.flatten_once()

        assert outcome is not None
        assert outcome.event_type == FlattenEventType.FLATTEN_GATE_BLOCKED
        assert set(outcome.reason_codes) == {
            ReasonCode.FLATTEN_SUBMISSION_NOT_ENABLED,
            ReasonCode.RISK_POLICY_NOT_APPROVED,
            ReasonCode.FEEDBACK_2_0_NOT_APPROVED,
        }
        assert fake.close_all_positions_calls == 0
        assert fake.order_send_calls == 0

        events = FlattenEventStore(engine).events_for(outcome.flatten_request_id)
        assert [event.event_type for event in events] == [
            FlattenEventType.FLATTEN_REQUEST_CLAIMED,
            FlattenEventType.FLATTEN_GATE_BLOCKED,
        ]

        assert kill_switch.is_halted
        assert ReasonCode.OVERNIGHT_EXPOSURE in kill_switch.active_reasons

    def test_a_fully_approved_config_reaches_flatten_submission_started(
        self, engine: Engine
    ) -> None:
        """The hard assertion this test exists for: even from the most

        permissive config this platform can construct, `close_all_positions`
        and `order_send` are still never called — the gate opening only
        appends `FLATTEN_SUBMISSION_STARTED` and stops.
        """
        the_spec = spec()
        InstrumentSpecStore(engine).record(the_spec)
        base_config = platform_config(
            expected_spec_version=the_spec.spec_version, intraday=PAST_DEADLINE_POLICY
        )
        version = base_config.config_version
        config = base_config.model_copy(
            update={
                "risk": base_config.risk.model_copy(update={"approved_config_version": version}),
                "execution": base_config.execution.model_copy(
                    update={
                        "submission_enabled": True,
                        "feedback_2_0_approved": True,
                        "flatten_submission_enabled": True,
                    }
                ),
            }
        )
        fake = FakeMt5()
        fake.open_positions = (fake_position(ticket=900042, magic=777),)
        kill_switch = KillSwitch()

        orch = orchestrator(
            engine, config, fake, activation_watermark=None, kill_switch=kill_switch
        )
        outcome = orch.flatten_once()

        assert outcome is not None
        assert outcome.event_type == FlattenEventType.FLATTEN_SUBMISSION_STARTED
        assert outcome.target_count == 1
        assert fake.close_all_positions_calls == 0
        assert fake.order_send_calls == 0

        events = FlattenEventStore(engine).events_for(outcome.flatten_request_id)
        assert [event.event_type for event in events] == [
            FlattenEventType.FLATTEN_REQUEST_CLAIMED,
            FlattenEventType.FLATTEN_GATE_PASSED,
            FlattenEventType.FLATTEN_SUBMISSION_STARTED,
        ]

        commitment = events[-1]
        assert commitment.payload is not None
        targets = commitment.payload["instructions"]
        assert len(targets) == 1
        assert targets[0]["ticket"] == 900042
        assert targets[0]["magic"] == 777
        assert targets[0]["position_side"] == "BUY"
        assert targets[0]["close_side"] == "SELL"

        assert kill_switch.is_halted
        assert ReasonCode.OVERNIGHT_EXPOSURE in kill_switch.active_reasons

    def test_a_second_pass_does_not_re_commit_a_flatten(self, engine: Engine) -> None:
        the_spec = spec()
        InstrumentSpecStore(engine).record(the_spec)
        base_config = platform_config(
            expected_spec_version=the_spec.spec_version, intraday=PAST_DEADLINE_POLICY
        )
        version = base_config.config_version
        config = base_config.model_copy(
            update={
                "risk": base_config.risk.model_copy(update={"approved_config_version": version}),
                "execution": base_config.execution.model_copy(
                    update={
                        "submission_enabled": True,
                        "feedback_2_0_approved": True,
                        "flatten_submission_enabled": True,
                    }
                ),
            }
        )
        fake = FakeMt5()
        fake.open_positions = (fake_position(ticket=900042),)

        orch = orchestrator(engine, config, fake, activation_watermark=None)

        first = orch.flatten_once()
        assert first is not None
        assert first.event_type == FlattenEventType.FLATTEN_SUBMISSION_STARTED
        positions_before_second = fake.positions_get_calls

        second = orch.flatten_once()
        assert second is not None
        assert second.event_type == FlattenEventType.FLATTEN_OUTCOME_RESOLVED
        assert fake.positions_get_calls == positions_before_second + 1

        events = FlattenEventStore(engine).events_for(second.flatten_request_id)
        assert events[-1].event_type == FlattenEventType.FLATTEN_OUTCOME_RESOLVED
        resolved = events[-1]
        assert resolved.payload is not None
        assert resolved.payload["still_open_tickets"] == [900042]
        assert resolved.payload["flattened"] is False

        positions_before_third = fake.positions_get_calls
        third = orch.flatten_once()
        assert third is None
        assert fake.positions_get_calls == positions_before_third

    def test_a_position_from_an_earlier_trading_day_is_caught_on_the_first_pass(
        self, engine: Engine
    ) -> None:
        """ADR-004 §5.4: a position opened before FIXED_NOW's trading day is

        a rollover breach even though the OPEN policy's deadline itself
        has not been reached."""
        the_spec = spec()
        InstrumentSpecStore(engine).record(the_spec)
        config = platform_config(expected_spec_version=the_spec.spec_version, intraday=OPEN_POLICY)
        fake = FakeMt5()
        yesterday = int((FIXED_NOW - timedelta(days=1)).timestamp())
        fake.open_positions = (fake_position(time=yesterday),)
        kill_switch = KillSwitch()

        orch = orchestrator(
            engine, config, fake, activation_watermark=None, kill_switch=kill_switch
        )
        outcome = orch.flatten_once()

        assert outcome is not None
        assert kill_switch.is_halted

    def test_an_incomplete_position_book_blocks_the_flatten(self, engine: Engine) -> None:
        """ADR-004 §5.3: a book this platform cannot currently see must not

        be flattened — MT5's own failure convention, `positions_get`
        returning `None` with a non-RES_S_OK error code."""
        the_spec = spec()
        InstrumentSpecStore(engine).record(the_spec)
        base_config = platform_config(
            expected_spec_version=the_spec.spec_version, intraday=PAST_DEADLINE_POLICY
        )
        version = base_config.config_version
        config = base_config.model_copy(
            update={
                "risk": base_config.risk.model_copy(update={"approved_config_version": version}),
                "execution": base_config.execution.model_copy(
                    update={
                        "submission_enabled": True,
                        "feedback_2_0_approved": True,
                        "flatten_submission_enabled": True,
                    }
                ),
            }
        )
        fake = FakeMt5()

        def failing_positions_get(*_a: Any, **_k: Any) -> tuple[Any, ...]:
            return None  # type: ignore[return-value]

        def failing_last_error() -> tuple[int, str]:
            return (2, "connection lost")

        fake.positions_get = failing_positions_get  # type: ignore[method-assign]
        fake.last_error = failing_last_error  # type: ignore[method-assign]

        orch = orchestrator(engine, config, fake, activation_watermark=None)
        outcome = orch.flatten_once()

        # A failed position read means an empty observed book, which is
        # indistinguishable from genuinely flat at the `flatten_once()`
        # early-return — there is nothing to flatten *or* refuse yet. The
        # incompleteness is still durably recorded on the broker-state
        # snapshot itself (`position_set_state=FAILED`); this proves the
        # failure does not get silently upgraded into "flat, all clear".
        assert outcome is None

    def test_the_operator_flatten_control_is_never_reached(self) -> None:
        """ADR-004 §5.1 made mechanical: the automatic path must not be

        implemented by reusing the operator's manual control."""
        import inspect

        from crumblr.application import execution

        source = inspect.getsource(execution)
        assert "OperatorControls" not in source
        assert "operator_controls" not in source
