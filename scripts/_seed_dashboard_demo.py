"""One-off seed script for a manual dashboard verification pass.

Not part of the shipped codebase's normal scripts — writes realistic-shaped
fixture data into whatever `CRUMBLR_DATABASE_URL` points at (this session's
own `crumblr_test_dev1`), plus a PAPER_LITE journal file, so the dashboard
can be looked at with real Agent/Risk/pipeline data instead of an empty
database. Safe to delete after use; not referenced by any other script.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from crumblr.agent_gateway.contracts import (
    AgentIdentity,
    AgentRole,
    AgentStatus,
    ChampionShadowStatus,
    TradingAssignment,
)
from crumblr.domain.enums import (
    BarOrigin,
    EntryType,
    Environment,
    KillSwitchState,
    RiskVerdict,
    Side,
    SupervisorVerdict,
)
from crumblr.domain.models import (
    Bar,
    DecisionCapsule,
    MarketBar,
    MarketTick,
    RiskDecision,
    TradeIntent,
)
from crumblr.domain.models import SupervisorDecision as SupervisorDecisionPayload
from crumblr.persistence.agent_gateway import (
    PostgresAgentIdentityStore,
    PostgresTradingAssignmentStore,
)
from crumblr.persistence.engine import create_db_engine, database_url
from crumblr.persistence.journal import CapsuleStore
from crumblr.persistence.market_data import MarketDataStore, bar_identity, tick_identity
from crumblr.persistence.risk_session import PostgresRiskSessionStore
from crumblr.persistence.safety_state import PostgresSafetyStateStore
from crumblr.risk.safety_state import SafetyState
from crumblr.risk.session import RiskSessionState

REPO_ROOT = Path(__file__).resolve().parent.parent
SYMBOL = "EUR/USD"


def main() -> None:
    engine = create_db_engine(database_url())
    now = datetime.now(UTC)

    tick = MarketTick(
        tick_id=tick_identity(
            source="demo",
            canonical_symbol=SYMBOL,
            event_time_utc=now,
            bid=Decimal("1.08492"),
            ask=Decimal("1.08498"),
        ),
        source="demo",
        canonical_symbol=SYMBOL,
        broker_symbol="EURUSD",
        event_time_utc=now,
        received_time_utc=now,
        bid=Decimal("1.08492"),
        ask=Decimal("1.08498"),
    )
    bars = []
    for i in range(30):
        open_time = now - timedelta(minutes=5 * (30 - i))
        base = Decimal("1.0840") + Decimal("0.0001") * (i % 5)
        bars.append(
            MarketBar(
                bar_id=bar_identity(
                    source="demo", canonical_symbol=SYMBOL, timeframe="M5", open_time_utc=open_time
                ),
                source="demo",
                canonical_symbol=SYMBOL,
                broker_symbol="EURUSD",
                timeframe="M5",
                bar=Bar(
                    open_time_utc=open_time,
                    open=base,
                    high=base + Decimal("0.0006"),
                    low=base - Decimal("0.0004"),
                    close=base + Decimal("0.0002"),
                    tick_volume=120 + i,
                ),
                origin=BarOrigin.BROKER,
                received_time_utc=open_time,
            )
        )
    market = MarketDataStore(engine)
    market.record_ticks([tick])
    market.record_bars(bars)

    PostgresSafetyStateStore(engine).save(
        SafetyState(
            state=KillSwitchState.RUNNING,
            reason_codes=(),
            recorded_at_utc=now,
            tripped_by="",
            detail=None,
        )
    )

    assignment_id = uuid4()
    agent_id = uuid4()
    PostgresTradingAssignmentStore(engine).register(
        TradingAssignment(
            assignment_id=assignment_id,
            assignment_version="assignment-v1",
            allowed_agent_id=agent_id,
            canonical_symbol=SYMBOL,
            timeframe="M5",
            strategy_artifact_id=uuid4(),
            strategy_artifact_hash="sha256:demo-strategy-artifact-hash-abc123",
            valid_from_utc=now - timedelta(days=1),
            valid_until_utc=now + timedelta(days=30),
            max_proposals_per_hour=10,
            allowed_risk_fraction_min=Decimal("0.001"),
            allowed_risk_fraction_max=Decimal("0.01"),
            required_evidence_fields=(),
            supervisor_policy_version="supervisor-policy-v1",
            environment=Environment.PAPER,
            champion_shadow_status=ChampionShadowStatus.SHADOW,
        )
    )
    PostgresAgentIdentityStore(engine).register(
        AgentIdentity(
            agent_id=agent_id,
            role=AgentRole.TRADER,
            runtime_version="neutral-agent-v1",
            service_identity="spiffe://crumblr/agents/neutral-demo",
            status=AgentStatus.ACTIVE,
            registered_at_utc=now,
        )
    )

    intent = TradeIntent(
        intent_id=uuid4(),
        strategy_id="neutral_v1",
        strategy_version="1.0.0",
        symbol=SYMBOL,
        side=Side.BUY,
        created_at_utc=now,
        expires_at_utc=now + timedelta(seconds=30),
        entry_type=EntryType.MARKET,
        reference_price=Decimal("1.08495"),
        stop_loss_price=Decimal("1.08300"),
        take_profit_price=Decimal("1.08900"),
        confidence=0.71,
        reason_codes=("trend_up", "spread_ok"),
        requested_risk_fraction=Decimal("0.005"),
        feature_snapshot_id=uuid4(),
    )
    risk = RiskDecision(
        decision_id=uuid4(),
        intent_id=intent.intent_id,
        verdict=RiskVerdict.PASS,
        reason_codes=(),
        decided_at_utc=now,
        risk_config_version="cfg-v1",
        approved_volume=Decimal("0.05"),
        account_equity=Decimal("10000"),
        stop_distance_points=195,
        risk_amount=Decimal("50"),
    )
    policy = SupervisorDecisionPayload(
        decision_id=uuid4(),
        intent_id=intent.intent_id,
        verdict=SupervisorVerdict.APPROVE,
        reason_codes=(),
        decided_at_utc=now,
        policy_version="external-agent-policy-v1",
    )
    capsule = DecisionCapsule(
        capsule_id=uuid4(),
        occurred_at_utc=now,
        correlation_id=uuid4(),
        canonical_symbol=SYMBOL,
        broker_symbol="EURUSD",
        market_snapshot_id=uuid4(),
        feature_set_version="features-v1",
        feature_values_hash="abc123",
        strategy_version="1.0.0",
        trade_intent=intent,
        risk_config_version="cfg-v1",
        risk_decision=risk,
        supervisor_decision=policy,
        code_commit="deadbeef",
        environment=Environment.PAPER,
    )
    CapsuleStore(engine).seal(capsule)

    PostgresRiskSessionStore(engine).save(
        RiskSessionState(
            trading_day=now.date(),
            session_start_equity=Decimal("10000"),
            current_equity=Decimal("9950"),
            peak_equity=Decimal("10050"),
            realized_pnl=Decimal("-50"),
            max_drawdown_fraction=Decimal("0.0099"),
            max_session_loss_fraction=Decimal("0.005"),
            open_risk_fraction=Decimal("0.005"),
            open_position_count=1,
            recorded_at_utc=now,
        )
    )

    journal_path = REPO_ROOT / "var" / "demo_paper_lite.journal.jsonl"
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        {
            "sequence": 0,
            "event_type": "PORTFOLIO_CREATED",
            "event_key": "portfolio",
            "previous_hash": None,
            "payload": {},
            "record_hash": "x",
        },
        {
            "sequence": 1,
            "event_type": "AUDIT_FACT",
            "event_key": "a1",
            "previous_hash": "x",
            "payload": {
                "fact": "PAPER_LITE_DECISION_WINDOW_CLAIMED",
                "correlation_id": str(uuid4()),
                "detail": now.isoformat(),
            },
            "record_hash": "y",
        },
        {
            "sequence": 2,
            "event_type": "AUDIT_FACT",
            "event_key": "a2",
            "previous_hash": "y",
            "payload": {
                "fact": "SUPERVISOR_SKIPPED_PAPER_MODE",
                "correlation_id": str(uuid4()),
                "detail": "external Supervisor omitted; Core Risk and platform Policy approved",
            },
            "record_hash": "z",
        },
    ]
    with journal_path.open("w", encoding="utf-8") as handle:
        for line in lines:
            handle.write(json.dumps(line) + "\n")

    print(f"Seeded engine at {database_url().split('@')[-1]}")
    print(f"assignment_id={assignment_id}")
    print(f"journal_path={journal_path}")
    engine.dispose()


if __name__ == "__main__":
    main()
