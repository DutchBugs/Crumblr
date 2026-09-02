from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from crumblr.domain.enums import Environment
from crumblr.persistence.paper_lite import (
    SUPERVISOR_SKIPPED_PAPER_MODE,
    DurablePaperBroker,
    PaperJournalConflictError,
    PaperJournalCorruptionError,
    PaperJournalEventType,
    generated_tick_from_snapshot,
)
from tests.conftest import (
    FIXED_NOW,
    make_approved_order,
    make_bar,
    make_instrument_spec,
    make_snapshot,
)


def make_broker(path: Path) -> DurablePaperBroker:
    return DurablePaperBroker(
        path,
        make_instrument_spec(),
        starting_balance=Decimal("10000"),
    )


class TestMarketObservationAdapter:
    def test_requires_a_confirmed_closed_bar(self) -> None:
        with pytest.raises(ValueError, match="confirmed closed bar"):
            generated_tick_from_snapshot(make_snapshot(bars=()))

    def test_forwards_the_latest_bar_and_current_quote(self) -> None:
        older = make_bar(open_time_utc=FIXED_NOW - timedelta(minutes=1))
        latest = make_bar(open_time_utc=FIXED_NOW)
        snapshot = make_snapshot(bars=(older, latest))

        tick = generated_tick_from_snapshot(snapshot)

        assert tick.bar == latest
        assert tick.bid == snapshot.bid
        assert tick.ask == snapshot.ask
        assert tick.event_time_utc == snapshot.event_time_utc


class TestDurablePaperBroker:
    def test_restart_reconstructs_a_fill_and_retry_does_not_double_fill(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "paper.jsonl"
        broker = make_broker(path)
        snapshot = make_snapshot(symbol_spec_version=make_instrument_spec().spec_version)
        broker.advance_snapshot(snapshot)
        order = make_approved_order(
            environment=Environment.PAPER,
            final_risk_decision_id=None,
        )

        first = broker.submit(order, authorized_risk_amount=Decimal("100"))
        assert len(broker.positions()) == 1
        assert broker.portfolio_view().exact_open_risk_amount == Decimal("10.00")

        restarted = make_broker(path)
        retried = restarted.submit(order, authorized_risk_amount=Decimal("100"))

        assert retried == first
        assert len(restarted.positions()) == 1
        assert restarted.portfolio_view().authorized_open_risk_amount == Decimal("100")
        assert restarted.portfolio_view().exact_open_risk_amount == Decimal("10.00")
        assert (
            sum(
                entry.event_type is PaperJournalEventType.PAPER_ORDER_ACCEPTED
                for entry in restarted.audit_entries
            )
            == 1
        )

    def test_same_request_id_with_different_order_content_fails_closed(
        self, tmp_path: Path
    ) -> None:
        broker = make_broker(tmp_path / "paper.jsonl")
        broker.advance_snapshot(make_snapshot())
        request_id = uuid4()
        original = make_approved_order(
            order_request_id=request_id,
            environment=Environment.PAPER,
            final_risk_decision_id=None,
        )
        broker.submit(original, authorized_risk_amount=Decimal("100"))

        conflicting = original.model_copy(update={"volume": Decimal("0.06")})
        with pytest.raises(PaperJournalConflictError, match="different content"):
            broker.submit(conflicting, authorized_risk_amount=Decimal("100"))

    def test_sl_exit_and_realized_pnl_survive_restart(self, tmp_path: Path) -> None:
        path = tmp_path / "paper.jsonl"
        broker = make_broker(path)
        broker.advance_snapshot(make_snapshot())
        order = make_approved_order(
            environment=Environment.PAPER,
            final_risk_decision_id=None,
            stop_loss_price=Decimal("1.08300"),
        )
        broker.submit(order, authorized_risk_amount=Decimal("100"))

        stop_bar = make_bar(
            open_time_utc=FIXED_NOW + timedelta(minutes=1),
            low=Decimal("1.08200"),
            high=Decimal("1.08550"),
            close=Decimal("1.08350"),
        )
        closed = broker.advance_snapshot(
            make_snapshot(
                event_time_utc=FIXED_NOW + timedelta(minutes=1),
                received_time_utc=FIXED_NOW + timedelta(minutes=1, milliseconds=8),
                bid=Decimal("1.08345"),
                ask=Decimal("1.08355"),
                bars=(stop_bar,),
            )
        )

        assert len(closed) == 1
        assert closed[0].exit_reason == "stop_loss"
        assert broker.portfolio_view().open_position_count == 0

        restarted = make_broker(path)
        assert restarted.portfolio_view() == broker.portfolio_view()
        assert restarted.closed_trades == broker.closed_trades

    def test_supervisor_skip_is_a_durable_explicit_fact(self, tmp_path: Path) -> None:
        path = tmp_path / "paper.jsonl"
        broker = make_broker(path)
        correlation_id = uuid4()

        broker.record_audit_fact(
            SUPERVISOR_SKIPPED_PAPER_MODE,
            correlation_id=correlation_id,
            detail="paper-only",
        )
        restarted = make_broker(path)

        assert any(
            entry.payload["fact"] == SUPERVISOR_SKIPPED_PAPER_MODE
            for entry in restarted.audit_entries
            if entry.event_type is PaperJournalEventType.AUDIT_FACT
        )

    def test_an_empty_flatten_retry_cannot_close_a_later_position(self, tmp_path: Path) -> None:
        broker = make_broker(tmp_path / "paper.jsonl")
        broker.advance_snapshot(make_snapshot())
        flatten_id = uuid4()
        assert broker.flatten_all(flatten_request_id=flatten_id, reason="friday") == ()
        broker.submit(
            make_approved_order(environment=Environment.PAPER, final_risk_decision_id=None),
            authorized_risk_amount=Decimal("100"),
        )

        assert broker.flatten_all(flatten_request_id=flatten_id, reason="friday") == ()
        assert len(broker.positions()) == 1

    def test_tampering_breaks_the_hash_chain(self, tmp_path: Path) -> None:
        path = tmp_path / "paper.jsonl"
        make_broker(path)
        lines = path.read_text(encoding="utf-8").splitlines()
        record = json.loads(lines[0])
        record["payload"]["starting_balance"] = "999999"
        path.write_text(json.dumps(record) + "\n", encoding="utf-8")

        with pytest.raises(PaperJournalCorruptionError, match="record hash mismatch"):
            make_broker(path)
