"""Fail-closed behaviour for safety-critical state.

Covers review findings F-002 (absence of evidence is not evidence of safety)
and F-003 (a halt must survive the process that tripped it).

The property under test throughout is that *not knowing* produces a refusal.
It is easy to write a control that refuses when it detects danger; the harder
and more valuable property is that it also refuses when it cannot tell.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from crumblr.domain.enums import (
    IncidentStatus,
    KillSwitchState,
    ReasonCode,
    ReconciliationStatus,
    Regime,
    SupervisorVerdict,
)
from crumblr.domain.models import SupervisorDecision
from crumblr.domain.timeutils import utc_now
from crumblr.evaluator import pretrade
from crumblr.risk.kill_switch import KillSwitch
from crumblr.risk.safety_state import (
    FileSafetyStateStore,
    InMemorySafetyStateStore,
    SafetyState,
)
from tests.conftest import FIXED_NOW, make_intent
from tests.unit.test_supervisor import features, known_good_context

NOW = FIXED_NOW


def judge(context: pretrade.SupervisorContext) -> SupervisorDecision:
    return pretrade.evaluate(
        make_intent(confidence=0.6),
        features(Regime.TREND),
        pretrade.SupervisorPolicy(),
        context,
        now=NOW,
    )


class TestSupervisorDefaultsAreUnsafe:
    """F-002: the defaults must be UNKNOWN, not the safe values."""

    def test_a_bare_context_reports_unknown_reconciliation(self) -> None:
        assert pretrade.SupervisorContext().reconciliation_status is ReconciliationStatus.UNKNOWN

    def test_a_bare_context_reports_unknown_incident_state(self) -> None:
        assert pretrade.SupervisorContext().incident_status is IncidentStatus.UNKNOWN

    def test_a_caller_that_wires_up_nothing_gets_a_halt(self) -> None:
        """The regression this finding was about: silence must not read as safe."""
        decision = judge(pretrade.SupervisorContext())
        assert decision.verdict is SupervisorVerdict.HALT
        assert ReasonCode.RECONCILIATION_UNKNOWN in decision.reason_codes


class TestUnknownReconciliationHalts:
    def test_unknown_reconciliation_halts(self) -> None:
        decision = judge(known_good_context(reconciliation_status=ReconciliationStatus.UNKNOWN))
        assert decision.verdict is SupervisorVerdict.HALT
        assert ReasonCode.RECONCILIATION_UNKNOWN in decision.reason_codes

    def test_mismatched_reconciliation_halts_with_its_own_code(self) -> None:
        """Unknown and mismatched are different failures and read differently."""
        decision = judge(known_good_context(reconciliation_status=ReconciliationStatus.MISMATCHED))
        assert decision.verdict is SupervisorVerdict.HALT
        assert ReasonCode.RECONCILIATION_MISMATCH in decision.reason_codes

    def test_matched_reconciliation_permits_the_trade(self) -> None:
        decision = judge(known_good_context())
        assert decision.verdict is SupervisorVerdict.APPROVE

    def test_disabling_the_supervisor_cannot_launder_unknown_state(self) -> None:
        """Switching off policy judgement must not switch off the safety gate."""
        decision = pretrade.evaluate(
            make_intent(confidence=0.6),
            features(Regime.TREND),
            pretrade.SupervisorPolicy(enabled=False),
            pretrade.SupervisorContext(),
            now=NOW,
        )
        assert decision.verdict is SupervisorVerdict.HALT
        assert ReasonCode.RECONCILIATION_UNKNOWN in decision.reason_codes


class TestUnknownIncidentStateVetoes:
    def test_unknown_incident_state_vetoes(self) -> None:
        decision = judge(known_good_context(incident_status=IncidentStatus.UNKNOWN))
        assert decision.verdict is SupervisorVerdict.VETO
        assert ReasonCode.INCIDENT_STATE_UNKNOWN in decision.reason_codes

    def test_an_active_incident_vetoes_with_its_own_code(self) -> None:
        decision = judge(known_good_context(incident_status=IncidentStatus.ACTIVE))
        assert ReasonCode.ACTIVE_INCIDENT in decision.reason_codes
        assert ReasonCode.INCIDENT_STATE_UNKNOWN not in decision.reason_codes

    def test_a_clear_register_permits_the_trade(self) -> None:
        decision = judge(known_good_context(incident_status=IncidentStatus.CLEAR))
        assert decision.verdict is SupervisorVerdict.APPROVE


class TestHaltSurvivesRestart:
    """F-003: a restart must not be a way to clear a halt."""

    def test_a_cold_start_with_no_record_is_halted(self, tmp_path: Path) -> None:
        store = FileSafetyStateStore(tmp_path / "safety.json")
        switch = KillSwitch.on_startup(store)
        assert switch.is_halted
        assert switch.state is KillSwitchState.UNKNOWN

    def test_a_halt_survives_a_new_process(self, tmp_path: Path) -> None:
        store = FileSafetyStateStore(tmp_path / "safety.json")
        first = KillSwitch(store)
        first.trip(
            reason_codes=(ReasonCode.DAILY_LOSS_LIMIT,),
            tripped_by="risk_engine",
            occurred_at_utc=utc_now(),
        )

        restarted = KillSwitch.on_startup(FileSafetyStateStore(tmp_path / "safety.json"))
        assert restarted.is_halted
        assert restarted.state is KillSwitchState.HALTED
        assert ReasonCode.DAILY_LOSS_LIMIT in restarted.active_reasons

    def test_only_an_operator_reset_survives_as_running(self, tmp_path: Path) -> None:
        path = tmp_path / "safety.json"
        switch = KillSwitch(FileSafetyStateStore(path))
        switch.trip(
            reason_codes=(ReasonCode.MAX_DRAWDOWN,),
            tripped_by="risk_engine",
            occurred_at_utc=utc_now(),
        )
        switch.reset(operator="levi", incident_note="INC-4 closed: feed replaced")

        assert not KillSwitch.on_startup(FileSafetyStateStore(path)).is_halted

    def test_a_corrupt_record_is_halted_not_ignored(self, tmp_path: Path) -> None:
        path = tmp_path / "safety.json"
        path.write_text("{ this is not json", encoding="utf-8")
        switch = KillSwitch.on_startup(FileSafetyStateStore(path))
        assert switch.is_halted
        assert switch.state is KillSwitchState.UNKNOWN
        assert switch.startup_detail is not None
        assert "unreadable" in switch.startup_detail

    def test_a_record_from_another_schema_is_halted(self, tmp_path: Path) -> None:
        """A record this version does not understand may not mean what it says."""
        path = tmp_path / "safety.json"
        path.write_text(json.dumps({"schema_version": 99, "state": "RUNNING"}), encoding="utf-8")
        assert KillSwitch.on_startup(FileSafetyStateStore(path)).is_halted

    def test_a_record_missing_fields_is_halted(self, tmp_path: Path) -> None:
        path = tmp_path / "safety.json"
        path.write_text(json.dumps({"schema_version": 1, "state": "RUNNING"}), encoding="utf-8")
        assert KillSwitch.on_startup(FileSafetyStateStore(path)).is_halted

    def test_an_unrecognised_state_value_is_halted(self, tmp_path: Path) -> None:
        path = tmp_path / "safety.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "state": "PROBABLY_FINE",
                    "reason_codes": [],
                    "recorded_at_utc": utc_now().isoformat(),
                }
            ),
            encoding="utf-8",
        )
        assert KillSwitch.on_startup(FileSafetyStateStore(path)).is_halted

    def test_the_write_is_atomic_enough_to_leave_no_partial_file(self, tmp_path: Path) -> None:
        """A half-written halt would read back as an unknown one, not a clear one."""
        path = tmp_path / "safety.json"
        store = FileSafetyStateStore(path)
        store.save(
            SafetyState(
                state=KillSwitchState.HALTED,
                reason_codes=(ReasonCode.MANUAL_HALT,),
                recorded_at_utc=utc_now(),
                tripped_by="operator",
            )
        )
        leftovers = [p for p in tmp_path.iterdir() if p.name != "safety.json"]
        assert leftovers == [], f"temporary files were left behind: {leftovers}"
        assert store.load().state is KillSwitchState.HALTED


class TestUnknownStateCountsAsHalted:
    def test_an_unknown_switch_reports_halted(self) -> None:
        switch = KillSwitch.on_startup(InMemorySafetyStateStore())
        assert switch.state is KillSwitchState.UNKNOWN
        assert switch.is_halted, "a system that has lost track of itself must not trade"

    def test_an_unknown_switch_carries_a_reason(self) -> None:
        switch = KillSwitch.on_startup(InMemorySafetyStateStore())
        assert ReasonCode.SAFETY_STATE_UNKNOWN in switch.active_reasons

    def test_an_explicitly_running_record_permits_trading(self) -> None:
        store = InMemorySafetyStateStore(
            SafetyState(
                state=KillSwitchState.RUNNING,
                reason_codes=(),
                recorded_at_utc=utc_now(),
            )
        )
        assert not KillSwitch.on_startup(store).is_halted

    def test_a_failed_persist_prevents_the_halt_from_being_believed(self) -> None:
        """If the record cannot be written, the process must not think it halted."""

        class FailingStore:
            def load(self) -> SafetyState:
                raise AssertionError("not used in this test")

            def save(self, state: SafetyState) -> None:
                raise OSError("disk unavailable")

        switch = KillSwitch(FailingStore())
        with pytest.raises(OSError):
            switch.trip(
                reason_codes=(ReasonCode.MANUAL_HALT,),
                tripped_by="operator",
                occurred_at_utc=utc_now(),
            )
