"""`scripts/deterministic_canary_agent.py`'s pure logic, in isolation --

no HTTP server, no DB, no MT5. The end-to-end wire contract itself is
proven separately (this fixture answered a real `HttpNeutralAgentClient
.decide()` call correctly during the READY smoke test); these tests pin
the deterministic derivation rules so a future edit cannot silently
change them.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from scripts.deterministic_canary_agent import _build_no_trade, _build_proposal, _claim_one_shot

from crumblr.agent_gateway.market_context import (
    AgentInstrumentFacts,
    AgentMarketContextProvenance,
    AgentMarketContextV1,
    AgentMarketData,
    AgentPlatformState,
)
from crumblr.domain.enums import (
    DataQuality,
    EntryType,
    KillSwitchState,
    ReconciliationStatus,
    SessionState,
    Side,
)
from crumblr.domain.timeutils import utc_now

_AGENT_ID = uuid4()
_ASSIGNMENT_ID = uuid4()
_STRATEGY_ARTIFACT_ID = uuid4()


def _context(**overrides: object) -> AgentMarketContextV1:
    now = utc_now()
    fields: dict[str, object] = {
        "provenance": AgentMarketContextProvenance(
            context_id=uuid4(),
            content_hash="test-content-hash",
            assignment_id=_ASSIGNMENT_ID,
            strategy_artifact_id=_STRATEGY_ARTIFACT_ID,
            strategy_artifact_hash="owner-demo-execution-canary-fixture-v1",
            issued_at_utc=now,
            expires_at_utc=now.replace(year=now.year + 1),
        ),
        "market": AgentMarketData(
            canonical_symbol="EUR/USD",
            timeframe="M5",
            market_snapshot_id=uuid4(),
            event_time_utc=now,
            bid=Decimal("1.15400"),
            ask=Decimal("1.15412"),
            spread_points=12,
            data_quality=DataQuality.GOOD,
            bars=(),
            source_bar_ids=(),
        ),
        "instrument": AgentInstrumentFacts(
            broker_symbol="EURUSD",
            digits=5,
            point=Decimal("0.00001"),
            tick_size=Decimal("0.00001"),
            stops_level=0,
            volume_min=Decimal("0.01"),
            volume_max=Decimal("100"),
            volume_step=Decimal("0.01"),
            spec_version="test-spec-version",
        ),
        "platform_state": AgentPlatformState(
            session_state=SessionState.OPEN,
            safety_state=KillSwitchState.RUNNING,
            reconciliation_status=ReconciliationStatus.MATCHED,
            feature_snapshot_id=uuid4(),
            open_position_count=0,
            open_risk_fraction=Decimal("0"),
        ),
    }
    fields.update(overrides)
    return AgentMarketContextV1(**fields)  # type: ignore[arg-type]


class TestBuildProposal:
    def test_buy_proposal_echoes_context_identity_exactly(self) -> None:
        context = _context()
        proposal = _build_proposal(
            context,
            agent_id=_AGENT_ID,
            side=Side.BUY,
            stop_distance_points=100,
            target_distance_points=200,
            requested_risk_fraction=Decimal("0.001"),
            reason_code="OWNER_DEMO_EXECUTION_CANARY",
        )
        assert proposal.agent_id == _AGENT_ID
        assert proposal.assignment_id == context.provenance.assignment_id
        assert proposal.context_hash == context.provenance.content_hash
        assert proposal.strategy_artifact_hash == context.provenance.strategy_artifact_hash
        assert proposal.side is Side.BUY
        assert proposal.entry_type is EntryType.MARKET
        assert proposal.reason_codes == ("OWNER_DEMO_EXECUTION_CANARY",)
        assert proposal.evidence_refs == ()
        assert proposal.requested_risk_fraction == Decimal("0.001")

    def test_buy_uses_ask_and_places_stop_below_target_above(self) -> None:
        context = _context()
        proposal = _build_proposal(
            context,
            agent_id=_AGENT_ID,
            side=Side.BUY,
            stop_distance_points=100,
            target_distance_points=200,
            requested_risk_fraction=Decimal("0.001"),
            reason_code="OWNER_DEMO_EXECUTION_CANARY",
        )
        assert proposal.reference_price == context.market.ask
        assert proposal.stop_loss_price == context.market.ask - Decimal("0.00100")
        assert proposal.take_profit_price == context.market.ask + Decimal("0.00200")
        assert proposal.stop_loss_price < proposal.reference_price < proposal.take_profit_price

    def test_sell_uses_bid_and_places_stop_above_target_below(self) -> None:
        context = _context()
        proposal = _build_proposal(
            context,
            agent_id=_AGENT_ID,
            side=Side.SELL,
            stop_distance_points=100,
            target_distance_points=200,
            requested_risk_fraction=Decimal("0.001"),
            reason_code="OWNER_DEMO_EXECUTION_CANARY",
        )
        assert proposal.reference_price == context.market.bid
        assert proposal.take_profit_price < proposal.reference_price < proposal.stop_loss_price

    def test_stop_distance_never_below_the_brokers_own_stops_level_floor(self) -> None:
        context = _context(
            instrument=AgentInstrumentFacts(
                broker_symbol="EURUSD",
                digits=5,
                point=Decimal("0.00001"),
                tick_size=Decimal("0.00001"),
                stops_level=500,  # deliberately larger than the requested 100-point stop
                volume_min=Decimal("0.01"),
                volume_max=Decimal("100"),
                volume_step=Decimal("0.01"),
                spec_version="test-spec-version",
            )
        )
        proposal = _build_proposal(
            context,
            agent_id=_AGENT_ID,
            side=Side.BUY,
            stop_distance_points=100,
            target_distance_points=200,
            requested_risk_fraction=Decimal("0.001"),
            reason_code="OWNER_DEMO_EXECUTION_CANARY",
        )
        stop_distance = (context.market.ask - proposal.stop_loss_price) / context.instrument.point
        assert stop_distance > 500

    def test_expires_after_submitted(self) -> None:
        context = _context()
        proposal = _build_proposal(
            context,
            agent_id=_AGENT_ID,
            side=Side.BUY,
            stop_distance_points=100,
            target_distance_points=200,
            requested_risk_fraction=Decimal("0.001"),
            reason_code="OWNER_DEMO_EXECUTION_CANARY",
        )
        assert proposal.expires_at_utc > proposal.submitted_at_utc


class TestBuildNoTrade:
    def test_echoes_context_identity(self) -> None:
        context = _context()
        decision = _build_no_trade(
            context, agent_id=_AGENT_ID, reason_code="OWNER_DEMO_EXECUTION_CANARY_ALREADY_USED"
        )
        assert decision.agent_id == _AGENT_ID
        assert decision.assignment_id == context.provenance.assignment_id
        assert decision.context_hash == context.provenance.content_hash
        assert decision.reason_codes == ("OWNER_DEMO_EXECUTION_CANARY_ALREADY_USED",)


class TestClaimOneShot:
    def test_first_claim_succeeds_second_fails(self, tmp_path: Path) -> None:
        marker = tmp_path / "nested" / "used.marker"
        assert _claim_one_shot(marker) is True
        assert marker.exists()
        assert _claim_one_shot(marker) is False

    def test_claim_is_exact_no_substring_or_pattern_matching(self, tmp_path: Path) -> None:
        other = tmp_path / "used.marker.bak"
        other.write_text("not the real marker")
        marker = tmp_path / "used.marker"
        assert _claim_one_shot(marker) is True
