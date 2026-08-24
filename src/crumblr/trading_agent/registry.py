"""Which strategy the configured `strategy_id` resolves to.

Selection happens here rather than in the orchestrator so that adding a
challenger is a registry entry, not a change to the execution path. The
supervisor independently gates which strategy ids it will approve, so a
registry entry alone is not permission to trade.
"""

from __future__ import annotations

from collections.abc import Callable

from crumblr.domain.models import Bar, InstrumentSpec, MarketSnapshot
from crumblr.trading_agent import baseline, ict
from crumblr.trading_agent.base import AgentContext, StrategyOutcome

StrategyCallable = Callable[
    [MarketSnapshot, tuple[Bar, ...], InstrumentSpec, AgentContext], StrategyOutcome
]


class StrategyEntry:
    """A registered strategy and the metadata the orchestrator needs."""

    def __init__(
        self,
        strategy_id: str,
        version: str,
        minimum_bars: int,
        evaluate: StrategyCallable,
        description: str,
    ) -> None:
        self.strategy_id = strategy_id
        self.version = version
        self.minimum_bars = minimum_bars
        self.evaluate = evaluate
        self.description = description


STRATEGIES: dict[str, StrategyEntry] = {
    baseline.STRATEGY_ID: StrategyEntry(
        strategy_id=baseline.STRATEGY_ID,
        version=baseline.STRATEGY_VERSION,
        minimum_bars=baseline.MINIMUM_BARS,
        evaluate=baseline.evaluate,
        description="Moving-average separation in a trending regime, ATR stop.",
    ),
    ict.STRATEGY_ID: StrategyEntry(
        strategy_id=ict.STRATEGY_ID,
        version=ict.STRATEGY_VERSION,
        minimum_bars=ict.MINIMUM_BARS,
        evaluate=ict.evaluate,
        description=(
            "ICT entry model: liquidity sweep, structure shift, displacement "
            "into a fair value gap, entered at a discount within the OTE band "
            "during a killzone."
        ),
    ),
}


def resolve(strategy_id: str) -> StrategyEntry:
    """Look up a strategy, failing loudly on an unknown id.

    A typo in configuration must stop the platform rather than silently fall
    back to some default strategy — which trade got taken, and by what, is the
    whole basis of the audit trail.
    """
    entry = STRATEGIES.get(strategy_id)
    if entry is None:
        known = ", ".join(sorted(STRATEGIES))
        raise KeyError(f"unknown strategy_id {strategy_id!r}; registered: {known}")
    return entry
