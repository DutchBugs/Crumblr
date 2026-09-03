"""`CanaryPermitStore`, against real PostgreSQL (Phase B item B8,

`review/adr/ADR-018-canary-permit.md`). Proves the one property that
only means anything under a real database: at most one `consume()`
attempt can ever win for a given `permit_id`, even under genuine
concurrent access.
"""

from __future__ import annotations

import threading
from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import Engine, select

from crumblr.domain.enums import EntryType
from crumblr.domain.models import CanaryPermit
from crumblr.persistence.canary_permit import CanaryPermitConsumeOutcome, CanaryPermitStore
from crumblr.persistence.schema import canary_permit_consumptions
from tests.conftest import FIXED_NOW

pytestmark = pytest.mark.integration


def permit(**overrides: Any) -> CanaryPermit:
    fields: dict[str, Any] = {
        "permit_id": uuid4(),
        "approved_account_ref": "d9ac869767271225",
        "expected_server": "PepperstoneUK-Demo",
        "canonical_symbol": "EUR/USD",
        "entry_type": EntryType.MARKET,
        "max_requested_risk_fraction": Decimal("0.0025"),
        "issued_by": "levi",
        "reason": "first constrained DEMO canary",
        "issued_at_utc": FIXED_NOW,
        "valid_until_utc": FIXED_NOW + timedelta(hours=1),
    }
    fields.update(overrides)
    return CanaryPermit(**fields)


class TestIssueAndRead:
    def test_a_freshly_issued_permit_round_trips(self, engine: Engine) -> None:
        store = CanaryPermitStore(engine)
        issued = permit()

        result = store.issue(issued)

        assert result.inserted is True
        read_back = store.permit_for(issued.permit_id)
        assert read_back == issued

    def test_an_unknown_permit_id_reads_as_none(self, engine: Engine) -> None:
        store = CanaryPermitStore(engine)
        assert store.permit_for(uuid4()) is None

    def test_an_unconsumed_permit_has_no_consumption_record(self, engine: Engine) -> None:
        store = CanaryPermitStore(engine)
        issued = permit()
        store.issue(issued)

        assert store.consumption_for(issued.permit_id) is None


class TestConsume:
    def test_a_fresh_permit_is_consumed(self, engine: Engine) -> None:
        store = CanaryPermitStore(engine)
        issued = permit()
        store.issue(issued)
        order_request_id = uuid4()

        result = store.consume(issued.permit_id, order_request_id=order_request_id, now=FIXED_NOW)

        assert result.outcome is CanaryPermitConsumeOutcome.CONSUMED
        assert result.consumption is not None
        assert result.consumption.order_request_id == order_request_id

        stored = store.consumption_for(issued.permit_id)
        assert stored is not None
        assert stored.order_request_id == order_request_id

    def test_a_second_consumption_attempt_is_refused_and_names_the_winner(
        self, engine: Engine
    ) -> None:
        store = CanaryPermitStore(engine)
        issued = permit()
        store.issue(issued)
        first_request_id = uuid4()
        second_request_id = uuid4()

        first = store.consume(issued.permit_id, order_request_id=first_request_id, now=FIXED_NOW)
        second = store.consume(issued.permit_id, order_request_id=second_request_id, now=FIXED_NOW)

        assert first.outcome is CanaryPermitConsumeOutcome.CONSUMED
        assert second.outcome is CanaryPermitConsumeOutcome.ALREADY_CONSUMED
        assert second.consumption is not None
        assert second.consumption.order_request_id == first_request_id

        # The loser's attempt genuinely inserted nothing.
        with engine.connect() as connection:
            rows = connection.execute(
                select(canary_permit_consumptions).where(
                    canary_permit_consumptions.c.permit_id == issued.permit_id
                )
            ).fetchall()
        assert len(rows) == 1

    def test_an_expired_permit_is_refused_without_inserting_a_consumption_row(
        self, engine: Engine
    ) -> None:
        store = CanaryPermitStore(engine)
        issued = permit(valid_until_utc=FIXED_NOW + timedelta(minutes=5))
        store.issue(issued)

        result = store.consume(
            issued.permit_id,
            order_request_id=uuid4(),
            now=FIXED_NOW + timedelta(minutes=6),
        )

        assert result.outcome is CanaryPermitConsumeOutcome.EXPIRED
        assert result.consumption is None
        assert store.consumption_for(issued.permit_id) is None

    def test_consuming_exactly_at_the_deadline_still_succeeds(self, engine: Engine) -> None:
        issued = permit(valid_until_utc=FIXED_NOW + timedelta(minutes=5))
        store = CanaryPermitStore(engine)
        store.issue(issued)

        result = store.consume(
            issued.permit_id,
            order_request_id=uuid4(),
            now=FIXED_NOW + timedelta(minutes=5),
        )

        assert result.outcome is CanaryPermitConsumeOutcome.CONSUMED

    def test_an_unknown_permit_id_is_not_found(self, engine: Engine) -> None:
        store = CanaryPermitStore(engine)

        result = store.consume(uuid4(), order_request_id=uuid4(), now=FIXED_NOW)

        assert result.outcome is CanaryPermitConsumeOutcome.NOT_FOUND
        assert result.consumption is None


class TestConsumeIsAtomicUnderRealConcurrency:
    """This test only proves anything under a real database — a single

    Postgres instance's real unique-constraint enforcement, real
    concurrent connections. Mirrors `test_agent_gateway_store.py
    ::TestRateLimitIsAtomicUnderRealConcurrency`'s own shape.
    """

    def test_exactly_one_of_many_concurrent_consumers_wins(self, engine: Engine) -> None:
        setup_store = CanaryPermitStore(engine)
        issued = permit()
        setup_store.issue(issued)

        order_request_ids = [uuid4() for _ in range(10)]
        results: list[Any] = []
        results_lock = threading.Lock()

        def attempt(order_request_id: Any) -> None:
            # A fresh store per thread, sharing only the engine -- the
            # same shape genuinely separate concurrent workers would have.
            worker_store = CanaryPermitStore(engine)
            result = worker_store.consume(
                issued.permit_id, order_request_id=order_request_id, now=FIXED_NOW
            )
            with results_lock:
                results.append(result)

        threads = [threading.Thread(target=attempt, args=(rid,)) for rid in order_request_ids]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        consumed = [r for r in results if r.outcome is CanaryPermitConsumeOutcome.CONSUMED]
        already = [r for r in results if r.outcome is CanaryPermitConsumeOutcome.ALREADY_CONSUMED]
        assert len(consumed) == 1
        assert len(already) == 9

        with engine.connect() as connection:
            rows = connection.execute(
                select(canary_permit_consumptions).where(
                    canary_permit_consumptions.c.permit_id == issued.permit_id
                )
            ).fetchall()
        assert len(rows) == 1
