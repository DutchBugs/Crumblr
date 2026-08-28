"""Store `Protocol`s for the Agent Gateway, plus in-memory implementations.

Same split the rest of this codebase uses everywhere a durable boundary
exists (`application/decision_window.py`, `risk/session.py`): a narrow
`Protocol` the Gateway depends on, an in-memory implementation for unit
tests and a short-lived process, and a PostgreSQL implementation
(`persistence/agent_gateway.py`) for anything that must survive a restart.

Every "register/issue/claim" method here is content-addressed and
fail-closed on conflict (`review/THREAT_MODEL_AGENT_GATEWAY.md` §5): the
same id with the same content is a safe no-op or an idempotent claim; the
same id with *different* content always raises, never silently overwrites.

`AgentDecisionOutcomeStore.transaction()`/`.lock_assignment()` exist so
`AgentGateway` can run one assignment's whole claim→evaluate→settle
sequence as a single serialized critical section (mirrors
`persistence/execution.py`'s optional `connection` parameter, extended
with a per-assignment advisory lock) — the fix for a real race a
self-review caught: reading a proposal-rate-limit count and then claiming
in two separate transactions let two concurrent proposals for the same
assignment both observe a below-limit count and both get accepted. The
in-memory implementation's lock/transaction are no-ops (tests run
single-threaded; there is nothing to serialize), but it exposes the same
shape so `AgentGateway`'s code does not need to know which backend it is
talking to.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

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
from crumblr.domain.hashing import fingerprint
from crumblr.domain.timeutils import UtcDatetime

# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #


class AgentIdentityStore(Protocol):
    """The latest-known `AgentIdentity` per `agent_id`.

    `register` also serves status transitions (suspend/retire): appending a
    new snapshot rather than mutating one, the same append-only discipline
    `persistence/risk_session.py`/`persistence/decision_window.py` use for
    state that must never look edited after the fact.
    """

    def register(self, identity: AgentIdentity) -> None: ...
    def current(self, agent_id: UUID) -> AgentIdentity | None: ...


class InMemoryAgentIdentityStore:
    def __init__(self) -> None:
        self._latest: dict[UUID, AgentIdentity] = {}

    def register(self, identity: AgentIdentity) -> None:
        self._latest[identity.agent_id] = identity

    def current(self, agent_id: UUID) -> AgentIdentity | None:
        return self._latest.get(agent_id)


# --------------------------------------------------------------------------- #
# Credentials (interim shared-secret auth — see agent_gateway/auth.py)
# --------------------------------------------------------------------------- #


class AgentCredentialStore(Protocol):
    def set_credential(self, *, agent_id: UUID, credential_hash: str) -> None: ...
    def current_hash(self, agent_id: UUID) -> str | None: ...


class InMemoryAgentCredentialStore:
    def __init__(self) -> None:
        self._hashes: dict[UUID, str] = {}

    def set_credential(self, *, agent_id: UUID, credential_hash: str) -> None:
        self._hashes[agent_id] = credential_hash

    def current_hash(self, agent_id: UUID) -> str | None:
        return self._hashes.get(agent_id)


# --------------------------------------------------------------------------- #
# Trading assignments — content-addressed, immutable once registered
# --------------------------------------------------------------------------- #


class TradingAssignmentStore(Protocol):
    def register(self, assignment: TradingAssignment) -> None: ...
    def current(self, assignment_id: UUID) -> TradingAssignment | None: ...


class InMemoryTradingAssignmentStore:
    def __init__(self) -> None:
        self._by_id: dict[UUID, TradingAssignment] = {}

    def register(self, assignment: TradingAssignment) -> None:
        existing = self._by_id.get(assignment.assignment_id)
        if existing is not None:
            if fingerprint(existing.model_dump(mode="json")) != fingerprint(
                assignment.model_dump(mode="json")
            ):
                raise AssignmentConflictError(
                    f"assignment_id {assignment.assignment_id} was already registered "
                    "with different content"
                )
            return
        self._by_id[assignment.assignment_id] = assignment

    def current(self, assignment_id: UUID) -> TradingAssignment | None:
        return self._by_id.get(assignment_id)


# --------------------------------------------------------------------------- #
# Decision context bundles — content-addressed, immutable once issued
# --------------------------------------------------------------------------- #


class DecisionContextBundleStore(Protocol):
    def issue(self, bundle: DecisionContextBundle) -> None: ...
    def by_id(self, context_id: UUID) -> DecisionContextBundle | None: ...
    def by_hash(self, content_hash: str) -> DecisionContextBundle | None: ...


class InMemoryDecisionContextBundleStore:
    def __init__(self) -> None:
        self._by_id: dict[UUID, DecisionContextBundle] = {}
        self._by_hash: dict[str, DecisionContextBundle] = {}

    def issue(self, bundle: DecisionContextBundle) -> None:
        existing = self._by_id.get(bundle.context_id)
        if existing is not None:
            if existing.content_hash != bundle.content_hash:
                raise ContextConflictError(
                    f"context_id {bundle.context_id} was already issued with different content"
                )
            return
        self._by_id[bundle.context_id] = bundle
        # A hash collision across different context_ids would itself be a
        # near-impossible SHA-256 collision, not a legitimate retry -- last
        # write is acceptable to overwrite in that theoretical case since it
        # can never be reached by a genuine retry (retries share context_id).
        self._by_hash[bundle.content_hash] = bundle

    def by_id(self, context_id: UUID) -> DecisionContextBundle | None:
        return self._by_id.get(context_id)

    def by_hash(self, content_hash: str) -> DecisionContextBundle | None:
        return self._by_hash.get(content_hash)


# --------------------------------------------------------------------------- #
# Decision outcomes (TradeProposal / NoTradeDecision) — idempotent claim
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class OutcomeClaimResult:
    claimed: bool
    """`True` only when *this* call's insert is the one that won the claim.

    `False` with no exception means an earlier attempt already holds this
    `outcome_id` with matching content — a safe retry, the same semantics
    `persistence/execution.py::ClaimResult` documents. This does **not**
    mean the outcome was ever settled (`ACCEPTED`/`REJECTED`) — an
    interrupted first attempt can leave a claimed-but-unsettled outcome;
    callers resume evaluation for that case rather than assuming
    acceptance (see `AgentGateway.submit_trade_proposal`)."""


@dataclass(frozen=True)
class AgentDecisionEventRecord:
    outcome_id: UUID
    event_type: AgentDecisionEventType
    occurred_at_utc: UtcDatetime
    reason_codes: tuple[str, ...] = ()
    detail: str | None = None


@dataclass
class _StoredOutcome:
    outcome_type: AgentOutcomeType
    agent_id: UUID
    assignment_id: UUID
    fingerprint: str
    claimed_at_utc: UtcDatetime
    events: list[AgentDecisionEventRecord] = field(default_factory=list)


_SETTLING_EVENT_TYPES = (AgentDecisionEventType.ACCEPTED, AgentDecisionEventType.REJECTED)


class AgentDecisionOutcomeStore(Protocol):
    def transaction(self) -> AbstractContextManager[Any]: ...

    def lock_assignment(self, assignment_id: UUID, *, connection: Any = None) -> None: ...

    def claim_trade_proposal(
        self, proposal: TradeProposal, *, now: UtcDatetime, connection: Any = None
    ) -> OutcomeClaimResult: ...

    def claim_no_trade(
        self, decision: NoTradeDecision, *, now: UtcDatetime, connection: Any = None
    ) -> OutcomeClaimResult: ...

    def outcome_type(self, outcome_id: UUID) -> AgentOutcomeType | None: ...

    def count_claimed_since(
        self, *, assignment_id: UUID, since: UtcDatetime, connection: Any = None
    ) -> int: ...

    def append_event(
        self,
        *,
        outcome_id: UUID,
        event_type: AgentDecisionEventType,
        occurred_at_utc: UtcDatetime,
        reason_codes: tuple[str, ...] = (),
        detail: str | None = None,
        connection: Any = None,
    ) -> None: ...

    def events_for(self, outcome_id: UUID) -> tuple[AgentDecisionEventRecord, ...]: ...

    def settlement_for(
        self, outcome_id: UUID, *, connection: Any = None
    ) -> AgentDecisionEventRecord | None:
        """The `ACCEPTED` or `REJECTED` event for `outcome_id`, if the claim

        was ever fully settled — `None` if it was claimed but the process
        that claimed it never got as far as recording a verdict (a
        crashed/interrupted attempt). Distinct from "no such outcome at
        all"; a caller checks `claim_trade_proposal`'s own result first to
        tell the two apart."""
        ...


class InMemoryAgentDecisionOutcomeStore:
    def __init__(self) -> None:
        self._outcomes: dict[UUID, _StoredOutcome] = {}

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """No real transaction: tests run single-threaded, there is nothing

        concurrent to serialize against."""
        yield None

    def lock_assignment(self, assignment_id: UUID, *, connection: Any = None) -> None:
        del assignment_id, connection  # no-op; see `transaction()`

    def claim_trade_proposal(
        self, proposal: TradeProposal, *, now: UtcDatetime, connection: Any = None
    ) -> OutcomeClaimResult:
        del connection
        return self._claim(
            outcome_id=proposal.proposal_id,
            outcome_type=AgentOutcomeType.TRADE_PROPOSAL,
            agent_id=proposal.agent_id,
            assignment_id=proposal.assignment_id,
            fingerprint_value=proposal.proposal_fingerprint,
            now=now,
        )

    def claim_no_trade(
        self, decision: NoTradeDecision, *, now: UtcDatetime, connection: Any = None
    ) -> OutcomeClaimResult:
        del connection
        return self._claim(
            outcome_id=decision.decision_id,
            outcome_type=AgentOutcomeType.NO_TRADE,
            agent_id=decision.agent_id,
            assignment_id=decision.assignment_id,
            fingerprint_value=decision.decision_fingerprint,
            now=now,
        )

    def _claim(
        self,
        *,
        outcome_id: UUID,
        outcome_type: AgentOutcomeType,
        agent_id: UUID,
        assignment_id: UUID,
        fingerprint_value: str,
        now: UtcDatetime,
    ) -> OutcomeClaimResult:
        existing = self._outcomes.get(outcome_id)
        if existing is not None:
            if existing.fingerprint != fingerprint_value:
                raise DecisionConflictError(
                    f"outcome id {outcome_id} was already claimed with a different "
                    f"fingerprint ({existing.fingerprint!r} != {fingerprint_value!r}) -- "
                    "the same idempotency key would refer to two different decisions"
                )
            return OutcomeClaimResult(claimed=False)
        self._outcomes[outcome_id] = _StoredOutcome(
            outcome_type=outcome_type,
            agent_id=agent_id,
            assignment_id=assignment_id,
            fingerprint=fingerprint_value,
            claimed_at_utc=now,
        )
        return OutcomeClaimResult(claimed=True)

    def outcome_type(self, outcome_id: UUID) -> AgentOutcomeType | None:
        stored = self._outcomes.get(outcome_id)
        return None if stored is None else stored.outcome_type

    def count_claimed_since(
        self, *, assignment_id: UUID, since: UtcDatetime, connection: Any = None
    ) -> int:
        del connection
        return sum(
            1
            for outcome in self._outcomes.values()
            if outcome.assignment_id == assignment_id and outcome.claimed_at_utc >= since
        )

    def append_event(
        self,
        *,
        outcome_id: UUID,
        event_type: AgentDecisionEventType,
        occurred_at_utc: UtcDatetime,
        reason_codes: tuple[str, ...] = (),
        detail: str | None = None,
        connection: Any = None,
    ) -> None:
        del connection
        stored = self._outcomes.get(outcome_id)
        if stored is None:
            raise KeyError(f"cannot append an event for an unclaimed outcome_id {outcome_id}")
        # Content-derived identity, same discipline as
        # `persistence/execution.py::event_id_for` -- re-appending the same
        # (outcome_id, event_type) pair (a resumed, interrupted attempt) is
        # a no-op, never a duplicate row.
        if any(event.event_type == event_type for event in stored.events):
            return
        stored.events.append(
            AgentDecisionEventRecord(
                outcome_id=outcome_id,
                event_type=event_type,
                occurred_at_utc=occurred_at_utc,
                reason_codes=reason_codes,
                detail=detail,
            )
        )

    def events_for(self, outcome_id: UUID) -> tuple[AgentDecisionEventRecord, ...]:
        stored = self._outcomes.get(outcome_id)
        return () if stored is None else tuple(stored.events)

    def settlement_for(
        self, outcome_id: UUID, *, connection: Any = None
    ) -> AgentDecisionEventRecord | None:
        del connection
        stored = self._outcomes.get(outcome_id)
        if stored is None:
            return None
        return next(
            (event for event in stored.events if event.event_type in _SETTLING_EVENT_TYPES), None
        )
