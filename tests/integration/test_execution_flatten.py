"""Automatic flatten submission, end to end, against real PostgreSQL and a

fake MT5 terminal — core critical path item 7, ADR-009, weekly close per
owner risk policy v1 (D1.5). Mirrors `test_execution_orchestrator.py`'s own
end-to-end shape: this is the test that proves the non-sending guarantee
holds for the *flatten* path specifically — a real broker-side position past
its deadline reaches a persisted `FLATTEN_SUBMISSION_STARTED` event, and
`close_all_positions`/`order_send` are never called.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine

from crumblr.config import IntradayConfig, PlatformConfig
from crumblr.domain.enums import FlattenEventType, ReasonCode
from crumblr.persistence.flatten import FlattenEventStore
from crumblr.persistence.instrument_specs import InstrumentSpecStore
from crumblr.risk.kill_switch import KillSwitch
from tests.conftest import FIXED_NOW
from tests.integration._execution_fixtures import (
    FakeFlattenCloseSink,
    FakeMt5,
    fake_position,
    orchestrator,
    platform_config,
    spec,
)

pytestmark = pytest.mark.integration

# `FIXED_NOW` (2026-08-17 12:00 UTC) is a Monday, and owner risk policy v1
# (D1.5) gives Monday-Thursday no deadline at all regardless of offsets —
# the deadline-dependent tests below need a moment on the Friday trading
# day instead. `FRIDAY_NOW` is that same week's Friday, at the same local
# time of day, so the "N hours out from the weekly close" reasoning below
# still holds: `weekly_close(FRIDAY_NOW) == 2026-08-21 21:00:00 UTC` (17:00
# America/New_York, daylight saving in effect in August), nine hours after
# FRIDAY_NOW — computed directly from `trading_agent/sessions.py
# ::weekly_close` rather than guessed, so the two policies below land on
# opposite sides of that nine-hour gap deterministically, exactly as they
# did against the old daily `session_close`.
FRIDAY_NOW = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)


def _friday_clock() -> datetime:
    return FRIDAY_NOW + timedelta(seconds=1)


PAST_DEADLINE_POLICY = IntradayConfig.model_validate(
    {
        "enabled": True,
        "last_entry_minutes_before_close": 660,  # 11h — still before the deadline below
        "flatten_minutes_before_close": 600,  # 10h — FRIDAY_NOW (9h out) is already past this
    }
)

OPEN_POLICY = IntradayConfig.model_validate(
    {
        "enabled": True,
        "last_entry_minutes_before_close": 120,  # 2h
        "flatten_minutes_before_close": 60,  # 1h — FRIDAY_NOW (9h out) is well before this
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

        orch = orchestrator(engine, config, fake, activation_watermark=None, clock=_friday_clock)
        outcome = orch.flatten_once()

        assert outcome is None
        assert fake.positions_get_calls == 1  # the broker was read, just found flat

    def test_before_the_deadline_nothing_is_committed(self, engine: Engine) -> None:
        the_spec = spec()
        InstrumentSpecStore(engine).record(the_spec)
        config = platform_config(expected_spec_version=the_spec.spec_version, intraday=OPEN_POLICY)
        fake = FakeMt5()
        fake.open_positions = (fake_position(time=int(FRIDAY_NOW.timestamp())),)

        orch = orchestrator(engine, config, fake, activation_watermark=None, clock=_friday_clock)
        outcome = orch.flatten_once()

        assert outcome is None

    def test_a_monday_never_reaches_a_deadline_regardless_of_the_policy(
        self, engine: Engine
    ) -> None:
        """Owner risk policy v1 (D1.5)'s own affirmative claim: Monday has

        no cutoff/flatten at all, even under the config that would already
        be past a Friday deadline."""
        the_spec = spec()
        InstrumentSpecStore(engine).record(the_spec)
        config = platform_config(
            expected_spec_version=the_spec.spec_version, intraday=PAST_DEADLINE_POLICY
        )
        fake = FakeMt5()
        fake.open_positions = (fake_position(time=int(FIXED_NOW.timestamp())),)

        orch = orchestrator(engine, config, fake, activation_watermark=None)  # FIXED_NOW, a Monday
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
            engine,
            config,
            fake,
            activation_watermark=None,
            kill_switch=kill_switch,
            clock=_friday_clock,
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
            engine,
            config,
            fake,
            activation_watermark=None,
            kill_switch=kill_switch,
            clock=_friday_clock,
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

        orch = orchestrator(engine, config, fake, activation_watermark=None, clock=_friday_clock)

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

    def test_a_position_from_before_the_weekly_close_is_caught_on_the_first_pass(
        self, engine: Engine
    ) -> None:
        """A position that survived the weekly close is a breach even

        though the OPEN policy's own deadline has not been reached —
        owner risk policy v1 (D1.5)'s `has_crossed_weekly_close`, replacing
        the old daily rollover check ADR-004 §5.4 named."""
        the_spec = spec()
        InstrumentSpecStore(engine).record(the_spec)
        config = platform_config(expected_spec_version=the_spec.spec_version, intraday=OPEN_POLICY)
        fake = FakeMt5()
        last_week = int((FRIDAY_NOW - timedelta(days=7)).timestamp())
        fake.open_positions = (fake_position(time=last_week),)
        kill_switch = KillSwitch()

        orch = orchestrator(
            engine,
            config,
            fake,
            activation_watermark=None,
            kill_switch=kill_switch,
            clock=_friday_clock,
        )
        outcome = orch.flatten_once()

        assert outcome is not None
        assert kill_switch.is_halted

    def test_an_incomplete_position_book_before_the_deadline_blocks_the_flatten(
        self, engine: Engine
    ) -> None:
        """ADR-004 §5.3: a book this platform cannot currently see must not

        be flattened — MT5's own failure convention, `positions_get`
        returning `None` with a non-RES_S_OK error code. Before the Friday
        deadline this is not yet the D1.5 HALT below — see the next test
        for that leg."""
        the_spec = spec()
        InstrumentSpecStore(engine).record(the_spec)
        base_config = platform_config(
            expected_spec_version=the_spec.spec_version, intraday=OPEN_POLICY
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
        kill_switch = KillSwitch()

        orch = orchestrator(
            engine,
            config,
            fake,
            activation_watermark=None,
            kill_switch=kill_switch,
            clock=_friday_clock,
        )
        outcome = orch.flatten_once()

        # A failed position read means an empty observed book, which is
        # indistinguishable from genuinely flat at the `flatten_once()`
        # early-return — there is nothing to flatten *or* refuse yet. The
        # incompleteness is still durably recorded on the broker-state
        # snapshot itself (`position_set_state=FAILED`). Before the Friday
        # deadline (OPEN_POLICY, FRIDAY_NOW is well inside it), this does
        # not yet trip the new D1.5 halt — see the next test for the
        # at/past-deadline leg.
        assert outcome is None
        assert not kill_switch.is_halted

    def test_an_incomplete_book_at_the_deadline_halts_rather_than_assuming_flat(
        self, engine: Engine
    ) -> None:
        """Owner risk policy v1 (D1.5)'s own explicit requirement: if flat

        state cannot be confirmed by the mandatory deadline, HALT and
        surface the incident rather than assume success. Same failing
        `positions_get` as the test above, but under `PAST_DEADLINE_POLICY`
        — `phase_at(FRIDAY_NOW, ...)` is `FLATTEN_REQUIRED`, so the
        emptiness this failure produces must not read as genuinely flat.
        """
        the_spec = spec()
        InstrumentSpecStore(engine).record(the_spec)
        config = platform_config(
            expected_spec_version=the_spec.spec_version, intraday=PAST_DEADLINE_POLICY
        )
        fake = FakeMt5()

        def failing_positions_get(*_a: Any, **_k: Any) -> tuple[Any, ...]:
            return None  # type: ignore[return-value]

        def failing_last_error() -> tuple[int, str]:
            return (2, "connection lost")

        fake.positions_get = failing_positions_get  # type: ignore[method-assign]
        fake.last_error = failing_last_error  # type: ignore[method-assign]
        kill_switch = KillSwitch()

        orch = orchestrator(
            engine,
            config,
            fake,
            activation_watermark=None,
            kill_switch=kill_switch,
            clock=_friday_clock,
        )
        outcome = orch.flatten_once()

        assert outcome is None
        assert kill_switch.is_halted
        assert ReasonCode.FLATTEN_STATE_UNKNOWN in kill_switch.active_reasons

    def test_the_operator_flatten_control_is_never_reached(self) -> None:
        """ADR-004 §5.1 made mechanical: the automatic path must not be

        implemented by reusing the operator's manual control."""
        import inspect

        from crumblr.application import execution

        source = inspect.getsource(execution)
        assert "OperatorControls" not in source
        assert "operator_controls" not in source


def _fully_approved_config(*, expected_spec_version: str) -> PlatformConfig:
    base_config = platform_config(
        expected_spec_version=expected_spec_version, intraday=PAST_DEADLINE_POLICY
    )
    version = base_config.config_version
    return base_config.model_copy(
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


class TestRealFlattenClose:
    """Phase B item B5 (`review/adr/ADR-020-real-flatten-close.md`): the

    real per-ticket close, reached only on the pass *after* commitment —
    `_commit_flatten` itself is unchanged (see `TestFlattenOnce` above,
    all still green). A real close is only ever attempted when a
    `FlattenCloseSink` is explicitly injected — every test below does so
    on purpose; every test above never does, and stays exactly as inert
    as before this slice.
    """

    def test_a_successful_close_resolves_flattened_true_on_the_next_pass(
        self, engine: Engine
    ) -> None:
        the_spec = spec()
        InstrumentSpecStore(engine).record(the_spec)
        config = _fully_approved_config(expected_spec_version=the_spec.spec_version)
        fake = FakeMt5()
        fake.open_positions = (fake_position(ticket=900042),)
        sink = FakeFlattenCloseSink(fake, succeed_tickets=frozenset({900042}))

        orch = orchestrator(
            engine,
            config,
            fake,
            activation_watermark=None,
            clock=_friday_clock,
            flatten_close_adapter=sink,
        )

        first = orch.flatten_once()
        assert first is not None
        assert first.event_type == FlattenEventType.FLATTEN_SUBMISSION_STARTED
        assert sink.close_calls == []  # not attempted on the commit pass itself

        second = orch.flatten_once()
        assert second is not None
        assert second.event_type == FlattenEventType.FLATTEN_OUTCOME_RESOLVED
        assert second.closed_count == 1
        assert sink.close_calls == [900042]

        events = FlattenEventStore(engine).events_for(second.flatten_request_id)
        resolved = events[-1]
        assert resolved.payload is not None
        assert resolved.payload["flattened"] is True
        assert resolved.payload["closed_tickets"] == [900042]

        # Idempotent: a third pass finds the occurrence already resolved
        # and never touches the broker or the close adapter again.
        calls_before_third = sink.close_calls.copy()
        third = orch.flatten_once()
        assert third is None
        assert sink.close_calls == calls_before_third

    def test_a_rejected_close_trips_flatten_close_failed_and_retries_next_pass(
        self, engine: Engine
    ) -> None:
        the_spec = spec()
        InstrumentSpecStore(engine).record(the_spec)
        config = _fully_approved_config(expected_spec_version=the_spec.spec_version)
        fake = FakeMt5()
        fake.open_positions = (fake_position(ticket=900042),)
        # Rejected on the first attempt, succeeds on the second — proves a
        # real retry-then-recover across passes, not a permanent brick.
        sink = FakeFlattenCloseSink(fake, succeed_tickets=frozenset())
        kill_switch = KillSwitch()

        orch = orchestrator(
            engine,
            config,
            fake,
            activation_watermark=None,
            clock=_friday_clock,
            kill_switch=kill_switch,
            flatten_close_adapter=sink,
        )
        orch.flatten_once()  # commit

        second = orch.flatten_once()  # attempt — rejected, still open
        assert second is not None
        assert second.event_type == FlattenEventType.FLATTEN_SUBMISSION_STARTED
        # The durable outcome names the specific reason even though the
        # kill switch itself already recorded OVERNIGHT_EXPOSURE first in
        # this same pass (`_trip_overnight_exposure` runs before the close
        # attempt — see that call site's own comment) — `KillSwitch.trip`
        # is a no-op once halted, so `_trip_flatten_close_failed`'s own
        # trip never overwrites it. Both reasons are real; only one wins
        # the global kill-switch slot, and the flatten-specific one stays
        # visible on the outcome/durable event regardless.
        assert ReasonCode.FLATTEN_CLOSE_FAILED in second.reason_codes
        assert kill_switch.is_halted
        assert ReasonCode.OVERNIGHT_EXPOSURE in kill_switch.active_reasons

        events = FlattenEventStore(engine).events_for(second.flatten_request_id)
        assert events[-1].event_type == FlattenEventType.FLATTEN_SUBMISSION_STARTED
        assert FlattenEventType.FLATTEN_OUTCOME_RESOLVED not in [
            event.event_type for event in events
        ]

        # The halt this attempt caused does not brick the gate: a third
        # pass reaches the broker again despite being halted (condition 7
        # tolerates FLATTEN_CLOSE_FAILED), this time succeeding.
        sink.succeed_tickets = frozenset({900042})
        third = orch.flatten_once()
        assert third is not None
        assert third.event_type == FlattenEventType.FLATTEN_OUTCOME_RESOLVED
        assert third.closed_count == 1
        assert sink.close_calls == [900042, 900042]

    def test_a_transport_failure_during_close_does_not_block_other_tickets(
        self, engine: Engine
    ) -> None:
        the_spec = spec()
        InstrumentSpecStore(engine).record(the_spec)
        config = _fully_approved_config(expected_spec_version=the_spec.spec_version)
        fake = FakeMt5()
        fake.open_positions = (
            fake_position(ticket=1, magic=1),
            fake_position(ticket=2, magic=2),
        )
        sink = FakeFlattenCloseSink(
            fake, succeed_tickets=frozenset({2}), raise_for_tickets=frozenset({1})
        )

        orch = orchestrator(
            engine,
            config,
            fake,
            activation_watermark=None,
            clock=_friday_clock,
            flatten_close_adapter=sink,
        )
        orch.flatten_once()  # commit

        second = orch.flatten_once()
        assert second is not None
        assert sorted(sink.close_calls) == [1, 2]  # both attempted
        assert second.event_type == FlattenEventType.FLATTEN_SUBMISSION_STARTED
        assert second.closed_count == 1  # only ticket 2 confirmed closed

        events = FlattenEventStore(engine).events_for(second.flatten_request_id)
        assert events[-1].event_type == FlattenEventType.FLATTEN_SUBMISSION_STARTED

    def test_an_already_externally_closed_ticket_is_never_re_attempted(
        self, engine: Engine
    ) -> None:
        """A position closed by some other means (a manual operator action,

        say) between the commit pass and this one is never redundantly
        resubmitted — only a ticket the fresh read still shows open is."""
        the_spec = spec()
        InstrumentSpecStore(engine).record(the_spec)
        config = _fully_approved_config(expected_spec_version=the_spec.spec_version)
        fake = FakeMt5()
        fake.open_positions = (fake_position(ticket=900042),)
        sink = FakeFlattenCloseSink(fake, succeed_tickets=frozenset({900042}))

        orch = orchestrator(
            engine,
            config,
            fake,
            activation_watermark=None,
            clock=_friday_clock,
            flatten_close_adapter=sink,
        )
        orch.flatten_once()  # commit

        fake.open_positions = ()  # already gone before the next pass reads it

        second = orch.flatten_once()
        assert second is not None
        assert second.event_type == FlattenEventType.FLATTEN_OUTCOME_RESOLVED
        assert sink.close_calls == []  # never attempted — nothing was still open
