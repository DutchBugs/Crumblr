"""`dashboard.paper_portfolio`: the paper portfolio panel's absence/safety

gating. The real replay numbers themselves (balance/equity/P&L/open-risk)
are `DurablePaperBroker.portfolio_view()`'s own already-tested computation
(`test_paper_lite_broker.py`) -- these tests check only what this module
adds: when it refuses to show a number at all, and why.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from crumblr.dashboard.paper_portfolio import build_paper_portfolio_panel
from crumblr.domain.models import InstrumentSpec
from crumblr.persistence.instrument_specs import InstrumentSpecStore
from crumblr.persistence.paper_lite import DurablePaperBroker
from tests.conftest import make_approved_order, make_instrument_spec, make_snapshot

SPEC = make_instrument_spec()
SETTINGS_YAML = """
mode: PAPER_LITE
starting_balance: '10000'
journal_path: var/paper_lite.journal.jsonl
safety_latch_path: var/paper_lite.safety_latch.json
account_currency: EUR
leverage: 30
operational_max_open_positions: 5
max_risk_per_trade: '0.02'
max_open_risk: '0.03'
max_daily_loss: '0.04'
max_drawdown: '0.08'
friday_last_entry_minutes_before_close: 15
friday_flatten_minutes_before_close: 5
"""


class _StubInstrumentSpecStore(InstrumentSpecStore):
    """Skips the real `InstrumentSpecStore.__init__` (no `Engine` needed) --

    `.latest()` is the only method this module ever calls, so overriding
    just that one is a precise, mypy-safe fake, not a duck-typed workaround.
    """

    def __init__(self, spec: InstrumentSpec | None) -> None:
        self._spec = spec

    def latest(self, *, canonical_symbol: str) -> InstrumentSpec | None:
        return self._spec


def _write_settings(tmp_path: Path, content: str = SETTINGS_YAML) -> Path:
    path = tmp_path / "paper_lite.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def test_no_journal_or_settings_path_configured_is_no_evidence(tmp_path: Path) -> None:
    result = build_paper_portfolio_panel(
        journal_path=None,
        paper_lite_settings_path=_write_settings(tmp_path),
        instrument_specs=_StubInstrumentSpecStore(SPEC),
        canonical_symbol="EUR/USD",
        expected_spec_version=SPEC.spec_version,
    )

    assert result.status == "NO EVIDENCE"
    assert result.portfolio is None
    assert result.positions == ()


def test_a_never_written_journal_file_is_no_evidence_not_an_error(tmp_path: Path) -> None:
    result = build_paper_portfolio_panel(
        journal_path=tmp_path / "never-written.jsonl",
        paper_lite_settings_path=_write_settings(tmp_path),
        instrument_specs=_StubInstrumentSpecStore(SPEC),
        canonical_symbol="EUR/USD",
        expected_spec_version=SPEC.spec_version,
    )

    assert result.status == "NO EVIDENCE"


def test_a_missing_settings_file_is_no_evidence(tmp_path: Path) -> None:
    journal_path = tmp_path / "paper.jsonl"
    DurablePaperBroker(journal_path, SPEC, starting_balance=Decimal("10000"))

    result = build_paper_portfolio_panel(
        journal_path=journal_path,
        paper_lite_settings_path=tmp_path / "does-not-exist.yaml",
        instrument_specs=_StubInstrumentSpecStore(SPEC),
        canonical_symbol="EUR/USD",
        expected_spec_version=SPEC.spec_version,
    )

    assert result.status == "NO EVIDENCE"


def test_malformed_settings_yaml_is_degraded_not_a_crash(tmp_path: Path) -> None:
    journal_path = tmp_path / "paper.jsonl"
    DurablePaperBroker(journal_path, SPEC, starting_balance=Decimal("10000"))
    settings_path = _write_settings(tmp_path, content="not: [valid, paper_lite, settings")

    result = build_paper_portfolio_panel(
        journal_path=journal_path,
        paper_lite_settings_path=settings_path,
        instrument_specs=_StubInstrumentSpecStore(SPEC),
        canonical_symbol="EUR/USD",
        expected_spec_version=SPEC.spec_version,
    )

    assert result.status == "DEGRADED"
    assert result.detail is not None


def test_no_instrument_spec_stored_yet_is_no_evidence(tmp_path: Path) -> None:
    journal_path = tmp_path / "paper.jsonl"
    DurablePaperBroker(journal_path, SPEC, starting_balance=Decimal("10000"))

    result = build_paper_portfolio_panel(
        journal_path=journal_path,
        paper_lite_settings_path=_write_settings(tmp_path),
        instrument_specs=_StubInstrumentSpecStore(None),
        canonical_symbol="EUR/USD",
        expected_spec_version=SPEC.spec_version,
    )

    assert result.status == "NO EVIDENCE"


class TestSpecPinGating:
    """Replaying the fill engine against a spec that is not the

    owner-approved pin could silently reconstruct different historical
    fills -- must refuse, never guess."""

    def test_no_expected_spec_version_configured_is_degraded(self, tmp_path: Path) -> None:
        journal_path = tmp_path / "paper.jsonl"
        DurablePaperBroker(journal_path, SPEC, starting_balance=Decimal("10000"))

        result = build_paper_portfolio_panel(
            journal_path=journal_path,
            paper_lite_settings_path=_write_settings(tmp_path),
            instrument_specs=_StubInstrumentSpecStore(SPEC),
            canonical_symbol="EUR/USD",
            expected_spec_version=None,
        )

        assert result.status == "DEGRADED"

    def test_a_spec_version_mismatch_is_degraded(self, tmp_path: Path) -> None:
        journal_path = tmp_path / "paper.jsonl"
        DurablePaperBroker(journal_path, SPEC, starting_balance=Decimal("10000"))

        result = build_paper_portfolio_panel(
            journal_path=journal_path,
            paper_lite_settings_path=_write_settings(tmp_path),
            instrument_specs=_StubInstrumentSpecStore(SPEC),
            canonical_symbol="EUR/USD",
            expected_spec_version="some-other-pinned-hash",
        )

        assert result.status == "DEGRADED"


def test_a_corrupt_journal_is_degraded_not_a_crash(tmp_path: Path) -> None:
    journal_path = tmp_path / "paper.jsonl"
    journal_path.write_text('{"sequence": 0, "event_type": "PORTFOLIO_CREATED", "payl', "utf-8")

    result = build_paper_portfolio_panel(
        journal_path=journal_path,
        paper_lite_settings_path=_write_settings(tmp_path),
        instrument_specs=_StubInstrumentSpecStore(SPEC),
        canonical_symbol="EUR/USD",
        expected_spec_version=SPEC.spec_version,
    )

    assert result.status == "DEGRADED"


def test_a_real_journal_replays_into_a_genuine_portfolio_view(tmp_path: Path) -> None:
    journal_path = tmp_path / "paper.jsonl"
    broker = DurablePaperBroker(journal_path, SPEC, starting_balance=Decimal("10000"))
    broker.advance_snapshot(make_snapshot())
    broker.submit(
        make_approved_order(final_risk_decision_id=None), authorized_risk_amount=Decimal("50")
    )

    result = build_paper_portfolio_panel(
        journal_path=journal_path,
        paper_lite_settings_path=_write_settings(tmp_path),
        instrument_specs=_StubInstrumentSpecStore(SPEC),
        canonical_symbol="EUR/USD",
        expected_spec_version=SPEC.spec_version,
    )

    assert result.status == "OK"
    assert result.detail is None
    assert result.portfolio is not None
    assert result.portfolio.balance == Decimal("10000")
    assert result.portfolio.open_position_count == 1
    assert len(result.positions) == 1
    assert result.positions[0].broker_symbol == SPEC.broker_symbol
