"""Durable storage for observed `InstrumentSpec`s (build.md §7).

`instrument_specs` (the table) has existed since the M2 baseline migration
but never had a producer: `LiveReader` observes a real spec on every
reconnect and only ever holds it in memory, for `spec_changed` detection
(`review/DEVIATIONS.md` D-045). F-048's live decision pipeline needs a
durable spec to size against without itself talking to MT5 — "Decision
Orchestrator = decide," never a second reader of the terminal — which is
what this store exists to close.

Keyed by `spec_version` (a content hash over the spec's semantic fields,
`InstrumentSpec.spec_version`), the same identity discipline every other
content-addressed table in this schema uses: re-observing an unchanged spec
is a no-op, and a broker-side change becomes a new row automatically rather
than an overwrite silently losing the previous one.
"""

from __future__ import annotations

from sqlalchemy import Engine, asc, desc, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from crumblr.domain.models import InstrumentSpec
from crumblr.persistence.schema import instrument_specs


class InstrumentSpecStore:
    """Append-only, content-keyed storage for broker instrument specs."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def record(self, spec: InstrumentSpec) -> None:
        """Store `spec`, keyed by its own `spec_version`. Idempotent."""
        with self._engine.begin() as connection:
            connection.execute(
                pg_insert(instrument_specs)
                .values(
                    spec_version=spec.spec_version,
                    canonical_symbol=spec.canonical_symbol,
                    broker_symbol=spec.broker_symbol,
                    captured_at_utc=spec.captured_at_utc,
                    contract_size=spec.contract_size,
                    point=spec.point,
                    tick_size=spec.tick_size,
                    tick_value=spec.tick_value,
                    volume_min=spec.volume_min,
                    volume_max=spec.volume_max,
                    volume_step=spec.volume_step,
                    digits=spec.digits,
                    payload=spec.model_dump(mode="json"),
                )
                .on_conflict_do_nothing(index_elements=["spec_version"])
            )

    def latest(self, *, canonical_symbol: str) -> InstrumentSpec | None:
        """The most recently observed spec for `canonical_symbol`, or `None`.

        Ordered by `captured_at_utc` (when the observation was taken), not
        `first_seen_utc` (when this row happened to be inserted) — a spec
        re-observed unchanged keeps its original row, so `first_seen_utc`
        would not track the most recent confirmation of it.
        """
        statement = (
            select(instrument_specs.c.payload)
            .where(instrument_specs.c.canonical_symbol == canonical_symbol)
            .order_by(desc(instrument_specs.c.captured_at_utc))
            .limit(1)
        )
        with self._engine.connect() as connection:
            row = connection.execute(statement).scalar_one_or_none()
        return InstrumentSpec.model_validate(row) if row is not None else None

    def earliest(self, *, canonical_symbol: str) -> InstrumentSpec | None:
        """The first spec ever durably observed for `canonical_symbol`, or `None`.

        The baseline reconciliation (F-053) compares `latest()` against: this
        platform never hard-codes a contract specification (O-001 — digits,
        volume steps, stops/freeze levels and filling modes are discovered,
        never assumed), so there is no config-declared "expected" spec to
        compare against the way `ExpectedState.flat()` compares account
        fields against `AccountGuardConfig`. The first confirmed observation
        is the only value this platform has ever asserted about the
        contract, so a later observation disagreeing with it is exactly the
        broker-side change reconciliation must catch.
        """
        statement = (
            select(instrument_specs.c.payload)
            .where(instrument_specs.c.canonical_symbol == canonical_symbol)
            .order_by(asc(instrument_specs.c.captured_at_utc))
            .limit(1)
        )
        with self._engine.connect() as connection:
            row = connection.execute(statement).scalar_one_or_none()
        return InstrumentSpec.model_validate(row) if row is not None else None
