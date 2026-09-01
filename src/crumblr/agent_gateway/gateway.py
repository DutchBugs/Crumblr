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
from uuid import UUID, uuid4

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
from crumblr.domain.timeutils import UtcDatetime

_RATE_LIMIT_WINDOW = timedelta(hours=1)
_DEFAULT_CONTEXT_VALIDITY = timedelta(minutes=5)


@dataclass(frozen=True)
class AgentDecisionOutcomeResult:
    """The result of one `submit_trade_proposal`/`submit_no_trade` call.

    `accepted=False` is a normal, expected, fully-audited result — not an
    error condition the caller must treat specially. `reason` is populated
    exactly when `accepted` is `False`.
    """

    outcome_id: UUID
    outcome_type: AgentOutcomeType
    accepted: bool
    reason: AgentRejectionReason | None = None


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
                    return self._result_from_settlement(settlement, AgentOutcomeType.TRADE_PROPOSAL)
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
            return self._settle(
                proposal.proposal_id,
                AgentOutcomeType.TRADE_PROPOSAL,
                reason,
                now=now,
                connection=connection,
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
            outcome_id=outcome_id, outcome_type=outcome_type, accepted=True
        )

    def _result_from_settlement(
        self, settlement: AgentDecisionEventRecord, outcome_type: AgentOutcomeType
    ) -> AgentDecisionOutcomeResult:
        """Replays an already-recorded verdict for an identical retry --

        never re-runs evaluation, so a retry can't observe a different
        result than the original call did (e.g. because the rate-limit
        window has since moved). Only reachable when `settlement_for`
        actually found an `ACCEPTED`/`REJECTED` event; an interrupted claim
        with no settlement is handled by the caller resuming evaluation
        instead of calling this."""
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
        return AgentDecisionOutcomeResult(
            outcome_id=settlement.outcome_id, outcome_type=outcome_type, accepted=True
        )
