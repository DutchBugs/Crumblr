"""One-shot real-terminal `order_check` evidence run (review 1.24 §8).

    CRUMBLR_DATABASE_URL=...:55432/crumblr_soak \
        uv run python scripts/run_execution_preflight_evidence.py

Review 1.24 formally passed Phase 4 and separately authorized exactly one
controlled, non-sending real-terminal `order_check` evidence run — see
`feedback.1.24.md` §8 for the exact condition list this script exists to
satisfy. It is deliberately a one-shot, not a poller like
`mt5_live_reader.py`/`live_decision.py`: run it, read the printed report,
stop. It does not retry and does not loop.

**No naturally-sealed real capsule exists yet** (F-051 part 2 — a real
Trader decision from real M5 bars — has not happened). This script
therefore constructs and seals exactly one evidence-only `DecisionCapsule`
of its own, so `ExecutionOrchestrator` has something to carry through the
full real preflight chain: claim, eligibility, the preflight gate, one
coherent broker-state observation, reconciliation, FINAL Risk, and finally
a real (non-mutating) `order_check` against the terminal.

That capsule is unambiguously labelled `strategy_id=
"phase4_order_check_evidence"` — distinct from `ict_v1`/`baseline_v1` — so
it can never be mistaken for real Trading Agent output, and does not count
toward F-051 part 2's real-bar-accumulation evidence. Its capsule-level
`strategy_version`/`risk_config_version` are the real, currently-loaded
config's actual values, because `evaluate_execution_eligibility` checks
those for real and there is no way to fake that check.

**This writes permanent rows.** `decision_capsules`, `execution_requests`
and `execution_events` are all write-once / append-only in this schema —
no `UPDATE` grant exists on any of them. That is the point: this is meant
to be a durable, auditable evidence trail, not a throwaway probe.

**Nothing here can send an order.** `OrderCheckMt5Gateway.order_send` /
`.cancel_pending_orders` / `.close_all_positions` are not implemented —
they unconditionally raise `ExecutionDisabledError` — regardless of
anything this script does. If the real terminal refuses `order_check`
because AlgoTrading is disabled, that is recorded honestly and the script
exits; it does not toggle AlgoTrading to force a pass (APP-016,
`feedback.1.24.md` §8's explicit instruction).
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from crumblr.application.bootstrap import build_durable_runtime
from crumblr.application.execution import ExecutionAttemptOutcome, ExecutionOrchestrator
from crumblr.config import load_config
from crumblr.domain.enums import EntryType, Environment, RiskVerdict, Side, SupervisorVerdict
from crumblr.domain.models import DecisionCapsule, RiskDecision, SupervisorDecision, TradeIntent
from crumblr.domain.money import points_to_price
from crumblr.domain.timeutils import utc_now
from crumblr.evaluator.pretrade import POLICY_VERSION
from crumblr.mt5_gateway.client import MissingCredentialsError, Mt5Client, read_credentials
from crumblr.mt5_gateway.execution import OrderCheckMt5Gateway
from crumblr.persistence.broker_state import BrokerStateStore
from crumblr.persistence.engine import DATABASE_URL_ENV_VAR, DEFAULT_TEST_URL
from crumblr.persistence.execution import ExecutionEventStore, ExecutionRequestStore
from crumblr.persistence.flatten import FlattenEventStore, FlattenRequestStore
from crumblr.persistence.instrument_specs import InstrumentSpecStore
from crumblr.persistence.journal import CapsuleStore
from crumblr.risk.trading_window import permits_new_entry, policy_from_config

REPO_ROOT = Path(__file__).resolve().parent.parent

_STRATEGY_ID = "phase4_order_check_evidence"
"""Distinct from every real Trading Agent strategy_id on purpose — this

capsule can never be mistaken for real ict_v1/baseline_v1 output, and does
not count toward F-051 part 2's real-bar-accumulation evidence."""


def _build_evidence_capsule(
    *,
    canonical_symbol: str,
    broker_symbol: str,
    strategy_version: str,
    risk_config_version: str,
    environment: Environment,
    account_equity: Decimal,
    volume_min: Decimal,
    point: Decimal,
    min_stop_distance_points: int,
    reference_price: Decimal,
) -> DecisionCapsule:
    """One evidence-only, fully self-consistent, intent-time-approved capsule.

    Sized at the broker's own minimum lot, stopped comfortably past the
    configured minimum distance. `RiskDecision`/`SupervisorDecision` are
    both PASS/APPROVE by direct construction — this script itself is the
    human decision that this one evidence attempt may proceed, standing in
    for the Risk Engine/Supervisor a real capsule would have already run.
    """
    now = utc_now()
    intent_id = uuid4()
    stop_distance_points = min_stop_distance_points * 2
    stop_offset = points_to_price(stop_distance_points, point)

    intent = TradeIntent(
        intent_id=intent_id,
        strategy_id=_STRATEGY_ID,
        strategy_version=_STRATEGY_ID,
        model_version=None,
        symbol=canonical_symbol,
        side=Side.BUY,
        created_at_utc=now,
        expires_at_utc=now + timedelta(hours=1),
        entry_type=EntryType.MARKET,
        reference_price=reference_price,
        stop_loss_price=reference_price - stop_offset,
        take_profit_price=None,
        confidence=1.0,
        reason_codes=("phase4_order_check_evidence_run",),
        requested_risk_fraction=Decimal("0.001"),
        feature_snapshot_id=uuid4(),
    )

    risk_decision = RiskDecision(
        decision_id=uuid4(),
        intent_id=intent_id,
        verdict=RiskVerdict.PASS,
        reason_codes=(),
        decided_at_utc=now,
        risk_config_version=risk_config_version,
        approved_volume=volume_min,
        account_equity=account_equity,
        stop_distance_points=stop_distance_points,
        risk_amount=account_equity * Decimal("0.001"),
    )

    supervisor_decision = SupervisorDecision(
        decision_id=uuid4(),
        intent_id=intent_id,
        verdict=SupervisorVerdict.APPROVE,
        reason_codes=(),
        decided_at_utc=now,
        policy_version=POLICY_VERSION,
        notes="review 1.24 section 8 real-terminal order_check evidence run "
        "— not a real trading decision",
    )

    return DecisionCapsule(
        capsule_id=uuid4(),
        occurred_at_utc=now,
        correlation_id=uuid4(),
        canonical_symbol=canonical_symbol,
        broker_symbol=broker_symbol,
        market_snapshot_id=uuid4(),
        feature_set_version="phase4_order_check_evidence-no-features",
        feature_values_hash="phase4_order_check_evidence-no-features",
        strategy_version=strategy_version,
        model_version=None,
        trade_intent=intent,
        risk_config_version=risk_config_version,
        risk_decision=risk_decision,
        supervisor_decision=supervisor_decision,
        code_commit="uncommitted-prototype",
        environment=environment,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--canonical-symbol", default="EUR/USD")
    parser.add_argument("--environment", default=Environment.PAPER.value)
    args = parser.parse_args()

    environment = Environment(args.environment)
    config = load_config(environment, config_dir=REPO_ROOT / "config")

    database_url = os.environ.get(DATABASE_URL_ENV_VAR)
    if not database_url:
        print(
            f"error: {DATABASE_URL_ENV_VAR} is not set. This script writes real broker "
            f"data and a real evidence capsule; it must not silently fall back to the "
            f"shared development/test database ({DEFAULT_TEST_URL!r}) — tests/integration "
            f"drops that schema at teardown (D-042). Point {DATABASE_URL_ENV_VAR} at the "
            f"same crumblr_soak database mt5_live_reader.py writes to.",
            file=sys.stderr,
        )
        return 2

    try:
        credentials = read_credentials()
    except MissingCredentialsError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    print("\n" + "=" * 78)
    print("  REAL-TERMINAL order_check EVIDENCE RUN — review 1.24 section 8")
    print("=" * 78)
    print(f"  symbol={args.canonical_symbol} environment={environment.value}")
    print("  EXECUTION STAYS NON-SENDING — order_send is not implemented on")
    print("  OrderCheckMt5Gateway and cannot be reached from this script.\n")

    now = utc_now()
    intraday = policy_from_config(config.intraday)
    if not permits_new_entry(now, intraday):
        print("  refusing to proceed: inside the configured last-entry blackout window.")
        print("  Not constructing an evidence capsule. Run again outside the blackout.\n")
        return 1

    client = Mt5Client()
    client.connect(credentials, terminal_path=os.environ.get("CRUMBLR_MT5_TERMINAL_PATH") or None)
    adapter = OrderCheckMt5Gateway(
        client, config.account_guard, canonical_symbol=args.canonical_symbol
    )

    runtime = build_durable_runtime(
        environment=environment,
        state_file=REPO_ROOT / "var" / "safety_state.json",
        url=database_url,
    )
    print(
        f"  kill_switch={runtime.kill_switch.state.value} "
        f"(new_orders={'disabled' if runtime.kill_switch.is_halted else 'enabled'})"
    )

    instrument_specs = InstrumentSpecStore(runtime.engine)
    spec = instrument_specs.latest(canonical_symbol=args.canonical_symbol)
    if spec is None:
        print(
            "  refusing to proceed: no InstrumentSpec has ever been observed/persisted "
            "for this symbol in this database. mt5_live_reader.py must have run at "
            "least once against this database first.\n"
        )
        runtime.dispose()
        return 1

    ticks = adapter.reader.ticks(
        args.canonical_symbol,
        since=now - timedelta(seconds=30),
        count=100,
        source="run_execution_preflight_evidence",
    )
    if not ticks:
        print("  refusing to proceed: no fresh tick was available from the real terminal.\n")
        runtime.dispose()
        return 1
    tick = ticks[-1]
    account = adapter.account()

    capsule = _build_evidence_capsule(
        canonical_symbol=args.canonical_symbol,
        broker_symbol=spec.broker_symbol,
        strategy_version=config.trading_agent.strategy_version,
        risk_config_version=config.config_version,
        environment=environment,
        account_equity=account.equity,
        volume_min=spec.volume_min,
        point=spec.point,
        min_stop_distance_points=config.risk.min_stop_distance_points,
        reference_price=tick.ask,
    )

    requests = ExecutionRequestStore(runtime.engine)
    events = ExecutionEventStore(runtime.engine)
    flatten_requests = FlattenRequestStore(runtime.engine)
    flatten_events = FlattenEventStore(runtime.engine)
    broker_state = BrokerStateStore(runtime.engine)
    CapsuleStore(runtime.engine).seal(capsule)
    assert capsule.trade_intent is not None
    print(
        f"  sealed evidence capsule={capsule.capsule_id} intent={capsule.trade_intent.intent_id}\n"
    )

    orchestrator = ExecutionOrchestrator(
        config,
        capsules=_SingleCapsuleSource(capsule),
        requests=requests,
        events=events,
        flatten_requests=flatten_requests,
        flatten_events=flatten_events,
        broker_state=broker_state,
        instrument_specs=instrument_specs,
        session_store=runtime.session_store,
        kill_switch=runtime.kill_switch,
        adapter=adapter,
        canonical_symbol=args.canonical_symbol,
        activation_watermark=capsule.occurred_at_utc - timedelta(seconds=1),
        worker_id="run_execution_preflight_evidence",
    )

    outcomes = orchestrator.run_once()
    print("-" * 78)
    print("  Outcome")
    print("-" * 78)
    if not outcomes:
        print("  no outcome recorded — the capsule was not claimed (unexpected).\n")
    for outcome in outcomes:
        _print_outcome(outcome, events)

    runtime.dispose()
    print("\n  Done. This script does not retry. Re-run manually if a fresh attempt")
    print("  is wanted; a new run mints a new intent/capsule and a new order_request_id.\n")
    return 0


class _SingleCapsuleSource:
    """`CapsuleSource` returning exactly the one evidence capsule this run sealed."""

    def __init__(self, capsule: DecisionCapsule) -> None:
        self._capsule = capsule

    def read_all(self, *, environment: Environment | None = None) -> tuple[DecisionCapsule, ...]:
        if environment is not None and self._capsule.environment is not environment:
            return ()
        return (self._capsule,)


def _print_outcome(outcome: ExecutionAttemptOutcome, events: ExecutionEventStore) -> None:
    print(f"  order_request_id = {outcome.order_request_id}")
    print(f"  event_type       = {outcome.event_type.value}")
    if outcome.reason_codes:
        print(f"  reason_codes     = {[code.value for code in outcome.reason_codes]}")
    print("\n  full durable event trail (execution_events):")
    for record in events.events_for(outcome.order_request_id):
        print(f"    {record.event_type.value:<24} {record.occurred_at_utc.isoformat()}")
        if record.reason_codes:
            print(f"        reason_codes = {[code.value for code in record.reason_codes]}")
        if record.detail:
            print(f"        detail       = {record.detail}")
        if record.payload:
            print(f"        payload      = {record.payload}")


if __name__ == "__main__":
    raise SystemExit(main())
