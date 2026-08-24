"""run → persist → stop the process → restart → recover (review 1.5 §4).

`test_halt_survives_restart.py` proves the property for the kill switch alone,
against a file. This proves it for a *run*: a whole replay, writing its
journal and its risk session to PostgreSQL, in a process that then exits.

The distinction the reviewer drew in 1.1 applies here too. Building two
objects in one interpreter proves a store round-trips; it does not prove
anything survives a restart. So the first half of every test below happens in
a genuinely separate Python process, which has exited by the time the
assertions run.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Engine

from crumblr.application.bootstrap import build_durable_runtime
from crumblr.application.reconstruction import reconstruct_from_journal
from crumblr.domain.enums import Environment
from crumblr.persistence.engine import DEFAULT_TEST_URL
from crumblr.persistence.journal import EventJournal
from crumblr.persistence.risk_session import PostgresRiskSessionStore

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]

RUN_IN_A_SEPARATE_PROCESS = """
import json, sys
from decimal import Decimal
from pathlib import Path

from scripts.run_replay import build_instrument_spec

from crumblr.application.bootstrap import build_durable_runtime
from crumblr.application.orchestration import ReplayOrchestrator
from crumblr.config import load_config
from crumblr.domain.enums import Environment
from crumblr.market_data.synthetic import SyntheticMarketConfig, generate_ticks
from crumblr.mt5_gateway.simulated import SimulatedBroker

state_file, url, repo_root = sys.argv[1], sys.argv[2], sys.argv[3]
start, end, balance = int(sys.argv[4]), int(sys.argv[5]), Decimal(sys.argv[6])

shipped = load_config(Environment.PAPER, config_dir=Path(repo_root) / "config")
agent = shipped.trading_agent.model_copy(update={"strategy_id": "baseline_v1"})
config = shipped.model_copy(update={"trading_agent": agent})

runtime = build_durable_runtime(
    environment=Environment.PAPER, state_file=Path(state_file), url=url
)
# An operator arming the system. Nothing automatic may do this, which is why
# it has to be spelled out in a test harness too.
runtime.kill_switch.reset(operator="process_a", incident_note="arming for a restart test")

spec = build_instrument_spec()
broker = SimulatedBroker(
    spec, starting_balance=balance, server=config.account_guard.expected_server
)
ticks = list(generate_ticks(SyntheticMarketConfig(bar_count=400), spec))[start:end]
result = ReplayOrchestrator(
    config,
    spec,
    broker,
    starting_equity=balance,
    recorder=runtime.recorder,
    kill_switch=runtime.kill_switch,
    session_store=runtime.session_store,
).run(ticks)
runtime.dispose()

# stdout is the handover to the parent; the logs go to stderr.
print(json.dumps({
    "capsules": len(result.capsules),
    "final_equity": str(result.final_equity),
    "open_positions": len(broker.positions()),
    "halted": result.halted,
    "halt_reasons": [code.value for code in result.halt_reasons],
    "session_start_equity": str(result.session_start_equity),
    "session_resumed": result.session_resumed,
    "last_event_time": ticks[-1].event_time_utc.isoformat(),
}))
"""

# The synthetic series rolls from one FX trading day to the next at tick 168,
# so these slices are chosen to exercise both cases deliberately rather than
# by accident: a restart inside a session, and a restart across the boundary.
FIRST_HALF = (0, 100)
SAME_DAY_CONTINUATION = (100, 168)
NEXT_DAY_CONTINUATION = (168, 260)
BALANCE = Decimal("10000")


def run_in_a_separate_process(
    state_file: Path,
    *,
    slice_: tuple[int, int] = (0, 300),
    balance: Decimal = BALANCE,
) -> dict[str, object]:
    """Run part of a replay in a fresh interpreter and let it exit."""
    start, end = slice_
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            RUN_IN_A_SEPARATE_PROCESS,
            str(state_file),
            DEFAULT_TEST_URL,
            str(REPO_ROOT),
            str(start),
            str(end),
            str(balance),
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONPATH": f"{REPO_ROOT}{os.pathsep}{REPO_ROOT / 'src'}"},
        check=False,
        timeout=300,
    )
    assert completed.returncode == 0, f"child process failed:\n{completed.stderr[-4000:]}"
    payload: dict[str, object] = json.loads(completed.stdout.strip().splitlines()[-1])
    return payload


class TestARunSurvivesTheProcessThatMadeIt:
    """The sequence review 1.5 §4 asks for, with a real process boundary."""

    def test_the_journal_holds_the_run_after_the_process_is_gone(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        child = run_in_a_separate_process(tmp_path / "safety.json")

        restored = reconstruct_from_journal(EventJournal(engine))

        assert restored.capsules, "the journal is empty; the run left nothing behind"
        assert len(restored.capsules) == child["capsules"]

    def test_a_halt_raised_by_the_child_is_still_in_force_here(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        state_file = tmp_path / "safety.json"
        child = run_in_a_separate_process(state_file)
        if not child["halted"]:
            pytest.skip("this replay did not halt; nothing to carry across the restart")

        runtime = build_durable_runtime(
            environment=Environment.PAPER, state_file=state_file, url=DEFAULT_TEST_URL
        )
        try:
            assert runtime.kill_switch.is_halted
            assert runtime.kill_switch.active_reasons, "a halt with no reason cannot be cleared"
        finally:
            runtime.dispose()

    def test_the_latch_and_the_journal_both_carry_the_safety_state(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        """ADR-002: two independent records, and a run writes to both."""
        state_file = tmp_path / "safety.json"
        run_in_a_separate_process(state_file)

        assert state_file.exists(), "the local latch was never written by the child"

        from crumblr.persistence.safety_state import PostgresSafetyStateStore
        from crumblr.risk.safety_state import FileSafetyStateStore

        journal_state = PostgresSafetyStateStore(engine).load()
        latch_state = FileSafetyStateStore(state_file).load()

        assert journal_state.state is latch_state.state, (
            "the journal and the latch disagree about the system's safety state"
        )


class TestTheRiskSessionIsPickedUpNotReset:
    """F-019, across a real restart.

    The second process is told nothing by the first except what is in
    PostgreSQL. It resumes the series where the first one stopped, which is
    what a restart actually looks like — replaying the same bars again would
    make market time run backwards, and that is a different scenario, covered
    at the bottom of this file.
    """

    def test_the_first_process_records_a_session(self, engine: Engine, tmp_path: Path) -> None:
        first = run_in_a_separate_process(tmp_path / "safety.json", slice_=FIRST_HALF)

        assert first["session_resumed"] is False, "there was nothing yet to resume"
        record = PostgresRiskSessionStore(engine).load_latest()
        assert record.is_known and record.state is not None

    def test_a_restart_inside_the_same_session_keeps_its_baseline(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        """The failure F-019 names: the daily allowance must not renew.

        Both halves fall inside one FX trading day, so the second process has
        to measure its daily loss from the same opening equity as the first —
        not from wherever the account happened to be when it started.
        """
        state_file = tmp_path / "safety.json"
        first = run_in_a_separate_process(state_file, slice_=FIRST_HALF)
        before = PostgresRiskSessionStore(engine).load_latest().state
        assert before is not None

        second = run_in_a_separate_process(
            state_file,
            slice_=SAME_DAY_CONTINUATION,
            balance=Decimal(str(first["final_equity"])),
        )

        assert second["session_resumed"] is True, (
            "the second process started a fresh session; the budget was reset"
        )
        assert Decimal(str(second["session_start_equity"])) == before.session_start_equity

    def test_a_restart_after_the_day_rolls_renews_the_daily_gate_only(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        """The daily allowance is meant to renew. The drawdown record is not."""
        state_file = tmp_path / "safety.json"
        first = run_in_a_separate_process(state_file, slice_=FIRST_HALF)
        before = PostgresRiskSessionStore(engine).load_latest().state
        assert before is not None

        second = run_in_a_separate_process(
            state_file,
            slice_=NEXT_DAY_CONTINUATION,
            balance=Decimal(str(first["final_equity"])),
        )

        assert second["session_resumed"] is True
        assert Decimal(str(second["session_start_equity"])) == Decimal(str(first["final_equity"]))

        after = PostgresRiskSessionStore(engine).load_latest().state
        assert after is not None
        assert after.trading_day > before.trading_day
        assert after.peak_equity >= before.peak_equity, "the high-water mark was lowered"

    def test_the_worst_drawdown_seen_is_never_walked_back_by_a_restart(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        state_file = tmp_path / "safety.json"
        first = run_in_a_separate_process(state_file, slice_=FIRST_HALF)
        before = PostgresRiskSessionStore(engine).load_latest().state
        assert before is not None

        run_in_a_separate_process(
            state_file,
            slice_=SAME_DAY_CONTINUATION,
            balance=Decimal(str(first["final_equity"])),
        )

        after = PostgresRiskSessionStore(engine).load_latest().state
        assert after is not None
        assert after.max_drawdown_fraction >= before.max_drawdown_fraction
        assert after.max_session_loss_fraction >= before.max_session_loss_fraction


class TestWhenTheRecordCannotBeTrusted:
    def test_a_process_replaying_time_backwards_halts(self, engine: Engine, tmp_path: Path) -> None:
        """A record from a later trading day than the data being fed in.

        In production this cannot happen without something being wrong with a
        clock or a database. In replay it is one command away, which makes it
        cheap to prove the system refuses rather than picks a side.
        """
        state_file = tmp_path / "safety.json"
        run_in_a_separate_process(state_file, slice_=NEXT_DAY_CONTINUATION)

        rewound = run_in_a_separate_process(state_file, slice_=FIRST_HALF)

        assert rewound["halted"] is True
        assert "SAFETY_STATE_UNKNOWN" in rewound["halt_reasons"]  # type: ignore[operator]
        assert rewound["session_resumed"] is False
