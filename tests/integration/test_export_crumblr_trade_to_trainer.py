"""`scripts/export_crumblr_trade_to_trainer.py::_resolve_isolated_close_window`
against real PostgreSQL.

An account-balance delta is only a valid proxy for one trade's realized
outcome if the window it spans is demonstrably isolated to that one
ticket -- these tests exercise the five-step isolation contract directly
against real `broker_account_snapshots`/`broker_position_snapshots`/
`execution_events` rows, not through the full `resolve_evidence()`
orchestration (covered separately, manually, against the real Attempt-7
trade).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from scripts.export_crumblr_trade_to_trainer import (
    EvidenceIncompleteError,
    _resolve_isolated_close_window,
)
from sqlalchemy import Engine

from crumblr.application.broker_state import BrokerStateObservation
from crumblr.domain.enums import Environment, ExecutionEventType
from crumblr.domain.models import DecisionCapsule
from crumblr.persistence.broker_state import BrokerStateStore
from crumblr.persistence.execution import ExecutionEventStore, ExecutionRequestStore
from crumblr.persistence.journal import CapsuleStore
from tests.conftest import (
    FIXED_NOW,
    make_broker_account_snapshot,
    make_broker_position_snapshot,
    make_intent,
    make_risk_decision,
    make_supervisor_decision,
)

pytestmark = pytest.mark.integration

TARGET_TICKET = 89517316
FOREIGN_TICKET = 11223344


def record_cycle(
    engine: Engine, *, observed_at: datetime, balance: Decimal, tickets: tuple[int, ...]
) -> None:
    """One broker-state capture cycle: one account snapshot plus one
    position row per ticket in `tickets`, all sharing that snapshot's own
    `snapshot_id` -- the real FK relationship `record()` writes atomically,
    never a coincidental timestamp match."""
    account = make_broker_account_snapshot(
        observed_at_utc=observed_at, balance=balance, equity=balance
    )
    positions = tuple(
        make_broker_position_snapshot(
            snapshot_id=account.snapshot_id, ticket=ticket, observed_at_utc=observed_at
        )
        for ticket in tickets
    )
    BrokerStateStore(engine).record(
        BrokerStateObservation(account=account, positions=positions, pending_orders=())
    )


def claim_and_fill(engine: Engine, *, order_request_id: UUID, filled_at: datetime) -> None:
    """The minimum durable chain needed for one order to legitimately have
    a `FILLED` execution event: a sealed capsule, a claimed request, then
    the event itself."""
    intent = make_intent()
    capsule = DecisionCapsule(
        capsule_id=uuid4(),
        occurred_at_utc=FIXED_NOW,
        correlation_id=uuid4(),
        canonical_symbol="EUR/USD",
        broker_symbol="EURUSD",
        market_snapshot_id=uuid4(),
        feature_set_version="features-v1",
        feature_values_hash="abc123",
        strategy_version="0.1.0",
        model_version=None,
        trade_intent=intent,
        risk_config_version="cfg-v1",
        risk_decision=make_risk_decision(intent.intent_id),
        supervisor_decision=make_supervisor_decision(intent.intent_id),
        code_commit="deadbeef",
        environment=Environment.PAPER,
    )
    CapsuleStore(engine).seal(capsule)
    ExecutionRequestStore(engine).claim(
        order_request_id=order_request_id,
        capsule_id=capsule.capsule_id,
        intent_id=intent.intent_id,
        fingerprint="fp-1",
        claimed_by="test",
        now=FIXED_NOW,
    )
    ExecutionEventStore(engine).append(
        order_request_id=order_request_id,
        event_type=ExecutionEventType.FILLED,
        occurred_at_utc=filled_at,
        payload={"mt5_order_ticket": 1, "mt5_deal_ticket": 1},
    )


class TestResolveIsolatedCloseWindow:
    def test_isolated_target_only_to_flat_is_the_happy_path(self, engine: Engine) -> None:
        last_open_at = FIXED_NOW
        post_close_at = FIXED_NOW + timedelta(minutes=1)
        record_cycle(
            engine, observed_at=last_open_at, balance=Decimal("9993.55"), tickets=(TARGET_TICKET,)
        )
        record_cycle(engine, observed_at=post_close_at, balance=Decimal("9993.66"), tickets=())

        with engine.connect() as connection:
            window = _resolve_isolated_close_window(
                connection, ticket=TARGET_TICKET, order_request_id=uuid4()
            )

        assert window.last_open_at == last_open_at
        assert window.balance_before_close == Decimal("9993.55")
        assert window.post_close_at == post_close_at
        assert window.balance_after_close == Decimal("9993.66")

    def test_another_ticket_open_at_the_last_open_snapshot_blocks(self, engine: Engine) -> None:
        """Step 1: isolation at the open boundary itself."""
        last_open_at = FIXED_NOW
        record_cycle(
            engine,
            observed_at=last_open_at,
            balance=Decimal("9993.55"),
            tickets=(TARGET_TICKET, FOREIGN_TICKET),
        )
        record_cycle(
            engine,
            observed_at=FIXED_NOW + timedelta(minutes=1),
            balance=Decimal("9993.66"),
            tickets=(FOREIGN_TICKET,),
        )

        with engine.connect() as connection, pytest.raises(EvidenceIncompleteError):
            _resolve_isolated_close_window(
                connection, ticket=TARGET_TICKET, order_request_id=uuid4()
            )

    def test_another_ticket_appears_between_last_open_and_close_blocks(
        self, engine: Engine
    ) -> None:
        """Step 3: a foreign ticket appearing mid-window, while the target
        is still open, must also block -- not only at the boundaries."""
        record_cycle(
            engine, observed_at=FIXED_NOW, balance=Decimal("9993.55"), tickets=(TARGET_TICKET,)
        )
        record_cycle(
            engine,
            observed_at=FIXED_NOW + timedelta(minutes=1),
            balance=Decimal("9990.00"),
            tickets=(TARGET_TICKET, FOREIGN_TICKET),
        )
        record_cycle(
            engine,
            observed_at=FIXED_NOW + timedelta(minutes=2),
            balance=Decimal("9993.66"),
            tickets=(),
        )

        with engine.connect() as connection, pytest.raises(EvidenceIncompleteError):
            _resolve_isolated_close_window(
                connection, ticket=TARGET_TICKET, order_request_id=uuid4()
            )

    def test_the_close_observation_is_not_flat_blocks(self, engine: Engine) -> None:
        """Step 5: Slice 1 requires the book to be fully flat at close, not
        merely target-absent -- a still-open foreign position must block."""
        record_cycle(
            engine, observed_at=FIXED_NOW, balance=Decimal("9993.55"), tickets=(TARGET_TICKET,)
        )
        record_cycle(
            engine,
            observed_at=FIXED_NOW + timedelta(minutes=1),
            balance=Decimal("9993.66"),
            tickets=(FOREIGN_TICKET,),
        )

        with engine.connect() as connection, pytest.raises(EvidenceIncompleteError):
            _resolve_isolated_close_window(
                connection, ticket=TARGET_TICKET, order_request_id=uuid4()
            )

    def test_a_different_orders_filled_event_inside_the_window_blocks(self, engine: Engine) -> None:
        """Step 4: a same-cycle open+close by another order, invisible to
        position snapshots between two polls, must still be caught via its
        durable `FILLED` execution event."""
        last_open_at = FIXED_NOW
        post_close_at = FIXED_NOW + timedelta(minutes=5)
        record_cycle(
            engine, observed_at=last_open_at, balance=Decimal("9993.55"), tickets=(TARGET_TICKET,)
        )
        record_cycle(engine, observed_at=post_close_at, balance=Decimal("9993.66"), tickets=())
        foreign_order_request_id = uuid4()
        claim_and_fill(
            engine,
            order_request_id=foreign_order_request_id,
            filled_at=last_open_at + timedelta(minutes=2),
        )

        with engine.connect() as connection, pytest.raises(EvidenceIncompleteError):
            _resolve_isolated_close_window(
                connection, ticket=TARGET_TICKET, order_request_id=uuid4()
            )

    def test_a_different_orders_filled_event_outside_the_window_does_not_block(
        self, engine: Engine
    ) -> None:
        """Sanity check against a false positive: a `FILLED` event well
        before the window opens must not contaminate an otherwise-isolated
        close."""
        last_open_at = FIXED_NOW
        post_close_at = FIXED_NOW + timedelta(minutes=1)
        record_cycle(
            engine, observed_at=last_open_at, balance=Decimal("9993.55"), tickets=(TARGET_TICKET,)
        )
        record_cycle(engine, observed_at=post_close_at, balance=Decimal("9993.66"), tickets=())
        foreign_order_request_id = uuid4()
        claim_and_fill(
            engine,
            order_request_id=foreign_order_request_id,
            filled_at=last_open_at - timedelta(hours=1),
        )

        with engine.connect() as connection:
            window = _resolve_isolated_close_window(
                connection, ticket=TARGET_TICKET, order_request_id=uuid4()
            )

        assert window.balance_after_close == Decimal("9993.66")
