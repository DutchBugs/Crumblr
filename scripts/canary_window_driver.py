"""Canary-mode window driver: ONE controlled Pepperstone DEMO canary from a genuine proposal.

    uv run python scripts/canary_window_driver.py \\
        --canary-permit-id <uuid> --apply-canary-config \\
        --agent-id 760e93be-117c-48a3-b997-f258055ec29b \\
        --assignment-id f98c0396-dd13-4a99-b1e9-b83ea0f15ed7 \\
        --strategy-artifact-hash 81894d6a9c44ddb0433c15f9779fb0a2e25c0e1eccee232e2fd145d72cb498c5 \\
        --expected-account-ref 4f857e6a72f9ad30 --expected-server PepperstoneUK-Demo \\
        --entry-type LIMIT --max-requested-risk-fraction 0.0005 \\
        --window-start 2026-10-06T14:00:00+00:00 --window-end 2026-10-06T15:00:00+00:00

Every flag is required and nothing defaults to something that could widen scope. The decision
logic lives in `crumblr/application/canary_window.py` (unit-tested with fakes); this file only
supplies the real I/O. It NEVER issues, edits or consumes a permit (read-only store access); the
permit is consumed, atomically, by the orchestrator inside `scripts/agent_canary_execution.py`,
the only code path that can reach the real `order_send`. `--check-only` evaluates the start
conditions and exits without running any cycle.

Exit codes: 0 window elapsed with no proposal | 2 refused to start | 3 a proposal was seen but not
submitted (stopped for review) | 4 blocker | 10 a broker submission was attempted (stopped).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import UUID

import yaml
from sqlalchemy import text
from sqlalchemy.engine import Engine

from crumblr.application.canary_window import (
    CanaryWindowSpec,
    CycleInspection,
    CycleResult,
    ExecutionEventRecord,
    ExitCode,
    ReaderEvidence,
    StartInputs,
    run_window,
    start_refusals,
)
from crumblr.config import load_config
from crumblr.domain.enums import EntryType, Environment
from crumblr.domain.models import CanaryPermit, CanaryPermitConsumption
from crumblr.local_admin import credential_store
from crumblr.persistence.canary_permit import CanaryPermitStore
from crumblr.persistence.engine import DATABASE_URL_ENV_VAR, create_db_engine

REPO_ROOT = Path(__file__).resolve().parent.parent
CHILD_SCRIPT = REPO_ROOT / "scripts" / "agent_canary_execution.py"
OVERLAY_PATH = REPO_ROOT / "config" / "agent_canary_demo.yaml"
HEALTH_PATH = REPO_ROOT / "var" / "live_reader_health.json"
EVIDENCE_ROOT = REPO_ROOT / "var" / "canary_evidence"
SYMBOL = "EUR/USD"
TIMEFRAME = "M5"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--canary-permit-id", type=UUID, required=True)
    parser.add_argument(
        "--apply-canary-config",
        action="store_true",
        required=True,
        help="required: this driver only ever runs the canary overlay, never a plain preflight",
    )
    parser.add_argument("--agent-id", type=UUID, required=True)
    parser.add_argument("--assignment-id", type=UUID, required=True)
    parser.add_argument("--strategy-artifact-hash", required=True)
    parser.add_argument("--expected-account-ref", required=True)
    parser.add_argument("--expected-server", required=True)
    parser.add_argument("--entry-type", choices=[e.value for e in EntryType], required=True)
    parser.add_argument("--max-requested-risk-fraction", type=Decimal, required=True)
    parser.add_argument("--window-start", type=datetime.fromisoformat, required=True)
    parser.add_argument("--window-end", type=datetime.fromisoformat, required=True)
    parser.add_argument("--agent-url", default="http://127.0.0.1:8765")
    parser.add_argument("--dashboard-url", default="http://127.0.0.1:8050")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args(argv)
    for name in ("window_start", "window_end"):
        value = getattr(args, name)
        if value.tzinfo is None:
            parser.error(f"--{name.replace('_', '-')} must carry a UTC offset")
    return args


def _json_default(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal | UUID):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, set | frozenset | tuple):
        return list(value)
    return str(value)


def _http_json(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.load(response)


def _secret(name: str) -> str:
    value = credential_store.read(name) or os.environ.get(name)
    if not value:
        raise SystemExit(f"required credential {name} is not available")
    return value


def child_command(args: argparse.Namespace, commit: str) -> list[str]:
    """The one command that may submit. Exactly the existing canary script, `--once`, with the
    canary overlay and this one permit; nothing flatten-related and nothing the operator did not
    restate. Kept separate so a test can validate it against the child's own argument parser."""
    return [
        sys.executable,
        str(CHILD_SCRIPT),
        "--agent-id", str(args.agent_id),
        "--assignment-id", str(args.assignment_id),
        "--agent-url", args.agent_url,
        "--code-commit", commit,
        "--enable-external-supervisor",
        "--once",
        "--apply-canary-config",
        "--canary-permit-id", str(args.canary_permit_id),
    ]  # fmt: skip


class RealWindowIO:
    def __init__(self, args: argparse.Namespace, engine: Engine) -> None:
        self._args = args
        self._engine = engine
        self._permits = CanaryPermitStore(engine)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        self._dir = EVIDENCE_ROOT / f"{stamp}_{str(args.canary_permit_id)[:8]}"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._log = self._dir / "window_log.jsonl"

    # --- clock -----------------------------------------------------------
    def now(self) -> datetime:
        return datetime.now(UTC)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    # --- evidence --------------------------------------------------------
    def record(self, kind: str, **fields: Any) -> None:
        line = json.dumps(
            {"t": self.now().isoformat(), "kind": kind, **fields}, default=_json_default
        )
        print(line, flush=True)
        with self._log.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    # --- reads -----------------------------------------------------------
    def permit_state(self) -> tuple[CanaryPermit | None, CanaryPermitConsumption | None]:
        permit_id = self._args.canary_permit_id
        return self._permits.permit_for(permit_id), self._permits.consumption_for(permit_id)

    def start_inputs(self) -> StartInputs:
        permit, consumption = self.permit_state()
        overlay = yaml.safe_load(OVERLAY_PATH.read_text(encoding="utf-8")) or {}
        config = load_config(Environment.PAPER, config_dir=REPO_ROOT / "config")
        live_ref = live_server = None
        with self._engine.connect() as connection:
            row = connection.execute(
                text(
                    "select account_ref, server, observed_at_utc from broker_account_snapshots "
                    "order by sequence desc limit 1"
                )
            ).first()
        if row is not None and (self.now() - row.observed_at_utc).total_seconds() <= 150:
            live_ref, live_server = row.account_ref, row.server
        agent_hash: str | None
        try:
            agent_hash = _http_json(self._args.agent_url + "/health")["neutral_context_strategy"][
                "strategy_artifact_hash"
            ]
        except Exception:  # unreadable Agent -> unknown -> refusal
            agent_hash = None
        return StartInputs(
            permit=permit,
            consumption=consumption,
            overlay=overlay,
            config_version=config.config_version,
            live_account_ref=live_ref,
            live_account_server=live_server,
            agent_artifact_hash=agent_hash,
            now=self.now(),
        )

    def latest_closed_bar_open(self) -> datetime | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                text(
                    "select max(open_time_utc) as t from market_bars "
                    "where canonical_symbol = :s and timeframe = :tf"
                ),
                {"s": SYMBOL, "tf": TIMEFRAME},
            ).first()
        return None if row is None else row.t

    def reader_evidence(self) -> ReaderEvidence:
        errors: list[str] = []
        kwargs: dict[str, Any] = {}
        now = self.now()

        def age(value: str | None) -> float | None:
            return None if not value else (now - datetime.fromisoformat(value)).total_seconds()

        try:
            health = json.loads(HEALTH_PATH.read_text(encoding="utf-8"))
            broker = health.get("broker_state") or {}
            kwargs.update(
                reader_status=health.get("status"),
                reader_connected=health.get("connected"),
                heartbeat_age_s=age(health.get("heartbeat_at_utc")),
                tick_age_s=age(health.get("last_tick_at_utc")),
                snapshot_age_s=age(broker.get("last_snapshot_at_utc")),
                position_set_state=broker.get("position_set_state"),
                pending_order_set_state=broker.get("pending_order_set_state"),
            )
        except Exception as error:
            errors.append(f"reader_health:{type(error).__name__}")
        try:
            state = _http_json(self._args.dashboard_url + "/api/state")
            kwargs.update(
                dashboard_mt5=state.get("mt5_connectivity"),
                dashboard_feed=state.get("data_feed_state"),
            )
        except Exception as error:
            errors.append(f"dashboard:{type(error).__name__}")
        try:
            agent = _http_json(self._args.agent_url + "/health")
            kwargs.update(
                agent_status=agent.get("status"),
                agent_artifact_hash=agent["neutral_context_strategy"]["strategy_artifact_hash"],
            )
        except Exception as error:
            errors.append(f"agent:{type(error).__name__}")
        return ReaderEvidence(errors=tuple(errors), **kwargs)

    # --- the one place a cycle can run ------------------------------------
    def _child_env(self) -> dict[str, str]:
        env = dict(os.environ)
        for name in ("CRUMBLR_MT5_LOGIN", "CRUMBLR_MT5_PASSWORD", "CRUMBLR_MT5_SERVER"):
            env[name] = _secret(name)
        env[DATABASE_URL_ENV_VAR] = _secret(DATABASE_URL_ENV_VAR)
        env["CRUMBLR_PAPER_LITE_GATEWAY_CREDENTIAL"] = _secret(
            "CRUMBLR_PAPER_LITE_GATEWAY_CREDENTIAL"
        )
        env["CRUMBLR_PAPER_LITE_AGENT_TOKEN"] = _secret("LOCAL_AGENT_SERVICE_TOKEN")
        return env

    def run_cycle(self, bar_open: datetime) -> CycleResult:
        args = self._args
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True
        ).strip()
        command = child_command(args, commit)
        completed = subprocess.run(
            command,
            cwd=REPO_ROOT,
            env=self._child_env(),
            capture_output=True,
            text=True,
            timeout=300,
        )
        log = self._dir / f"cycle_{bar_open.strftime('%H%M')}_{self.now().strftime('%H%M%S')}.log"
        log.write_text(
            f"# exit={completed.returncode}\n--- STDOUT ---\n{completed.stdout}\n"
            f"--- STDERR ---\n{completed.stderr}",
            encoding="utf-8",
        )
        outcome_id = capsule_id = None
        accepted: bool | None = None
        for line in completed.stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                payload = json.loads(line)
            except ValueError:
                continue
            if "gateway_accepted" in payload:
                accepted = bool(payload["gateway_accepted"])
                outcome_id = UUID(payload["outcome_id"])
            elif "sealed_capsule_id" in payload:
                capsule_id = UUID(payload["sealed_capsule_id"])
        return CycleResult(completed.returncode, outcome_id, capsule_id, accepted, log.name)

    def inspect_cycle(self, cycle: CycleResult | None) -> CycleInspection:
        consumption = self._permits.consumption_for(self._args.canary_permit_id)
        outcome_type: str | None = None
        reasons: tuple[str, ...] = ()
        risk = policy = None
        events: list[ExecutionEventRecord] = []
        with self._engine.connect() as connection:
            if cycle is not None and cycle.outcome_id is not None:
                row = connection.execute(
                    text(
                        "select outcome_type, payload from agent_decision_outcomes "
                        "where outcome_id = :i"
                    ),
                    {"i": cycle.outcome_id},
                ).first()
                if row is not None:
                    outcome_type = str(row.outcome_type)
                    reasons = tuple(str(c) for c in (row.payload or {}).get("reason_codes", ()))
            if cycle is not None and cycle.capsule_id is not None:
                capsule = connection.execute(
                    text("select payload from decision_capsules where capsule_id = :i"),
                    {"i": cycle.capsule_id},
                ).first()
                if capsule is not None:
                    body = capsule.payload or {}
                    risk = (body.get("risk_decision") or {}).get("verdict")
                    policy = (body.get("supervisor_decision") or {}).get("verdict")
                for event in connection.execute(
                    text(
                        "select e.event_type, e.occurred_at_utc, e.reason_codes, "
                        "e.detail, e.payload "
                        "from execution_events e join execution_requests r "
                        "on r.order_request_id = e.order_request_id "
                        "where r.capsule_id = :i order by e.sequence"
                    ),
                    {"i": cycle.capsule_id},
                ):
                    events.append(
                        ExecutionEventRecord(
                            event_type=str(event.event_type),
                            occurred_at_utc=event.occurred_at_utc,
                            reason_codes=tuple(str(c) for c in (event.reason_codes or ())),
                            detail=event.detail,
                            payload=event.payload or {},
                        )
                    )
        return CycleInspection(
            outcome_type=outcome_type,
            reason_codes=reasons,
            permit_consumed=consumption is not None,
            execution_events=tuple(events),
            risk_verdict=risk,
            policy_verdict=policy,
        )


def build_spec(args: argparse.Namespace) -> CanaryWindowSpec:
    return CanaryWindowSpec(
        permit_id=args.canary_permit_id,
        agent_id=args.agent_id,
        assignment_id=args.assignment_id,
        strategy_artifact_hash=args.strategy_artifact_hash,
        expected_account_ref=args.expected_account_ref,
        expected_server=args.expected_server,
        entry_type=EntryType(args.entry_type),
        max_requested_risk_fraction=args.max_requested_risk_fraction,
        window_start_utc=args.window_start,
        window_end_utc=args.window_end,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    spec = build_spec(args)
    engine = create_db_engine(_secret(DATABASE_URL_ENV_VAR), connect_timeout_seconds=5)
    try:
        io = RealWindowIO(args, engine)
        if args.check_only:
            refusals = start_refusals(spec, io.start_inputs())
            io.record("check_only", refusals=list(refusals), would_start=not refusals)
            return int(
                ExitCode.REFUSED_TO_START if refusals else ExitCode.WINDOW_ELAPSED_NO_PROPOSAL
            )
        dirty = subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=no"], cwd=REPO_ROOT, text=True
        ).strip()
        if dirty:
            io.record(
                "REFUSED_TO_START", reasons=["TRACKED_FILES_DIRTY_AUDIT_COMMIT_WOULD_BE_UNTRUE"]
            )
            return int(ExitCode.REFUSED_TO_START)
        outcome = run_window(spec, io)
        io.record(
            "exit",
            exit_code=int(outcome.exit_code),
            reason=outcome.reason,
            cycles=outcome.cycles_run,
        )
        return int(outcome.exit_code)
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
