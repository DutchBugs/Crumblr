"""Build a `FlattenPlan` from an observed position book (core critical path

item 7, ADR-009). Pure: no I/O, no broker, no clock of its own — every
input is already-observed, mirroring `application/broker_state.py`'s own
"capture and return, never decide or compare" scoping discipline one step
further down. This module only *shapes* an observation into the commitment
contract; deciding whether a flatten may be committed at all is
`risk/flatten_gate.py`'s job, evaluated separately and before this is ever
called.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from uuid import UUID

from crumblr.domain.enums import Environment, Side
from crumblr.domain.models import FlattenInstruction, FlattenPlan, PositionState
from crumblr.domain.timeutils import UtcDatetime
from crumblr.risk.trading_window import has_crossed_weekly_close


def build_flatten_plan(
    positions: Sequence[PositionState],
    *,
    flatten_request_id: UUID,
    environment: Environment,
    canonical_symbol: str,
    trading_day: date,
    session_close_utc: UtcDatetime,
    flatten_deadline_utc: UtcDatetime,
    past_deadline: bool,
    broker_state_snapshot_id: UUID,
    now: UtcDatetime,
) -> FlattenPlan:
    """One `FlattenInstruction` per position, closing side derived as the

    genuine inverse (validated again inside `FlattenInstruction` itself,
    not trusted from this derivation alone). `crossed_weekly_close` on the
    plan is the aggregate — true if any target instruction is — while each
    instruction also carries its own, since a mixed book (one position
    past the deadline, another that merely crossed the weekly close) is a
    real case ADR-004 §7 leaves open, and the per-instruction detail keeps
    that reachable without a schema change.

    Owner risk policy v1 (D1.5) renamed this field from `crossed_rollover`
    — a real, deliberate change to a persisted audit-payload key
    (`FLATTEN_SUBMISSION_STARTED` events persist the full serialized
    `FlattenPlan`): historical rows carry `crossed_rollover`, rows from
    this change onward carry `crossed_weekly_close`. Recorded in
    `review/adr/ADR-012-owner-session-policy-v1.md` and
    `review/INTEGRATION_NOTICES.md`, not silent — nothing re-validates the
    persisted payload back into a typed model today, so nothing breaks,
    but an auditor reading old and new rows side by side needs to know why
    the key differs.
    """
    instructions = tuple(
        FlattenInstruction(
            flatten_request_id=flatten_request_id,
            ticket=position.ticket,
            broker_symbol=position.broker_symbol,
            position_side=position.side,
            close_side=Side.SELL if position.side is Side.BUY else Side.BUY,
            volume=position.volume,
            open_price=position.open_price,
            opened_at_utc=position.opened_at_utc,
            magic=position.magic,
            crossed_weekly_close=has_crossed_weekly_close(position.opened_at_utc, now),
            observed_at_utc=now,
        )
        for position in positions
    )
    crossed_weekly_close = any(instruction.crossed_weekly_close for instruction in instructions)
    return FlattenPlan(
        flatten_request_id=flatten_request_id,
        environment=environment,
        canonical_symbol=canonical_symbol,
        trading_day=trading_day,
        session_close_utc=session_close_utc,
        flatten_deadline_utc=flatten_deadline_utc,
        past_deadline=past_deadline,
        crossed_weekly_close=crossed_weekly_close,
        observed_at_utc=now,
        broker_state_snapshot_id=broker_state_snapshot_id,
        instructions=instructions,
    )
