"""Structured logging baseline (review finding F-013).

The reviewer's requirements, restated as the properties under test: records are
structured and parseable, carry a UTC timestamp, propagate a correlation id,
redact secrets — and, the two that matter most, **logging cannot break safety
logic and cannot change a trading result**.

The last point is why every test that touches determinism is here rather than
taken on trust. Observability that alters what it observes is worse than none.
"""

from __future__ import annotations

import io
import json
from decimal import Decimal
from typing import Any

import pytest

from crumblr.domain.enums import KillSwitchState, ReasonCode
from crumblr.domain.timeutils import utc_now
from crumblr.observability.logging import (
    REDACTED,
    ComponentLogger,
    configure_logging,
    get_logger,
)
from crumblr.risk.kill_switch import KillSwitch
from crumblr.risk.safety_state import InMemorySafetyStateStore, SafetyState


@pytest.fixture
def captured() -> io.StringIO:
    """Logging configured against an in-memory stream, needing no infrastructure."""
    stream = io.StringIO()
    configure_logging(stream=stream, level="DEBUG")
    return stream


def records(stream: io.StringIO) -> list[dict[str, Any]]:
    """Every emitted line, parsed. Fails loudly if a line is not JSON."""
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


class TestItInitialisesWithoutInfrastructure:
    """Requirement 1: no database, no collector, no network."""

    def test_configuring_against_a_stream_is_enough(self, captured: io.StringIO) -> None:
        get_logger("test").info("hello")
        assert records(captured)

    def test_a_logger_can_be_obtained_before_explicit_configuration(self) -> None:
        """A component that logs at import time must not crash the process."""
        assert isinstance(get_logger("unconfigured"), ComponentLogger)


class TestRecordsAreStructured:
    """Requirements 2 and 3: parseable, with a UTC timestamp."""

    def test_every_record_is_valid_json(self, captured: io.StringIO) -> None:
        log = get_logger("test")
        log.info("first", value=1)
        log.warning("second", value=2)
        assert len(records(captured)) == 2

    def test_the_expected_fields_are_present(self, captured: io.StringIO) -> None:
        get_logger("risk").info("risk.blocked", reason_codes=["SPREAD_TOO_WIDE"])
        record = records(captured)[-1]
        assert record["event"] == "risk.blocked"
        assert record["component"] == "risk"
        assert record["service"] == "crumblr"
        assert record["level"] == "info"
        assert record["reason_codes"] == ["SPREAD_TOO_WIDE"]

    def test_the_timestamp_is_utc(self, captured: io.StringIO) -> None:
        get_logger("test").info("event")
        timestamp = records(captured)[-1]["timestamp"]
        assert timestamp.endswith("Z"), f"expected a UTC timestamp, got {timestamp!r}"

    def test_severity_is_recorded(self, captured: io.StringIO) -> None:
        log = get_logger("test")
        log.debug("d")
        log.info("i")
        log.warning("w")
        log.error("e")
        assert [r["level"] for r in records(captured)] == ["debug", "info", "warning", "error"]

    def test_field_naming_is_stable_across_records(self, captured: io.StringIO) -> None:
        """Deterministic field naming: the same keys, spelled the same way."""
        log = get_logger("test")
        log.info("one")
        log.info("two")
        first, second = records(captured)
        common = {"event", "component", "service", "level", "timestamp"}
        assert common <= first.keys()
        assert first.keys() == second.keys()


class TestCorrelationIdPropagates:
    """Requirement 4."""

    def test_a_bound_correlation_id_appears_in_the_record(self, captured: io.StringIO) -> None:
        get_logger("orchestration").bind(correlation_id="abc-123").info("decision.made")
        assert records(captured)[-1]["correlation_id"] == "abc-123"

    def test_a_correlation_id_passed_per_call_appears(self, captured: io.StringIO) -> None:
        get_logger("risk").info("risk.blocked", correlation_id="def-456")
        assert records(captured)[-1]["correlation_id"] == "def-456"

    def test_binding_does_not_leak_into_the_unbound_logger(self, captured: io.StringIO) -> None:
        log = get_logger("test")
        log.bind(correlation_id="scoped").info("bound")
        log.info("unbound")
        bound, unbound = records(captured)
        assert bound["correlation_id"] == "scoped"
        assert "correlation_id" not in unbound


class TestSecretsAreRedacted:
    """Requirement 5. build.md §21 keeps credentials out of logs entirely."""

    @pytest.mark.parametrize(
        "field",
        ["password", "mt5_password", "api_key", "secret_key", "auth_token", "credentials"],
    )
    def test_a_credential_shaped_field_is_redacted(self, captured: io.StringIO, field: str) -> None:
        get_logger("gateway").info("connect", **{field: "hunter2"})
        record = records(captured)[-1]
        assert record[field] == REDACTED
        assert "hunter2" not in json.dumps(record)

    def test_nested_credentials_are_redacted(self, captured: io.StringIO) -> None:
        get_logger("gateway").info("connect", account={"login": 5001, "password": "hunter2"})
        assert "hunter2" not in json.dumps(records(captured)[-1])

    def test_credentials_inside_a_list_are_redacted(self, captured: io.StringIO) -> None:
        get_logger("gateway").info("connect", accounts=[{"secret": "abc"}, {"secret": "def"}])
        rendered = json.dumps(records(captured)[-1])
        assert "abc" not in rendered
        assert "def" not in rendered

    def test_ordinary_fields_survive_untouched(self, captured: io.StringIO) -> None:
        get_logger("gateway").info("connect", login=5001, server="DemoBroker-Demo")
        record = records(captured)[-1]
        assert record["login"] == 5001
        assert record["server"] == "DemoBroker-Demo"


class TestLoggingCannotBreakSafetyLogic:
    """Requirement 6. The property that justifies the swallowed exceptions."""

    def test_a_failing_stream_does_not_raise_into_the_caller(self) -> None:
        class ExplodingStream(io.StringIO):
            def write(self, _s: str) -> int:
                raise OSError("stream is gone")

        configure_logging(stream=ExplodingStream())
        get_logger("test").error("this must not propagate")

    def test_an_unserialisable_value_does_not_raise(self, captured: io.StringIO) -> None:
        class Awkward:
            def __repr__(self) -> str:
                raise RuntimeError("cannot be rendered")

        get_logger("test").info("event", awkward=Awkward())

    def test_the_kill_switch_still_trips_when_logging_fails(self) -> None:
        """The case this rule exists for: a halt must not depend on a log line."""

        class ExplodingStream(io.StringIO):
            def write(self, _s: str) -> int:
                raise OSError("stream is gone")

        configure_logging(stream=ExplodingStream())
        switch = KillSwitch(InMemorySafetyStateStore())
        switch.trip(
            reason_codes=(ReasonCode.DAILY_LOSS_LIMIT,),
            tripped_by="risk_engine",
            occurred_at_utc=utc_now(),
        )
        assert switch.is_halted, "logging failure must not prevent a halt"

    def test_a_failing_logger_does_not_prevent_startup_recovery(self) -> None:
        class ExplodingStream(io.StringIO):
            def write(self, _s: str) -> int:
                raise OSError("stream is gone")

        configure_logging(stream=ExplodingStream())
        store = InMemorySafetyStateStore(
            SafetyState(
                state=KillSwitchState.HALTED,
                reason_codes=(ReasonCode.MANUAL_HALT,),
                recorded_at_utc=utc_now(),
            )
        )
        assert KillSwitch.on_startup(store).is_halted


class TestLoggingDoesNotAffectResults:
    """Requirement 7. Observability that changes the outcome is not observability."""

    @staticmethod
    def _replay_fingerprints(stream: io.StringIO | None) -> list[str]:
        from pathlib import Path

        from scripts.run_replay import build_instrument_spec

        from crumblr.application.orchestration import ReplayOrchestrator
        from crumblr.config import load_config
        from crumblr.domain.enums import Environment
        from crumblr.market_data.synthetic import SyntheticMarketConfig, generate_ticks
        from crumblr.mt5_gateway.simulated import SimulatedBroker

        if stream is not None:
            configure_logging(stream=stream, level="DEBUG")
        else:
            configure_logging(stream=io.StringIO(), level="CRITICAL")

        repo_root = Path(__file__).resolve().parents[2]
        config = load_config(Environment.PAPER, config_dir=repo_root / "config")
        spec = build_instrument_spec()
        broker = SimulatedBroker(
            spec,
            starting_balance=Decimal("10000"),
            server=config.account_guard.expected_server,
        )
        ticks = list(generate_ticks(SyntheticMarketConfig(bar_count=600), spec))
        result = ReplayOrchestrator(config, spec, broker, starting_equity=Decimal("10000")).run(
            ticks
        )
        return [capsule.provenance_fingerprint for capsule in result.capsules]

    def test_verbose_logging_produces_the_same_decisions_as_silence(self) -> None:
        noisy = io.StringIO()
        with_logs = self._replay_fingerprints(noisy)
        without_logs = self._replay_fingerprints(None)

        assert records(noisy), "the verbose run must actually have logged something"
        assert with_logs == without_logs, "logging changed the decisions"

    def test_decision_hashes_are_unaffected_by_logging(self) -> None:
        from uuid import uuid4

        from tests.conftest import make_intent

        # The feature snapshot id is part of the hash and is random per intent,
        # so it has to be held fixed for the comparison to mean anything.
        shared = {"feature_snapshot_id": uuid4()}

        configure_logging(stream=io.StringIO(), level="CRITICAL")
        quiet = make_intent(**shared).decision_hash

        loud = io.StringIO()
        configure_logging(stream=loud, level="DEBUG")
        get_logger("test").info("noise")
        assert make_intent(**shared).decision_hash == quiet
        assert records(loud), "the loud run must actually have logged something"
