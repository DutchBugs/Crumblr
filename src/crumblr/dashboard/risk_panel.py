"""Read-only Risk panel: configured limits plus the current ledger reading.

The four limits come straight from `config.RiskConfig` — never hardcoded in
a template, so a config change is reflected without a code change. The three
"current" values come from `risk.session.RiskSessionStore.load_latest()`,
the same durable ledger both `LiveDecisionOrchestrator` and PAPER_LITE write
under the shared `RiskLedgerLock` (ADR-021/AG-012) — one read serves both
pipelines. `recover_session()` is deliberately not called here: it is
mutating-intent trading logic (fail-closed recovery for a live decision),
out of scope for a read-only display.
"""

from __future__ import annotations

from dataclasses import dataclass

from crumblr.config import RiskConfig
from crumblr.risk.session import RiskSessionStore


@dataclass(frozen=True)
class RiskPanelState:
    max_risk_per_trade: str
    max_open_risk: str
    max_daily_loss: str
    max_drawdown: str

    current_open_risk: str
    """A formatted fraction, `"—"` if never recorded, `"UNKNOWN"` if the

    ledger could not be read at all."""
    current_daily_loss: str
    current_drawdown: str


def build_risk_panel(*, risk_config: RiskConfig, session_store: RiskSessionStore) -> RiskPanelState:
    record = session_store.load_latest()

    if not record.is_known:
        current_open_risk = current_daily_loss = current_drawdown = "UNKNOWN"
    elif record.state is None:
        current_open_risk = current_daily_loss = current_drawdown = "—"
    else:
        state = record.state
        current_open_risk = (
            "—" if state.open_risk_fraction is None else str(state.open_risk_fraction)
        )
        current_daily_loss = str(state.max_session_loss_fraction)
        current_drawdown = str(state.max_drawdown_fraction)

    return RiskPanelState(
        max_risk_per_trade=str(risk_config.max_risk_per_trade),
        max_open_risk=str(risk_config.max_open_risk),
        max_daily_loss=str(risk_config.max_daily_loss),
        max_drawdown=str(risk_config.max_drawdown),
        current_open_risk=current_open_risk,
        current_daily_loss=current_daily_loss,
        current_drawdown=current_drawdown,
    )
