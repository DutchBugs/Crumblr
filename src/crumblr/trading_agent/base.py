"""The interface every strategy implements.

build.md §9.3 stage D calls for one champion executing while challengers run in
shadow. That is only possible if strategies are interchangeable, so the
orchestrator depends on these protocols and never on a particular strategy.

A strategy returns two things: the decision, and the evidence behind it. The
evidence is what the decision capsule stores, and it is what makes a decision
re-examinable months later.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from crumblr.domain.enums import Regime, Side
from crumblr.domain.models import Bar, InstrumentSpec, MarketSnapshot, TradeIntent


@dataclass(frozen=True)
class AgentContext:
    """The portfolio and policy context a strategy may read (build.md §9.1).

    A strategy proposes a stop structure *within policy*, so it is told the
    minimum stop distance. Without it a strategy proposes stops that are
    reasonable on their own terms and rejected by the risk engine.
    """

    open_position_sides: tuple[Side, ...] = ()
    requested_risk_fraction: Decimal = Decimal("0.005")
    min_stop_distance_points: int = 0


@dataclass(frozen=True)
class StrategyDecision:
    """A strategy always produces a decision, even when that decision is nothing."""

    side: Side
    confidence: float
    reason_codes: tuple[str, ...]
    intent: TradeIntent | None


@runtime_checkable
class FeatureEvidence(Protocol):
    """What a strategy must be able to say about the inputs it acted on.

    `model_dump` is part of this contract, not an implementation detail
    every strategy happens to share: D-031 requires the actual values a
    decision was made from to be durably recorded, not only their hash and
    version, and `application.recording.RunRecorder.record_features` reads
    every strategy's evidence through this one Protocol regardless of which
    concrete `Contract` subclass a given strategy returns (`FeatureSnapshot`
    for `baseline_v1`, `IctFeatureSnapshot` for `ict_v1`, each with its own,
    different fields).
    """

    @property
    def feature_snapshot_id(self) -> UUID: ...

    @property
    def feature_set_version(self) -> str: ...

    @property
    def feature_values_hash(self) -> str: ...

    @property
    def regime(self) -> Regime: ...

    @property
    def symbol(self) -> str: ...

    def model_dump(self, *, mode: str = "python") -> dict[str, Any]: ...


@dataclass(frozen=True)
class StrategyOutcome:
    """One evaluated window.

    `features` is None only while the strategy lacks the history to say
    anything at all. That is distinct from having looked and decided not to
    trade, which produces evidence like any other decision.
    """

    decision: StrategyDecision
    features: FeatureEvidence | None

    @property
    def is_warming_up(self) -> bool:
        return self.features is None


class Strategy(Protocol):
    """The callable shape the orchestrator invokes each window."""

    strategy_id: str
    strategy_version: str
    minimum_bars: int

    def __call__(
        self,
        snapshot: MarketSnapshot,
        bars: tuple[Bar, ...],
        spec: InstrumentSpec,
        context: AgentContext,
    ) -> StrategyOutcome: ...
