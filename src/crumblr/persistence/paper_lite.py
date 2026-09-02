"""Durable, paper-only wrapper around :class:`SimulatedBroker`.

The fill engine remains the existing in-memory simulator. This module only
adds an append-only hash-chain journal and deterministic replay so a process
restart reconstructs the same positions, P&L, SL/TP exits and handled request
ids. It deliberately has no generic ``BrokerPort`` constructor argument: a
real MT5 adapter cannot be injected into the paper path by configuration.

Paper fills remain an approximation. The simulator's documented spread,
slippage and pessimistic same-bar SL/TP assumptions are preserved exactly.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import UUID

from crumblr.domain.enums import DataQuality, Side
from crumblr.domain.events import OrderCheckCompleted
from crumblr.domain.hashing import fingerprint
from crumblr.domain.models import (
    AccountState,
    ApprovedOrder,
    Bar,
    ExecutionResult,
    InstrumentSpec,
    MarketSnapshot,
    PositionState,
)
from crumblr.domain.money import ZERO
from crumblr.domain.timeutils import UtcDatetime
from crumblr.market_data.synthetic import GeneratedTick
from crumblr.mt5_gateway.simulated import ClosedTrade, SimulatedBroker
from crumblr.risk.sizing import realised_risk

PAPER_JOURNAL_SCHEMA_VERSION = "1.0"
SUPERVISOR_SKIPPED_PAPER_MODE = "SUPERVISOR_SKIPPED_PAPER_MODE"


class PaperJournalError(RuntimeError):
    """Base error for a paper journal that cannot be trusted or applied."""


class PaperJournalConflictError(PaperJournalError):
    """One idempotency key was reused with materially different content."""


class PaperJournalCorruptionError(PaperJournalError):
    """The append-only journal's sequence or hash chain is invalid."""


class PaperJournalEventType(StrEnum):
    PORTFOLIO_CREATED = "PORTFOLIO_CREATED"
    MARKET_OBSERVED = "MARKET_OBSERVED"
    PAPER_ORDER_ACCEPTED = "PAPER_ORDER_ACCEPTED"
    PAPER_FLATTEN_REQUESTED = "PAPER_FLATTEN_REQUESTED"
    AUDIT_FACT = "AUDIT_FACT"


@dataclass(frozen=True)
class PaperJournalEntry:
    sequence: int
    event_type: PaperJournalEventType
    event_key: str
    previous_hash: str | None
    payload: dict[str, Any]
    record_hash: str


@dataclass(frozen=True)
class PaperPortfolioView:
    """Strategy-neutral read model suitable for a dashboard adapter.

    ``authorized_open_risk_amount`` is the sum of the original RiskDecision
    amounts for positions that remain open. It is useful visibility, but is
    deliberately *not* labelled or used as Core's exact projected-open-risk
    semantic; that shared seam is still pending (worklog PL-001).
    """

    balance: Decimal
    equity: Decimal
    unrealised_profit: Decimal
    realized_profit: Decimal
    open_position_count: int
    closed_trade_count: int
    authorized_open_risk_amount: Decimal
    exact_open_risk_amount: Decimal
    exact_open_risk_fraction: Decimal
    latest_observation_time_utc: str | None


def generated_tick_from_snapshot(snapshot: MarketSnapshot) -> GeneratedTick:
    """Adapt one trusted real snapshot to the simulator's existing input.

    The latest confirmed closed bar drives SL/TP resolution while the current
    quote drives account mark-to-market and any following market entry. A
    snapshot without a confirmed bar cannot be simulated honestly and fails
    closed instead of inventing an OHLC range.
    """

    if not snapshot.bars:
        raise ValueError("PAPER_LITE requires at least one confirmed closed bar")
    return GeneratedTick(
        bar=snapshot.bars[-1],
        bid=snapshot.bid,
        ask=snapshot.ask,
        spread_points=snapshot.spread_points,
        event_time_utc=snapshot.event_time_utc,
        received_time_utc=snapshot.received_time_utc,
        data_quality=snapshot.data_quality,
    )


class DurablePaperBroker:
    """A reconstructable paper portfolio backed only by ``SimulatedBroker``."""

    def __init__(
        self,
        journal_path: Path,
        spec: InstrumentSpec,
        *,
        starting_balance: Decimal,
        account_currency: str = "EUR",
        leverage: int = 30,
    ) -> None:
        if starting_balance <= ZERO:
            raise ValueError("starting paper balance must be positive and explicit")

        self._path = journal_path
        self._spec = spec
        self._starting_balance = starting_balance
        self._account_currency = account_currency
        self._leverage = leverage
        self._broker = self._new_broker()
        self._entries: list[PaperJournalEntry] = []
        self._event_payload_hashes: dict[str, str] = {}
        self._order_fingerprints: dict[UUID, str] = {}
        self._order_results: dict[UUID, ExecutionResult] = {}
        self._order_risk_amounts: dict[int, Decimal] = {}
        self._flatten_results: dict[UUID, tuple[int, ...]] = {}
        self._latest_tick: GeneratedTick | None = None

        if self._path.exists():
            self._load_and_replay()
        else:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._append(
                PaperJournalEventType.PORTFOLIO_CREATED,
                event_key="portfolio",
                payload=self._header_payload(),
            )

    def _new_broker(self) -> SimulatedBroker:
        return SimulatedBroker(
            self._spec,
            starting_balance=self._starting_balance,
            account_currency=self._account_currency,
            server="Crumblr-PAPER_LITE",
            leverage=self._leverage,
        )

    def _header_payload(self) -> dict[str, Any]:
        return {
            "journal_schema_version": PAPER_JOURNAL_SCHEMA_VERSION,
            "starting_balance": str(self._starting_balance),
            "account_currency": self._account_currency,
            "leverage": self._leverage,
            "instrument_spec": self._spec.model_dump(mode="json"),
            "instrument_spec_version": self._spec.spec_version,
        }

    @property
    def audit_entries(self) -> tuple[PaperJournalEntry, ...]:
        return tuple(self._entries)

    @property
    def closed_trades(self) -> tuple[ClosedTrade, ...]:
        return self._broker.closed_trades

    @property
    def has_market_observation(self) -> bool:
        return self._latest_tick is not None

    def account(self) -> AccountState:
        return self._broker.account()

    def positions(self) -> tuple[PositionState, ...]:
        return self._broker.positions()

    def order_check(self, order: ApprovedOrder) -> OrderCheckCompleted:
        return self._broker.order_check(order)

    def advance_snapshot(self, snapshot: MarketSnapshot) -> tuple[ClosedTrade, ...]:
        return self.advance(generated_tick_from_snapshot(snapshot))

    def advance(self, tick: GeneratedTick) -> tuple[ClosedTrade, ...]:
        payload = _tick_payload(tick)
        event_key = f"market:{fingerprint(payload)}"
        if event_key in self._event_payload_hashes:
            return ()
        self._append(PaperJournalEventType.MARKET_OBSERVED, event_key=event_key, payload=payload)
        self._latest_tick = tick
        return self._broker.advance(tick)

    def submit(self, order: ApprovedOrder, *, authorized_risk_amount: Decimal) -> ExecutionResult:
        """Durably claim then simulate one order; retry-safe across restart."""

        if authorized_risk_amount < ZERO:
            raise ValueError("authorized_risk_amount must not be negative")
        order_fingerprint = fingerprint(order.model_dump(mode="json"))
        existing = self._order_fingerprints.get(order.order_request_id)
        if existing is not None:
            if existing != order_fingerprint:
                raise PaperJournalConflictError(
                    f"paper order_request_id {order.order_request_id} was reused with "
                    "different content"
                )
            return self._order_results[order.order_request_id]

        payload = {
            "order": order.model_dump(mode="json"),
            "order_fingerprint": order_fingerprint,
            "authorized_risk_amount": str(authorized_risk_amount),
        }
        self._append(
            PaperJournalEventType.PAPER_ORDER_ACCEPTED,
            event_key=f"order:{order.order_request_id}",
            payload=payload,
        )
        return self._apply_order(order, order_fingerprint, authorized_risk_amount)

    def flatten_all(self, *, flatten_request_id: UUID, reason: str) -> tuple[int, ...]:
        """Durably claim then flatten the simulated book at its current quote."""

        if flatten_request_id in self._flatten_results:
            return self._flatten_results[flatten_request_id]
        payload = {"flatten_request_id": str(flatten_request_id), "reason": reason}
        self._append(
            PaperJournalEventType.PAPER_FLATTEN_REQUESTED,
            event_key=f"flatten:{flatten_request_id}",
            payload=payload,
        )
        closed = self._broker.close_all_positions(reason=reason)
        self._flatten_results[flatten_request_id] = closed
        return closed

    def record_audit_fact(
        self, fact: str, *, correlation_id: UUID, detail: str | None = None
    ) -> bool:
        payload = {
            "fact": fact,
            "correlation_id": str(correlation_id),
            "detail": detail,
        }
        return self._append(
            PaperJournalEventType.AUDIT_FACT,
            event_key=f"audit:{fact}:{correlation_id}",
            payload=payload,
        )

    def portfolio_view(self) -> PaperPortfolioView:
        positions = self.positions()
        open_tickets = {position.ticket for position in positions}
        authorized_open_risk = sum(
            (
                amount
                for ticket, amount in self._order_risk_amounts.items()
                if ticket in open_tickets
            ),
            ZERO,
        )
        realized_profit = self._broker.balance - self._starting_balance
        exact_open_risk = self.exact_open_risk_amount()
        exact_open_risk_fraction = (
            exact_open_risk / self._broker.equity if self._broker.equity > ZERO else ZERO
        )
        latest = self._entries[-1] if self._entries else None
        latest_observation = None
        if latest is not None:
            for entry in reversed(self._entries):
                if entry.event_type is PaperJournalEventType.MARKET_OBSERVED:
                    latest_observation = str(entry.payload["received_time_utc"])
                    break
        return PaperPortfolioView(
            balance=self._broker.balance,
            equity=self._broker.equity,
            unrealised_profit=self._broker.unrealised_profit,
            realized_profit=realized_profit,
            open_position_count=len(positions),
            closed_trade_count=len(self.closed_trades),
            authorized_open_risk_amount=authorized_open_risk,
            exact_open_risk_amount=exact_open_risk,
            exact_open_risk_fraction=exact_open_risk_fraction,
            latest_observation_time_utc=latest_observation,
        )

    def exact_open_risk_amount(self) -> Decimal:
        """Loss from the current executable quote to every remaining stop.

        This is derived from the simulator's current bid/ask, broker-reported
        position volume and the trusted instrument tick facts. A stop that has
        moved beyond the executable quote locks profit and contributes zero
        downside risk rather than a misleading absolute distance.
        """

        positions = self.positions()
        if not positions:
            return ZERO
        if self._latest_tick is None:
            raise PaperJournalError("open paper positions have no market observation")

        total = ZERO
        for position in positions:
            if position.stop_loss_price is None:
                raise PaperJournalError(f"paper position {position.ticket} has no stop-loss price")
            if position.side is Side.BUY:
                distance = max(ZERO, self._latest_tick.bid - position.stop_loss_price)
            else:
                distance = max(ZERO, position.stop_loss_price - self._latest_tick.ask)
            if distance > ZERO:
                total += realised_risk(position.volume, distance, self._spec)
        return total

    def exact_open_risk_fraction(self) -> Decimal:
        equity = self._broker.equity
        if equity <= ZERO:
            raise PaperJournalError("paper equity is not positive")
        return self.exact_open_risk_amount() / equity

    def _apply_order(
        self,
        order: ApprovedOrder,
        order_fingerprint: str,
        authorized_risk_amount: Decimal,
    ) -> ExecutionResult:
        result = self._broker.order_send(order)
        self._order_fingerprints[order.order_request_id] = order_fingerprint
        self._order_results[order.order_request_id] = result
        if result.mt5_position_ticket is not None:
            self._order_risk_amounts[result.mt5_position_ticket] = authorized_risk_amount
        return result

    def _load_and_replay(self) -> None:
        raw_entries = self._read_entries()
        if not raw_entries:
            raise PaperJournalCorruptionError("paper journal exists but is empty")
        header = raw_entries[0]
        if header.event_type is not PaperJournalEventType.PORTFOLIO_CREATED:
            raise PaperJournalCorruptionError("paper journal does not start with PORTFOLIO_CREATED")
        if header.payload != self._header_payload():
            raise PaperJournalConflictError(
                "existing paper journal belongs to a different balance, currency, leverage, "
                "or instrument spec"
            )

        self._entries = raw_entries
        for entry in raw_entries:
            payload_hash = fingerprint(entry.payload)
            existing = self._event_payload_hashes.get(entry.event_key)
            if existing is not None:
                if existing != payload_hash:
                    raise PaperJournalConflictError(
                        f"event key {entry.event_key!r} has conflicting payloads"
                    )
                continue
            self._event_payload_hashes[entry.event_key] = payload_hash

            if entry.event_type is PaperJournalEventType.MARKET_OBSERVED:
                tick = _tick_from_payload(entry.payload)
                self._latest_tick = tick
                self._broker.advance(tick)
            elif entry.event_type is PaperJournalEventType.PAPER_ORDER_ACCEPTED:
                order = ApprovedOrder.model_validate(entry.payload["order"])
                order_fingerprint = str(entry.payload["order_fingerprint"])
                expected = fingerprint(order.model_dump(mode="json"))
                if order_fingerprint != expected:
                    raise PaperJournalCorruptionError(
                        f"stored order fingerprint mismatch for {order.order_request_id}"
                    )
                self._apply_order(
                    order,
                    order_fingerprint,
                    Decimal(str(entry.payload["authorized_risk_amount"])),
                )
            elif entry.event_type is PaperJournalEventType.PAPER_FLATTEN_REQUESTED:
                request_id = UUID(str(entry.payload["flatten_request_id"]))
                closed = self._broker.close_all_positions(reason=str(entry.payload["reason"]))
                self._flatten_results[request_id] = closed

    def _read_entries(self) -> list[PaperJournalEntry]:
        entries: list[PaperJournalEntry] = []
        previous_hash: str | None = None
        with self._path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.endswith("\n"):
                    raise PaperJournalCorruptionError(
                        f"paper journal line {line_number} is incomplete"
                    )
                try:
                    raw = json.loads(line)
                    entry = PaperJournalEntry(
                        sequence=int(raw["sequence"]),
                        event_type=PaperJournalEventType(raw["event_type"]),
                        event_key=str(raw["event_key"]),
                        previous_hash=raw["previous_hash"],
                        payload=dict(raw["payload"]),
                        record_hash=str(raw["record_hash"]),
                    )
                except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                    raise PaperJournalCorruptionError(
                        f"paper journal line {line_number} is malformed: {error}"
                    ) from error
                if entry.sequence != len(entries):
                    raise PaperJournalCorruptionError(
                        f"paper journal sequence {entry.sequence} is not expected {len(entries)}"
                    )
                if entry.previous_hash != previous_hash:
                    raise PaperJournalCorruptionError(
                        f"paper journal hash chain breaks at sequence {entry.sequence}"
                    )
                if entry.record_hash != _record_hash(entry):
                    raise PaperJournalCorruptionError(
                        f"paper journal record hash mismatch at sequence {entry.sequence}"
                    )
                entries.append(entry)
                previous_hash = entry.record_hash
        return entries

    def _append(
        self,
        event_type: PaperJournalEventType,
        *,
        event_key: str,
        payload: dict[str, Any],
    ) -> bool:
        payload_hash = fingerprint(payload)
        existing = self._event_payload_hashes.get(event_key)
        if existing is not None:
            if existing != payload_hash:
                raise PaperJournalConflictError(
                    f"paper journal event key {event_key!r} was reused with different content"
                )
            return False

        entry = PaperJournalEntry(
            sequence=len(self._entries),
            event_type=event_type,
            event_key=event_key,
            previous_hash=self._entries[-1].record_hash if self._entries else None,
            payload=payload,
            record_hash="",
        )
        entry = PaperJournalEntry(**{**asdict(entry), "record_hash": _record_hash(entry)})
        encoded = _canonical_json(_entry_without_empty_hash(entry)) + "\n"
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        self._entries.append(entry)
        self._event_payload_hashes[event_key] = payload_hash
        return True


def _tick_payload(tick: GeneratedTick) -> dict[str, Any]:
    return {
        "bar": tick.bar.model_dump(mode="json"),
        "bid": str(tick.bid),
        "ask": str(tick.ask),
        "spread_points": tick.spread_points,
        "event_time_utc": tick.event_time_utc.isoformat(),
        "received_time_utc": tick.received_time_utc.isoformat(),
        "data_quality": tick.data_quality.value,
        "injected_fault": tick.injected_fault,
    }


def _tick_from_payload(payload: dict[str, Any]) -> GeneratedTick:
    return GeneratedTick(
        bar=Bar.model_validate(payload["bar"]),
        bid=Decimal(str(payload["bid"])),
        ask=Decimal(str(payload["ask"])),
        spread_points=int(payload["spread_points"]),
        event_time_utc=_parse_datetime(str(payload["event_time_utc"])),
        received_time_utc=_parse_datetime(str(payload["received_time_utc"])),
        data_quality=DataQuality(str(payload["data_quality"])),
        injected_fault=(
            str(payload["injected_fault"]) if payload.get("injected_fault") is not None else None
        ),
    )


def _parse_datetime(value: str) -> UtcDatetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise PaperJournalCorruptionError(f"paper journal timestamp is timezone-naive: {value!r}")
    return parsed.astimezone(UTC)


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _record_material(entry: PaperJournalEntry) -> dict[str, Any]:
    return {
        "sequence": entry.sequence,
        "event_type": entry.event_type.value,
        "event_key": entry.event_key,
        "previous_hash": entry.previous_hash,
        "payload": entry.payload,
    }


def _record_hash(entry: PaperJournalEntry) -> str:
    return hashlib.sha256(_canonical_json(_record_material(entry)).encode("utf-8")).hexdigest()


def _entry_without_empty_hash(entry: PaperJournalEntry) -> dict[str, Any]:
    value = _record_material(entry)
    value["record_hash"] = entry.record_hash
    return value
