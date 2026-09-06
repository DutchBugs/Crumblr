"""Research-only contracts and Trainer integration.

Nothing in this package has execution, Risk, Policy, assignment-promotion,
broker, or MT5 authority.
"""

from crumblr.research.contracts import (
    BacktestReport,
    BacktestRequest,
    EvaluationRecord,
    StrategyArtifact,
    StrategyChangeProposal,
    TrainingFinding,
)

__all__ = [
    "BacktestReport",
    "BacktestRequest",
    "EvaluationRecord",
    "StrategyArtifact",
    "StrategyChangeProposal",
    "TrainingFinding",
]
