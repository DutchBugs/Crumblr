"""PostgreSQL schema for the event journal (build.md §18, ADR-003).

The invariants from ADR-003 are enforced here in the DDL wherever the database
can enforce them, rather than in application code where they depend on every
future caller remembering:

- **Append-only** — `INSERT`/`SELECT` grants only; an `UPDATE` fails as a
  permission error instead of succeeding quietly.
- **Producer-assigned identity** — `event_id` is the primary key. `sequence`
  exists for physical ordering and is never identity.
- **Three clocks** — `occurred_at_utc` (market time), `recorded_at_utc` (write
  time), `sequence` (insertion order). Replay orders by the first.
- **Exact money** — `NUMERIC`, never a float type.
- **UTC** — `TIMESTAMPTZ`, never a naive timestamp.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Identity,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

metadata = MetaData()

APPLICATION_ROLE = "crumblr_app"
"""The role the platform connects as. Granted INSERT and SELECT, never UPDATE."""


def _utc_column(name: str, **kwargs: object) -> Column[object]:
    """A timezone-aware timestamp. `timezone=True` is not optional here."""
    return Column(name, DateTime(timezone=True), **kwargs)  # type: ignore[arg-type]


events = Table(
    "events",
    metadata,
    # Identity comes from the producer. The column below is a database-assigned
    # ordinal used only as a tie-break for equal timestamps — a rebuilt database
    # would renumber it, which is exactly why it is not identity.
    Column("event_id", UUID(as_uuid=True), primary_key=True),
    Column("sequence", BigInteger, Identity(always=False), nullable=False, unique=True),
    Column("event_type", String(64), nullable=False),
    Column("schema_version", Integer, nullable=False),
    # Market time — what replay and audit order by.
    _utc_column("occurred_at_utc", nullable=False),
    # Write time — never used for ordering.
    _utc_column("recorded_at_utc", nullable=False, server_default=text("now()")),
    Column("correlation_id", UUID(as_uuid=True), nullable=False),
    Column("causation_id", UUID(as_uuid=True), nullable=True),
    Column("environment", String(16), nullable=False),
    Column("source", String(64), nullable=False),
    Column("payload", JSONB, nullable=False),
    CheckConstraint("schema_version >= 1", name="events_schema_version_positive"),
    # Ordering by market time, with the sequence as a deterministic tie-break
    # so two events sharing a timestamp always read back in the same order.
    Index("ix_events_replay_order", "occurred_at_utc", "sequence"),
    Index("ix_events_correlation", "correlation_id"),
    Index("ix_events_type_time", "event_type", "occurred_at_utc"),
)
"""The journal. Append-only: the one table the whole audit trail rests on."""


decision_capsules = Table(
    "decision_capsules",
    metadata,
    Column("capsule_id", UUID(as_uuid=True), primary_key=True),
    Column("sequence", BigInteger, Identity(always=False), nullable=False, unique=True),
    _utc_column("occurred_at_utc", nullable=False),
    _utc_column("recorded_at_utc", nullable=False, server_default=text("now()")),
    Column("correlation_id", UUID(as_uuid=True), nullable=False),
    Column("canonical_symbol", String(64), nullable=False),
    Column("broker_symbol", String(64), nullable=False),
    Column("environment", String(16), nullable=False),
    Column("strategy_version", String(128), nullable=False),
    Column("model_version", String(128), nullable=True),
    Column("feature_set_version", String(128), nullable=False),
    Column("risk_config_version", String(128), nullable=False),
    Column("code_commit", String(128), nullable=False),
    # Stored so a load can recompute and compare. A mismatch means the row was
    # altered underneath us, which is a tamper signal rather than a bad read.
    Column("provenance_fingerprint", Text, nullable=False),
    Column("payload", JSONB, nullable=False),
    Index("ix_capsules_replay_order", "occurred_at_utc", "sequence"),
    Index("ix_capsules_correlation", "correlation_id"),
)
"""Sealed decision records (build.md §11). Immutable once written."""


safety_state_events = Table(
    "safety_state_events",
    metadata,
    Column("event_id", UUID(as_uuid=True), primary_key=True),
    Column("sequence", BigInteger, Identity(always=False), nullable=False, unique=True),
    Column("state", String(16), nullable=False),
    _utc_column("occurred_at_utc", nullable=False),
    _utc_column("recorded_at_utc", nullable=False, server_default=text("now()")),
    Column("reason_codes", JSONB, nullable=False),
    Column("tripped_by", String(64), nullable=True),
    Column("detail", Text, nullable=True),
    Column("schema_version", Integer, nullable=False),
    Index("ix_safety_state_order", "sequence"),
)
"""Every halt and reset, appended. The record of authority for ADR-002."""


market_ticks = Table(
    "market_ticks",
    metadata,
    # Identity is content-derived by the producer, so the same tick delivered
    # twice collapses to one row while two genuinely different quotes sharing
    # a timestamp — which real feeds do produce — stay two.
    Column("tick_id", UUID(as_uuid=True), primary_key=True),
    Column("sequence", BigInteger, Identity(always=False), nullable=False, unique=True),
    Column("source", String(128), nullable=False),
    Column("canonical_symbol", String(64), nullable=False),
    Column("broker_symbol", String(64), nullable=False),
    _utc_column("event_time_utc", nullable=False),
    _utc_column("received_time_utc", nullable=False),
    _utc_column("recorded_at_utc", nullable=False, server_default=text("now()")),
    Column("bid", Numeric, nullable=False),
    Column("ask", Numeric, nullable=False),
    Column("last", Numeric, nullable=True),
    Column("volume", BigInteger, nullable=True),
    Column("flags", BigInteger, nullable=True),
    Column("data_quality", String(16), nullable=False),
    Column("anomalies", JSONB, nullable=False),
    Column("payload", JSONB, nullable=False),
    Index("ix_ticks_replay_order", "canonical_symbol", "event_time_utc", "sequence"),
    Index("ix_ticks_source_time", "source", "event_time_utc"),
)
"""What the market showed, as it arrived (build.md §12.1; review 1.6 F-022).

The event journal records what the system *did*. This records what it *saw*.
Keeping them apart is §12.1's separation of raw from derived, and it is what
makes a decision checkable against its input rather than only against itself."""


market_bars = Table(
    "market_bars",
    metadata,
    # One bar per source, symbol, timeframe and open time. The primary key is
    # derived from exactly those four, so re-ingesting a series is a no-op and
    # a *different* bar for an interval already stored is a conflict rather
    # than an overwrite — build.md §26 M2 requires raw data to be immutable.
    Column("bar_id", UUID(as_uuid=True), primary_key=True),
    Column("sequence", BigInteger, Identity(always=False), nullable=False, unique=True),
    Column("source", String(128), nullable=False),
    Column("canonical_symbol", String(64), nullable=False),
    Column("broker_symbol", String(64), nullable=False),
    Column("timeframe", String(16), nullable=False),
    _utc_column("open_time_utc", nullable=False),
    _utc_column("received_time_utc", nullable=False),
    _utc_column("recorded_at_utc", nullable=False, server_default=text("now()")),
    Column("open", Numeric, nullable=False),
    Column("high", Numeric, nullable=False),
    Column("low", Numeric, nullable=False),
    Column("close", Numeric, nullable=False),
    Column("tick_volume", BigInteger, nullable=False),
    Column("real_volume", BigInteger, nullable=True),
    Column("spread_points", Integer, nullable=True),
    # Whether a broker sent this bar or this platform built it, and by which
    # transformation. Without these a derived bar is indistinguishable from a
    # delivered one and a change to the aggregation rules rewrites history.
    Column("origin", String(32), nullable=False),
    Column("pipeline_version", String(128), nullable=True),
    Column("tick_count", Integer, nullable=True),
    Column("data_quality", String(16), nullable=False),
    Column("anomalies", JSONB, nullable=False),
    Column("payload", JSONB, nullable=False),
    CheckConstraint("high >= low", name="market_bars_high_at_least_low"),
    Index("ix_bars_replay_order", "canonical_symbol", "timeframe", "open_time_utc", "sequence"),
    Index("ix_bars_source_time", "source", "open_time_utc"),
)
"""Normalized bars, each carrying the account of where it came from."""


risk_session_states = Table(
    "risk_session_states",
    metadata,
    Column("event_id", UUID(as_uuid=True), primary_key=True),
    Column("sequence", BigInteger, Identity(always=False), nullable=False, unique=True),
    Column("trading_day", Date, nullable=False),
    # Every money column is NUMERIC. A daily-loss budget held as a float is a
    # budget that disagrees with itself by the eighth decimal, which is the
    # kind of disagreement that resolves in whichever direction is convenient.
    Column("session_start_equity", Numeric, nullable=False),
    Column("current_equity", Numeric, nullable=False),
    Column("peak_equity", Numeric, nullable=False),
    Column("realized_pnl", Numeric, nullable=False),
    Column("max_drawdown_fraction", Numeric, nullable=False),
    Column("max_session_loss_fraction", Numeric, nullable=False),
    Column("open_risk_fraction", Numeric, nullable=False),
    Column("open_position_count", Integer, nullable=False),
    _utc_column("occurred_at_utc", nullable=False),
    _utc_column("recorded_at_utc", nullable=False, server_default=text("now()")),
    Column("schema_version", Integer, nullable=False),
    Index("ix_risk_session_order", "sequence"),
    Index("ix_risk_session_day", "trading_day", "sequence"),
)
"""Risk-session snapshots (review F-019). Append-only, like every other record
of something the system must not be able to forget in the permissive
direction."""


config_versions = Table(
    "config_versions",
    metadata,
    # The content hash is the identity: the same configuration recorded twice
    # is one row, and a changed configuration is unambiguously a new one.
    Column("config_version", Text, primary_key=True),
    _utc_column("first_seen_utc", nullable=False, server_default=text("now()")),
    Column("environment", String(16), nullable=False),
    Column("payload", JSONB, nullable=False),
)
"""Configurations, keyed by content hash (build.md §17)."""


instrument_specs = Table(
    "instrument_specs",
    metadata,
    Column("spec_version", Text, primary_key=True),
    Column("canonical_symbol", String(64), nullable=False),
    Column("broker_symbol", String(64), nullable=False),
    _utc_column("captured_at_utc", nullable=False),
    _utc_column("first_seen_utc", nullable=False, server_default=text("now()")),
    # Money and sizes are NUMERIC. Storing these as float would reintroduce the
    # error the domain layer rejects at its boundary, one level down.
    Column("contract_size", Numeric, nullable=False),
    Column("point", Numeric, nullable=False),
    Column("tick_size", Numeric, nullable=False),
    Column("tick_value", Numeric, nullable=False),
    Column("volume_min", Numeric, nullable=False),
    Column("volume_max", Numeric, nullable=False),
    Column("volume_step", Numeric, nullable=False),
    Column("digits", Integer, nullable=False),
    Column("payload", JSONB, nullable=False),
    Index("ix_specs_symbol", "canonical_symbol", "captured_at_utc"),
)
"""Broker symbol specifications, keyed by content hash (build.md §7)."""


APPEND_ONLY_TABLES: tuple[str, ...] = (
    "events",
    "decision_capsules",
    "safety_state_events",
    "risk_session_states",
    "market_ticks",
    "market_bars",
    "config_versions",
    "instrument_specs",
)
"""Tables the application role may only insert into and read from."""


def append_only_grants(role: str = APPLICATION_ROLE) -> tuple[str, ...]:
    """DDL that makes append-only a permission rather than a convention.

    ADR-003 invariant 1. Enforcing this in the database means a mistaken
    `UPDATE` fails loudly instead of silently rewriting history — which is the
    one failure an audit trail cannot survive.
    """
    statements = [f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {role}"]
    statements += [f"GRANT SELECT, INSERT ON {table} TO {role}" for table in APPEND_ONLY_TABLES]
    statements += [
        f"GRANT USAGE, SELECT ON SEQUENCE {table}_sequence_seq TO {role}"
        for table in (
            "events",
            "decision_capsules",
            "safety_state_events",
            "risk_session_states",
            "market_ticks",
            "market_bars",
        )
    ]
    return tuple(statements)
