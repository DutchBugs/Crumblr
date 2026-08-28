"""PostgreSQL stores for the Agent Gateway (ADR-005 Step B).

Implements the `Protocol`s in `agent_gateway/stores.py` against the tables
in `persistence/schema.py`. Same two patterns the rest of this schema
already proves out:

- **Latest-snapshot-wins** (`agent_identities`) — `persistence/decision_window.py`'s
  shape: append a new row, read the latest by `sequence`.
- **Content-addressed claim** (`agent_trading_assignments`,
  `agent_decision_context_bundles`, `agent_decision_outcomes`) —
  `persistence/execution.py`'s shape: `INSERT ... ON CONFLICT DO NOTHING
  RETURNING`, and a fingerprint/content comparison on the loser to tell an
  idempotent retry from a genuine conflict.

Restart-safety needs no special recovery step here (unlike
`decision_window_states`' `recover_decision_window`): every Gateway method
reads straight from PostgreSQL on each call rather than caching identity,
assignment or claim state in memory, so a freshly-started process sees
exactly what a long-running one would.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import Connection, Engine, and_, desc, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from crumblr.agent_gateway.contracts import (
    AgentIdentity,
    DecisionContextBundle,
    NoTradeDecision,
    TradeProposal,
    TradingAssignment,
)
from crumblr.agent_gateway.errors import (
    AssignmentConflictError,
    ContextConflictError,
    DecisionConflictError,
)
from crumblr.agent_gateway.events import AgentDecisionEventType, AgentOutcomeType
from crumblr.agent_gateway.stores import AgentDecisionEventRecord, OutcomeClaimResult
from crumblr.domain.hashing import fingerprint
from crumblr.domain.timeutils import UtcDatetime
from crumblr.persistence.schema import (
    agent_credentials,
    agent_decision_context_bundles,
    agent_decision_events,
    agent_decision_outcomes,
    agent_identities,
    agent_trading_assignments,
)


def _event_id_for(*, outcome_id: UUID, event_type: AgentDecisionEventType) -> UUID:
    """Derived, not random — mirrors `persistence/execution.py::event_id_for`:

    a retry that re-appends the same logical event (e.g. `RECEIVED` on a
    resumed, previously-interrupted claim) converges on the same row
    instead of duplicating it."""
    return uuid5(NAMESPACE_URL, f"crumblr:agent_decision_event:{outcome_id}:{event_type.value}")


class PostgresAgentIdentityStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def register(self, identity: AgentIdentity) -> None:
        statement = agent_identities.insert().values(
            event_id=uuid4(),
            agent_id=identity.agent_id,
            role=identity.role.value,
            status=identity.status.value,
            service_identity=identity.service_identity,
            registered_at_utc=identity.registered_at_utc,
            payload=identity.model_dump(mode="json"),
        )
        with self._engine.begin() as connection:
            connection.execute(statement)

    def current(self, agent_id: UUID) -> AgentIdentity | None:
        statement = (
            select(agent_identities.c.payload)
            .where(agent_identities.c.agent_id == agent_id)
            .order_by(desc(agent_identities.c.sequence))
            .limit(1)
        )
        with self._engine.connect() as connection:
            row = connection.execute(statement).first()
        if row is None:
            return None
        return AgentIdentity.model_validate(row[0])


class PostgresAgentCredentialStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def set_credential(self, *, agent_id: UUID, credential_hash: str) -> None:
        statement = agent_credentials.insert().values(
            event_id=uuid4(), agent_id=agent_id, credential_hash=credential_hash
        )
        with self._engine.begin() as connection:
            connection.execute(statement)

    def current_hash(self, agent_id: UUID) -> str | None:
        statement = (
            select(agent_credentials.c.credential_hash)
            .where(agent_credentials.c.agent_id == agent_id)
            .order_by(desc(agent_credentials.c.sequence))
            .limit(1)
        )
        with self._engine.connect() as connection:
            row = connection.execute(statement).first()
        return None if row is None else row[0]


class PostgresTradingAssignmentStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def register(self, assignment: TradingAssignment) -> None:
        payload = assignment.model_dump(mode="json")
        content_fingerprint = fingerprint(payload)
        statement = (
            pg_insert(agent_trading_assignments)
            .values(
                assignment_id=assignment.assignment_id,
                allowed_agent_id=assignment.allowed_agent_id,
                canonical_symbol=assignment.canonical_symbol,
                valid_from_utc=assignment.valid_from_utc,
                valid_until_utc=assignment.valid_until_utc,
                fingerprint=content_fingerprint,
                payload=payload,
            )
            .on_conflict_do_nothing(index_elements=["assignment_id"])
            .returning(agent_trading_assignments.c.assignment_id)
        )
        with self._engine.begin() as connection:
            won = connection.execute(statement).first() is not None
            if won:
                return
            existing_fingerprint = connection.execute(
                select(agent_trading_assignments.c.fingerprint).where(
                    agent_trading_assignments.c.assignment_id == assignment.assignment_id
                )
            ).scalar_one()
            if existing_fingerprint != content_fingerprint:
                raise AssignmentConflictError(
                    f"assignment_id {assignment.assignment_id} was already registered "
                    "with different content"
                )

    def current(self, assignment_id: UUID) -> TradingAssignment | None:
        statement = select(agent_trading_assignments.c.payload).where(
            agent_trading_assignments.c.assignment_id == assignment_id
        )
        with self._engine.connect() as connection:
            row = connection.execute(statement).first()
        if row is None:
            return None
        return TradingAssignment.model_validate(row[0])


class PostgresDecisionContextBundleStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def issue(self, bundle: DecisionContextBundle) -> None:
        statement = (
            pg_insert(agent_decision_context_bundles)
            .values(
                context_id=bundle.context_id,
                assignment_id=bundle.assignment_id,
                content_hash=bundle.content_hash,
                issued_at_utc=bundle.issued_at_utc,
                expires_at_utc=bundle.expires_at_utc,
                payload=bundle.model_dump(mode="json"),
            )
            .on_conflict_do_nothing(index_elements=["context_id"])
            .returning(agent_decision_context_bundles.c.context_id)
        )
        with self._engine.begin() as connection:
            won = connection.execute(statement).first() is not None
            if won:
                return
            existing_hash = connection.execute(
                select(agent_decision_context_bundles.c.content_hash).where(
                    agent_decision_context_bundles.c.context_id == bundle.context_id
                )
            ).scalar_one()
            if existing_hash != bundle.content_hash:
                raise ContextConflictError(
                    f"context_id {bundle.context_id} was already issued with different content"
                )

    def by_id(self, context_id: UUID) -> DecisionContextBundle | None:
        statement = select(agent_decision_context_bundles.c.payload).where(
            agent_decision_context_bundles.c.context_id == context_id
        )
        with self._engine.connect() as connection:
            row = connection.execute(statement).first()
        if row is None:
            return None
        return DecisionContextBundle.model_validate(row[0])

    def by_hash(self, content_hash: str) -> DecisionContextBundle | None:
        statement = (
            select(agent_decision_context_bundles.c.payload)
            .where(agent_decision_context_bundles.c.content_hash == content_hash)
            .order_by(desc(agent_decision_context_bundles.c.sequence))
            .limit(1)
        )
        with self._engine.connect() as connection:
            row = connection.execute(statement).first()
        if row is None:
            return None
        return DecisionContextBundle.model_validate(row[0])


class PostgresAgentDecisionOutcomeStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @contextmanager
    def transaction(self) -> Iterator[Connection]:
        with self._engine.begin() as connection:
            yield connection

    def lock_assignment(self, assignment_id: UUID, *, connection: Connection | None = None) -> None:
        """Serializes the whole claim→count→evaluate→settle sequence for one

        `assignment_id` (`AgentGateway.submit_trade_proposal`/`submit_no_trade`
        hold this for the duration of the call, via `transaction()`). A
        Postgres transaction-scoped advisory lock — released automatically
        at commit/rollback, never needs an explicit unlock. Fixes a real
        race a self-review caught: reading the proposal-rate-limit count
        and then claiming in two separate transactions let two concurrent
        proposals for the same assignment both observe a below-limit count
        and both get accepted. Requires an active transaction (a caller
        must be inside `transaction()`) — there is no meaningful "lock
        outside a transaction" for an advisory *transaction* lock.
        """
        if connection is None:
            raise RuntimeError(
                "lock_assignment() requires a connection from an open transaction() -- "
                "an advisory transaction lock has no effect outside one"
            )
        connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": str(assignment_id)}
        )

    def claim_trade_proposal(
        self, proposal: TradeProposal, *, now: UtcDatetime, connection: Connection | None = None
    ) -> OutcomeClaimResult:
        return self._claim(
            outcome_id=proposal.proposal_id,
            outcome_type=AgentOutcomeType.TRADE_PROPOSAL,
            agent_id=proposal.agent_id,
            assignment_id=proposal.assignment_id,
            fingerprint_value=proposal.proposal_fingerprint,
            payload=proposal.model_dump(mode="json"),
            now=now,
            connection=connection,
        )

    def claim_no_trade(
        self, decision: NoTradeDecision, *, now: UtcDatetime, connection: Connection | None = None
    ) -> OutcomeClaimResult:
        return self._claim(
            outcome_id=decision.decision_id,
            outcome_type=AgentOutcomeType.NO_TRADE,
            agent_id=decision.agent_id,
            assignment_id=decision.assignment_id,
            fingerprint_value=decision.decision_fingerprint,
            payload=decision.model_dump(mode="json"),
            now=now,
            connection=connection,
        )

    def _claim(
        self,
        *,
        outcome_id: UUID,
        outcome_type: AgentOutcomeType,
        agent_id: UUID,
        assignment_id: UUID,
        fingerprint_value: str,
        payload: dict[str, Any],
        now: UtcDatetime,
        connection: Connection | None,
    ) -> OutcomeClaimResult:
        statement = (
            pg_insert(agent_decision_outcomes)
            .values(
                outcome_id=outcome_id,
                outcome_type=outcome_type.value,
                agent_id=agent_id,
                assignment_id=assignment_id,
                fingerprint=fingerprint_value,
                claimed_at_utc=now,
                payload=payload,
            )
            .on_conflict_do_nothing(index_elements=["outcome_id"])
            .returning(agent_decision_outcomes.c.outcome_id)
        )
        if connection is not None:
            return self._run_claim(connection, statement, outcome_id, fingerprint_value)
        with self._engine.begin() as own_connection:
            return self._run_claim(own_connection, statement, outcome_id, fingerprint_value)

    def _run_claim(
        self, connection: Connection, statement: Any, outcome_id: UUID, fingerprint_value: str
    ) -> OutcomeClaimResult:
        won = connection.execute(statement).first() is not None
        if won:
            return OutcomeClaimResult(claimed=True)
        existing_fingerprint = connection.execute(
            select(agent_decision_outcomes.c.fingerprint).where(
                agent_decision_outcomes.c.outcome_id == outcome_id
            )
        ).scalar_one()
        if existing_fingerprint != fingerprint_value:
            raise DecisionConflictError(
                f"outcome id {outcome_id} was already claimed with a different "
                f"fingerprint ({existing_fingerprint!r} != {fingerprint_value!r}) -- "
                "the same idempotency key would refer to two different decisions"
            )
        return OutcomeClaimResult(claimed=False)

    def outcome_type(self, outcome_id: UUID) -> AgentOutcomeType | None:
        statement = select(agent_decision_outcomes.c.outcome_type).where(
            agent_decision_outcomes.c.outcome_id == outcome_id
        )
        with self._engine.connect() as connection:
            row = connection.execute(statement).first()
        return None if row is None else AgentOutcomeType(row[0])

    def count_claimed_since(
        self, *, assignment_id: UUID, since: UtcDatetime, connection: Connection | None = None
    ) -> int:
        statement = (
            select(func.count())
            .select_from(agent_decision_outcomes)
            .where(
                and_(
                    agent_decision_outcomes.c.assignment_id == assignment_id,
                    agent_decision_outcomes.c.claimed_at_utc >= since,
                )
            )
        )
        if connection is not None:
            return int(connection.execute(statement).scalar_one())
        with self._engine.connect() as own_connection:
            return int(own_connection.execute(statement).scalar_one())

    def append_event(
        self,
        *,
        outcome_id: UUID,
        event_type: AgentDecisionEventType,
        occurred_at_utc: UtcDatetime,
        reason_codes: tuple[str, ...] = (),
        detail: str | None = None,
        connection: Connection | None = None,
    ) -> None:
        statement = (
            pg_insert(agent_decision_events)
            .values(
                event_id=_event_id_for(outcome_id=outcome_id, event_type=event_type),
                outcome_id=outcome_id,
                event_type=event_type.value,
                occurred_at_utc=occurred_at_utc,
                reason_codes=list(reason_codes),
                detail=detail,
            )
            .on_conflict_do_nothing(index_elements=["event_id"])
        )
        if connection is not None:
            connection.execute(statement)
            return
        with self._engine.begin() as own_connection:
            own_connection.execute(statement)

    def events_for(self, outcome_id: UUID) -> tuple[AgentDecisionEventRecord, ...]:
        statement = (
            select(agent_decision_events)
            .where(agent_decision_events.c.outcome_id == outcome_id)
            .order_by(agent_decision_events.c.sequence)
        )
        with self._engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return tuple(
            AgentDecisionEventRecord(
                outcome_id=row["outcome_id"],
                event_type=AgentDecisionEventType(row["event_type"]),
                occurred_at_utc=row["occurred_at_utc"],
                reason_codes=tuple(row["reason_codes"]),
                detail=row["detail"],
            )
            for row in rows
        )

    def settlement_for(
        self, outcome_id: UUID, *, connection: Connection | None = None
    ) -> AgentDecisionEventRecord | None:
        statement = (
            select(agent_decision_events)
            .where(
                agent_decision_events.c.outcome_id == outcome_id,
                agent_decision_events.c.event_type.in_(
                    [AgentDecisionEventType.ACCEPTED.value, AgentDecisionEventType.REJECTED.value]
                ),
            )
            .limit(1)
        )
        if connection is not None:
            row = connection.execute(statement).mappings().first()
        else:
            with self._engine.connect() as own_connection:
                row = own_connection.execute(statement).mappings().first()
        if row is None:
            return None
        return AgentDecisionEventRecord(
            outcome_id=row["outcome_id"],
            event_type=AgentDecisionEventType(row["event_type"]),
            occurred_at_utc=row["occurred_at_utc"],
            reason_codes=tuple(row["reason_codes"]),
            detail=row["detail"],
        )
