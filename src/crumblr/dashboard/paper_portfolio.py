"""Read-only PAPER_LITE portfolio panel (work order: "paper portfolio reducer").

`persistence.paper_lite.PaperPortfolioView` already exists and is explicitly
documented as "a strategy-neutral read model suitable for a dashboard
adapter" -- this module is that adapter, nothing more. It introduces no new
evidence source and no new P&L computation of its own: `balance`/`equity`/
`unrealised_profit`/`realized_profit`/the open-risk figures are all produced
by `DurablePaperBroker.portfolio_view()`, replaying the exact same append-only
journal `dashboard/paper_lite_journal.py` already reads for the activity feed,
through the exact same `SimulatedBroker` fill engine PAPER_LITE's own runner
(`scripts/paper_lite.py`) uses -- never a second, dashboard-local
reimplementation of fill/P&L logic, which would be a genuinely new (and
easily divergent) evidence source.

`DurablePaperBroker` transitively imports `crumblr.mt5_gateway.simulated
.SimulatedBroker` -- a pure-Python fill-model simulator with no MetaTrader5
SDK dependency at all (confirmed: no `MetaTrader5`/`mt5_gateway.readonly`/
`mt5_gateway.execution` import anywhere in `simulated.py`). This module's own
imports stay literally within `crumblr.persistence`/`crumblr.application`, so
`TestReadOnlyBoundary::test_the_dashboard_package_never_imports_metatrader5`'s
per-file AST check is not just technically satisfied but genuinely honored:
nothing this module pulls in ever touches a real MT5 terminal or credential.

Two deliberate safety gates, both fail-closed to "no confident number" rather
than a plausible-looking one:

- `DurablePaperBroker`'s constructor WRITES a fresh `PORTFOLIO_CREATED`
  header to disk when `journal_path` does not exist yet -- exactly the side
  effect `dashboard/paper_lite_journal.py`'s own module docstring already
  flags and avoids for the activity feed. This module never constructs the
  broker unless the journal file already exists, so this read path can never
  be the one that creates a paper journal.
- Replaying the fill engine against a spec that is not the owner-approved
  pin for this exact symbol (`config.MarketConfig.expected_spec_version`,
  F-055) could silently reconstruct different historical fills than what
  genuinely happened, since pip size/point conversion feed directly into the
  simulator's math. The same "never trust an unpinned spec" rule
  reconciliation already applies is applied here before any number is shown.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

from crumblr.application.paper_lite import load_paper_lite_settings
from crumblr.domain.models import PositionState
from crumblr.persistence.instrument_specs import InstrumentSpecStore
from crumblr.persistence.paper_lite import DurablePaperBroker, PaperJournalError, PaperPortfolioView

PaperPortfolioStatus = Literal["OK", "DEGRADED", "NO EVIDENCE"]


@dataclass(frozen=True)
class PaperPositionRow:
    ticket: int
    broker_symbol: str
    side: str
    volume: str
    open_price: str
    current_price: str | None
    stop_loss_price: str | None
    take_profit_price: str | None
    profit: str
    swap: str


@dataclass(frozen=True)
class PaperPortfolioPanelState:
    """`status` mirrors the rest of the dashboard's explicit-absence

    discipline (`AgentHealthState`, `SnapshotCompleteness`, ...): `NO
    EVIDENCE` means nothing to read yet (no journal/settings configured, or
    the journal file has not been written yet -- a first start, not a
    problem); `DEGRADED` means real evidence exists but cannot be trusted
    (a corrupt/conflicting journal, or a spec that is not the approved
    pin); `OK` means `portfolio`/`positions` are a genuine replay of the
    real durable journal. `portfolio`/`positions` are only populated when
    `status == "OK"`.
    """

    status: PaperPortfolioStatus
    detail: str | None
    portfolio: PaperPortfolioView | None
    positions: tuple[PaperPositionRow, ...]


def _no_evidence(detail: str) -> PaperPortfolioPanelState:
    return PaperPortfolioPanelState(
        status="NO EVIDENCE", detail=detail, portfolio=None, positions=()
    )


def _degraded(detail: str) -> PaperPortfolioPanelState:
    return PaperPortfolioPanelState(status="DEGRADED", detail=detail, portfolio=None, positions=())


def _position_row(position: PositionState) -> PaperPositionRow:
    return PaperPositionRow(
        ticket=position.ticket,
        broker_symbol=position.broker_symbol,
        side=position.side.value,
        volume=str(position.volume),
        open_price=str(position.open_price),
        current_price=(str(position.current_price) if position.current_price is not None else None),
        stop_loss_price=(
            str(position.stop_loss_price) if position.stop_loss_price is not None else None
        ),
        take_profit_price=(
            str(position.take_profit_price) if position.take_profit_price is not None else None
        ),
        profit=str(position.profit),
        swap=str(position.swap),
    )


def build_paper_portfolio_panel(
    *,
    journal_path: Path | None,
    paper_lite_settings_path: Path | None,
    instrument_specs: InstrumentSpecStore,
    canonical_symbol: str,
    expected_spec_version: str | None,
) -> PaperPortfolioPanelState:
    if journal_path is None or paper_lite_settings_path is None:
        return _no_evidence("PAPER_LITE journal/settings path not configured for this dashboard")
    if not journal_path.exists():
        return _no_evidence("no PAPER_LITE journal has been written yet")
    if not paper_lite_settings_path.exists():
        return _no_evidence(f"{paper_lite_settings_path} does not exist")

    try:
        settings = load_paper_lite_settings(paper_lite_settings_path)
    except (ValueError, OSError, yaml.YAMLError) as error:
        return _degraded(f"PAPER_LITE settings could not be read: {error}")

    spec = instrument_specs.latest(canonical_symbol=canonical_symbol)
    if spec is None:
        return _no_evidence("no real read-only instrument spec is stored yet")
    if expected_spec_version is None or spec.spec_version != expected_spec_version:
        return _degraded(
            "the latest observed instrument spec does not match the owner-approved "
            "expected_spec_version pin -- replay would not be trustworthy"
        )

    try:
        broker = DurablePaperBroker(
            journal_path,
            spec,
            starting_balance=settings.starting_balance,
            account_currency=settings.account_currency,
            leverage=settings.leverage,
        )
    except (PaperJournalError, ValueError) as error:
        return _degraded(f"PAPER_LITE journal could not be replayed: {error}")

    positions = tuple(_position_row(position) for position in broker.positions())
    return PaperPortfolioPanelState(
        status="OK", detail=None, portfolio=broker.portfolio_view(), positions=positions
    )
