"""`AgentMarketContextV1` -- the strategy-neutral outbound context payload
(feedback.1.28 section 3, correcting feedback.1.27 section 5.1's original
"trusted feature observation required by the assigned strategy" wording,
which that later review explicitly supersedes).

**What changed and why.** AG-015 -> F-066 (`review/AGENT_FEEDBACK.md`)
found that materializing a strategy-specific setup observation for an
external agent put Crumblr in the business of computing (or worse,
fabricating) another party's strategy logic. `feedback.1.28.md` section 3
is explicit about the correction: the external context must contain
"trusted, strategy-neutral source data and platform state from which the
assigned Trading Agent can make its own strategy decision" -- observations
and constraints, never setup detections. This module builds exactly that,
in the four categories that review names:

    BINDING / PROVENANCE   -- what this context is bound to and until when
    MARKET                 -- platform-owned, strategy-neutral market data
    INSTRUMENT              -- read-only broker/instrument facts
    PLATFORM STATE          -- read-only session/safety/reconciliation health

Deliberately absent, by design, matching `feedback.1.28.md` section 3's
own negative list: `liquidity_sweep_detected`, `FVG_CONFIRMED`,
`WAITING_FOR_MSS`, `PIVOT_2_2_CONFIRMED`, an OTE entry, a strategy-specific
regime, or any strategy-specific reason code. None of that belongs here
"unless those values were produced by that strategy's own versioned
runtime/artifact and are being returned as agent evidence -- never
fabricated by Core." This module fabricates nothing; it only forwards
values Crumblr already trusts and already recorded.

**A fork-agnostic Crumblr artifact, not a wire format.** This is Crumblr's
own general-purpose representation of "what an external agent may see" --
it is not the Static Agent fork's `TraderContext 1.0` shape
(`static_agent_transport.py` is that fork-specific adapter, and stays
narrowly scoped to the unhealthy-market smoke case until F-066's
fork-side work lands). A future adapter for any external agent -- the
Static Agent fork or a second, differently-shaped one (F-066 item 8's own
regression proof) -- consumes this contract, never the other way round.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from crumblr.agent_gateway.contracts import OpenRiskFraction, PolicyHints
from crumblr.domain.enums import DataQuality, KillSwitchState, ReconciliationStatus, SessionState
from crumblr.domain.models import Bar, Contract, InstrumentSpec, MarketSnapshot, Symbol, VersionTag
from crumblr.domain.money import ExactDecimal, Price, Volume
from crumblr.domain.timeutils import UtcDatetime

MARKET_CONTEXT_SCHEMA_VERSION: Literal["1.0"] = "1.0"

DEFAULT_MAX_BARS = 400
"""Matches `application/orchestration.py::MAX_HISTORY_BARS` -- the same
rolling window an internal strategy already sees, so an external agent is
not given more or less history than Crumblr's own strategies get by
default. A future `TradingAssignment`/strategy-artifact-declared "required
closed-bar lookback" (`feedback.1.28.md` section 4's own example of
acceptable declarative metadata) would override this; that field does not
exist yet, so this constant is today's bound."""


class AgentMarketContextProvenance(Contract):
    """BINDING / PROVENANCE -- what this context is bound to and until
    when. `content_hash` is `DecisionContextBundle.content_hash`, the
    same trusted binding a `TradeProposal`/`NoTradeDecision` must already
    quote back (`agent_gateway/contracts.py`)."""

    context_id: UUID
    content_hash: str
    assignment_id: UUID
    strategy_artifact_id: UUID
    strategy_artifact_hash: VersionTag
    issued_at_utc: UtcDatetime
    expires_at_utc: UtcDatetime


class AgentMarketData(Contract):
    """MARKET -- platform-owned, strategy-neutral. Observations and
    constraints, never a setup detection: a current trusted quote, a
    bounded window of confirmed closed bars with their exact source
    identities, and freshness/quality -- nothing that says what the
    market *means*.
    """

    canonical_symbol: Symbol
    timeframe: str
    market_snapshot_id: UUID
    event_time_utc: UtcDatetime
    bid: Price
    ask: Price
    spread_points: int
    data_quality: DataQuality
    bars: tuple[Bar, ...]
    source_bar_ids: tuple[str, ...]

    @property
    def bars_count(self) -> int:
        return len(self.bars)


class AgentInstrumentFacts(Contract):
    """INSTRUMENT / BROKER FACTS -- read-only."""

    broker_symbol: Symbol
    digits: int
    point: ExactDecimal
    tick_size: ExactDecimal
    stops_level: int
    volume_min: Volume
    volume_max: Volume
    volume_step: Volume
    spec_version: str


class AgentPlatformState(Contract):
    """PLATFORM STATE -- read-only, and fail-closed by construction: every
    field here defaults to the unresolved/unknown member of its enum
    rather than a permissive one, the same "absence of evidence is not
    evidence of safety" rule the rest of this codebase already holds to
    (review finding F-002). `feature_snapshot_id` is the `agent_context_v1`
    binding (AG-006) -- an audit/context-evidence anchor, still not
    strategy analysis.

    `open_risk_fraction` is `OpenRiskFraction`
    (`agent_gateway/contracts.py`), not `domain.money.RiskFraction` --
    `RiskFraction` requires `gt=0`, so a genuinely flat book could never
    construct as `Decimal("0")` and every caller had nothing to pass but
    `None`, collapsing "flat" and "could not be established" into the
    same wire value. `None` still means the figure could not be
    established; a numeric value, including `Decimal("0")`, means it was.
    """

    session_state: SessionState
    safety_state: KillSwitchState
    reconciliation_status: ReconciliationStatus
    feature_snapshot_id: UUID
    open_position_count: int
    open_risk_fraction: OpenRiskFraction | None = None


class AgentMarketContextV1(Contract):
    """The strategy-neutral outbound context payload. See the module
    docstring for the architectural correction this implements."""

    schema_version: Literal["1.0"] = MARKET_CONTEXT_SCHEMA_VERSION
    provenance: AgentMarketContextProvenance
    market: AgentMarketData
    instrument: AgentInstrumentFacts
    platform_state: AgentPlatformState
    policy_hints: PolicyHints | None = None


def _source_bar_id(*, symbol: str, timeframe: str, bar: Bar) -> str:
    return f"{symbol}:{timeframe}:{bar.open_time_utc.isoformat()}"


def build_agent_market_context_v1(
    *,
    context_id: UUID,
    content_hash: str,
    assignment_id: UUID,
    strategy_artifact_id: UUID,
    strategy_artifact_hash: VersionTag,
    issued_at_utc: UtcDatetime,
    expires_at_utc: UtcDatetime,
    snapshot: MarketSnapshot,
    spec: InstrumentSpec,
    session_state: SessionState,
    safety_state: KillSwitchState,
    reconciliation_status: ReconciliationStatus,
    feature_snapshot_id: UUID,
    open_position_count: int,
    open_risk_fraction: OpenRiskFraction | None = None,
    policy_hints: PolicyHints | None = None,
    max_bars: int = DEFAULT_MAX_BARS,
) -> AgentMarketContextV1:
    """Build the strategy-neutral context from Crumblr's own already-trusted
    objects. Every argument is a value the caller already resolved itself
    (matching `risk.policies.evaluate()`'s own "everything pre-observed"
    style) -- this function performs no I/O and fetches nothing.

    `snapshot.bars` is truncated to the most recent `max_bars` -- the
    caller's own history buffer may hold more than that; this bounds what
    an external agent actually receives, deliberately.
    """
    if max_bars < 0:
        raise ValueError(f"max_bars must not be negative: {max_bars}")
    # `snapshot.bars[-0:]` is `snapshot.bars[0:]` -- ALL bars, not zero --
    # since `-0 == 0` in Python slicing. Handled explicitly rather than
    # silently ignoring `max_bars=0`'s caller intent.
    bounded_bars = tuple(snapshot.bars[-max_bars:]) if max_bars > 0 else ()
    source_bar_ids = tuple(
        _source_bar_id(symbol=snapshot.symbol, timeframe=snapshot.timeframe, bar=bar)
        for bar in bounded_bars
    )

    return AgentMarketContextV1(
        provenance=AgentMarketContextProvenance(
            context_id=context_id,
            content_hash=content_hash,
            assignment_id=assignment_id,
            strategy_artifact_id=strategy_artifact_id,
            strategy_artifact_hash=strategy_artifact_hash,
            issued_at_utc=issued_at_utc,
            expires_at_utc=expires_at_utc,
        ),
        market=AgentMarketData(
            canonical_symbol=snapshot.symbol,
            timeframe=snapshot.timeframe,
            market_snapshot_id=snapshot.snapshot_id,
            event_time_utc=snapshot.event_time_utc,
            bid=snapshot.bid,
            ask=snapshot.ask,
            spread_points=snapshot.spread_points,
            data_quality=snapshot.data_quality,
            bars=bounded_bars,
            source_bar_ids=source_bar_ids,
        ),
        instrument=AgentInstrumentFacts(
            broker_symbol=spec.broker_symbol,
            digits=spec.digits,
            point=spec.point,
            tick_size=spec.tick_size,
            stops_level=spec.stops_level,
            volume_min=spec.volume_min,
            volume_max=spec.volume_max,
            volume_step=spec.volume_step,
            spec_version=spec.spec_version,
        ),
        platform_state=AgentPlatformState(
            session_state=session_state,
            safety_state=safety_state,
            reconciliation_status=reconciliation_status,
            feature_snapshot_id=feature_snapshot_id,
            open_position_count=open_position_count,
            open_risk_fraction=open_risk_fraction,
        ),
        policy_hints=policy_hints,
    )
