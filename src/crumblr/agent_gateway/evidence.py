"""Platform-owned feature evidence for external-agent decision context

(review 1.26 §5 — AG-006's resolution).

Not a universal cross-strategy feature engine. Review 1.26 §5 explicitly
rejected that path: "AG-006 is not a reason to first invent one universal
`compute_features()` that normalizes `baseline_v1`, `ict_v1`, and every
future external strategy." `AgentContextEvidence` is instead its own,
deliberately narrow, honestly-named evidence shape
(`feature_set_version = "agent_context_v1"`) — it structurally satisfies
`trading_agent.base.FeatureEvidence` (a `runtime_checkable Protocol`, so no
import of or change to that Dev-1-owned module is needed) without
pretending a `baseline_v1`/`ict_v1` technical-analysis computation ever
ran. `regime` is always `Regime.UNKNOWN`: honest, not a guess — the
Supervisor is already built to treat `UNKNOWN` with suspicion
(`domain/enums.py::Regime`'s own docstring: "exists so the supervisor can
veto on it"), which is exactly the right posture for "no TA regime was
computed here."

Reuses `persistence/features.py::FeatureSnapshotStore` unmodified — review
1.26 §5: "the existing feature persistence layer is already intentionally
generic ... that is enough for the product direction."

Every field the review's requirements name is satisfied by construction,
not by convention:

- created by Crumblr, never by the external agent — nothing in this
  module accepts agent input; `AgentGateway.publish_context` is the only
  caller (`gateway.py`).
- exists durably before the bundle is issued —
  `AgentGateway.publish_context` records it via `FeatureSnapshotStore`
  *before* constructing the `DecisionContextBundle` that references it,
  and `issue_context_bundle` independently refuses a bundle whose
  `feature_snapshot_id` does not already resolve to a stored row.
- immutable / content-addressed — `feature_snapshot_id` is a `uuid5` of
  the symbol and the computation instant, mirroring
  `trading_agent/features.py::compute_features`'s own derivation, so
  republishing the same observation twice collapses to one snapshot.
- no placeholder UUIDs, no post-hoc fabrication — there is no code path
  in this module that constructs `AgentContextEvidence` from anything
  other than real, caller-supplied observation data.
"""

from __future__ import annotations

from uuid import NAMESPACE_DNS, UUID, uuid5

from crumblr.domain.enums import DataQuality, Regime, SessionState
from crumblr.domain.hashing import fingerprint
from crumblr.domain.models import Contract, Symbol, VersionTag
from crumblr.domain.timeutils import UtcDatetime

FEATURE_SET_VERSION = "agent_context_v1"

_NAMESPACE = "crumblr:agent_context_evidence"


class AgentContextEvidence(Contract):
    """What Crumblr actually observed and is telling an external agent

    about, sealed durably before the `DecisionContextBundle` referencing it
    is ever issued. Structurally satisfies `trading_agent.base.FeatureEvidence`
    — `feature_snapshot_id`, `feature_set_version`, `feature_values_hash`,
    `regime`, `symbol`, `model_dump` (inherited from `Contract`) are exactly
    what the Protocol requires; `computed_at_utc` is additionally required
    by `FeatureSnapshotStore.record()` itself, the same way every existing
    concrete `FeatureEvidence` implementation already carries it without it
    being part of the Protocol.
    """

    feature_snapshot_id: UUID
    feature_set_version: VersionTag = FEATURE_SET_VERSION
    symbol: Symbol
    computed_at_utc: UtcDatetime
    market_snapshot_id: UUID
    instrument_spec_version: str
    session_state: SessionState
    data_quality: DataQuality
    regime: Regime = Regime.UNKNOWN
    """Always `UNKNOWN` — no technical-analysis regime classification is

    computed for this evidence shape; guessing one would be exactly the
    "pretend a baseline/ICT calculation occurred" the review forbids."""

    @property
    def feature_values_hash(self) -> str:
        """Plain `@property`, not a `computed_field` — matches

        `FeatureSnapshot`/`IctFeatureSnapshot`'s own pattern
        (`trading_agent/features.py`): computed on demand, not part of the
        stored payload."""
        return fingerprint(
            {
                "feature_set_version": self.feature_set_version,
                "symbol": self.symbol,
                "market_snapshot_id": self.market_snapshot_id,
                "instrument_spec_version": self.instrument_spec_version,
                "session_state": self.session_state,
                "data_quality": self.data_quality,
                "regime": self.regime,
            }
        )


def build_agent_context_evidence(
    *,
    symbol: str,
    computed_at_utc: UtcDatetime,
    market_snapshot_id: UUID,
    instrument_spec_version: str,
    session_state: SessionState,
    data_quality: DataQuality,
) -> AgentContextEvidence:
    """Constructs evidence with a content-derived `feature_snapshot_id` —

    the same `(symbol, computed_at_utc)` pair republished (e.g. two
    proposals against the same closed decision window) collapses to one
    snapshot rather than duplicating, the same identity discipline
    `trading_agent/features.py::compute_features` already uses.
    """
    feature_snapshot_id = uuid5(
        NAMESPACE_DNS, f"{_NAMESPACE}:{symbol}:{computed_at_utc.isoformat()}"
    )
    return AgentContextEvidence(
        feature_snapshot_id=feature_snapshot_id,
        symbol=symbol,
        computed_at_utc=computed_at_utc,
        market_snapshot_id=market_snapshot_id,
        instrument_spec_version=instrument_spec_version,
        session_state=session_state,
        data_quality=data_quality,
    )
