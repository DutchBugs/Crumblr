"""Outbound wire payload for the Static Agent fork's `TraderContext 1.0`
contract (feedback.1.27 section 5, feedback.1.28 section 9's transport
smoke-test carve-out).

**Scope, deliberately narrow: the "market data unhealthy" NO_TRADE
degenerate case only.** `crumblr_trader.py::CrumblrStaticTrader.evaluate()`
(`DutchBugs/crumblr-static-agent-host`) checks
`market.market_data_health != "HEALTHY"` and short-circuits to a safe
`NO_TRADE` *before* it ever reads `features.observation.reason_codes` --
verified by reading the actual fork code, not assumed from its schema --
which is the one path this module can honestly build against today. A
genuine HEALTHY directional/NO_TRADE strategy decision needs the
fork-side strategy-runtime work `feedback.1.28.md` section 13 assigns to
the external Agent Developer (AG-015 -> F-066, `review/AGENT_FEEDBACK.md`)
-- not this module, and not Crumblr's `ict_v1` (`feedback.1.28.md`
section 2 explicitly rejects reusing an internal strategy to manufacture
an external agent's strategy state).

`features.observation` is present only because the schema requires the
key to exist -- its content is the `_PLACEHOLDER_OBSERVATION_*` constants
below, never read on this path. `build_unhealthy_market_context()` refuses
(raises `ValueError`) to build a payload claiming
`market_data_health == "HEALTHY"`: sending one would silently exercise the
closed reason-code vocabulary this module does not and must not speak
(AG-015).

`compute_input_identity`/`canonical_json`/`canonical_decimal` mirror
`crumblr_strategy_agent.identity`'s exact algorithm byte for byte -- the
fork independently recomputes and compares this value
(`TraderContext.from_payload`), so any divergence in JSON key
ordering/separators or decimal formatting fails the whole request closed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any

from crumblr.domain.timeutils import UtcDatetime

TRADER_CONTEXT_SCHEMA_VERSION = "1.0"
DATA_ORIGIN = "LIVE_FORWARD"
FEATURES_SCHEMA_VERSION = "1.0"
FEATURES_PRODUCER = "CRUMBLR_FROZEN_STRATEGY_CORE"

STATIC_AGENT_STRATEGY_IDENTITY: dict[str, str] = {
    "strategy_id": "ICT_SB_EURUSD_PIVOT2",
    "version": "5.0",
    "source_hash": "eb6e762a95d35ada8f25734440c9ee3008dcbbfe5ced8e3a3d3cda3e6293cda7",
    "profile": "EURUSD_PIVOT2_CORE_V5",
    "config_id": "EURUSD_V5_DEFAULTS",
}
"""The frozen strategy package's identity, exactly as the fork's own
`contracts/crumblr-trader-context-1.0.schema.json` (`const` fields) and
`strategy_assets/ict_sb_eurusd_pivot2/5.0/manifest.json` declare it --
cross-checked against both files independently, not transcribed from one
alone. Not a claim that Crumblr computed or verified this strategy's
logic; it identifies which already-frozen, already-approved artifact the
request is nominally bound to, the same way `TradingAssignment
.strategy_artifact_hash` identifies an internal assignment's artifact."""

STATIC_AGENT_CANONICAL_SYMBOL = "EURUSD"
"""The fork's `market.canonical_symbol` is a schema `const` and uses no
separator -- Crumblr's own canonical symbol is `"EUR/USD"`
(`domain.models.Symbol`). Callers pass Crumblr's own broker/instrument
data through this module's parameters; this constant is what actually
goes on the wire, never a field a caller can override."""

STATIC_AGENT_TIMEFRAME = "M5"

_PLACEHOLDER_OBSERVATION_EVENT_TYPE = "NO_TRADE"
_PLACEHOLDER_OBSERVATION_REASON_CODES: tuple[str, ...] = ("NOT_EVALUATED_MARKET_DATA_UNHEALTHY",)
"""Schema-valid, never read. `CrumblrStaticTrader.evaluate()` returns via
its `market_data_health != "HEALTHY"` branch before `context.observation`
is ever accessed -- confirmed by reading the code, not inferred from the
schema alone. `reason_codes` deliberately does not claim any of the
fork's closed Pivot-2-2 vocabulary (AG-015): "not evaluated" is the
honest description of what actually happened."""


def canonical_json(value: Any) -> str:
    """Mirrors `crumblr_strategy_agent.identity.canonical_json` exactly."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def canonical_decimal(value: Decimal) -> str:
    """Mirrors `crumblr_strategy_agent.identity.canonical_decimal` exactly
    -- a non-exponent representation with trailing zeros stripped."""
    if not value.is_finite():
        raise ValueError("decimal must be finite")
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def _stamp(value: UtcDatetime) -> str:
    """Whole seconds, UTC -- `TraderContext.from_payload`'s `_utc()`
    rejects a non-zero microsecond and anything not UTC.

    `UtcDatetime`'s own UTC coercion only runs inside Pydantic model
    construction (a `BeforeValidator`); this module's plain dataclass and
    function parameters get none of that for free, so it is checked here,
    explicitly, at the Crumblr boundary -- the same place every other
    `UtcDatetime`-typed Pydantic field in this codebase would fail closed
    instead of silently emitting a malformed payload the fork rejects.
    """
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"timestamp must be UTC: {value!r}")
    return value.replace(microsecond=0).isoformat()


def compute_input_identity(
    *,
    strategy: dict[str, Any],
    market: dict[str, Any],
    instrument_spec: dict[str, Any],
    features: dict[str, Any],
) -> str:
    """Mirrors `crumblr_strategy_agent.identity.compute_input_identity`
    exactly -- the fork independently recomputes this from the payload it
    receives and rejects a mismatch."""
    material = {
        "strategy": strategy,
        "market": market,
        "instrument_spec": instrument_spec,
        "features": features,
    }
    return f"input_{hashlib.sha256(canonical_json(material).encode('utf-8')).hexdigest()}"


@dataclass(frozen=True)
class InstrumentSpecFacts:
    """Read-only instrument facts the fork's `instrument_spec` block
    needs -- a narrow slice of `domain.models.InstrumentSpec`, not the
    whole contract, so this module cannot accidentally forward a field
    the wire contract does not ask for."""

    broker_symbol: str
    digits: int
    point: Decimal
    tick_size: Decimal
    observed_at_utc: UtcDatetime


def build_unhealthy_market_context(
    *,
    decision_window_id: str,
    decision_time_utc: UtcDatetime,
    mode: str,
    market_data_health: str,
    last_completed_bar_close_time_utc: UtcDatetime,
    broker_symbol: str,
    instrument_spec: InstrumentSpecFacts,
    available_at_utc: UtcDatetime,
    source_bar_ids: tuple[str, ...],
) -> dict[str, Any]:
    """Build a `TraderContext 1.0` payload proving Crumblr's own
    market-data health is honestly unhealthy -- the one directional-proof
    case this module can send today without speaking the fork's closed
    Pivot-2-2 reason-code vocabulary (AG-015).

    `mode` must be `"LIVE_SHADOW"` or `"LIVE_DEMO_PROPOSAL_ONLY"` (the
    schema's own enum) -- not validated here beyond that shape; the fork
    validates it structurally and this module does not duplicate that.
    """
    if market_data_health == "HEALTHY":
        raise ValueError(
            "build_unhealthy_market_context() refuses market_data_health='HEALTHY' -- "
            "that path reaches CrumblrStaticTrader's closed reason-code vocabulary "
            "(AG-015), which this module deliberately does not speak"
        )
    if not source_bar_ids:
        raise ValueError("source_bar_ids must be non-empty")

    strategy = dict(STATIC_AGENT_STRATEGY_IDENTITY)
    market = {
        "canonical_symbol": STATIC_AGENT_CANONICAL_SYMBOL,
        "broker_symbol": broker_symbol,
        "timeframe": STATIC_AGENT_TIMEFRAME,
        "market_data_health": market_data_health,
        "last_completed_bar_close_time_utc": _stamp(last_completed_bar_close_time_utc),
    }
    instrument_spec_block = {
        "broker_symbol": instrument_spec.broker_symbol,
        "digits": instrument_spec.digits,
        "point": canonical_decimal(instrument_spec.point),
        "tick_size": canonical_decimal(instrument_spec.tick_size),
        "observed_at_utc": _stamp(instrument_spec.observed_at_utc),
    }
    features = {
        "schema_version": FEATURES_SCHEMA_VERSION,
        "producer": FEATURES_PRODUCER,
        "available_at_utc": _stamp(available_at_utc),
        "source_bar_ids": list(source_bar_ids),
        "observation": {
            "event_type": _PLACEHOLDER_OBSERVATION_EVENT_TYPE,
            "reason_codes": list(_PLACEHOLDER_OBSERVATION_REASON_CODES),
            "uses_only_confirmed_data": True,
        },
    }
    input_identity = compute_input_identity(
        strategy=strategy, market=market, instrument_spec=instrument_spec_block, features=features
    )

    return {
        "schema_version": TRADER_CONTEXT_SCHEMA_VERSION,
        "decision_window_id": decision_window_id,
        "decision_time_utc": _stamp(decision_time_utc),
        "mode": mode,
        "data_origin": DATA_ORIGIN,
        "strategy": strategy,
        "market": market,
        "instrument_spec": instrument_spec_block,
        "features": features,
        "input_identity": input_identity,
    }
