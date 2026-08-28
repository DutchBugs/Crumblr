"""The orchestrator's normal path writes to PostgreSQL (review 1.5 step 1).

`test_replay_from_journal.py` already proved the storage layer could hold a
run: it took the capsules a run produced and wrote them itself. That is a
statement about `CapsuleStore`, not about the platform, and the gap was
recorded as D-030 — the running system kept its audit trail in a list.

These tests use the wiring instead. Nothing here writes to the journal by
hand; the orchestrator does it as it decides, and the assertions are made
against what the `events` table can be made to say afterwards:

    run → persist → read the journal back → the same decision sequence

Equality is the evidence contract from build.md §25.2, the provenance
fingerprint, folded over the sequence in the order it happened.
"""

from __future__ import annotations

import itertools
from decimal import Decimal
from pathlib import Path

import pytest
from scripts.run_replay import build_instrument_spec
from sqlalchemy import Engine

from crumblr.application.bootstrap import build_durable_runtime
from crumblr.application.orchestration import ReplayOrchestrator, RunResult
from crumblr.application.reconstruction import (
    decision_fingerprint,
    reconstruct_from_journal,
    tally_from_capsules,
)
from crumblr.config import PlatformConfig, load_config
from crumblr.domain.enums import Environment, KillSwitchState
from crumblr.domain.events import EventType
from crumblr.persistence.engine import DEFAULT_TEST_URL, database_url
from crumblr.persistence.journal import CapsuleStore, EventJournal
from crumblr.risk.kill_switch import KillSwitch

pytestmark = pytest.mark.integration

TEST_URL = database_url(DEFAULT_TEST_URL)
"""Resolved once, honouring `CRUMBLR_DATABASE_URL` — see `test_live_decision.py`'s

`TEST_URL` for the full reasoning (workspace database isolation)."""

REPO_ROOT = Path(__file__).resolve().parents[2]
BALANCE = Decimal("10000")
BARS = 400


@pytest.fixture(scope="module")
def config() -> PlatformConfig:
    """The shipped configuration, driven by the benchmark strategy.

    `ict_v1` is deliberately selective — three setups in twelve thousand bars
    — which is the right property for a strategy and the wrong one for
    exercising a journal.
    """
    shipped = load_config(Environment.PAPER, config_dir=REPO_ROOT / "config")
    agent = shipped.trading_agent.model_copy(update={"strategy_id": "baseline_v1"})
    return shipped.model_copy(update={"trading_agent": agent})


def arm(kill_switch: KillSwitch) -> KillSwitch:
    """Clear the fail-closed startup state, as an operator would.

    A fresh database has never recorded a RUNNING state, so every runtime
    built against one starts halted. Tests that want to exercise trading have
    to say so explicitly — which is the same thing an operator has to do, and
    the reason `reset` demands an identity and an incident note.
    """
    kill_switch.reset(operator="integration-test", incident_note="arming a fresh test database")
    return kill_switch


def persisted_replay(
    config: PlatformConfig, engine: Engine, state_file: Path, *, bars: int = BARS
) -> tuple[RunResult, EventJournal]:
    """One replay whose every stage goes through the journal."""
    runtime = build_durable_runtime(
        environment=config.environment,
        state_file=state_file,
        url=TEST_URL,
    )
    arm(runtime.kill_switch)

    from crumblr.market_data.synthetic import SyntheticMarketConfig, generate_ticks
    from crumblr.mt5_gateway.simulated import SimulatedBroker

    spec = build_instrument_spec()
    broker = SimulatedBroker(
        spec, starting_balance=BALANCE, server=config.account_guard.expected_server
    )
    ticks = list(generate_ticks(SyntheticMarketConfig(bar_count=bars), spec))
    result = ReplayOrchestrator(
        config,
        spec,
        broker,
        starting_equity=BALANCE,
        recorder=runtime.recorder,
        kill_switch=runtime.kill_switch,
    ).run(ticks)
    return result, EventJournal(engine)


class TestTheRunningSystemWritesItsOwnAuditTrail:
    """D-030: the orchestrator used to keep capsules in a list."""

    def test_a_run_leaves_a_journal_behind(
        self, engine: Engine, config: PlatformConfig, tmp_path: Path
    ) -> None:
        result, journal = persisted_replay(config, engine, tmp_path / "safety.json")

        assert result.capsules, "the run must reach decisions to be worth persisting"
        assert journal.count() > 0, "the orchestrator wrote nothing"

    def test_every_sealed_capsule_is_in_the_journal_and_in_the_capsule_store(
        self, engine: Engine, config: PlatformConfig, tmp_path: Path
    ) -> None:
        """Two records of the same thing, and they have to agree.

        The journal is the run's account of itself; `decision_capsules` is the
        queryable projection with the provenance columns broken out. A capsule
        in one and not the other is a hole in the audit trail.
        """
        result, journal = persisted_replay(config, engine, tmp_path / "safety.json")

        from_journal = reconstruct_from_journal(journal).capsules
        from_store = CapsuleStore(engine).read_all()

        assert len(from_journal) == len(result.capsules)
        assert [c.capsule_id for c in from_store] == [c.capsule_id for c in from_journal]

    def test_a_decision_window_is_visible_as_a_whole_flow(
        self, engine: Engine, config: PlatformConfig, tmp_path: Path
    ) -> None:
        """build.md §3 forbids skipping a stage; the journal has to show that."""
        result, journal = persisted_replay(config, engine, tmp_path / "safety.json")
        executed = next(c for c in result.capsules if c.execution_result is not None)

        types = [
            event.event_type for event in journal.read_all(correlation_id=executed.correlation_id)
        ]

        assert types == [
            EventType.SIGNAL_GENERATED,
            EventType.TRADE_INTENT_CREATED,
            EventType.RISK_DECISION_MADE,
            EventType.SUPERVISOR_DECISION_MADE,
            EventType.ORDER_CHECK_COMPLETED,
            EventType.ORDER_SUBMITTED,
            EventType.ORDER_RESULT_RECEIVED,
            EventType.POSITION_CHANGED,
            EventType.DECISION_CAPSULE_SEALED,
        ]

    def test_the_causation_chain_walks_a_window_back_to_its_signal(
        self, engine: Engine, config: PlatformConfig, tmp_path: Path
    ) -> None:
        """Provenance is the point: every event names the one that caused it."""
        result, journal = persisted_replay(config, engine, tmp_path / "safety.json")
        executed = next(c for c in result.capsules if c.execution_result is not None)

        chain = journal.read_all(correlation_id=executed.correlation_id)

        assert chain[0].causation_id is None, "the first event in a window has no cause"
        for earlier, later in itertools.pairwise(chain):
            assert later.causation_id == earlier.event_id


class TestTheJournalReproducesTheRun:
    """The acceptance criterion review 1.5 names for this step."""

    def test_the_reconstructed_decision_sequence_is_the_one_that_ran(
        self, engine: Engine, config: PlatformConfig, tmp_path: Path
    ) -> None:
        result, _ = persisted_replay(config, engine, tmp_path / "safety.json")

        # Read through a fresh journal object, so nothing in memory is doing
        # the remembering.
        restored = reconstruct_from_journal(EventJournal(engine))

        assert restored.fingerprint == decision_fingerprint(result.capsules)

    def test_the_reconstructed_tally_matches_what_the_run_counted(
        self, engine: Engine, config: PlatformConfig, tmp_path: Path
    ) -> None:
        """Not only the digest: the counts a human would read off a report."""
        result, _ = persisted_replay(config, engine, tmp_path / "safety.json")
        restored = reconstruct_from_journal(EventJournal(engine))

        rebuilt = restored.tally
        live = result.tally

        assert rebuilt.no_trade == live.no_trade
        assert rebuilt.intents == live.intents
        assert (rebuilt.risk_passed, rebuilt.risk_blocked, rebuilt.risk_halted) == (
            live.risk_passed,
            live.risk_blocked,
            live.risk_halted,
        )
        assert (
            rebuilt.supervisor_approved,
            rebuilt.supervisor_vetoed,
            rebuilt.supervisor_halted,
        ) == (live.supervisor_approved, live.supervisor_vetoed, live.supervisor_halted)
        assert rebuilt.orders_filled == live.orders_filled
        assert rebuilt.order_check_rejected == live.order_check_rejected

    def test_the_no_trade_reasons_survive_and_they_only_live_in_the_journal(
        self, engine: Engine, config: PlatformConfig, tmp_path: Path
    ) -> None:
        """A capsule with no intent does not carry why. The signal event does."""
        result, _ = persisted_replay(config, engine, tmp_path / "safety.json")
        restored = reconstruct_from_journal(EventJournal(engine))

        assert result.tally.no_trade_reasons, "the run must refuse for stated reasons"
        assert restored.tally.no_trade_reasons == result.tally.no_trade_reasons
        assert tally_from_capsules(restored.capsules).no_trade_reasons == {}, (
            "the capsules alone cannot explain a NO_TRADE — that is why the "
            "signal event is journalled"
        )

    def test_the_risk_and_supervisor_reasons_survive(
        self, engine: Engine, config: PlatformConfig, tmp_path: Path
    ) -> None:
        result, _ = persisted_replay(config, engine, tmp_path / "safety.json")
        restored = reconstruct_from_journal(EventJournal(engine)).tally

        assert restored.risk_reasons == result.tally.risk_reasons
        assert restored.supervisor_reasons == result.tally.supervisor_reasons


class TestWritingTheSameRunTwice:
    """ADR-003 invariant 3 at the level of a whole run, not a single append."""

    def test_replaying_the_same_series_appends_no_second_copy(
        self, engine: Engine, config: PlatformConfig, tmp_path: Path
    ) -> None:
        """The property deterministic event ids exist for.

        A rerun after an ambiguous outcome — a crash, an interrupted write —
        has to converge on the history that is already there. With random
        event ids it would instead double it, and the journal would report a
        run that never happened.
        """
        first, journal = persisted_replay(config, engine, tmp_path / "safety.json")
        events_after_first = journal.count()
        capsules_after_first = len(CapsuleStore(engine).read_all())

        second, _ = persisted_replay(config, engine, tmp_path / "safety.json")

        assert journal.count() == events_after_first
        assert len(CapsuleStore(engine).read_all()) == capsules_after_first
        assert decision_fingerprint(second.capsules) == decision_fingerprint(first.capsules)


class TestSafetyStateOnTheNormalPath:
    """ADR-002, wired in rather than tested in isolation."""

    def test_a_runtime_on_a_fresh_database_starts_with_orders_disabled(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        """No record of RUNNING is not the same as permission to trade."""
        runtime = build_durable_runtime(
            environment=Environment.PAPER,
            state_file=tmp_path / "safety.json",
            url=TEST_URL,
        )
        try:
            assert runtime.kill_switch.is_halted
            assert runtime.kill_switch.state is KillSwitchState.UNKNOWN
        finally:
            runtime.dispose()

    def test_a_halt_raised_during_a_run_reaches_both_the_journal_and_the_latch(
        self, engine: Engine, config: PlatformConfig, tmp_path: Path
    ) -> None:
        """The halt has to be durable in both places ADR-002 reads from."""
        state_file = tmp_path / "safety.json"
        result, journal = persisted_replay(config, engine, state_file)
        if not result.halted:
            pytest.skip("this replay did not breach a loss gate; nothing to assert")

        halts = journal.read_all(event_type=EventType.SYSTEM_HALTED)
        assert halts, "a halt that is not in the journal did not happen, as far as audit goes"

        assert state_file.exists(), "the local latch was never written"
        restarted = build_durable_runtime(
            environment=config.environment,
            state_file=state_file,
            url=TEST_URL,
        )
        try:
            assert restarted.kill_switch.is_halted
        finally:
            restarted.dispose()
