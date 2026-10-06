"""The canary-mode window driver stays closed unless every approved condition holds.

Pure decision logic (`crumblr/application/canary_window.py`) driven by a fake clock and a fake
`WindowIO`: no database, no MT5, no network, no broker. The orchestrator-level scope checks the
child run applies at submission time (account, server, symbol, entry type, agent, assignment,
artifact, risk fraction, expiry) are pinned separately by
`tests/unit/test_canary_permit_scope_mismatches.py`.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from crumblr.application.canary_window import (
    BAR,
    CanaryWindowSpec,
    CycleInspection,
    CycleResult,
    ExecutionEventRecord,
    ExitCode,
    ReaderEvidence,
    StartInputs,
    bar_is_in_window,
    run_window,
    start_refusals,
)
from crumblr.domain.enums import EntryType
from crumblr.domain.models import CanaryPermit, CanaryPermitConsumption

ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = "81894d6a9c44ddb0433c15f9779fb0a2e25c0e1eccee232e2fd145d72cb498c5"
ACCOUNT_REF = "4f857e6a72f9ad30"
SERVER = "PepperstoneUK-Demo"
AGENT = UUID("760e93be-117c-48a3-b997-f258055ec29b")
ASSIGNMENT = UUID("f98c0396-dd13-4a99-b1e9-b83ea0f15ed7")
CONFIG_VERSION = "0f92d6418858ecfbfc7072acdfb9a06a04dff8207e77d95797467f1c5291acba"
W0 = datetime(2026, 10, 6, 14, 0, tzinfo=UTC)
W1 = datetime(2026, 10, 6, 15, 0, tzinfo=UTC)
PERMIT_ID = UUID("11111111-2222-3333-4444-555555555555")


def good_permit(**overrides: Any) -> CanaryPermit:
    fields: dict[str, Any] = {
        "permit_id": PERMIT_ID,
        "approved_account_ref": ACCOUNT_REF,
        "expected_server": SERVER,
        "canonical_symbol": "EUR/USD",
        "entry_type": EntryType.LIMIT,
        "agent_id": AGENT,
        "assignment_id": ASSIGNMENT,
        "strategy_artifact_hash": ARTIFACT,
        "max_requested_risk_fraction": Decimal("0.0005"),
        "issued_by": "owner",
        "reason": "unit test",
        "issued_at_utc": W0 - timedelta(minutes=30),
        "valid_until_utc": W1 + timedelta(minutes=5),
    }
    fields.update(overrides)
    return CanaryPermit(**fields)


def good_spec(**overrides: Any) -> CanaryWindowSpec:
    fields: dict[str, Any] = {
        "permit_id": PERMIT_ID,
        "agent_id": AGENT,
        "assignment_id": ASSIGNMENT,
        "strategy_artifact_hash": ARTIFACT,
        "expected_account_ref": ACCOUNT_REF,
        "expected_server": SERVER,
        "entry_type": EntryType.LIMIT,
        "max_requested_risk_fraction": Decimal("0.0005"),
        "window_start_utc": W0,
        "window_end_utc": W1,
    }
    fields.update(overrides)
    return CanaryWindowSpec(**fields)


def good_overlay() -> dict[str, Any]:
    return {
        "risk": {"approved_config_version": CONFIG_VERSION},
        "execution": {
            "submission_enabled": True,
            "feedback_2_0_approved": True,
            "approved_canary_account_ref": ACCOUNT_REF,
        },
    }


def good_inputs(**overrides: Any) -> StartInputs:
    fields: dict[str, Any] = {
        "permit": good_permit(),
        "consumption": None,
        "overlay": good_overlay(),
        "config_version": CONFIG_VERSION,
        "live_account_ref": ACCOUNT_REF,
        "live_account_server": SERVER,
        "agent_artifact_hash": ARTIFACT,
        "now": W0 - timedelta(minutes=10),
    }
    fields.update(overrides)
    return StartInputs(**fields)


def healthy() -> ReaderEvidence:
    return ReaderEvidence(
        reader_status="HEALTHY",
        reader_connected=True,
        heartbeat_age_s=2.0,
        tick_age_s=1.0,
        snapshot_age_s=30.0,
        position_set_state="COMPLETE",
        pending_order_set_state="COMPLETE",
        dashboard_mt5="CONNECTED",
        dashboard_feed="HEALTHY",
        agent_status="READY",
        agent_artifact_hash=ARTIFACT,
    )


class TestStartIsRefusedUnlessEverythingMatches:
    def test_the_fully_matching_case_has_no_refusal(self) -> None:
        assert start_refusals(good_spec(), good_inputs()) == ()

    @pytest.mark.parametrize(
        ("inputs", "expected"),
        [
            ({"permit": None}, "PERMIT_NOT_FOUND"),
            (
                {
                    "consumption": CanaryPermitConsumption(
                        permit_id=PERMIT_ID,
                        order_request_id=uuid4(),
                        consumed_at_utc=W0 - timedelta(minutes=20),
                    )
                },
                "PERMIT_ALREADY_CONSUMED",
            ),
            ({"now": W1 + timedelta(minutes=6)}, "PERMIT_EXPIRED"),
            ({"permit": good_permit(approved_account_ref="0" * 16)}, "PERMIT_ACCOUNT_REF_MISMATCH"),
            (
                {"permit": good_permit(expected_server="PepperstoneUK-Live")},
                "PERMIT_SERVER_MISMATCH",
            ),
            ({"permit": good_permit(agent_id=uuid4())}, "PERMIT_AGENT_MISMATCH"),
            ({"permit": good_permit(assignment_id=uuid4())}, "PERMIT_ASSIGNMENT_MISMATCH"),
            ({"permit": good_permit(strategy_artifact_hash="b" * 64)}, "PERMIT_ARTIFACT_MISMATCH"),
            ({"permit": good_permit(entry_type=EntryType.MARKET)}, "PERMIT_ENTRY_TYPE_MISMATCH"),
            (
                {"permit": good_permit(max_requested_risk_fraction=Decimal("0.005"))},
                "PERMIT_RISK_FRACTION_MISMATCH",
            ),
            (
                {"permit": good_permit(max_requested_risk_fraction=Decimal("0.0001"))},
                "PERMIT_RISK_FRACTION_MISMATCH",
            ),
            (
                {"permit": good_permit(issued_at_utc=W0 + timedelta(minutes=1))},
                "WINDOW_STARTS_BEFORE_PERMIT_ISSUED",
            ),
            (
                {"permit": good_permit(valid_until_utc=W1 - timedelta(minutes=1))},
                "PERMIT_EXPIRES_BEFORE_WINDOW_ENDS",
            ),
            (
                {"permit": good_permit(valid_until_utc=W1 + timedelta(hours=10))},
                "PERMIT_VALID_LONGER_THAN_THE_WINDOW",
            ),
            (
                {"permit": good_permit(valid_until_utc=W1 + timedelta(minutes=11))},
                "PERMIT_VALID_LONGER_THAN_THE_WINDOW",
            ),
            (
                {"permit": good_permit(issued_at_utc=W0 - timedelta(minutes=61))},
                "PERMIT_ISSUED_TOO_LONG_BEFORE_THE_WINDOW",
            ),
            ({"live_account_ref": "ffffffffffffffff"}, "LIVE_ACCOUNT_REF_MISMATCH_OR_UNKNOWN"),
            ({"live_account_ref": None}, "LIVE_ACCOUNT_REF_MISMATCH_OR_UNKNOWN"),
            (
                {"live_account_server": "PepperstoneUK-Live"},
                "LIVE_ACCOUNT_SERVER_MISMATCH_OR_UNKNOWN",
            ),
            ({"agent_artifact_hash": "c" * 64}, "RUNNING_AGENT_ARTIFACT_MISMATCH_OR_UNKNOWN"),
            ({"agent_artifact_hash": None}, "RUNNING_AGENT_ARTIFACT_MISMATCH_OR_UNKNOWN"),
        ],
    )
    def test_each_mismatch_is_refused_with_its_own_reason(
        self, inputs: dict[str, Any], expected: str
    ) -> None:
        assert expected in start_refusals(good_spec(), good_inputs(**inputs))

    @pytest.mark.parametrize(
        ("spec", "expected"),
        [
            (good_spec(window_end_utc=W0), "WINDOW_END_NOT_AFTER_START"),
            (
                good_spec(window_end_utc=W0 + timedelta(minutes=61)),
                "WINDOW_LONGER_THAN_ONE_STRATEGY_WINDOW",
            ),
        ],
    )
    def test_a_malformed_or_oversized_window_is_refused(
        self, spec: CanaryWindowSpec, expected: str
    ) -> None:
        assert expected in start_refusals(spec, good_inputs())

    def test_a_window_that_is_already_over_is_refused(self) -> None:
        assert "WINDOW_ALREADY_OVER" in start_refusals(good_spec(), good_inputs(now=W1))

    def test_the_operator_cannot_be_silently_widened_by_a_looser_restatement(self) -> None:
        """The permit is the ceiling; the restated scope must equal it, not merely fit under it."""
        looser = good_spec(max_requested_risk_fraction=Decimal("0.01"))
        assert "PERMIT_RISK_FRACTION_MISMATCH" in start_refusals(looser, good_inputs())


class TestOverlayMayOnlyBeTheFourApprovedLines:
    @pytest.mark.parametrize(
        ("mutate", "expected"),
        [
            (
                lambda o: o["execution"].update(flatten_submission_enabled=True),
                "OVERLAY_ENABLES_FLATTEN",
            ),
            (
                lambda o: o["execution"].update(flatten_submission_enabled=False),
                "OVERLAY_EXECUTION_KEYS_NOT_EXACTLY_APPROVED",
            ),
            (
                lambda o: o["execution"].update(max_spread_points=999),
                "OVERLAY_EXECUTION_KEYS_NOT_EXACTLY_APPROVED",
            ),
            (
                lambda o: o["risk"].update(max_risk_per_trade="0.5"),
                "OVERLAY_RISK_KEYS_NOT_EXACTLY_APPROVED",
            ),
            (
                lambda o: o.update(account_guard={"require_demo_account": False}),
                "OVERLAY_HAS_UNAPPROVED_SECTION",
            ),
            (
                lambda o: o["execution"].update(submission_enabled=False),
                "OVERLAY_SUBMISSION_NOT_ENABLED",
            ),
            (
                lambda o: o["execution"].update(feedback_2_0_approved=False),
                "OVERLAY_FEEDBACK_2_0_NOT_APPROVED",
            ),
            (
                lambda o: o["execution"].update(approved_canary_account_ref="0" * 16),
                "OVERLAY_ACCOUNT_REF_MISMATCH",
            ),
            (
                lambda o: o["risk"].update(approved_config_version="stale"),
                "OVERLAY_RISK_CONFIG_VERSION_MISMATCH",
            ),
        ],
    )
    def test_anything_beyond_the_approved_overlay_is_refused(
        self, mutate: Any, expected: str
    ) -> None:
        overlay = good_overlay()
        mutate(overlay)
        assert expected in start_refusals(good_spec(), good_inputs(overlay=overlay))

    def test_the_shipped_overlay_file_is_exactly_the_approved_shape(self) -> None:
        import yaml

        shipped = yaml.safe_load((ROOT / "config" / "agent_canary_demo.yaml").read_text("utf-8"))
        assert set(shipped) == {"risk", "execution"}
        assert set(shipped["risk"]) == {"approved_config_version"}
        assert set(shipped["execution"]) == {
            "submission_enabled",
            "feedback_2_0_approved",
            "approved_canary_account_ref",
        }


class FakeIO:
    """A scripted world: a clock that only moves when the driver sleeps."""

    def __init__(
        self,
        *,
        start: datetime,
        inputs: StartInputs | None = None,
        inspections: list[CycleInspection] | None = None,
        cycle_results: list[CycleResult | Exception] | None = None,
        evidence: list[ReaderEvidence] | None = None,
        bar_lag_s: float = 30.0,
    ) -> None:
        self.clock = start
        self._inputs = inputs or good_inputs(now=start)
        self.inspections = list(inspections or [])
        self.cycle_results = list(cycle_results or [])
        self.evidence = list(evidence or [])
        self.bar_lag = timedelta(seconds=bar_lag_s)
        self.cycles: list[datetime] = []
        self.records: list[tuple[str, dict[str, Any]]] = []
        self.permit: CanaryPermit | None = good_permit()
        self.consumption: CanaryPermitConsumption | None = None

    def now(self) -> datetime:
        return self.clock

    def sleep(self, seconds: float) -> None:
        self.clock += timedelta(seconds=seconds)

    def start_inputs(self) -> StartInputs:
        return replace(self._inputs, now=self.clock)

    def permit_state(self) -> tuple[CanaryPermit | None, CanaryPermitConsumption | None]:
        return self.permit, self.consumption

    def latest_closed_bar_open(self) -> datetime | None:
        available = self.clock - self.bar_lag - BAR
        minute = available.minute - available.minute % 5
        return available.replace(minute=minute, second=0, microsecond=0)

    def reader_evidence(self) -> ReaderEvidence:
        return self.evidence.pop(0) if self.evidence else healthy()

    def run_cycle(self, bar_open: datetime) -> CycleResult:
        self.cycles.append(bar_open)
        item = (
            self.cycle_results.pop(0)
            if self.cycle_results
            else CycleResult(0, uuid4(), uuid4(), True)
        )
        if isinstance(item, Exception):
            raise item
        return item

    def inspect_cycle(self, cycle: CycleResult | None) -> CycleInspection:
        if self.inspections:
            return self.inspections.pop(0)
        return CycleInspection(outcome_type="NO_TRADE", reason_codes=("NO_LIQUIDITY_SWEEP",))

    def record(self, kind: str, **fields: Any) -> None:
        self.records.append((kind, fields))

    def kinds(self) -> list[str]:
        return [kind for kind, _ in self.records]


ATTEMPT_FILLED = CycleInspection(
    outcome_type="TRADE_PROPOSAL",
    permit_consumed=True,
    execution_events=(
        ExecutionEventRecord("REQUEST_CLAIMED"),
        ExecutionEventRecord("SUBMISSION_STARTED"),
        ExecutionEventRecord("FILLED"),
    ),
)
ATTEMPT_REJECTED = CycleInspection(
    outcome_type="TRADE_PROPOSAL",
    permit_consumed=True,
    execution_events=(ExecutionEventRecord("SUBMISSION_STARTED"), ExecutionEventRecord("REJECTED")),
)
PROPOSAL_BLOCKED = CycleInspection(
    outcome_type="TRADE_PROPOSAL",
    execution_events=(
        ExecutionEventRecord("REQUEST_CLAIMED"),
        ExecutionEventRecord("FINAL_RISK_BLOCKED", reason_codes=("RISK_PER_TRADE_LIMIT",)),
    ),
)


class TestDriverStaysClosedWithoutAValidPermit:
    @pytest.mark.parametrize(
        "inputs",
        [
            {"permit": None},
            {
                "consumption": CanaryPermitConsumption(
                    permit_id=PERMIT_ID, order_request_id=uuid4(), consumed_at_utc=W0
                )
            },
            {"permit": good_permit(valid_until_utc=W0 - timedelta(minutes=15))},
            {"permit": good_permit(approved_account_ref="0" * 16)},
            {"permit": good_permit(strategy_artifact_hash="d" * 64)},
            {"permit": good_permit(max_requested_risk_fraction=Decimal("0.01"))},
            {"permit": good_permit(entry_type=EntryType.MARKET)},
        ],
    )
    def test_no_cycle_is_ever_run_and_the_exit_is_refused(self, inputs: dict[str, Any]) -> None:
        io = FakeIO(start=W0 - timedelta(minutes=10), inputs=good_inputs(**inputs))
        outcome = run_window(good_spec(), io)

        assert outcome.exit_code is ExitCode.REFUSED_TO_START
        assert io.cycles == []
        assert io.kinds() == ["start", "REFUSED_TO_START"]


class TestBarWindowBoundaries:
    @pytest.mark.parametrize(
        ("close", "expected"),
        [
            (W0 - timedelta(seconds=1), False),  # closed before the window opened
            (W0, True),  # closes exactly as the window opens: its decision is inside
            (W1 - BAR, True),  # last bar whose decision still falls inside
            (W1 - timedelta(seconds=1), True),
            (W1, False),  # closes exactly at the end: its decision would be outside
            (W1 + BAR, False),
        ],
    )
    def test_only_bars_whose_decision_falls_inside_the_window_are_tradable(
        self, close: datetime, expected: bool
    ) -> None:
        assert bar_is_in_window(good_spec(), close - BAR) is expected


class TestOnlyInsideTheSelectedWindowOneBarAtATime:
    def test_nothing_runs_before_the_window_opens_and_the_first_cycle_is_inside_it(self) -> None:
        io = FakeIO(start=W0 - timedelta(minutes=30))
        run_window(good_spec(), io)
        assert io.cycles, "expected cycles inside the window"
        record_times = [bar + BAR for bar in io.cycles]
        assert all(W0 <= close < W1 for close in record_times)

    def test_each_closed_bar_is_processed_once_and_the_bar_closing_at_the_end_is_excluded(
        self,
    ) -> None:
        io = FakeIO(start=W0 - timedelta(minutes=2))
        outcome = run_window(good_spec(), io)

        assert outcome.exit_code is ExitCode.WINDOW_ELAPSED_NO_PROPOSAL
        assert len(io.cycles) == len(set(io.cycles)) == 12
        assert io.cycles[0] == W0 - BAR  # the bar that closed at the window start
        assert (
            io.cycles[-1] == W1 - 2 * BAR
        )  # last bar whose decision still falls inside the window
        assert W1 - BAR not in io.cycles  # its decision would land after the window closed
        assert all(bar_is_in_window(good_spec(), bar) for bar in io.cycles)

    def test_a_bar_noticed_too_late_is_skipped_never_traded_stale(self) -> None:
        io = FakeIO(start=W0 - timedelta(minutes=1), bar_lag_s=200.0)
        run_window(good_spec(), io)
        assert io.cycles == []
        assert "skipped_stale_bar" in io.kinds()

    def test_a_window_start_in_the_past_does_not_replay_bars_before_it(self) -> None:
        io = FakeIO(
            start=W0 + timedelta(minutes=20), inputs=good_inputs(now=W0 + timedelta(minutes=20))
        )
        run_window(good_spec(), io)
        assert all(bar + BAR >= W0 for bar in io.cycles)


class TestStopsAfterTheFirstSubmissionAttemptOrUnsubmittedProposal:
    def _run(self, inspections: list[CycleInspection]) -> tuple[Any, FakeIO]:
        io = FakeIO(start=W0 - timedelta(minutes=2), inspections=inspections)
        return run_window(good_spec(), io), io

    @pytest.mark.parametrize("attempt", [ATTEMPT_FILLED, ATTEMPT_REJECTED])
    def test_stops_immediately_after_a_submission_attempt_accepted_or_rejected(
        self, attempt: CycleInspection
    ) -> None:
        none = CycleInspection(outcome_type="NO_TRADE")
        outcome, io = self._run([none, none, attempt])

        assert outcome.exit_code is ExitCode.SUBMISSION_ATTEMPTED
        assert len(io.cycles) == 3  # no fourth cycle, even though the window has 9 bars left
        assert io.kinds()[-1] == "STOP_SUBMISSION_ATTEMPTED"

    def test_an_attempt_is_recognised_from_the_consumed_permit_alone(self) -> None:
        consumed_only = CycleInspection(outcome_type="TRADE_PROPOSAL", permit_consumed=True)
        outcome, io = self._run([consumed_only])
        assert outcome.exit_code is ExitCode.SUBMISSION_ATTEMPTED
        assert len(io.cycles) == 1

    def test_an_attempt_is_recognised_from_submission_started_alone(self) -> None:
        started_only = CycleInspection(
            outcome_type="TRADE_PROPOSAL",
            execution_events=(ExecutionEventRecord("SUBMISSION_STARTED"),),
        )
        outcome, io = self._run([started_only])
        assert outcome.exit_code is ExitCode.SUBMISSION_ATTEMPTED
        assert len(io.cycles) == 1

    def test_a_blocked_proposal_stops_for_review_and_is_never_retried(self) -> None:
        outcome, io = self._run([PROPOSAL_BLOCKED])
        assert outcome.exit_code is ExitCode.PROPOSAL_NOT_SUBMITTED
        assert len(io.cycles) == 1
        assert io.kinds()[-1] == "STOP_PROPOSAL_NOT_SUBMITTED"

    def test_no_trade_cycles_do_not_stop_the_driver(self) -> None:
        outcome, io = self._run([])
        assert outcome.exit_code is ExitCode.WINDOW_ELAPSED_NO_PROPOSAL
        assert len(io.cycles) == 12

    def test_a_cycle_that_crashes_after_submission_started_is_inspected_and_stops(self) -> None:
        io = FakeIO(
            start=W0 - timedelta(minutes=2),
            cycle_results=[TimeoutError("child killed")],
            inspections=[ATTEMPT_FILLED],
        )
        outcome = run_window(good_spec(), io)
        assert outcome.exit_code is ExitCode.SUBMISSION_ATTEMPTED
        assert len(io.cycles) == 1

    def test_a_cycle_that_crashes_with_no_submission_is_a_blocker_and_is_not_retried(self) -> None:
        io = FakeIO(start=W0 - timedelta(minutes=2), cycle_results=[RuntimeError("boom")])
        outcome = run_window(good_spec(), io)
        assert outcome.exit_code is ExitCode.BLOCKED
        assert len(io.cycles) == 1

    def test_a_nonzero_child_exit_with_no_submission_is_a_blocker(self) -> None:
        io = FakeIO(start=W0 - timedelta(minutes=2), cycle_results=[CycleResult(1)])
        assert run_window(good_spec(), io).exit_code is ExitCode.BLOCKED
        assert len(io.cycles) == 1

    def test_an_unknown_outcome_is_a_blocker_not_a_silent_continue(self) -> None:
        io = FakeIO(
            start=W0 - timedelta(minutes=2), inspections=[CycleInspection(outcome_type=None)]
        )
        assert run_window(good_spec(), io).exit_code is ExitCode.BLOCKED
        assert len(io.cycles) == 1


class TestFreshnessGateAndPermitRecheckBeforeEveryCycle:
    @pytest.mark.parametrize(
        "bad",
        [
            {"reader_status": "STALE"},
            {"reader_connected": False},
            {"heartbeat_age_s": 31.0},
            {"heartbeat_age_s": None},
            {"heartbeat_age_s": -60.0},
            {"tick_age_s": 45.0},
            {"snapshot_age_s": 151.0},
            {"position_set_state": "INCOMPLETE"},
            {"pending_order_set_state": "UNKNOWN"},
            {"dashboard_mt5": "DISCONNECTED"},
            {"dashboard_feed": "STALE"},
            {"agent_status": None},
            {"agent_artifact_hash": "e" * 64},
            {"errors": ("reader_health:FileNotFoundError",)},
        ],
    )
    def test_any_stale_or_unknown_reading_stops_the_driver_before_a_cycle(
        self, bad: dict[str, Any]
    ) -> None:
        io = FakeIO(start=W0 - timedelta(minutes=2), evidence=[replace(healthy(), **bad)])
        outcome = run_window(good_spec(), io)

        assert outcome.exit_code is ExitCode.BLOCKED
        assert io.cycles == []

    def test_a_permit_consumed_between_bars_stops_the_driver_before_the_next_cycle(self) -> None:
        io = FakeIO(start=W0 - timedelta(minutes=2))
        original = io.run_cycle

        def consume_after_first(bar_open: datetime) -> CycleResult:
            result = original(bar_open)
            io.consumption = CanaryPermitConsumption(
                permit_id=PERMIT_ID, order_request_id=uuid4(), consumed_at_utc=io.clock
            )
            return result

        io.run_cycle = consume_after_first  # type: ignore[method-assign]
        io.inspections = [CycleInspection(outcome_type="NO_TRADE")]
        outcome = run_window(good_spec(), io)

        assert outcome.exit_code is ExitCode.BLOCKED
        assert "PERMIT_ALREADY_CONSUMED" in outcome.reasons
        assert len(io.cycles) == 1

    def test_a_permit_that_expires_mid_window_stops_the_driver(self) -> None:
        io = FakeIO(start=W0 - timedelta(minutes=2))
        io.permit = good_permit(valid_until_utc=W0 + timedelta(minutes=7))
        outcome = run_window(good_spec(), io)
        assert outcome.exit_code is ExitCode.BLOCKED
        assert "PERMIT_EXPIRED" in outcome.reasons


class TestTheDriverNeverTouchesAPermitOrWidensScope:
    DRIVER = ROOT / "scripts" / "canary_window_driver.py"
    LIBRARY = ROOT / "src" / "crumblr" / "application" / "canary_window.py"

    def _sources(self) -> dict[str, str]:
        return {
            path.name: re.sub(r'""".*?"""', "", path.read_text("utf-8"), flags=re.S)
            for path in (self.DRIVER, self.LIBRARY)
        }

    def test_neither_file_can_issue_edit_or_consume_a_permit(self) -> None:
        for name, source in self._sources().items():
            for forbidden in (
                ".issue(",
                ".consume(",
                ".transaction(",
                "CanaryPermit(",
                "INSERT",
                "UPDATE",
            ):
                assert forbidden not in source, f"{name} contains {forbidden!r}"

    def test_the_store_is_only_read(self) -> None:
        source = self._sources()["canary_window_driver.py"]
        assert "permit_for(" in source and "consumption_for(" in source

    def test_the_child_run_always_gets_both_canary_flags_and_nothing_flatten_related(self) -> None:
        source = self._sources()["canary_window_driver.py"]
        assert '"--apply-canary-config"' in source
        assert '"--canary-permit-id"' in source
        assert "flatten" not in source.lower()

    def test_apply_canary_config_is_a_required_argument(self) -> None:
        text = self.DRIVER.read_text("utf-8")
        block = text[text.index('"--apply-canary-config"') :][:200]
        assert "required=True" in block

    def test_the_driver_never_imports_the_real_order_send_gateway(self) -> None:
        for source in self._sources().values():
            assert "demo_execution" not in source

    def test_every_scope_argument_is_required_with_no_default(self) -> None:
        text = self.DRIVER.read_text("utf-8")
        for flag in (
            "--canary-permit-id",
            "--agent-id",
            "--assignment-id",
            "--strategy-artifact-hash",
            "--expected-account-ref",
            "--expected-server",
            "--entry-type",
            "--max-requested-risk-fraction",
            "--window-start",
            "--window-end",
        ):
            line = text[text.index(f'"{flag}"') :].split(")\n", 1)[0]
            assert "required=True" in line, flag
            assert "default=" not in line, flag


class TestChildCommandMatchesTheCanaryScriptsOwnParser:
    def _args(self) -> Any:
        from scripts.canary_window_driver import parse_args

        return parse_args(
            [
                "--canary-permit-id", str(PERMIT_ID),
                "--apply-canary-config",
                "--agent-id", str(AGENT),
                "--assignment-id", str(ASSIGNMENT),
                "--strategy-artifact-hash", ARTIFACT,
                "--expected-account-ref", ACCOUNT_REF,
                "--expected-server", SERVER,
                "--entry-type", "LIMIT",
                "--max-requested-risk-fraction", "0.0005",
                "--window-start", "2026-10-06T14:00:00+00:00",
                "--window-end", "2026-10-06T15:00:00+00:00",
            ]
        )  # fmt: skip

    def test_the_child_command_parses_with_the_real_script_and_carries_exactly_the_scope(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from scripts.agent_canary_execution import parse_args as child_parse
        from scripts.canary_window_driver import child_command

        command = child_command(self._args(), "deadbeef")
        monkeypatch.setattr("sys.argv", ["agent_canary_execution.py", *command[2:]])
        parsed = child_parse()

        assert parsed.once is True
        assert parsed.apply_canary_config is True
        assert parsed.enable_external_supervisor is True
        assert parsed.canary_permit_id == PERMIT_ID
        assert parsed.agent_id == AGENT
        assert parsed.assignment_id == ASSIGNMENT
        assert parsed.code_commit == "deadbeef"
        assert parsed.environment == "paper"

    def test_the_driver_cli_refuses_to_parse_without_apply_canary_config(self) -> None:
        from scripts.canary_window_driver import parse_args

        with pytest.raises(SystemExit):
            parse_args(
                [
                    "--canary-permit-id", str(PERMIT_ID),
                    "--agent-id", str(AGENT),
                    "--assignment-id", str(ASSIGNMENT),
                    "--strategy-artifact-hash", ARTIFACT,
                    "--expected-account-ref", ACCOUNT_REF,
                    "--expected-server", SERVER,
                    "--entry-type", "LIMIT",
                    "--max-requested-risk-fraction", "0.0005",
                    "--window-start", "2026-10-06T14:00:00+00:00",
                    "--window-end", "2026-10-06T15:00:00+00:00",
                ]
            )  # fmt: skip

    def test_a_window_without_a_utc_offset_is_refused(self) -> None:
        from scripts.canary_window_driver import parse_args

        with pytest.raises(SystemExit):
            parse_args(
                [
                    "--canary-permit-id", str(PERMIT_ID),
                    "--apply-canary-config",
                    "--agent-id", str(AGENT),
                    "--assignment-id", str(ASSIGNMENT),
                    "--strategy-artifact-hash", ARTIFACT,
                    "--expected-account-ref", ACCOUNT_REF,
                    "--expected-server", SERVER,
                    "--entry-type", "LIMIT",
                    "--max-requested-risk-fraction", "0.0005",
                    "--window-start", "2026-10-06T14:00:00",
                    "--window-end", "2026-10-06T15:00:00+00:00",
                ]
            )  # fmt: skip


class TestPermitIsLimitedToTheSelectedWindow:
    def test_the_margins_are_inclusive_at_their_limits(self) -> None:
        edge = good_permit(
            issued_at_utc=W0 - timedelta(minutes=60), valid_until_utc=W1 + timedelta(minutes=10)
        )
        inputs = good_inputs(permit=edge, now=W0 - timedelta(minutes=5))
        assert start_refusals(good_spec(), inputs) == ()
