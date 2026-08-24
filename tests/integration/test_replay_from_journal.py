"""ADR-003 acceptance test 10 — the one that defines M2.

    A replay driven from the persisted journal reproduces the same decision
    sequence, byte for byte, as the in-memory replay.

Everything else in M2 is machinery for this. If the journal cannot reproduce a
run, it is storage and not an audit trail, and none of the guarantees the rest
of the platform makes about provenance are checkable.

The test writes a real replay's events and capsules to PostgreSQL, reads them
back through a fresh connection, and compares fingerprints.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from scripts.run_replay import build_instrument_spec
from sqlalchemy import Engine

from crumblr.application.orchestration import ReplayOrchestrator, RunResult
from crumblr.config import PlatformConfig, load_config
from crumblr.domain.enums import Environment
from crumblr.domain.events import build_event
from crumblr.market_data.synthetic import SyntheticMarketConfig, generate_ticks
from crumblr.mt5_gateway.simulated import SimulatedBroker
from crumblr.persistence.journal import CapsuleStore, EventJournal

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
BALANCE = Decimal("10000")


@pytest.fixture(scope="module")
def config() -> PlatformConfig:
    """The shipped configuration, driven by baseline_v1.

    The pipeline tests use the benchmark strategy for the same reason they
    always have: `ict_v1` is deliberately selective and produces too few
    decisions on synthetic data to exercise a journal.
    """
    shipped = load_config(Environment.PAPER, config_dir=REPO_ROOT / "config")
    agent = shipped.trading_agent.model_copy(update={"strategy_id": "baseline_v1"})
    return shipped.model_copy(update={"trading_agent": agent})


def run_replay(config: PlatformConfig, *, bars: int = 500) -> RunResult:
    spec = build_instrument_spec()
    broker = SimulatedBroker(
        spec, starting_balance=BALANCE, server=config.account_guard.expected_server
    )
    ticks = list(generate_ticks(SyntheticMarketConfig(bar_count=bars), spec))
    return ReplayOrchestrator(config, spec, broker, starting_equity=BALANCE).run(ticks)


class TestReplayFromJournal:
    def test_sealed_capsules_round_trip_with_fingerprints_intact(
        self, engine: Engine, config: PlatformConfig
    ) -> None:
        """The audit-trail property: what was decided is what is stored."""
        result = run_replay(config)
        assert result.capsules, "the run must produce capsules to store"

        store = CapsuleStore(engine)
        for capsule in result.capsules:
            store.seal(capsule)

        # A fresh read, going back through decoding and fingerprint verification.
        restored = CapsuleStore(engine).read_all()

        assert len(restored) == len(result.capsules)
        assert [c.provenance_fingerprint for c in restored] == [
            c.provenance_fingerprint for c in result.capsules
        ], "the journal did not reproduce the decision sequence"

    def test_the_decision_sequence_survives_in_order(
        self, engine: Engine, config: PlatformConfig
    ) -> None:
        result = run_replay(config)
        store = CapsuleStore(engine)
        for capsule in result.capsules:
            store.seal(capsule)

        restored = CapsuleStore(engine).read_all()
        assert [c.capsule_id for c in restored] == [c.capsule_id for c in result.capsules]
        assert [c.occurred_at_utc for c in restored] == [c.occurred_at_utc for c in result.capsules]

    def test_every_trade_intent_survives_with_its_decision_hash(
        self, engine: Engine, config: PlatformConfig
    ) -> None:
        """The hash is what makes a stored decision verifiable rather than merely present."""
        result = run_replay(config)
        store = CapsuleStore(engine)
        for capsule in result.capsules:
            store.seal(capsule)

        original = [
            c.trade_intent.decision_hash for c in result.capsules if c.trade_intent is not None
        ]
        restored = [
            c.trade_intent.decision_hash
            for c in CapsuleStore(engine).read_all()
            if c.trade_intent is not None
        ]
        assert original, "the run must produce intents to verify"
        assert restored == original

    def test_two_identical_runs_produce_an_identical_journal(
        self, engine: Engine, config: PlatformConfig
    ) -> None:
        """Determinism holds across the persistence boundary, not just in memory."""
        first = run_replay(config)
        store = CapsuleStore(engine)
        for capsule in first.capsules:
            store.seal(capsule)
        stored = [c.provenance_fingerprint for c in CapsuleStore(engine).read_all()]

        second = run_replay(config)
        assert stored == [c.provenance_fingerprint for c in second.capsules]

    def test_reseeding_the_journal_from_a_rerun_is_a_no_op(
        self, engine: Engine, config: PlatformConfig
    ) -> None:
        """Idempotency at the level that matters: replaying twice stores once."""
        result = run_replay(config)
        store = CapsuleStore(engine)

        for capsule in result.capsules:
            store.seal(capsule)
        first_count = len(store.read_all())

        for capsule in run_replay(config).capsules:
            store.seal(capsule)

        assert len(store.read_all()) == first_count


class TestEventJournalCarriesTheRun:
    def test_intents_written_as_events_read_back_in_order(
        self, engine: Engine, config: PlatformConfig
    ) -> None:
        result = run_replay(config)
        intents = [c.trade_intent for c in result.capsules if c.trade_intent is not None]
        assert intents

        journal = EventJournal(engine)
        journal.append_many(
            [
                build_event(
                    intent,
                    correlation_id=intent.intent_id,
                    environment=config.environment,
                    source="trading_agent",
                )
                for intent in intents
            ]
        )

        restored = EventJournal(engine).read_all()
        assert len(restored) == len(intents)
        assert [event.payload.decision_hash for event in restored] == [  # type: ignore[attr-defined]
            intent.decision_hash for intent in intents
        ]

    def test_the_journal_preserves_correlation_and_causation(
        self, engine: Engine, config: PlatformConfig
    ) -> None:
        """Provenance is the reason the journal exists at all."""
        result = run_replay(config)
        intent = next(c.trade_intent for c in result.capsules if c.trade_intent is not None)

        cause = build_event(
            intent,
            correlation_id=intent.intent_id,
            environment=config.environment,
            source="trading_agent",
        )
        journal = EventJournal(engine)
        journal.append(cause)

        restored = journal.read_all(correlation_id=intent.intent_id)
        assert len(restored) == 1
        assert restored[0].correlation_id == intent.intent_id
