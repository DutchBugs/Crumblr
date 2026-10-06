"""Canary-mode window driver: the decision logic, with no I/O of its own.

`scripts/canary_window_driver.py` wires real I/O into this; the unit tests wire fakes. Nothing
in this module can reach a broker, a database or a clock: it only decides, from what the
injected `WindowIO` reports, whether to refuse, wait, run one decision cycle, or stop.

What this driver is for
-----------------------
One controlled Pepperstone DEMO canary from a genuine Static Agent proposal. It runs the
existing `scripts/agent_canary_execution.py --once` (the only code that can reach the real
`order_send`) once per newly closed M5 bar, inside one explicitly selected strategy window, with
the operator's pre-issued one-shot permit. It never issues, edits or consumes a permit itself
(the orchestrator consumes it, atomically, inside the child run), never widens its scope and
never touches any flag the owner did not approve.

Safety properties enforced here (each has a test)
-------------------------------------------------
* Refuses to start unless the permit exists, is unexpired and unconsumed, and EXACTLY matches
  what the operator restated on the command line: account fingerprint, server, agent,
  assignment, StrategyArtifact hash, entry type and max risk fraction, and its validity covers
  the whole window without extending far beyond it (<= 10 min past the end, issued <= 60 min
  before the start). The live account, the overlay file and the running Agent must agree too.
* Runs only inside the selected window, at most one newly closed bar at a time, never a bar
  whose decision would fall outside the window, never twice for the same bar.
* Reader/Agent freshness is re-verified before every cycle; anything stale stops the driver.
* Stops immediately after the first submission attempt (permit consumed or `SUBMISSION_STARTED`),
  accepted or rejected, and after any proposal that was not submitted, so a human reviews it.
  It never retries an intent: a cycle that raises is inspected once and then the driver stops.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import IntEnum
from typing import Any, Protocol
from uuid import UUID

from crumblr.domain.enums import EntryType
from crumblr.domain.models import CanaryPermit, CanaryPermitConsumption

BAR = timedelta(minutes=5)
MAX_WINDOW = timedelta(minutes=60)
PERMIT_MAX_LEAD_BEFORE_WINDOW = timedelta(minutes=60)
"""A permit issued longer before the window than this is not "for this window"."""
PERMIT_MAX_MARGIN_AFTER_WINDOW = timedelta(minutes=10)
"""A permit valid longer past the window end than this is wider than the window."""
MAX_BAR_AGE_AT_CYCLE = timedelta(seconds=90)
"""A bar older than this when the driver notices it is skipped, never traded late."""
MIN_TIME_LEFT_IN_WINDOW = timedelta(seconds=15)
POLL_SECONDS = 3.0

READER_HEARTBEAT_MAX_AGE_S = 30.0
READER_TICK_MAX_AGE_S = 30.0
READER_SNAPSHOT_MAX_AGE_S = 150.0

CANONICAL_SYMBOL = "EUR/USD"
SUBMISSION_STARTED = "SUBMISSION_STARTED"

APPROVED_OVERLAY_EXECUTION_KEYS = frozenset(
    {"submission_enabled", "feedback_2_0_approved", "approved_canary_account_ref"}
)
APPROVED_OVERLAY_RISK_KEYS = frozenset({"approved_config_version"})


class ExitCode(IntEnum):
    WINDOW_ELAPSED_NO_PROPOSAL = 0
    REFUSED_TO_START = 2
    PROPOSAL_NOT_SUBMITTED = 3
    BLOCKED = 4
    SUBMISSION_ATTEMPTED = 10


@dataclass(frozen=True)
class CanaryWindowSpec:
    """Everything the operator must restate; nothing has a default that could widen scope."""

    permit_id: UUID
    agent_id: UUID
    assignment_id: UUID
    strategy_artifact_hash: str
    expected_account_ref: str
    expected_server: str
    entry_type: EntryType
    max_requested_risk_fraction: Decimal
    window_start_utc: datetime
    window_end_utc: datetime


@dataclass(frozen=True)
class StartInputs:
    permit: CanaryPermit | None
    consumption: CanaryPermitConsumption | None
    overlay: Mapping[str, Any]
    config_version: str
    live_account_ref: str | None
    live_account_server: str | None
    agent_artifact_hash: str | None
    now: datetime


def overlay_refusals(
    overlay: Mapping[str, Any], *, expected_account_ref: str, config_version: str
) -> tuple[str, ...]:
    """The canary overlay may say exactly the four approved things and nothing else."""
    reasons: list[str] = []
    if set(overlay) - {"risk", "execution"}:
        reasons.append("OVERLAY_HAS_UNAPPROVED_SECTION")
    risk = overlay.get("risk") or {}
    execution = overlay.get("execution") or {}
    if set(risk) != APPROVED_OVERLAY_RISK_KEYS:
        reasons.append("OVERLAY_RISK_KEYS_NOT_EXACTLY_APPROVED")
    if set(execution) != APPROVED_OVERLAY_EXECUTION_KEYS:
        reasons.append("OVERLAY_EXECUTION_KEYS_NOT_EXACTLY_APPROVED")
    if execution.get("flatten_submission_enabled") not in (None, False):
        reasons.append("OVERLAY_ENABLES_FLATTEN")
    if execution.get("submission_enabled") is not True:
        reasons.append("OVERLAY_SUBMISSION_NOT_ENABLED")
    if execution.get("feedback_2_0_approved") is not True:
        reasons.append("OVERLAY_FEEDBACK_2_0_NOT_APPROVED")
    if execution.get("approved_canary_account_ref") != expected_account_ref:
        reasons.append("OVERLAY_ACCOUNT_REF_MISMATCH")
    if risk.get("approved_config_version") != config_version:
        reasons.append("OVERLAY_RISK_CONFIG_VERSION_MISMATCH")
    return tuple(reasons)


def start_refusals(spec: CanaryWindowSpec, inputs: StartInputs) -> tuple[str, ...]:
    """Every reason this driver may not start. Empty means it may. Never short-circuits."""
    reasons: list[str] = []
    now = inputs.now

    if spec.window_end_utc <= spec.window_start_utc:
        reasons.append("WINDOW_END_NOT_AFTER_START")
    elif spec.window_end_utc - spec.window_start_utc > MAX_WINDOW:
        reasons.append("WINDOW_LONGER_THAN_ONE_STRATEGY_WINDOW")
    if now >= spec.window_end_utc:
        reasons.append("WINDOW_ALREADY_OVER")

    permit = inputs.permit
    if permit is None:
        reasons.append("PERMIT_NOT_FOUND")
    else:
        if inputs.consumption is not None:
            reasons.append("PERMIT_ALREADY_CONSUMED")
        if now >= permit.valid_until_utc:
            reasons.append("PERMIT_EXPIRED")
        if spec.window_start_utc < permit.issued_at_utc:
            reasons.append("WINDOW_STARTS_BEFORE_PERMIT_ISSUED")
        if spec.window_end_utc > permit.valid_until_utc:
            reasons.append("PERMIT_EXPIRES_BEFORE_WINDOW_ENDS")
        if permit.valid_until_utc > spec.window_end_utc + PERMIT_MAX_MARGIN_AFTER_WINDOW:
            reasons.append("PERMIT_VALID_LONGER_THAN_THE_WINDOW")
        if permit.issued_at_utc < spec.window_start_utc - PERMIT_MAX_LEAD_BEFORE_WINDOW:
            reasons.append("PERMIT_ISSUED_TOO_LONG_BEFORE_THE_WINDOW")
        if permit.approved_account_ref != spec.expected_account_ref:
            reasons.append("PERMIT_ACCOUNT_REF_MISMATCH")
        if permit.expected_server != spec.expected_server:
            reasons.append("PERMIT_SERVER_MISMATCH")
        if permit.canonical_symbol != CANONICAL_SYMBOL:
            reasons.append("PERMIT_SYMBOL_MISMATCH")
        if permit.agent_id != spec.agent_id:
            reasons.append("PERMIT_AGENT_MISMATCH")
        if permit.assignment_id != spec.assignment_id:
            reasons.append("PERMIT_ASSIGNMENT_MISMATCH")
        if permit.strategy_artifact_hash != spec.strategy_artifact_hash:
            reasons.append("PERMIT_ARTIFACT_MISMATCH")
        if permit.entry_type != spec.entry_type:
            reasons.append("PERMIT_ENTRY_TYPE_MISMATCH")
        if permit.max_requested_risk_fraction != spec.max_requested_risk_fraction:
            reasons.append("PERMIT_RISK_FRACTION_MISMATCH")

    reasons.extend(
        overlay_refusals(
            inputs.overlay,
            expected_account_ref=spec.expected_account_ref,
            config_version=inputs.config_version,
        )
    )
    if inputs.live_account_ref != spec.expected_account_ref:
        reasons.append("LIVE_ACCOUNT_REF_MISMATCH_OR_UNKNOWN")
    if inputs.live_account_server != spec.expected_server:
        reasons.append("LIVE_ACCOUNT_SERVER_MISMATCH_OR_UNKNOWN")
    if inputs.agent_artifact_hash != spec.strategy_artifact_hash:
        reasons.append("RUNNING_AGENT_ARTIFACT_MISMATCH_OR_UNKNOWN")
    return tuple(reasons)


def permit_still_usable_refusals(
    spec: CanaryWindowSpec,
    permit: CanaryPermit | None,
    consumption: CanaryPermitConsumption | None,
    now: datetime,
) -> tuple[str, ...]:
    """Re-checked before every cycle: the permit may have been consumed or expired meanwhile."""
    reasons: list[str] = []
    if permit is None:
        reasons.append("PERMIT_NOT_FOUND")
        return tuple(reasons)
    if consumption is not None:
        reasons.append("PERMIT_ALREADY_CONSUMED")
    if now >= permit.valid_until_utc:
        reasons.append("PERMIT_EXPIRED")
    return tuple(reasons)


@dataclass(frozen=True)
class ReaderEvidence:
    """What the driver observed about the Reader, dashboard and Agent just before a cycle."""

    reader_status: str | None = None
    reader_connected: bool | None = None
    heartbeat_age_s: float | None = None
    tick_age_s: float | None = None
    snapshot_age_s: float | None = None
    position_set_state: str | None = None
    pending_order_set_state: str | None = None
    dashboard_mt5: str | None = None
    dashboard_feed: str | None = None
    agent_status: str | None = None
    agent_artifact_hash: str | None = None
    errors: tuple[str, ...] = ()


def freshness_refusals(evidence: ReaderEvidence, *, expected_artifact_hash: str) -> tuple[str, ...]:
    """The per-bar freshness gate. Anything unknown counts as stale."""
    reasons: list[str] = [f"UNREADABLE:{error}" for error in evidence.errors]

    def stale(age: float | None, limit: float) -> bool:
        return age is None or age > limit or age < -5.0

    if evidence.reader_status != "HEALTHY" or evidence.reader_connected is not True:
        reasons.append("READER_NOT_HEALTHY_AND_CONNECTED")
    if stale(evidence.heartbeat_age_s, READER_HEARTBEAT_MAX_AGE_S):
        reasons.append("READER_HEARTBEAT_STALE")
    if stale(evidence.tick_age_s, READER_TICK_MAX_AGE_S):
        reasons.append("READER_TICK_STALE")
    if stale(evidence.snapshot_age_s, READER_SNAPSHOT_MAX_AGE_S):
        reasons.append("BROKER_SNAPSHOT_STALE")
    if evidence.position_set_state != "COMPLETE" or evidence.pending_order_set_state != "COMPLETE":
        reasons.append("BROKER_STATE_INCOMPLETE")
    if evidence.dashboard_mt5 != "CONNECTED" or evidence.dashboard_feed != "HEALTHY":
        reasons.append("DASHBOARD_REPORTS_UNHEALTHY")
    if evidence.agent_status != "READY":
        reasons.append("AGENT_NOT_READY")
    if evidence.agent_artifact_hash != expected_artifact_hash:
        reasons.append("AGENT_ARTIFACT_MISMATCH")
    return tuple(reasons)


@dataclass(frozen=True)
class CycleResult:
    exit_code: int
    outcome_id: UUID | None = None
    capsule_id: UUID | None = None
    gateway_accepted: bool | None = None
    raw_log: str | None = None


@dataclass(frozen=True)
class ExecutionEventRecord:
    event_type: str
    occurred_at_utc: datetime | None = None
    reason_codes: tuple[str, ...] = ()
    detail: str | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CycleInspection:
    outcome_type: str | None
    reason_codes: tuple[str, ...] = ()
    permit_consumed: bool = False
    execution_events: tuple[ExecutionEventRecord, ...] = ()
    risk_verdict: str | None = None
    policy_verdict: str | None = None

    @property
    def submission_attempted(self) -> bool:
        return self.permit_consumed or any(
            event.event_type == SUBMISSION_STARTED for event in self.execution_events
        )


class WindowIO(Protocol):
    def now(self) -> datetime: ...

    def sleep(self, seconds: float) -> None: ...

    def start_inputs(self) -> StartInputs: ...

    def permit_state(self) -> tuple[CanaryPermit | None, CanaryPermitConsumption | None]: ...

    def latest_closed_bar_open(self) -> datetime | None: ...

    def reader_evidence(self) -> ReaderEvidence: ...

    def run_cycle(self, bar_open: datetime) -> CycleResult: ...

    def inspect_cycle(self, cycle: CycleResult | None) -> CycleInspection: ...

    def record(self, kind: str, **fields: Any) -> None: ...


@dataclass(frozen=True)
class WindowOutcome:
    exit_code: ExitCode
    reason: str
    cycles_run: int
    reasons: tuple[str, ...] = ()


def bar_is_in_window(spec: CanaryWindowSpec, bar_open: datetime) -> bool:
    """A bar is tradable only if the decision made just after it closes is inside the window."""
    close = bar_open + BAR
    return spec.window_start_utc <= close < spec.window_end_utc


def run_window(spec: CanaryWindowSpec, io: WindowIO) -> WindowOutcome:
    io.record(
        "start",
        permit_id=str(spec.permit_id),
        window=[spec.window_start_utc.isoformat(), spec.window_end_utc.isoformat()],
        entry_type=spec.entry_type.value,
        max_requested_risk_fraction=str(spec.max_requested_risk_fraction),
        expected_account_ref=spec.expected_account_ref,
    )
    refusals = start_refusals(spec, io.start_inputs())
    if refusals:
        io.record("REFUSED_TO_START", reasons=list(refusals))
        return WindowOutcome(ExitCode.REFUSED_TO_START, "refused to start", 0, refusals)

    processed: set[datetime] = set()
    cycles = 0
    while True:
        now = io.now()
        if now >= spec.window_end_utc:
            io.record("window_over", cycles_run=cycles)
            return WindowOutcome(ExitCode.WINDOW_ELAPSED_NO_PROPOSAL, "window elapsed", cycles)
        if now < spec.window_start_utc:
            io.sleep(min(POLL_SECONDS * 5, (spec.window_start_utc - now).total_seconds()))
            continue

        bar_open = io.latest_closed_bar_open()
        if bar_open is None or bar_open in processed or not bar_is_in_window(spec, bar_open):
            io.sleep(POLL_SECONDS)
            continue

        processed.add(bar_open)
        if now - (bar_open + BAR) > MAX_BAR_AGE_AT_CYCLE:
            io.record("skipped_stale_bar", bar_open=bar_open.isoformat())
            continue
        if spec.window_end_utc - now < MIN_TIME_LEFT_IN_WINDOW:
            io.record("skipped_bar_too_close_to_window_end", bar_open=bar_open.isoformat())
            continue

        permit, consumption = io.permit_state()
        permit_reasons = permit_still_usable_refusals(spec, permit, consumption, now)
        if permit_reasons:
            io.record("BLOCKER", reasons=list(permit_reasons), stage="permit_recheck")
            return WindowOutcome(
                ExitCode.BLOCKED, "permit no longer usable", cycles, permit_reasons
            )

        evidence = io.reader_evidence()
        stale = freshness_refusals(evidence, expected_artifact_hash=spec.strategy_artifact_hash)
        if stale:
            io.record("BLOCKER", reasons=list(stale), stage="freshness_gate", evidence=evidence)
            return WindowOutcome(ExitCode.BLOCKED, "freshness gate failed", cycles, stale)

        io.record("cycle_start", bar_open=bar_open.isoformat(), evidence=evidence)
        cycle: CycleResult | None = None
        failure: str | None = None
        try:
            cycle = io.run_cycle(bar_open)
        except Exception as error:  # any failure stops the driver; see below
            failure = f"{type(error).__name__}: {error}"
        cycles += 1

        # Always inspect, even after a failure: a run killed after SUBMISSION_STARTED must be seen.
        inspection = io.inspect_cycle(cycle)
        io.record(
            "cycle_done",
            bar_open=bar_open.isoformat(),
            cycle=cycle,
            inspection=inspection,
            failure=failure,
        )

        if inspection.submission_attempted:
            io.record("STOP_SUBMISSION_ATTEMPTED", inspection=inspection)
            return WindowOutcome(ExitCode.SUBMISSION_ATTEMPTED, "submission attempted", cycles)
        if failure is not None or cycle is None or cycle.exit_code != 0:
            io.record("BLOCKER", reasons=["CYCLE_FAILED"], stage="cycle", failure=failure)
            return WindowOutcome(ExitCode.BLOCKED, "cycle failed", cycles, ("CYCLE_FAILED",))
        if inspection.outcome_type is None:
            io.record("BLOCKER", reasons=["OUTCOME_UNKNOWN"], stage="inspection")
            return WindowOutcome(ExitCode.BLOCKED, "outcome unknown", cycles, ("OUTCOME_UNKNOWN",))
        if inspection.outcome_type != "NO_TRADE":
            io.record("STOP_PROPOSAL_NOT_SUBMITTED", inspection=inspection)
            return WindowOutcome(
                ExitCode.PROPOSAL_NOT_SUBMITTED, "proposal seen but not submitted", cycles
            )
