"""Durable storage for the feature *values* a decision was made from (D-031).

A `DecisionCapsule` has always carried `feature_set_version` and
`feature_values_hash` — proof that a later recomputation matches, never a
way to see what the strategy actually saw. Review 1.17 §9 / review 1.18 §8:
that gap must close before a live-shadow run counts as promotion-quality
evidence, and the first F-051 wiring run is explicitly allowed to happen
before it does — this is what closes it.

Content-keyed by `feature_snapshot_id` (both `compute_features` and the ICT
model's own snapshot builder derive it as a `uuid5` of the symbol and the
computation instant), the same identity discipline `InstrumentSpecStore`
uses: recomputing the same window's features twice — a replay rerun, or a
live restart re-deciding an already-decided window — collapses to one row.

Two different concrete shapes exist (`FeatureSnapshot` for `baseline_v1`,
`IctFeatureSnapshot` for `ict_v1`), distinguished by `feature_set_version`.
This store does not decode a payload back into either — nothing in this
codebase reconstructs a typed feature snapshot from storage today, the same
as `decision_capsules`' payload is queried by column but not partially
reassembled. `get_payload` returns the raw JSON for a human or a future
consumer to interpret against whichever `feature_set_version` it names.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import Engine, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from crumblr.persistence.schema import feature_snapshots
from crumblr.trading_agent.base import FeatureEvidence


class FeatureSnapshotStore:
    """Append-only, content-keyed storage for `FeatureEvidence` payloads."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def record(self, features: FeatureEvidence) -> None:
        """Store `features`, keyed by its own `feature_snapshot_id`. Idempotent."""
        payload = features.model_dump(mode="json")
        # `computed_at_utc` is on every concrete `FeatureEvidence`
        # implementation today (`FeatureSnapshot`, `IctFeatureSnapshot`) but
        # is not part of the Protocol itself — the Protocol only promises
        # what a decision needs to identify and hash its evidence by, not
        # every field every implementation happens to carry. Read it from
        # the dump rather than widening the Protocol for a field only this
        # store needs.
        with self._engine.begin() as connection:
            connection.execute(
                pg_insert(feature_snapshots)
                .values(
                    feature_snapshot_id=features.feature_snapshot_id,
                    feature_set_version=features.feature_set_version,
                    canonical_symbol=features.symbol,
                    computed_at_utc=payload["computed_at_utc"],
                    payload=payload,
                )
                .on_conflict_do_nothing(index_elements=["feature_snapshot_id"])
            )

    def get_payload(self, feature_snapshot_id: UUID) -> dict[str, Any] | None:
        """The raw stored JSON for one snapshot, or `None` if never recorded."""
        statement = select(feature_snapshots.c.payload).where(
            feature_snapshots.c.feature_snapshot_id == feature_snapshot_id
        )
        with self._engine.connect() as connection:
            row = connection.execute(statement).scalar_one_or_none()
        return dict(row) if row is not None else None
