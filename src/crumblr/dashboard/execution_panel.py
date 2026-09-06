"""Read-only derivation of the "Execution" header card from real config.

Review feedback on the first version of this dashboard: the header card's
`DISABLED` label was a hardcoded template literal, true today but not
*derived* from anything — a config change would not have been reflected.
This reads the four named gates directly (`ExecutionConfig.submission_enabled`,
`.feedback_2_0_approved`, `.flatten_submission_enabled`,
`PlatformConfig.live_trading_acknowledged`) rather than calling the full
`risk.submission_gate.evaluate_submission_gate` — that function additionally
needs live account/reconciliation/market-data/kill-switch state to evaluate
its other six conditions, which is execution-time context this passive
status page does not hold and should not reconstruct just to answer "are the
config-level gates open." A `False` on any of these four already means real
`order_send` is unreachable regardless of the other six, so checking exactly
these four is a strict, honest subset of the real gate, never a looser one.
"""

from __future__ import annotations

from dataclasses import dataclass

from crumblr.config import ExecutionConfig


@dataclass(frozen=True)
class ExecutionGateState:
    disabled: bool
    """`True` unless every one of `closed_gates` is empty — i.e. unless all

    four named gates are `True`. No shipped config sets all four, so this is
    `True` in every real deployment today; it is computed, not asserted."""

    closed_gates: tuple[str, ...]
    """Names of the gates currently `False`, for a diagnostic detail line —

    e.g. `("feedback_2_0_approved", "live_trading_acknowledged")`."""


def build_execution_gate_state(
    *, execution_config: ExecutionConfig, live_trading_acknowledged: bool
) -> ExecutionGateState:
    checks = {
        "submission_enabled": execution_config.submission_enabled,
        "feedback_2_0_approved": execution_config.feedback_2_0_approved,
        "flatten_submission_enabled": execution_config.flatten_submission_enabled,
        "live_trading_acknowledged": live_trading_acknowledged,
    }
    closed_gates = tuple(name for name, value in checks.items() if not value)
    return ExecutionGateState(disabled=bool(closed_gates), closed_gates=closed_gates)
