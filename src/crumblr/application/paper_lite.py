"""PAPER_LITE application orchestration.

This is a deliberately narrow product/integration path:

    trusted read-only market snapshot -> neutral external-agent context
    -> AgentGateway -> Core Risk -> strategy-neutral platform Policy
    -> explicit external-Supervisor skip -> DurablePaperBroker

The module never imports the real MT5 execution adapter. The concrete paper
broker constructs :class:`SimulatedBroker` internally, so changing a flag or
passing a different adapter cannot turn this path into broker submission.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

import yaml
from pydantic import Field, model_validator
from sqlalchemy.engine import make_url

from crumblr.agent_gateway.contracts import (
    NoTradeDecision,
    PolicyHints,
    TradeProposal,
    TradingAssignment,
)
from crumblr.agent_gateway.decision_path import (
    AgentDecisionPathResult,
    PortfolioSnapshot,
    evaluate_agent_trade_intent,
)
from crumblr.agent_gateway.evidence import build_agent_context_evidence
from crumblr.agent_gateway.gateway import AgentDecisionOutcomeResult, AgentGateway
from crumblr.agent_gateway.market_context import (
    AgentMarketContextV1,
    build_agent_market_context_v1,
)
from crumblr.application.recording import RunRecorder
from crumblr.config import (
    AccountGuardConfig,
    ConfigSection,
    IntradayConfig,
    PlatformConfig,
    RiskConfig,
)
from crumblr.domain.enums import (
    EntryType,
    Environment,
    IncidentStatus,
    ReasonCode,
    ReconciliationStatus,
    RiskVerdict,
    SupervisorVerdict,
)
from crumblr.domain.events import SystemHalted
from crumblr.domain.hashing import fingerprint
from crumblr.domain.models import ApprovedOrder, ExecutionResult, InstrumentSpec, MarketSnapshot
from crumblr.domain.money import ZERO, ExactDecimal
from crumblr.domain.timeutils import UtcDatetime, utc_now
from crumblr.mt5_gateway.simulated import ClosedTrade
from crumblr.persistence.paper_lite import (
    PAPER_LITE_INCIDENT_CLEAR_ASSERTED,
    SUPERVISOR_SKIPPED_PAPER_MODE,
    DurablePaperBroker,
    PaperPortfolioView,
)
from crumblr.risk import session
from crumblr.risk.kill_switch import EquityLedger, KillSwitch
from crumblr.risk.session import RiskLedgerLock, RiskSessionStore
from crumblr.trading_agent.sessions import (
    NEW_YORK,
    WEEK_CLOSE_HOUR_ET,
    is_market_open,
    trading_day,
)

PAPER_LITE_MODE: Literal["PAPER_LITE"] = "PAPER_LITE"
PAPER_LITE_POLICY_VERSION = "owner-risk-policy-v1-paper-lite"
PAPER_LITE_DATABASE_NAME = "crumblr_test_dev3"

OWNER_MAX_RISK_PER_TRADE = Decimal("0.02")
OWNER_MAX_OPEN_RISK = Decimal("0.03")
OWNER_MAX_DAILY_LOSS = Decimal("0.04")
OWNER_MAX_DRAWDOWN = Decimal("0.08")


class PaperLiteConfigurationError(ValueError):
    """The runtime configuration would weaken or mislabel PAPER_LITE."""


class PaperLiteSafetyError(RuntimeError):
    """The paper portfolio cannot continue honestly without operator action."""


def require_paper_lite_database_url(url: str) -> str:
    """Refuse every database except the track-isolated PAPER_LITE database."""

    if make_url(url).database != PAPER_LITE_DATABASE_NAME:
        raise PaperLiteConfigurationError(
            f"PAPER_LITE requires the dedicated {PAPER_LITE_DATABASE_NAME} database"
        )
    return url


@dataclass(frozen=True)
class PaperLiteIncidentClearAssertion:
    """Operator-bound capability for asserting CLEAR in this paper process."""

    operator: str
    note: str
    asserted_at_utc: UtcDatetime

    def __post_init__(self) -> None:
        if not self.operator.strip():
            raise PaperLiteConfigurationError("incident-CLEAR assertion requires an operator")
        if not self.note.strip():
            raise PaperLiteConfigurationError("incident-CLEAR assertion requires an audit note")
        if self.asserted_at_utc.utcoffset() is None:
            raise PaperLiteConfigurationError(
                "incident-CLEAR assertion timestamp must be timezone-aware"
            )

    @property
    def assertion_id(self) -> UUID:
        return uuid5(
            NAMESPACE_URL,
            f"crumblr:paper-lite:incident-clear:{fingerprint(self.audit_payload)}",
        )

    @property
    def audit_payload(self) -> dict[str, str]:
        return {
            "operator": self.operator,
            "note": self.note,
            "asserted_at_utc": self.asserted_at_utc.isoformat(),
        }

    def record(self, broker: DurablePaperBroker) -> None:
        broker.record_audit_fact(
            PAPER_LITE_INCIDENT_CLEAR_ASSERTED,
            correlation_id=self.assertion_id,
            detail=json.dumps(self.audit_payload, sort_keys=True, separators=(",", ":")),
        )


class PaperLiteSettings(ConfigSection):
    """Explicit, secret-free runtime settings loaded from ``paper_lite.yaml``."""

    mode: Literal["PAPER_LITE"]
    starting_balance: Annotated[ExactDecimal, Field(gt=ZERO)]
    journal_path: Path
    safety_latch_path: Path
    account_currency: Annotated[str, Field(min_length=3, max_length=8)]
    leverage: int = Field(gt=0)
    operational_max_open_positions: int = Field(gt=1)
    max_risk_per_trade: ExactDecimal
    max_open_risk: ExactDecimal
    max_daily_loss: ExactDecimal
    max_drawdown: ExactDecimal
    friday_last_entry_minutes_before_close: int = Field(ge=0)
    friday_flatten_minutes_before_close: int = Field(ge=0)

    @model_validator(mode="after")
    def _owner_policy_is_exact(self) -> PaperLiteSettings:
        expected = (
            OWNER_MAX_RISK_PER_TRADE,
            OWNER_MAX_OPEN_RISK,
            OWNER_MAX_DAILY_LOSS,
            OWNER_MAX_DRAWDOWN,
        )
        actual = (
            self.max_risk_per_trade,
            self.max_open_risk,
            self.max_daily_loss,
            self.max_drawdown,
        )
        if actual != expected:
            raise ValueError(f"PAPER_LITE must use Owner Risk Policy v1: {expected!r}")
        if self.friday_last_entry_minutes_before_close != 15:
            raise ValueError("PAPER_LITE Friday last-entry offset must be 15 minutes")
        if self.friday_flatten_minutes_before_close != 5:
            raise ValueError("PAPER_LITE Friday flatten offset must be 5 minutes")
        return self

    def platform_config(self, base: PlatformConfig) -> PlatformConfig:
        """Apply Owner Policy v1 to a PAPER base without enabling submission.

        Core's current ``IntradayPolicy`` means *every* daily rollover. Owner
        Policy v1 means only the Friday/weekend boundary. It is disabled here
        and the temporary PAPER_LITE-only weekend guard below enforces the
        owner rule using Core's canonical New York market clock. PL-003 tracks
        replacement by Dev 1's shared Core seam.
        """

        risk = RiskConfig(
            max_risk_per_trade=self.max_risk_per_trade,
            max_open_risk=self.max_open_risk,
            max_daily_loss=self.max_daily_loss,
            max_drawdown=self.max_drawdown,
            max_orders_per_hour=base.risk.max_orders_per_hour,
            max_open_positions=self.operational_max_open_positions,
            min_stop_distance_points=base.risk.min_stop_distance_points,
            approved_config_version=None,
        )
        execution = base.execution.model_copy(
            update={
                "submission_enabled": False,
                "feedback_2_0_approved": False,
                "flatten_submission_enabled": False,
            }
        )
        account_guard = AccountGuardConfig(
            expected_server="Crumblr-PAPER_LITE",
            expected_login=None,
            require_demo_account=True,
            expected_currency=self.account_currency,
            expected_leverage=self.leverage,
        )
        intraday = IntradayConfig(
            enabled=False,
            last_entry_minutes_before_close=self.friday_last_entry_minutes_before_close,
            flatten_minutes_before_close=self.friday_flatten_minutes_before_close,
        )
        return base.model_copy(
            update={
                "environment": Environment.PAPER,
                "risk": risk,
                "execution": execution,
                "account_guard": account_guard,
                "intraday": intraday,
                "live_trading_acknowledged": False,
            }
        )


def load_paper_lite_settings(path: Path) -> PaperLiteSettings:
    """Load a dedicated PAPER_LITE file; malformed/extra keys fail closed."""

    with path.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    if not isinstance(raw, dict):
        raise PaperLiteConfigurationError(f"{path} must contain a YAML mapping")
    return PaperLiteSettings.model_validate(raw)


class PaperLiteSessionPhase(StrEnum):
    OPEN = "OPEN"
    NO_NEW_ENTRIES = "NO_NEW_ENTRIES"
    FLATTEN_REQUIRED = "FLATTEN_REQUIRED"
    CLOSED = "CLOSED"


def paper_lite_session_phase(
    moment: UtcDatetime,
    *,
    last_entry_offset: timedelta = timedelta(minutes=15),
    flatten_offset: timedelta = timedelta(minutes=5),
) -> PaperLiteSessionPhase:
    """Owner Policy v1's weekday-overnight / Friday-only boundary.

    ``NEW_YORK``, ``WEEK_CLOSE_HOUR_ET`` and ``is_market_open`` are Core's
    canonical market facts. No UTC close hour is copied here, so DST remains
    correct. This narrow guard is temporary integration code pending PL-003.
    """

    if last_entry_offset < flatten_offset:
        raise ValueError("last-entry offset must not be closer than flatten offset")
    if not is_market_open(moment):
        return PaperLiteSessionPhase.CLOSED
    local = moment.astimezone(NEW_YORK)
    if local.weekday() != 4:
        return PaperLiteSessionPhase.OPEN
    close = datetime.combine(local.date(), time(WEEK_CLOSE_HOUR_ET, 0), tzinfo=NEW_YORK).astimezone(
        moment.tzinfo
    )
    if moment >= close - flatten_offset:
        return PaperLiteSessionPhase.FLATTEN_REQUIRED
    if moment >= close - last_entry_offset:
        return PaperLiteSessionPhase.NO_NEW_ENTRIES
    return PaperLiteSessionPhase.OPEN


class PaperLiteTradingAgent(Protocol):
    """External/toy Agent adapter; it receives only neutral Crumblr context."""

    @property
    def agent_id(self) -> UUID: ...

    @property
    def credential_secret(self) -> str: ...

    def decide(self, context: AgentMarketContextV1) -> TradeProposal | NoTradeDecision: ...


@dataclass(frozen=True)
class PaperLitePortfolioProvider:
    broker: DurablePaperBroker

    def current(self) -> PortfolioSnapshot:
        return PortfolioSnapshot(
            account=self.broker.account(),
            open_positions=self.broker.positions(),
            reconciliation_status=ReconciliationStatus.MATCHED,
        )


class PaperLiteOutcomeType(StrEnum):
    ALREADY_PROCESSED = "ALREADY_PROCESSED"
    NO_TRADE = "NO_TRADE"
    GATEWAY_REJECTED = "GATEWAY_REJECTED"
    SESSION_BLOCKED = "SESSION_BLOCKED"
    EXACT_OPEN_RISK_UNAVAILABLE = "EXACT_OPEN_RISK_UNAVAILABLE"
    RISK_BLOCKED = "RISK_BLOCKED"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    PAPER_ORDER_CHECK_BLOCKED = "PAPER_ORDER_CHECK_BLOCKED"
    PAPER_FILLED = "PAPER_FILLED"


@dataclass(frozen=True)
class PaperLiteOutcome:
    outcome_type: PaperLiteOutcomeType
    context: AgentMarketContextV1 | None
    gateway_result: AgentDecisionOutcomeResult | None
    portfolio: PaperPortfolioView
    decision_path: AgentDecisionPathResult | None = None
    execution_result: ExecutionResult | None = None
    closed_trades: tuple[ClosedTrade, ...] = ()
    detail: str | None = None


class PaperLiteOrchestrator:
    """One PAPER_LITE market observation and external-agent decision at a time."""

    def __init__(
        self,
        config: PlatformConfig,
        *,
        settings: PaperLiteSettings,
        assignment: TradingAssignment,
        agent: PaperLiteTradingAgent,
        gateway: AgentGateway,
        broker: DurablePaperBroker,
        recorder: RunRecorder,
        session_store: RiskSessionStore,
        # Forced by ADR-021's widened `evaluate_agent_trade_intent` signature
        # -- threaded through to the two calls below unchanged. This class's
        # own `_recover_risk_session()`/`_persist_risk_session()` pair does
        # *not* acquire this lock (AG-023, `review/AGENT_FEEDBACK.md`):
        # whether/how PAPER_LITE's separate recover/persist cycle should is
        # a Dev-3 design decision, not made here.
        risk_ledger_lock: RiskLedgerLock,
        kill_switch: KillSwitch,
        code_commit: str,
        incident_clear_assertion: PaperLiteIncidentClearAssertion | None = None,
        clock: Callable[[], UtcDatetime] = utc_now,
    ) -> None:
        _validate_paper_lite_platform_config(config, settings)
        if assignment.environment is not Environment.PAPER:
            raise PaperLiteConfigurationError("PAPER_LITE assignment must target PAPER")
        if assignment.allowed_agent_id != agent.agent_id:
            raise PaperLiteConfigurationError("PAPER_LITE Agent does not own the assignment")
        self._config = config
        self._settings = settings
        self._assignment = assignment
        self._agent = agent
        self._gateway = gateway
        self._broker = broker
        self._recorder = recorder
        self._session_store = session_store
        self._risk_ledger_lock = risk_ledger_lock
        self._kill_switch = kill_switch
        self._code_commit = code_commit
        self._incident_clear_assertion = incident_clear_assertion
        self._clock = clock
        self._risk_ledger: EquityLedger | None = None
        self._risk_trading_day: date | None = None
        self._risk_recorded_at: UtcDatetime | None = None
        if incident_clear_assertion is not None:
            incident_clear_assertion.record(broker)

    def process(
        self,
        snapshot: MarketSnapshot,
        spec: InstrumentSpec,
        *,
        incident_status: IncidentStatus = IncidentStatus.UNKNOWN,
    ) -> PaperLiteOutcome:
        """Advance paper state, obtain an Agent decision and evaluate it."""

        if spec.canonical_symbol != self._assignment.canonical_symbol:
            raise PaperLiteSafetyError("instrument spec does not match the assignment symbol")
        if snapshot.symbol != self._assignment.canonical_symbol:
            raise PaperLiteSafetyError("market snapshot does not match the assignment symbol")
        if snapshot.timeframe != self._assignment.timeframe:
            raise PaperLiteSafetyError("market snapshot timeframe does not match the assignment")
        if snapshot.symbol_spec_version != spec.spec_version:
            raise PaperLiteSafetyError("market snapshot and instrument spec versions disagree")
        if incident_status is IncidentStatus.CLEAR and self._incident_clear_assertion is None:
            raise PaperLiteSafetyError(
                "IncidentStatus.CLEAR requires an operator-bound PAPER_LITE assertion"
            )

        if self._broker.has_market_observation:
            self._recover_risk_session(snapshot)
        closed_trades = self._broker.advance_snapshot(snapshot)
        if self._risk_ledger is None:
            self._recover_risk_session(snapshot)
        now = self._clock()
        self._risk_recorded_at = snapshot.received_time_utc
        session_phase = paper_lite_session_phase(
            now,
            last_entry_offset=timedelta(
                minutes=self._settings.friday_last_entry_minutes_before_close
            ),
            flatten_offset=timedelta(minutes=self._settings.friday_flatten_minutes_before_close),
        )
        if session_phase is PaperLiteSessionPhase.FLATTEN_REQUIRED and self._broker.positions():
            flatten_id = uuid5(
                NAMESPACE_URL,
                f"crumblr:paper-lite:friday-flatten:{now.astimezone(NEW_YORK).date()}",
            )
            closed_count_before = len(self._broker.closed_trades)
            flattened_tickets = self._broker.flatten_all(
                flatten_request_id=flatten_id,
                reason="owner_policy_v1_friday_flatten",
            )
            flattened_trades = self._broker.closed_trades[closed_count_before:]
            if tuple(trade.ticket for trade in flattened_trades) != flattened_tickets:
                raise PaperLiteSafetyError(
                    "Friday flatten result does not match the paper closed-trade ledger"
                )
            closed_trades = (*closed_trades, *flattened_trades)
        elif session_phase is PaperLiteSessionPhase.CLOSED and self._broker.positions():
            self._trip_risk_session(
                (ReasonCode.OVERNIGHT_EXPOSURE,),
                snapshot,
                "paper exposure reached a closed weekend without a confirmed Friday flatten",
            )
            self._persist_risk_session()
            raise PaperLiteSafetyError(
                "paper exposure reached a closed weekend without a confirmed Friday flatten"
            )
        self._persist_risk_session()

        latest_bar = snapshot.bars[-1]
        window_id = uuid5(
            NAMESPACE_URL,
            "crumblr:paper-lite:decision-window:"
            f"{self._assignment.assignment_id}:{snapshot.symbol}:{snapshot.timeframe}:"
            f"{latest_bar.open_time_utc.isoformat()}",
        )
        claimed = self._broker.record_audit_fact(
            "PAPER_LITE_DECISION_WINDOW_CLAIMED",
            correlation_id=window_id,
            detail=latest_bar.open_time_utc.isoformat(),
        )
        if not claimed:
            return PaperLiteOutcome(
                outcome_type=PaperLiteOutcomeType.ALREADY_PROCESSED,
                context=None,
                gateway_result=None,
                portfolio=self._broker.portfolio_view(),
                closed_trades=closed_trades,
                detail="this assignment/bar decision window was already claimed",
            )

        account = self._broker.account()
        positions = self._broker.positions()
        open_risk = self._broker.open_risk_assessment()
        portfolio_summary_hash = fingerprint(
            {
                "account": account.model_dump(mode="json"),
                "positions": [position.model_dump(mode="json") for position in positions],
            }
        )
        bundle = self._gateway.publish_context(
            assignment_id=self._assignment.assignment_id,
            symbol=snapshot.symbol,
            market_snapshot_id=snapshot.snapshot_id,
            instrument_spec_version=spec.spec_version,
            portfolio_summary_hash=portfolio_summary_hash,
            session_state=snapshot.session_state,
            data_quality=snapshot.data_quality,
            now=now,
            policy_hints=PolicyHints(
                max_intents_per_hour_hint=self._assignment.max_proposals_per_hour,
                min_stop_distance_points_hint=self._config.risk.min_stop_distance_points,
                session_blackout_active=session_phase is not PaperLiteSessionPhase.OPEN,
                notes=PAPER_LITE_POLICY_VERSION,
            ),
        )
        context = build_agent_market_context_v1(
            context_id=bundle.context_id,
            content_hash=bundle.content_hash,
            assignment_id=self._assignment.assignment_id,
            strategy_artifact_id=self._assignment.strategy_artifact_id,
            strategy_artifact_hash=self._assignment.strategy_artifact_hash,
            issued_at_utc=bundle.issued_at_utc,
            expires_at_utc=bundle.expires_at_utc,
            snapshot=snapshot,
            spec=spec,
            session_state=snapshot.session_state,
            safety_state=self._kill_switch.state,
            reconciliation_status=ReconciliationStatus.MATCHED,
            feature_snapshot_id=bundle.feature_snapshot_id,
            open_position_count=len(positions),
            # The Agent contract represents a flat book as absence; its
            # RiskFraction type intentionally excludes numeric zero.
            open_risk_fraction=open_risk.fraction if positions else None,
            policy_hints=bundle.policy_hints,
        )
        decision = self._agent.decide(context)
        gateway_result = self._submit_to_gateway(decision, now=now)

        if not gateway_result.accepted:
            return self._outcome(
                PaperLiteOutcomeType.GATEWAY_REJECTED,
                context,
                gateway_result,
                closed_trades=closed_trades,
                detail=(gateway_result.reason.value if gateway_result.reason is not None else None),
            )

        features = build_agent_context_evidence(
            symbol=snapshot.symbol,
            computed_at_utc=bundle.issued_at_utc,
            market_snapshot_id=snapshot.snapshot_id,
            instrument_spec_version=spec.spec_version,
            session_state=snapshot.session_state,
            data_quality=snapshot.data_quality,
        )
        if isinstance(decision, NoTradeDecision):
            decision_path = evaluate_agent_trade_intent(
                None,
                outcome_id=gateway_result.outcome_id,
                strategy_version=self._assignment.strategy_artifact_hash,
                snapshot=snapshot,
                spec=spec,
                features=features,
                config=self._config,
                portfolio_state=PaperLitePortfolioProvider(self._broker),
                session_store=self._session_store,
                risk_ledger_lock=self._risk_ledger_lock,
                kill_switch=self._kill_switch,
                recorder=self._recorder,
                environment=Environment.PAPER,
                code_commit=self._code_commit,
                now=now,
                incident_status=incident_status,
            )
            return self._outcome(
                PaperLiteOutcomeType.NO_TRADE,
                context,
                gateway_result,
                decision_path=decision_path,
                closed_trades=closed_trades,
            )

        if self._kill_switch.is_halted:
            detail = ",".join(reason.value for reason in self._kill_switch.active_reasons)
            self._broker.record_audit_fact(
                "PAPER_LITE_SAFETY_HALTED",
                correlation_id=gateway_result.outcome_id,
                detail=detail,
            )
            return self._outcome(
                PaperLiteOutcomeType.RISK_BLOCKED,
                context,
                gateway_result,
                closed_trades=closed_trades,
                detail=detail,
            )

        if session_phase is not PaperLiteSessionPhase.OPEN:
            self._broker.record_audit_fact(
                "PAPER_LITE_SESSION_BLOCKED",
                correlation_id=gateway_result.outcome_id,
                detail=session_phase.value,
            )
            return self._outcome(
                PaperLiteOutcomeType.SESSION_BLOCKED,
                context,
                gateway_result,
                closed_trades=closed_trades,
                detail=session_phase.value,
            )

        # Core owns the exact assessment above. The shared Agent decision path
        # does not consume that seam yet, so never route a second proposal
        # through its legacy position-count approximation.
        if positions:
            self._broker.record_audit_fact(
                "PAPER_LITE_EXACT_OPEN_RISK_UNAVAILABLE",
                correlation_id=gateway_result.outcome_id,
                detail="shared Agent decision path does not consume Core open-risk yet",
            )
            return self._outcome(
                PaperLiteOutcomeType.EXACT_OPEN_RISK_UNAVAILABLE,
                context,
                gateway_result,
                closed_trades=closed_trades,
                detail="shared Agent decision path does not consume Core open-risk yet",
            )

        assert gateway_result.trade_intent is not None
        decision_path = evaluate_agent_trade_intent(
            gateway_result.trade_intent,
            outcome_id=gateway_result.outcome_id,
            strategy_version=self._assignment.strategy_artifact_hash,
            snapshot=snapshot,
            spec=spec,
            features=features,
            config=self._config,
            portfolio_state=PaperLitePortfolioProvider(self._broker),
            session_store=self._session_store,
            risk_ledger_lock=self._risk_ledger_lock,
            kill_switch=self._kill_switch,
            recorder=self._recorder,
            environment=Environment.PAPER,
            code_commit=self._code_commit,
            now=now,
            incident_status=incident_status,
        )
        risk = decision_path.risk_decision
        if risk is None or risk.verdict is not RiskVerdict.PASS:
            return self._outcome(
                PaperLiteOutcomeType.RISK_BLOCKED,
                context,
                gateway_result,
                decision_path=decision_path,
                closed_trades=closed_trades,
            )
        policy = decision_path.supervisor_decision
        if policy is None or policy.verdict is not SupervisorVerdict.APPROVE:
            return self._outcome(
                PaperLiteOutcomeType.POLICY_BLOCKED,
                context,
                gateway_result,
                decision_path=decision_path,
                closed_trades=closed_trades,
            )

        self._broker.record_audit_fact(
            SUPERVISOR_SKIPPED_PAPER_MODE,
            correlation_id=gateway_result.outcome_id,
            detail="external Supervisor omitted; Core Risk and platform Policy approved",
        )
        intent = gateway_result.trade_intent
        assert risk.approved_volume is not None
        assert risk.risk_amount is not None
        assert intent.stop_loss_price is not None
        order = ApprovedOrder(
            order_request_id=uuid5(
                NAMESPACE_URL, f"crumblr:paper-lite:order:{intent.decision_hash}"
            ),
            intent_id=intent.intent_id,
            intent_risk_decision_id=risk.decision_id,
            final_risk_decision_id=None,
            supervisor_decision_id=policy.decision_id,
            broker_symbol=spec.broker_symbol,
            side=intent.side,
            entry_type=intent.entry_type,
            volume=risk.approved_volume,
            price=None if intent.entry_type is EntryType.MARKET else intent.reference_price,
            stop_loss_price=intent.stop_loss_price,
            take_profit_price=intent.take_profit_price,
            max_slippage_points=self._config.execution.max_slippage_points,
            created_at_utc=now,
            expires_at_utc=intent.expires_at_utc,
            environment=Environment.PAPER,
        )
        check = self._broker.order_check(order)
        if not check.accepted:
            self._broker.record_audit_fact(
                "PAPER_LITE_ORDER_CHECK_BLOCKED",
                correlation_id=gateway_result.outcome_id,
                detail=check.comment,
            )
            return self._outcome(
                PaperLiteOutcomeType.PAPER_ORDER_CHECK_BLOCKED,
                context,
                gateway_result,
                decision_path=decision_path,
                closed_trades=closed_trades,
                detail=check.comment,
            )
        execution = self._broker.submit(order, authorized_risk_amount=risk.risk_amount)
        return self._outcome(
            PaperLiteOutcomeType.PAPER_FILLED,
            context,
            gateway_result,
            decision_path=decision_path,
            execution_result=execution,
            closed_trades=closed_trades,
        )

    def _submit_to_gateway(
        self, decision: TradeProposal | NoTradeDecision, *, now: UtcDatetime
    ) -> AgentDecisionOutcomeResult:
        if isinstance(decision, TradeProposal):
            return self._gateway.submit_trade_proposal(
                agent_id=self._agent.agent_id,
                credential_secret=self._agent.credential_secret,
                proposal=decision,
                now=now,
            )
        return self._gateway.submit_no_trade(
            agent_id=self._agent.agent_id,
            credential_secret=self._agent.credential_secret,
            decision=decision,
            now=now,
        )

    def _outcome(
        self,
        outcome_type: PaperLiteOutcomeType,
        context: AgentMarketContextV1,
        gateway_result: AgentDecisionOutcomeResult,
        *,
        decision_path: AgentDecisionPathResult | None = None,
        execution_result: ExecutionResult | None = None,
        closed_trades: tuple[ClosedTrade, ...] = (),
        detail: str | None = None,
    ) -> PaperLiteOutcome:
        self._persist_risk_session()
        return PaperLiteOutcome(
            outcome_type=outcome_type,
            context=context,
            gateway_result=gateway_result,
            portfolio=self._broker.portfolio_view(),
            decision_path=decision_path,
            execution_result=execution_result,
            closed_trades=closed_trades,
            detail=detail,
        )

    def _recover_risk_session(self, snapshot: MarketSnapshot) -> None:
        # AG-023 (`review/AGENT_FEEDBACK.md`): the read is now serialized
        # against LiveDecisionOrchestrator's/decision_path.py's own
        # RiskLedgerLock-protected cycles (ADR-021) via the same lock, so
        # this can no longer observe a torn/uncommitted write from either.
        # Does not by itself make this method's later, separate
        # `_persist_risk_session()` call part of the same atomic
        # transaction -- see that method's own docstring for the honest
        # remaining gap.
        account = self._broker.account()
        positions = self._broker.positions()
        with self._risk_ledger_lock.held(self._assignment.canonical_symbol) as connection:
            record = self._session_store.load_latest(connection=connection)
        market_day = trading_day(snapshot.event_time_utc)

        if record.is_known and record.state is None and positions:
            self._risk_ledger = EquityLedger(starting_equity=account.equity)
            self._risk_trading_day = market_day
            self._trip_risk_session(
                (ReasonCode.SAFETY_STATE_UNKNOWN,),
                snapshot,
                "paper journal contains exposure but the durable risk session is absent",
            )
            return

        recovery = session.recover_session(
            record,
            live_equity=account.equity,
            live_open_positions=len(positions),
            market_day=market_day,
            max_daily_loss=self._config.risk.max_daily_loss,
            max_drawdown=self._config.risk.max_drawdown,
        )
        self._risk_ledger = recovery.ledger
        self._risk_trading_day = recovery.trading_day
        if recovery.must_halt:
            # PL-006 (owner Shared-Core work order 2026-09-03 item 3): this
            # now also covers a recovered session whose own recorded worst
            # already exhausted the daily-loss/drawdown limit -
            # `risk/session.py::recover_session()` checks that itself, as
            # normal Core Risk semantics rather than PAPER_LITE-local glue.
            # A prior version of this method re-checked
            # `recovery.ledger.max_session_loss_fraction`/
            # `max_drawdown_fraction` by hand here; that duplicate check is
            # removed now that the shared function does it.
            self._trip_risk_session(recovery.reason_codes, snapshot, recovery.detail)
            return

    def _persist_risk_session(self) -> None:
        """AG-023 (`review/AGENT_FEEDBACK.md`): the write is now serialized
        against the same `RiskLedgerLock` `LiveDecisionOrchestrator`/
        `decision_path.py` hold (ADR-021) -- closes the torn-write class of
        the race and means a later `_recover_risk_session()` call (here or
        in either Core pipeline) is guaranteed to observe this write
        wholly, never partially.

        **Honest remaining gap, not fixed here:** this method and
        `_recover_risk_session()` are two separate, independently-locked
        critical sections, not one atomic recover-update-persist
        transaction the way `LiveDecisionOrchestrator.decide_once()`'s own
        ADR-021 redesign is. A concurrent writer could still commit a
        newer state in the window between this class's own recover call
        and this call, which this call's own write would then silently
        overwrite -- a genuine, narrower, but non-zero lost-update window.
        Closing it fully would mean moving this call to run immediately
        after `_recover_risk_session()` (mirroring the Core redesign
        exactly), but unlike `LiveDecisionOrchestrator`, this class's
        `_broker` can synchronously fill an order *within the same
        `process()` call*, after `_recover_risk_session()` already ran --
        moving the persist earlier would silently stop capturing a
        same-cycle fill's own realized P&L/equity change until the next
        cycle instead. That is a real behavioral tradeoff for whoever owns
        this class's fill-timing design, not something to decide
        unilaterally while closing a locking gap.
        """
        if (
            self._risk_ledger is None
            or self._risk_trading_day is None
            or self._risk_recorded_at is None
        ):
            return
        account = self._broker.account()
        positions = self._broker.positions()
        open_risk = self._broker.open_risk_assessment()
        self._risk_ledger.update(account.equity)
        state = session.snapshot(
            self._risk_ledger,
            trading_day=self._risk_trading_day,
            realized_pnl=self._broker.portfolio_view().realized_profit,
            open_risk_fraction=open_risk.fraction,
            open_position_count=len(positions),
            recorded_at_utc=self._risk_recorded_at,
        )
        with self._risk_ledger_lock.held(self._assignment.canonical_symbol) as connection:
            self._session_store.save(state, connection=connection)

    def _trip_risk_session(
        self,
        reason_codes: tuple[ReasonCode, ...],
        snapshot: MarketSnapshot,
        detail: str | None,
    ) -> None:
        if self._kill_switch.is_halted:
            return
        state_before = self._kill_switch.state
        self._kill_switch.trip(
            reason_codes=reason_codes,
            tripped_by="paper_lite_risk_session_recovery",
            occurred_at_utc=snapshot.received_time_utc,
            detail=detail,
        )
        self._recorder.record(
            SystemHalted(
                state_before=state_before,
                state_after=self._kill_switch.state,
                reason_codes=reason_codes,
                tripped_by="paper_lite_risk_session_recovery",
                detail=detail,
            ),
            correlation_id=snapshot.snapshot_id,
            occurred_at_utc=snapshot.received_time_utc,
            source="paper_lite",
        )
        self._recorder.flush()


def _validate_paper_lite_platform_config(
    config: PlatformConfig, settings: PaperLiteSettings
) -> None:
    if settings.mode != PAPER_LITE_MODE:
        raise PaperLiteConfigurationError("mode must be PAPER_LITE")
    if config.environment is not Environment.PAPER:
        raise PaperLiteConfigurationError("PAPER_LITE requires Environment.PAPER")
    if config.live_trading_acknowledged:
        raise PaperLiteConfigurationError("PAPER_LITE cannot acknowledge live trading")
    if (
        config.execution.submission_enabled
        or config.execution.feedback_2_0_approved
        or config.execution.flatten_submission_enabled
    ):
        raise PaperLiteConfigurationError("every real/flatten submission flag must remain false")
    limits = (
        config.risk.max_risk_per_trade,
        config.risk.max_open_risk,
        config.risk.max_daily_loss,
        config.risk.max_drawdown,
    )
    expected = (
        OWNER_MAX_RISK_PER_TRADE,
        OWNER_MAX_OPEN_RISK,
        OWNER_MAX_DAILY_LOSS,
        OWNER_MAX_DRAWDOWN,
    )
    if limits != expected:
        raise PaperLiteConfigurationError("platform config does not match Owner Risk Policy v1")
