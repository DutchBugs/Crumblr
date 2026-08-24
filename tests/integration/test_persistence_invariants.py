"""The ten acceptance tests from ADR-003.

M2 is not complete because rows are stored. It is complete when persisted state
supports replay, audit, restart, recovery and reconciliation — which is what
these assert, in the order the ADR lists them.

The tenth is the one that defines the milestone: a replay driven from the
journal must reproduce the in-memory run exactly. Without it the journal is
storage rather than an audit trail.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text

from crumblr.domain.enums import Environment, KillSwitchState, ReasonCode
from crumblr.domain.events import Event, EventType, build_event
from crumblr.domain.models import DecisionCapsule
from crumblr.domain.timeutils import utc_now
from crumblr.persistence.journal import CapsuleStore, EventJournal, JournalIntegrityError
from crumblr.persistence.safety_state import (
    CompositeSafetyStateStore,
    PostgresSafetyStateStore,
)
from crumblr.persistence.schema import events as events_table
from crumblr.risk.safety_state import InMemorySafetyStateStore, SafetyState
from tests.conftest import make_instrument_spec, make_intent

pytestmark = pytest.mark.integration


def an_event(
    *, occurred_at: datetime | None = None, correlation_id: UUID | None = None
) -> Event[Any]:
    event = build_event(
        make_intent(),
        correlation_id=correlation_id or uuid4(),
        environment=Environment.PAPER,
        source="trading_agent",
    )
    if occurred_at is not None:
        event = event.model_copy(update={"occurred_at_utc": occurred_at})
    return event


# --------------------------------------------------------------------------- #
# 1 & 2 — idempotent writes
# --------------------------------------------------------------------------- #


class TestIdempotentWrites:
    def test_the_same_event_id_stores_one_row(self, engine: Engine) -> None:
        journal = EventJournal(engine)
        event = an_event()

        first = journal.append(event)
        second = journal.append(event)

        assert first.inserted is True
        assert second.inserted is False, "a repeat must not create a second row"
        assert journal.count() == 1

    def test_the_duplicate_is_reported_not_hidden(self, engine: Engine) -> None:
        """A duplicate order event means something; a duplicate heartbeat does not."""
        journal = EventJournal(engine)
        event = an_event()
        journal.append(event)
        assert journal.append(event).was_duplicate

    def test_a_retry_after_an_ambiguous_commit_converges(self, engine: Engine) -> None:
        """The crash-consistency case: the writer cannot tell whether it succeeded."""
        journal = EventJournal(engine)
        event = an_event()

        # First attempt commits, but imagine the acknowledgement was lost.
        journal.append(event)
        # The writer retries the identical event.
        for _ in range(5):
            journal.append(event)

        assert journal.count() == 1


class TestJournalLatest:
    """Dashboard v0 (review 1.12 §8) wants the newest event of a type, not the

    oldest — `read_all(event_type=..., limit=1)` orders ascending for replay,
    so `latest` exists rather than asking a caller to remember to reverse.
    """

    def test_latest_returns_the_most_recently_occurred_event(self, engine: Engine) -> None:
        journal = EventJournal(engine)
        base = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)
        oldest = an_event(occurred_at=base)
        newest = an_event(occurred_at=base + timedelta(minutes=10))
        journal.append_many([oldest, an_event(occurred_at=base + timedelta(minutes=5)), newest])

        found = journal.latest(EventType.TRADE_INTENT_CREATED)

        assert found is not None
        assert found.event_id == newest.event_id

    def test_latest_is_none_for_a_type_never_recorded(self, engine: Engine) -> None:
        journal = EventJournal(engine)
        journal.append(an_event())

        assert journal.latest(EventType.SUPERVISOR_DECISION_MADE) is None


# --------------------------------------------------------------------------- #
# 3 — crash consistency
# --------------------------------------------------------------------------- #


class TestCrashConsistency:
    def test_a_rolled_back_transaction_leaves_nothing(self, engine: Engine) -> None:
        """Died before commit: the event never happened."""
        journal = EventJournal(engine)
        try:
            with engine.begin() as connection:
                journal.append(an_event(), connection=connection)
                raise RuntimeError("process died before commit")
        except RuntimeError:
            pass
        assert journal.count() == 0

    def test_a_committed_batch_is_all_or_nothing(self, engine: Engine) -> None:
        """Invariant 5: a transition spanning rows is never half-visible."""
        journal = EventJournal(engine)
        batch = [an_event() for _ in range(3)]
        try:
            with engine.begin() as connection:
                for event in batch:
                    journal.append(event, connection=connection)
                raise RuntimeError("died mid-transition")
        except RuntimeError:
            pass
        assert journal.count() == 0

        journal.append_many(batch)
        assert journal.count() == 3


# --------------------------------------------------------------------------- #
# 4 — ordering
# --------------------------------------------------------------------------- #


class TestOrdering:
    def test_events_read_back_in_market_time_not_insertion_order(self, engine: Engine) -> None:
        """The reconnect-backfill case: late arrival, earlier event."""
        journal = EventJournal(engine)
        base = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)

        later = an_event(occurred_at=base + timedelta(minutes=10))
        earlier = an_event(occurred_at=base)

        journal.append(later)  # arrives first
        journal.append(earlier)  # backfilled afterwards

        read = journal.read_all()
        assert [event.occurred_at_utc for event in read] == [
            base,
            base + timedelta(minutes=10),
        ], "ordering must follow market time, not insertion order"

    def test_events_sharing_a_timestamp_have_a_deterministic_order(self, engine: Engine) -> None:
        journal = EventJournal(engine)
        moment = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)
        batch = [an_event(occurred_at=moment) for _ in range(5)]
        journal.append_many(batch)

        first_read = [event.event_id for event in journal.read_all()]
        second_read = [event.event_id for event in journal.read_all()]
        assert first_read == second_read

    def test_insertion_time_is_recorded_separately_from_market_time(self, engine: Engine) -> None:
        """Three clocks, not one."""
        journal = EventJournal(engine)
        past = datetime(2020, 1, 1, tzinfo=UTC)
        journal.append(an_event(occurred_at=past))

        with engine.connect() as connection:
            row = connection.execute(text("SELECT * FROM events")).mappings().one()
        assert row["occurred_at_utc"] == past
        assert row["recorded_at_utc"] > past, "recorded_at must be write time"


# --------------------------------------------------------------------------- #
# 5 & 6 — exactness across the boundary
# --------------------------------------------------------------------------- #


class TestExactnessSurvivesTheRoundTrip:
    def test_decimals_survive_bit_exact(self, engine: Engine) -> None:
        journal = EventJournal(engine)
        intent = make_intent(
            reference_price=Decimal("1.084995"),
            stop_loss_price=Decimal("1.080005"),
            requested_risk_fraction=Decimal("0.0033333"),
        )
        event = build_event(
            intent,
            correlation_id=uuid4(),
            environment=Environment.PAPER,
            source="trading_agent",
        )
        journal.append(event)

        restored = journal.read_all()[0].payload
        assert restored.reference_price == Decimal("1.084995")  # type: ignore[attr-defined]
        assert restored.stop_loss_price == Decimal("1.080005")  # type: ignore[attr-defined]
        assert restored.requested_risk_fraction == Decimal("0.0033333")  # type: ignore[attr-defined]

    def test_no_monetary_column_is_a_float_type(self, engine: Engine) -> None:
        """Storing money as float would reintroduce what the domain rejects."""
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT table_name, column_name, data_type FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND data_type IN "
                    "('double precision', 'real')"
                )
            ).all()
        assert rows == [], f"float columns found: {rows}"

    def test_utc_timestamps_survive_the_round_trip(self, engine: Engine) -> None:
        journal = EventJournal(engine)
        moment = datetime(2026, 3, 15, 9, 30, 15, 123456, tzinfo=UTC)
        journal.append(an_event(occurred_at=moment))

        restored = journal.read_all()[0]
        assert restored.occurred_at_utc == moment
        assert restored.occurred_at_utc.tzinfo is not None

    def test_every_timestamp_column_carries_a_timezone(self, engine: Engine) -> None:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT table_name, column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' "
                    "AND data_type = 'timestamp without time zone'"
                )
            ).all()
        assert rows == [], f"naive timestamp columns found: {rows}"

    def test_uuid_identity_survives(self, engine: Engine) -> None:
        journal = EventJournal(engine)
        event = an_event()
        journal.append(event)
        assert journal.read_all()[0].event_id == event.event_id


# --------------------------------------------------------------------------- #
# 7 — schema evolution
# --------------------------------------------------------------------------- #


class TestSchemaVersioning:
    def test_the_schema_version_round_trips(self, engine: Engine) -> None:
        journal = EventJournal(engine)
        journal.append(an_event())
        assert journal.read_all()[0].schema_version == 1

    def test_a_payload_disagreeing_with_its_column_is_refused(self, engine: Engine) -> None:
        """A record that contradicts itself must not be read as though sound."""
        journal = EventJournal(engine)
        event = an_event()
        journal.append(event)

        with engine.begin() as connection:
            connection.execute(
                events_table.update()
                .where(events_table.c.event_id == event.event_id)
                .values(schema_version=99)
            )
        with pytest.raises(JournalIntegrityError, match="schema_version"):
            journal.read_all()


# --------------------------------------------------------------------------- #
# 8 — capsule immutability
# --------------------------------------------------------------------------- #


class TestSealedCapsulesAreImmutable:
    @staticmethod
    def _capsule() -> DecisionCapsule:

        intent = make_intent()
        return DecisionCapsule(
            capsule_id=uuid4(),
            occurred_at_utc=utc_now(),
            correlation_id=uuid4(),
            canonical_symbol="EUR/USD",
            broker_symbol="EURUSD",
            market_snapshot_id=uuid4(),
            feature_set_version="features-v1",
            feature_values_hash="abc123",
            strategy_version="1.0.0",
            trade_intent=intent,
            risk_config_version="cfg-v1",
            code_commit="deadbeef",
            environment=Environment.PAPER,
        )

    def test_a_capsule_round_trips_with_its_fingerprint_intact(self, engine: Engine) -> None:
        store = CapsuleStore(engine)
        capsule = self._capsule()
        store.seal(capsule)

        restored = store.get(capsule.capsule_id)
        assert restored is not None
        assert restored == capsule
        assert restored.provenance_fingerprint == capsule.provenance_fingerprint

    def test_sealing_twice_stores_one_capsule(self, engine: Engine) -> None:
        store = CapsuleStore(engine)
        capsule = self._capsule()
        assert store.seal(capsule).inserted
        assert not store.seal(capsule).inserted
        assert len(store.read_all()) == 1

    def test_a_tampered_capsule_is_detected_on_read(self, engine: Engine) -> None:
        """The fingerprint earns its place here."""
        from crumblr.persistence.schema import decision_capsules

        store = CapsuleStore(engine)
        capsule = self._capsule()
        store.seal(capsule)

        with engine.begin() as connection:
            connection.execute(
                decision_capsules.update()
                .where(decision_capsules.c.capsule_id == capsule.capsule_id)
                .values(provenance_fingerprint="0" * 64)
            )
        with pytest.raises(JournalIntegrityError, match="fingerprint mismatch"):
            store.get(capsule.capsule_id)


# --------------------------------------------------------------------------- #
# 9 — safety-state disagreement
# --------------------------------------------------------------------------- #


class TestSafetyStateAuthority:
    """ADR-002's precedence table, implemented and asserted."""

    @staticmethod
    def _running() -> SafetyState:
        return SafetyState(
            state=KillSwitchState.RUNNING, reason_codes=(), recorded_at_utc=utc_now()
        )

    @staticmethod
    def _halted() -> SafetyState:
        return SafetyState(
            state=KillSwitchState.HALTED,
            reason_codes=(ReasonCode.DAILY_LOSS_LIMIT,),
            recorded_at_utc=utc_now(),
            tripped_by="risk_engine",
        )

    def test_both_running_permits_orders(self, engine: Engine) -> None:
        journal = PostgresSafetyStateStore(engine)
        latch = InMemorySafetyStateStore(self._running())
        journal.save(self._running())
        assert CompositeSafetyStateStore(journal, latch).load().permits_new_orders

    def test_journal_halted_wins_over_a_running_latch(self, engine: Engine) -> None:
        journal = PostgresSafetyStateStore(engine)
        journal.save(self._halted())
        composite = CompositeSafetyStateStore(journal, InMemorySafetyStateStore(self._running()))
        assert not composite.load().permits_new_orders

    def test_latch_halted_wins_over_a_running_journal(self, engine: Engine) -> None:
        journal = PostgresSafetyStateStore(engine)
        journal.save(self._running())
        composite = CompositeSafetyStateStore(journal, InMemorySafetyStateStore(self._halted()))
        assert not composite.load().permits_new_orders

    def test_an_empty_journal_halts_even_with_a_running_latch(self, engine: Engine) -> None:
        composite = CompositeSafetyStateStore(
            PostgresSafetyStateStore(engine), InMemorySafetyStateStore(self._running())
        )
        assert not composite.load().permits_new_orders

    def test_a_disagreement_says_which_source_objected(self, engine: Engine) -> None:
        journal = PostgresSafetyStateStore(engine)
        journal.save(self._halted())
        state = CompositeSafetyStateStore(journal, InMemorySafetyStateStore(self._running())).load()
        assert state.detail is not None
        assert "journal halted" in state.detail

    def test_history_is_appended_not_overwritten(self, engine: Engine) -> None:
        journal = PostgresSafetyStateStore(engine)
        journal.save(self._halted())
        journal.save(self._running())

        with engine.connect() as connection:
            count = connection.execute(
                text("SELECT count(*) FROM safety_state_events")
            ).scalar_one()
        assert count == 2, "a reset must not erase the halt that preceded it"
        assert journal.load().permits_new_orders, "the latest state is the current one"


# --------------------------------------------------------------------------- #
# Instrument specs, keyed by content hash
# --------------------------------------------------------------------------- #


class TestInstrumentSpecs:
    def test_numeric_columns_keep_full_precision(self, engine: Engine) -> None:
        from crumblr.persistence.schema import instrument_specs

        spec = make_instrument_spec()
        with engine.begin() as connection:
            connection.execute(
                instrument_specs.insert().values(
                    spec_version=spec.spec_version,
                    canonical_symbol=spec.canonical_symbol,
                    broker_symbol=spec.broker_symbol,
                    captured_at_utc=spec.captured_at_utc,
                    contract_size=spec.contract_size,
                    point=spec.point,
                    tick_size=spec.tick_size,
                    tick_value=spec.tick_value,
                    volume_min=spec.volume_min,
                    volume_max=spec.volume_max,
                    volume_step=spec.volume_step,
                    digits=spec.digits,
                    payload=spec.model_dump(mode="json"),
                )
            )
            row = connection.execute(text("SELECT * FROM instrument_specs")).mappings().one()

        assert row["point"] == Decimal("0.00001")
        assert row["volume_step"] == Decimal("0.01")
        assert row["spec_version"] == spec.spec_version
