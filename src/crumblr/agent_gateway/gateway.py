"""The Agent Gateway (ADR-005 Step B) — external Trader ingestion, in shadow.

Owns exactly the responsibilities `CRUMBLR_DEV2_AGENT_INTEGRATION_INSTRUCTIONS_V3.md`
lists for Step B: service identity/authentication, assignment
authorization, context-hash binding, expiry, proposal-rate limits,
idempotency, conflicting-retry detection, explicit `NO_TRADE` handling and
fail-closed error handling. **Does not yet map an accepted proposal into a
platform `TradeIntent`** — see `review/AGENT_FEEDBACK.md` AG-006:
`TradeIntent` requires a `feature_snapshot_id`, and deciding what that
means for an externally-originated decision is a shared-contract question
this pass deliberately did not resolve alone (instructions §4 — stop and
raise, don't force it). What this module proves instead is everything
ADR-005 §8's "first proof target" actually requires: identity, assignment,
context and outcome durably recorded in SHADOW, zero broker execution,
zero calls into `crumblr.application.execution`/`mt5_gateway`/the platform
database write path other than this package's own tables.

Every `submit_*` call durably claims the attempt (`AgentDecisionOutcomeStore`)
*before* running any authorization check, so a legitimate refusal is still
an auditable row, not a raised exception a caller has to catch to observe
(guide §9's "every proposal, NO_TRADE, rejection and timeout is auditable").
Only a fundamentally invalid call — unknown/inactive agent, bad credential,
impersonation, or a structural content conflict — raises; see
`agent_gateway/errors.py`'s module docstring for the exact split.

The whole claim→count→evaluate→settle sequence for one `assignment_id` runs
inside a single locked transaction (`AgentDecisionOutcomeStore.transaction()`/
`.lock_assignment()`), fixing a real concurrency bug a self-review caught:
reading the proposal-rate-limit count and then claiming in two separate
transactions let two concurrent proposals for the same assignment both
observe a below-limit count and both get accepted. The same lock also
makes an interrupted attempt (claimed, but the process crashed before a
verdict was recorded) safely resumable: a retry that finds a claim with no
settling event re-runs evaluation with fresh inputs rather than defaulting
to acceptance — see `submit_trade_proposal`/`submit_no_trade`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import NAMESPACE_DNS, UUID, uuid4, uuid5

from crumblr.agent_gateway.auth import hash_credential, verify_credential
from crumblr.agent_gateway.contracts import (
    AgentIdentity,
    AgentStatus,
    DecisionContextBundle,
    NoTradeDecision,
    PolicyHints,
    TradeProposal,
    TradingAssignment,
)
from crumblr.agent_gateway.errors import (
    AgentNotActiveError,
    AgentRejectionReason,
    AuthenticationError,
    ImpersonationError,
    UnknownAgentError,
    UnknownFeatureSnapshotError,
)
from crumblr.agent_gateway.events import AgentDecisionEventType, AgentOutcomeType
from crumblr.agent_gateway.evidence import build_agent_context_evidence
from crumblr.agent_gateway.stores import (
    AgentCredentialStore,
    AgentDecisionEventRecord,
    AgentDecisionOutcomeStore,
    AgentIdentityStore,
    DecisionContextBundleStore,
    FeatureEvidenceStore,
    TradingAssignmentStore,
)
from crumblr.domain.enums import DataQuality, SessionState
from crumblr.domain.models import TradeIntent
from crumblr.domain.timeutils import UtcDatetime

_RATE_LIMIT_WINDOW = timedelta(hours=1)
_DEFAULT_CONTEXT_VALIDITY = timedelta(minutes=5)
_INTENT_NAMESPACE = "crumblr:agent_trade_intent"
_EXTERNAL_AGENT_STRATEGY_ID = "external_agent"
"""`TradeIntent.strategy_id` for every intent this Gateway constructs --

never `baseline_v1`/`ict_v1`, so a reader can always tell an agent-driven
intent apart from an internal strategy's at a glance. The specific agent's
own strategy artifact is still fully traceable via `strategy_version`
(`TradingAssignment.strategy_artifact_hash`)."""


@dataclass(frozen=True)
class AgentDecisionOutcomeResult:
    """The result of one `submit_trade_proposal`/`submit_no_trade` call.

    `accepted=False` is a normal, expected, fully-audited result — not an
    error condition the caller must treat specially. `reason` is populated
    exactly when `accepted` is `False`.
    `trade_intent` is populated only when `accepted` is `True` **and**
    `outcome_type` is `TRADE_PROPOSAL` — never for `NO_TRADE` (there is
    nothing to map) and never for a rejection (review 1.26 §7 item 2: the
    platform-owned `TradeIntent` this Gateway constructs from an accepted
    proposal). Deterministic — the same accepted proposal always maps to
    the same `TradeIntent` (same `intent_id`, same every field), so a
    replayed identical retry reconstructs byte-identical content rather
    than needing its own separate durable storage; see
    `AgentGateway._build_trade_intent`.
    """

    outcome_id: UUID
    outcome_type: AgentOutcomeType
    accepted: bool
    reason: AgentRejectionReason | None = None
    trade_intent: TradeIntent | None = None


class AgentGateway:
    def __init__(
        self,
        *,
        identities: AgentIdentityStore,
        credentials: AgentCredentialStore,
        assignments: TradingAssignmentStore,
        contexts: DecisionContextBundleStore,
        outcomes: AgentDecisionOutcomeStore,
        feature_evidence: FeatureEvidenceStore,
    ) -> None:
        self._identities = identities
        self._credentials = credentials
        self._assignments = assignments
        self._contexts = contexts
        self._outcomes = outcomes
        self._feature_evidence = feature_evidence

    # ----------------------------------------------------------------- #
    # Administrative — Crumblr-internal only, never reachable by an agent
    # ----------------------------------------------------------------- #

    def register_identity(self, identity: AgentIdentity, *, credential_secret: str) -> None:
        """Registers a new identity, or a status transition for an existing

        one (a fresh `AgentIdentity` snapshot with the same `agent_id` and a
        new `status`). Always re-issues the credential — there is no
        "update status only" path, so a caller cannot accidentally leave a
        stale credential attached to a freshly-reactivated identity."""
        self._identities.register(identity)
        self._credentials.set_credential(
            agent_id=identity.agent_id, credential_hash=hash_credential(credential_secret)
        )

    def issue_assignment(self, assignment: TradingAssignment) -> None:
        self._assignments.register(assignment)

    def issue_context_bundle(self, bundle: DecisionContextBundle) -> DecisionContextBundle:
        """Fails closed if `bundle.feature_snapshot_id` does not resolve to

        a durably-stored `AgentContextEvidence` (review 1.26 §5: "Gateway
        refuses an unknown/missing snapshot") — never issues a bundle on an
        unchecked claim that evidence exists, whether this is called via
        `publish_context` below or directly with a hand-built bundle."""
        if self._feature_evidence.get_payload(bundle.feature_snapshot_id) is None:
            raise UnknownFeatureSnapshotError(
                f"feature_snapshot_id {bundle.feature_snapshot_id} does not resolve to any "
                "durably-stored AgentContextEvidence"
            )
        self._contexts.issue(bundle)
        return bundle

    def publish_context(
        self,
        *,
        assignment_id: UUID,
        symbol: str,
        market_snapshot_id: UUID,
        instrument_spec_version: str,
        portfolio_summary_hash: str,
        session_state: SessionState,
        data_quality: DataQuality,
        now: UtcDatetime,
        policy_hints: PolicyHints | None = None,
        news_snapshot_id: UUID | None = None,
        validity: timedelta = _DEFAULT_CONTEXT_VALIDITY,
    ) -> DecisionContextBundle:
        """The "platform context publication" entry point review 1.26 §5's

        flow starts with: builds and durably records the
        `AgentContextEvidence` this observation is based on *before*
        constructing the bundle that references it, then issues the bundle
        through the same fail-closed `issue_context_bundle` path a
        hand-built bundle goes through. This is the normal way a real
        caller issues a context bundle; `issue_context_bundle` stays public
        for tests and for a caller that already has its own evidence.
        """
        evidence = build_agent_context_evidence(
            symbol=symbol,
            computed_at_utc=now,
            market_snapshot_id=market_snapshot_id,
            instrument_spec_version=instrument_spec_version,
            session_state=session_state,
            data_quality=data_quality,
        )
        self._feature_evidence.record(evidence)
        bundle = DecisionContextBundle(
            context_id=uuid4(),
            assignment_id=assignment_id,
            market_snapshot_id=market_snapshot_id,
            instrument_spec_version=instrument_spec_version,
            portfolio_summary_hash=portfolio_summary_hash,
            session_state=session_state,
            data_quality=data_quality,
            feature_snapshot_id=evidence.feature_snapshot_id,
            policy_hints=policy_hints,
            news_snapshot_id=news_snapshot_id,
            issued_at_utc=now,
            expires_at_utc=now + validity,
        )
        return self.issue_context_bundle(bundle)

    # ----------------------------------------------------------------- #
    # Authentication
    # ----------------------------------------------------------------- #

    def authenticate(self, *, agent_id: UUID, credential_secret: str) -> AgentIdentity:
        """Fails closed at every step — see `threat_model` §4.1: an unknown

        agent, a wrong secret and a suspended/retired agent must all be
        refused, and none of the three may be inferred as the others'
        absence."""
        identity = self._identities.current(agent_id)
        if identity is None:
            raise UnknownAgentError(f"no AgentIdentity registered for {agent_id}")

        stored_hash = self._credentials.current_hash(agent_id)
        if stored_hash is None or not verify_credential(
            secret=credential_secret, stored_hash=stored_hash
        ):
            raise AuthenticationError(f"credential check failed for agent {agent_id}")

        if identity.status is not AgentStatus.ACTIVE:
            raise AgentNotActiveError(f"agent {agent_id} is {identity.status}, not ACTIVE")

        return identity

    # ----------------------------------------------------------------- #
    # TradeProposal
    # ----------------------------------------------------------------- #

    def submit_trade_proposal(
        self,
        *,
        agent_id: UUID,
        credential_secret: str,
        proposal: TradeProposal,
        now: UtcDatetime,
    ) -> AgentDecisionOutcomeResult:
        identity = self.authenticate(agent_id=agent_id, credential_secret=credential_secret)
        if proposal.agent_id != identity.agent_id:
            raise ImpersonationError(
                f"proposal.agent_id {proposal.agent_id} does not match the authenticated "
                f"agent {identity.agent_id}"
            )

        with self._outcomes.transaction() as connection:
            self._outcomes.lock_assignment(proposal.assignment_id, connection=connection)

            claim = self._outcomes.claim_trade_proposal(proposal, now=now, connection=connection)
            if not claim.claimed:
                settlement = self._outcomes.settlement_for(
                    proposal.proposal_id, connection=connection
                )
                if settlement is not None:
                    return self._result_from_settlement(
                        settlement, AgentOutcomeType.TRADE_PROPOSAL, proposal=proposal
                    )
                # Claimed by an earlier attempt that never recorded a verdict
                # (crashed/interrupted) -- fall through and evaluate fresh,
                # rather than assuming that attempt would have accepted it.

            self._outcomes.append_event(
                outcome_id=proposal.proposal_id,
                event_type=AgentDecisionEventType.RECEIVED,
                occurred_at_utc=now,
                connection=connection,
            )

            assignment = self._assignments.current(proposal.assignment_id)
            count_including_self = (
                None
                if assignment is None
                else self._outcomes.count_claimed_since(
                    assignment_id=assignment.assignment_id,
                    since=now - _RATE_LIMIT_WINDOW,
                    connection=connection,
                )
            )

            reason = self._evaluate_proposal(
                identity=identity,
                proposal=proposal,
                assignment=assignment,
                count_including_self=count_including_self,
                now=now,
            )
            trade_intent = None
            if reason is None:
                assert assignment is not None  # narrowed: an evaluation reason of None implies this
                trade_intent = self._build_trade_intent(proposal=proposal, assignment=assignment)
            return self._settle(
                proposal.proposal_id,
                AgentOutcomeType.TRADE_PROPOSAL,
                reason,
                now=now,
                connection=connection,
                trade_intent=trade_intent,
            )

    def _build_trade_intent(
        self, *, proposal: TradeProposal, assignment: TradingAssignment
    ) -> TradeIntent:
        """Maps an accepted `TradeProposal` into a platform-owned `TradeIntent`

        (review 1.26 §7 item 2). Deterministic and independent of the
        current call's `now` — the same proposal always maps to the same
        intent (same `intent_id`, same every field), so a replayed retry
        reconstructs byte-identical content rather than needing its own
        separate durable storage; see `_result_from_settlement`. Depends
        only on `proposal` (immutable, content-addressed once claimed) and
        `assignment` (immutable once registered) — deliberately **not**
        `AgentIdentity`, even though `model_version` looked tempting to
        carry through: identity is a mutable, append-only-latest-wins
        snapshot (`register_identity` can change it at any time), so
        sourcing anything from a fresh `current()` lookup at replay time
        could make a "byte-identical reconstruction" silently drift if the
        agent's registration changed between the original acceptance and a
        later retry — caught by a self-review before it shipped.
        `feature_snapshot_id` is `bundle.feature_snapshot_id` directly,
        never fabricated (AG-006's own requirement, carried forward here)
        — the bundle is guaranteed to exist and resolve because
        `_context_reason` already validated it during evaluation, and
        `strategy_id` is a fixed sentinel (never `baseline_v1`/`ict_v1`)
        so an agent-driven intent is never mistaken for an internal
        strategy's.
        """
        bundle = self._contexts.by_hash(proposal.context_hash)
        assert bundle is not None  # already validated by _context_reason
        intent_id = uuid5(NAMESPACE_DNS, f"{_INTENT_NAMESPACE}:{proposal.proposal_id}")
        return TradeIntent(
            intent_id=intent_id,
            strategy_id=_EXTERNAL_AGENT_STRATEGY_ID,
            strategy_version=assignment.strategy_artifact_hash,
            model_version=None,
            symbol=assignment.canonical_symbol,
            side=proposal.side,
            created_at_utc=proposal.submitted_at_utc,
            expires_at_utc=proposal.expires_at_utc,
            entry_type=proposal.entry_type,
            reference_price=proposal.reference_price,
            stop_loss_price=proposal.stop_loss_price,
            take_profit_price=proposal.take_profit_price,
            confidence=proposal.confidence,
            reason_codes=proposal.reason_codes,
            requested_risk_fraction=proposal.requested_risk_fraction,
            feature_snapshot_id=bundle.feature_snapshot_id,
        )

    def _evaluate_proposal(
        self,
        *,
        identity: AgentIdentity,
        proposal: TradeProposal,
        assignment: TradingAssignment | None,
        count_including_self: int | None,
        now: UtcDatetime,
    ) -> AgentRejectionReason | None:
        reason = self._assignment_scope_reason(identity=identity, assignment=assignment, now=now)
        if reason is not None:
            return reason
        assert assignment is not None  # narrowed: _assignment_scope_reason returned None

        if proposal.strategy_artifact_hash != assignment.strategy_artifact_hash:
            # Hard StrategyArtifact binding. `_build_trade_intent` already
            # sources `TradeIntent.strategy_version` from
            # `assignment.strategy_artifact_hash` alone, never from this
            # claim -- so this check cannot change what a constructed
            # `TradeIntent` says. It exists so a disagreement is refused
            # and durably audited rather than silently discarded: without
            # it, an agent could run artifact B, report B's hash here, and
            # be audited entirely as artifact A after the fact, since
            # nothing would ever have recorded that its own claim
            # disagreed with what was actually assigned.
            return AgentRejectionReason.STRATEGY_ARTIFACT_MISMATCH

        if (
            count_including_self is not None
            and count_including_self > assignment.max_proposals_per_hour
        ):
            return AgentRejectionReason.RATE_LIMIT_EXCEEDED
        if not (
            assignment.allowed_risk_fraction_min
            <= proposal.requested_risk_fraction
            <= assignment.allowed_risk_fraction_max
        ):
            return AgentRejectionReason.RISK_FRACTION_OUT_OF_BAND
        if not proposal.reason_codes:
            # TradeProposal itself places no non-empty constraint on
            # reason_codes, but the platform-owned TradeIntent this
            # Gateway maps an accepted proposal into does
            # (`domain/models.py::TradeIntent._check_directional_requirements`)
            # -- caught here, at evaluation time, so a missing reason is an
            # ordinary auditable rejection rather than a construction
            # failure deep inside `_build_trade_intent`.
            return AgentRejectionReason.MISSING_REASON_CODES
        if assignment.required_evidence_fields and not proposal.evidence_refs:
            # Conservative, not exhaustive: proves *some* evidence was cited
            # when the assignment demands it. Does not verify the cited
            # evidence actually covers each named field -- that needs
            # evidence-content inspection, out of scope until an ingestion
            # path exists (AG-005, deferred to Step D).
            return AgentRejectionReason.MISSING_REQUIRED_EVIDENCE
        if now >= proposal.expires_at_utc:
            return AgentRejectionReason.PROPOSAL_EXPIRED

        return self._context_reason(
            assignment=assignment, context_hash=proposal.context_hash, now=now
        )

    # ----------------------------------------------------------------- #
    # NoTradeDecision
    # ----------------------------------------------------------------- #

    def submit_no_trade(
        self,
        *,
        agent_id: UUID,
        credential_secret: str,
        decision: NoTradeDecision,
        now: UtcDatetime,
    ) -> AgentDecisionOutcomeResult:
        identity = self.authenticate(agent_id=agent_id, credential_secret=credential_secret)
        if decision.agent_id != identity.agent_id:
            raise ImpersonationError(
                f"decision.agent_id {decision.agent_id} does not match the authenticated "
                f"agent {identity.agent_id}"
            )

        with self._outcomes.transaction() as connection:
            self._outcomes.lock_assignment(decision.assignment_id, connection=connection)

            claim = self._outcomes.claim_no_trade(decision, now=now, connection=connection)
            if not claim.claimed:
                settlement = self._outcomes.settlement_for(
                    decision.decision_id, connection=connection
                )
                if settlement is not None:
                    return self._result_from_settlement(settlement, AgentOutcomeType.NO_TRADE)

            self._outcomes.append_event(
                outcome_id=decision.decision_id,
                event_type=AgentDecisionEventType.RECEIVED,
                occurred_at_utc=now,
                connection=connection,
            )

            assignment = self._assignments.current(decision.assignment_id)
            reason = self._evaluate_no_trade(
                identity=identity, decision=decision, assignment=assignment, now=now
            )
            return self._settle(
                decision.decision_id,
                AgentOutcomeType.NO_TRADE,
                reason,
                now=now,
                connection=connection,
            )

    def _evaluate_no_trade(
        self,
        *,
        identity: AgentIdentity,
        decision: NoTradeDecision,
        assignment: TradingAssignment | None,
        now: UtcDatetime,
    ) -> AgentRejectionReason | None:
        reason = self._assignment_scope_reason(identity=identity, assignment=assignment, now=now)
        if reason is not None:
            return reason
        assert assignment is not None  # narrowed: _assignment_scope_reason returned None

        return self._context_reason(
            assignment=assignment, context_hash=decision.context_hash, now=now
        )

    # ----------------------------------------------------------------- #
    # Shared checks (kept as single implementations -- a self-review found
    # these duplicated verbatim between the two evaluate methods, risking a
    # future fix landing in one copy and not the other)
    # ----------------------------------------------------------------- #

    def _assignment_scope_reason(
        self, *, identity: AgentIdentity, assignment: TradingAssignment | None, now: UtcDatetime
    ) -> AgentRejectionReason | None:
        if assignment is None:
            return AgentRejectionReason.UNKNOWN_ASSIGNMENT
        if assignment.allowed_agent_id != identity.agent_id:
            return AgentRejectionReason.ASSIGNMENT_NOT_OWNED
        if not (assignment.valid_from_utc <= now <= assignment.valid_until_utc):
            return AgentRejectionReason.ASSIGNMENT_NOT_VALID_AT_TIME
        return None

    def _context_reason(
        self, *, assignment: TradingAssignment, context_hash: str, now: UtcDatetime
    ) -> AgentRejectionReason | None:
        bundle = self._contexts.by_hash(context_hash)
        if bundle is None:
            return AgentRejectionReason.UNKNOWN_CONTEXT
        if bundle.assignment_id != assignment.assignment_id:
            return AgentRejectionReason.CONTEXT_ASSIGNMENT_MISMATCH
        if now >= bundle.expires_at_utc:
            return AgentRejectionReason.CONTEXT_EXPIRED
        return None

    # ----------------------------------------------------------------- #
    # Shared claim settlement
    # ----------------------------------------------------------------- #

    def _settle(
        self,
        outcome_id: UUID,
        outcome_type: AgentOutcomeType,
        reason: AgentRejectionReason | None,
        *,
        now: UtcDatetime,
        connection: Any = None,
        trade_intent: TradeIntent | None = None,
    ) -> AgentDecisionOutcomeResult:
        if reason is not None:
            self._outcomes.append_event(
                outcome_id=outcome_id,
                event_type=AgentDecisionEventType.REJECTED,
                occurred_at_utc=now,
                reason_codes=(reason,),
                connection=connection,
            )
            return AgentDecisionOutcomeResult(
                outcome_id=outcome_id, outcome_type=outcome_type, accepted=False, reason=reason
            )
        self._outcomes.append_event(
            outcome_id=outcome_id,
            event_type=AgentDecisionEventType.ACCEPTED,
            occurred_at_utc=now,
            connection=connection,
        )
        return AgentDecisionOutcomeResult(
            outcome_id=outcome_id,
            outcome_type=outcome_type,
            accepted=True,
            trade_intent=trade_intent,
        )

    def _result_from_settlement(
        self,
        settlement: AgentDecisionEventRecord,
        outcome_type: AgentOutcomeType,
        *,
        proposal: TradeProposal | None = None,
    ) -> AgentDecisionOutcomeResult:
        """Replays an already-recorded verdict for an identical retry --

        never re-runs evaluation, so a retry can't observe a different
        result than the original call did (e.g. because the rate-limit
        window has since moved). Only reachable when `settlement_for`
        actually found an `ACCEPTED`/`REJECTED` event; an interrupted claim
        with no settlement is handled by the caller resuming evaluation
        instead of calling this.

        `proposal` is required exactly when `outcome_type` is
        `TRADE_PROPOSAL` and the settlement was `ACCEPTED` — reconstructs
        the same deterministic `TradeIntent` `_build_trade_intent` would
        have produced originally, rather than storing it separately.
        Deliberately does not take an `AgentIdentity` here (see
        `_build_trade_intent`'s own docstring) — a fresh identity lookup at
        replay time could make the reconstruction drift from the original."""
        if settlement.event_type is AgentDecisionEventType.REJECTED:
            reason = (
                AgentRejectionReason(settlement.reason_codes[0])
                if settlement.reason_codes
                else None
            )
            return AgentDecisionOutcomeResult(
                outcome_id=settlement.outcome_id,
                outcome_type=outcome_type,
                accepted=False,
                reason=reason,
            )
        trade_intent = None
        if outcome_type is AgentOutcomeType.TRADE_PROPOSAL:
            assert proposal is not None
            assignment = self._assignments.current(proposal.assignment_id)
            assert assignment is not None  # existed when this was first accepted
            trade_intent = self._build_trade_intent(proposal=proposal, assignment=assignment)
        return AgentDecisionOutcomeResult(
            outcome_id=settlement.outcome_id,
            outcome_type=outcome_type,
            accepted=True,
            trade_intent=trade_intent,
        )
