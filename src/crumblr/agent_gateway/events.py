"""Local event/outcome vocabulary for the Agent Gateway audit trail.

Deliberately not added to `crumblr.domain.enums` — that module is shared
territory (`CRUMBLR_DEV2_AGENT_INTEGRATION_INSTRUCTIONS_V2.md` §5) and
nothing here needs to be visible to, or agreed with, the Core/Execution
track yet. If a later step maps an accepted proposal into a platform
`TradeIntent`, the *platform* event types that decision triggers
(`ExecutionEventType` etc.) remain Dev-1's, unaffected by this module.
"""

from __future__ import annotations

from enum import StrEnum


class AgentOutcomeType(StrEnum):
    """What kind of decision a claimed `agent_decision_outcomes` row holds."""

    TRADE_PROPOSAL = "TRADE_PROPOSAL"
    NO_TRADE = "NO_TRADE"


class AgentDecisionEventType(StrEnum):
    """Append-only lifecycle steps for one claimed outcome — mirrors the

    shape of `crumblr.domain.enums.ExecutionEventType`'s append-only
    lifecycle log, at this track's own boundary."""

    RECEIVED = "RECEIVED"
    """The outcome was durably claimed — this and only this happens before

    any authorization/validation checks are recorded, so "an agent sent
    something" is never lost even if it is then rejected."""

    ACCEPTED = "ACCEPTED"
    """Every Gateway check passed. Does not imply a `TradeIntent` was

    constructed — that mapping is a later step (`review/AGENT_STATUS.md`
    AG-006), not part of Step B's ingestion boundary."""

    REJECTED = "REJECTED"
    """A Gateway check failed after the claim succeeded — e.g. an

    assignment scope or context-freshness check. The claim itself is not
    undone; the row exists as a durable record of what was received and
    why it was refused."""
