"""Fail-closed error hierarchy for the Agent Gateway (ADR-005 Step B).

Two different kinds of "no" live here, deliberately kept apart:

- **Exceptions** — the caller was never a valid interlocutor for this call:
  unknown/inactive agent, a failed credential check, or a structural
  content conflict (the same id reused with different content — an
  impersonation/tampering shape, not an ordinary refusal). Nothing is
  durably claimed before these fire, except `DecisionConflictError`, which
  fires *because* something was already durably claimed under this id.
- **`AgentRejectionReason`** — the caller authenticated fine and the
  content was well-formed, but this specific decision does not qualify
  (wrong assignment, expired context, over the rate limit, ...). These are
  never exceptions: `AgentGateway.submit_trade_proposal`/`submit_no_trade`
  always durably claim the attempt first (`review/EXTERNAL_AGENT_ARCHITECTURE_GUIDE.md`
  §9 "every proposal, NO_TRADE, rejection and timeout is auditable") and
  return a typed rejection reason rather than raising, so a legitimate
  refusal is a normal, auditable, machine-readable outcome
  (ADR-005 rule 6) — not a raised error a caller has to catch to observe.
"""

from __future__ import annotations

from enum import StrEnum


class AgentGatewayError(RuntimeError):
    """Base for every Gateway exception. A caller that only wants to know

    "was this call itself invalid" (as opposed to "was this decision
    refused") can catch this base class."""


class UnknownAgentError(AgentGatewayError):
    """No `AgentIdentity` has ever been registered for this `agent_id`."""


class AgentNotActiveError(AgentGatewayError):
    """The identity exists but its current status is not `ACTIVE`."""


class AuthenticationError(AgentGatewayError):
    """The presented credential does not match the agent's stored one.

    Never distinguishes "wrong secret" from "unknown agent" in its message
    — that distinction is available to the caller via the exception type,
    not via message content that could help an attacker enumerate valid
    `agent_id`s.
    """


class ImpersonationError(AgentGatewayError):
    """The proposal/decision names a different `agent_id` than the one that

    just authenticated — never treated as an ordinary scope rejection,
    because it means the authenticated credential is being used to submit
    content on behalf of an identity it does not own."""


class AssignmentConflictError(AgentGatewayError):
    """The same `assignment_id` was already registered with different content."""


class ContextConflictError(AgentGatewayError):
    """The same `context_id` was already issued with different content."""


class UnknownFeatureSnapshotError(AgentGatewayError):
    """A `DecisionContextBundle`'s `feature_snapshot_id` does not resolve to

    any durably-stored `AgentContextEvidence` (review 1.26 §5: "Gateway
    refuses an unknown/missing snapshot"). Raised by `issue_context_bundle`
    — a bundle is never issued on an unchecked claim that evidence exists."""


class DecisionConflictError(AgentGatewayError):
    """The same `proposal_id`/`decision_id` was already claimed with a

    different fingerprint (ADR-005 §7 "Idempotency" row) — a fail-closed
    conflict, never a silent overwrite of the first claim."""


class EventConflictError(AgentGatewayError):
    """The same lifecycle event (`outcome_id`, `event_type`) was already

    recorded with different content — self-review finding: `append_event`'s
    `(outcome_id, event_type)`-derived id makes a same-id-different-content
    collision structurally possible (a bug or a race resuming evaluation
    with different inputs), and unlike every other "register/issue/claim"
    method in this package, it had no fail-closed conflict check at all —
    `ON CONFLICT DO NOTHING` alone would have silently discarded the
    second, different write. Content is compared field for field
    (`reason_codes`, `detail`) against the already-durable row rather than
    a stored fingerprint, so no schema change was needed. Deliberately
    excludes `occurred_at_utc`: `RECEIVED` is re-appended on every
    resumed-but-unsettled retry (AG-008) with that call's own fresh
    wall-clock `now`, which is expected to differ from the original
    attempt's, not a conflict — the timestamp-inclusive version of this
    check was itself a self-review finding, fixed before ever committed."""


class MalformedInputError(AgentGatewayError):
    """The caller supplied a value that could not even be validated into

    its contract type — wraps the underlying `pydantic.ValidationError` so
    every Gateway entry point fails closed the same way regardless of which
    contract rejected the input."""


class AgentRejectionReason(StrEnum):
    """Machine-readable reasons a well-formed, authenticated submission is

    still refused. Recorded on the `REJECTED` audit event, never only as
    free text — the same "a hard rule is never reduced to prose alone"
    discipline ADR-005 rule 6 requires of internal Risk/Policy refusals,
    held to at this boundary too.
    """

    UNKNOWN_ASSIGNMENT = "UNKNOWN_ASSIGNMENT"
    ASSIGNMENT_NOT_OWNED = "ASSIGNMENT_NOT_OWNED"
    ASSIGNMENT_NOT_VALID_AT_TIME = "ASSIGNMENT_NOT_VALID_AT_TIME"
    STRATEGY_ARTIFACT_MISMATCH = "STRATEGY_ARTIFACT_MISMATCH"
    """`TradeProposal.strategy_artifact_hash` does not equal the

    `TradingAssignment.strategy_artifact_hash` the agent was actually
    assigned. `_build_trade_intent` already sources
    `TradeIntent.strategy_version` exclusively from the trusted
    `assignment.strategy_artifact_hash`, never from the proposal's own
    (unverified) claim -- so a mismatch here could never corrupt a
    constructed `TradeIntent`. It is still refused, explicitly, rather
    than silently ignored: an agent that ran artifact B, reported B's
    hash, but gets audited under artifact A's identity (because Crumblr
    only ever looked at the assignment and never checked the claim)
    leaves no trace that anything was wrong. Rejecting here makes the
    disagreement itself part of the durable audit trail."""
    RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"
    RISK_FRACTION_OUT_OF_BAND = "RISK_FRACTION_OUT_OF_BAND"
    MISSING_REQUIRED_EVIDENCE = "MISSING_REQUIRED_EVIDENCE"
    MISSING_REASON_CODES = "MISSING_REASON_CODES"
    PROPOSAL_EXPIRED = "PROPOSAL_EXPIRED"
    UNKNOWN_CONTEXT = "UNKNOWN_CONTEXT"
    CONTEXT_ASSIGNMENT_MISMATCH = "CONTEXT_ASSIGNMENT_MISMATCH"
    CONTEXT_EXPIRED = "CONTEXT_EXPIRED"
