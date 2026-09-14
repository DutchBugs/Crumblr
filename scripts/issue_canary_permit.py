"""Issue exactly one owner-authorized DEMO canary permit (FEEDBACK.2.0

DEMO EXECUTION, Phase B item B8).

    uv run python scripts/issue_canary_permit.py \\
        --login <the real MT5 account login> --server PepperstoneUK-Demo \\
        --agent-id 760e93be-117c-48a3-b997-f258055ec29b \\
        --assignment-id f98c0396-dd13-4a99-b1e9-b83ea0f15ed7 \\
        --strategy-artifact-hash 81894d6a9c44ddb0433c15f9779fb0a2e25c0e1eccee232e2fd145d72cb498c5 \\
        --max-requested-risk-fraction 0.001 \\
        --valid-for-minutes 60 \\
        --issued-by "<operator identity>" \\
        --reason "<why this exact attempt is authorized>"

This is deliberately the only place a `CanaryPermit` is ever constructed
outside a test — a human runs this once, by hand, for one specific,
already-decided attempt. It never auto-selects a "latest" or "any valid"
identity: `--agent-id`/`--assignment-id`/`--strategy-artifact-hash` must
be given explicitly (all three, or none for an internal-strategy canary),
`--login`/`--server` must be given explicitly, and every other field has
no default that could silently authorize more than the operator typed.

`canonical_symbol` is always `EUR/USD` and `entry_type` is always
`MARKET` — `CanaryPermit`'s own model validator enforces this ("the
first canary is EUR/USD only" / "the first canary is MARKET-entry
only"), so there is no flag for either; widening either is a new,
separate, reviewed engineering decision, not a CLI option.

`--login`/`--server` are converted to the same `login_hash`-style
fingerprint `AccountState.login_hash`/`ExecutionConfig
.approved_canary_account_ref` already use
(`fingerprint({"login": ..., "server": ...})[:16]`) — the raw login
number is never itself persisted as `approved_account_ref`, and this
script never prints anything else about the account.

The validity window is capped at 24h by `CanaryPermit`'s own model
validator — this script does not raise that cap; it only lets an
operator choose a shorter one.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

from crumblr.domain.enums import EntryType
from crumblr.domain.hashing import fingerprint
from crumblr.domain.models import CanaryPermit
from crumblr.domain.timeutils import utc_now
from crumblr.persistence.canary_permit import CanaryPermitStore
from crumblr.persistence.engine import DATABASE_URL_ENV_VAR, DEFAULT_TEST_URL, create_db_engine

REPO_ROOT = Path(__file__).resolve().parent.parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--login", type=int, required=True, help="the real MT5 account login")
    parser.add_argument("--server", required=True)
    parser.add_argument(
        "--agent-id",
        type=UUID,
        default=None,
        help="omit together with --assignment-id/--strategy-artifact-hash for an "
        "internal-strategy canary (ict_v1/baseline_v1)",
    )
    parser.add_argument("--assignment-id", type=UUID, default=None)
    parser.add_argument("--strategy-artifact-hash", default=None)
    parser.add_argument(
        "--max-requested-risk-fraction",
        type=Decimal,
        required=True,
        help="the owner-chosen cap for this one attempt -- never inferred from "
        "RiskConfig.max_risk_per_trade",
    )
    parser.add_argument("--valid-for-minutes", type=int, required=True)
    parser.add_argument("--issued-by", required=True, help="explicit operator identity")
    parser.add_argument(
        "--reason", required=True, help="why this exact attempt is authorized, right now"
    )
    parser.add_argument(
        "--permit-id",
        type=UUID,
        default=None,
        help="omit to generate a fresh random id (printed, never auto-selected by any consumer)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    agent_fields = (args.agent_id, args.assignment_id, args.strategy_artifact_hash)
    if any(field is not None for field in agent_fields) and not all(
        field is not None for field in agent_fields
    ):
        print(
            "error: --agent-id/--assignment-id/--strategy-artifact-hash must be given "
            "together (an agent-driven canary) or not at all (an internal-strategy "
            "canary) -- a partial identity binding scopes nothing",
            file=sys.stderr,
        )
        return 2

    database_url = os.environ.get(DATABASE_URL_ENV_VAR)
    if not database_url:
        print(
            f"error: {DATABASE_URL_ENV_VAR} is not set. This issues a real, durable "
            f"canary permit; it must not silently fall back to the shared development/"
            f"test database ({DEFAULT_TEST_URL!r}). Point {DATABASE_URL_ENV_VAR} at the "
            f"same crumblr_soak database the canary's own ExecutionOrchestrator will "
            f"read from.",
            file=sys.stderr,
        )
        return 2

    now = utc_now()
    approved_account_ref = fingerprint({"login": args.login, "server": args.server})[:16]

    permit = CanaryPermit(
        permit_id=args.permit_id or uuid4(),
        approved_account_ref=approved_account_ref,
        expected_server=args.server,
        canonical_symbol="EUR/USD",
        entry_type=EntryType.MARKET,
        agent_id=args.agent_id,
        assignment_id=args.assignment_id,
        strategy_artifact_hash=args.strategy_artifact_hash,
        max_requested_risk_fraction=args.max_requested_risk_fraction,
        issued_by=args.issued_by,
        reason=args.reason,
        issued_at_utc=now,
        valid_until_utc=now + timedelta(minutes=args.valid_for_minutes),
    )

    engine = create_db_engine(database_url)
    try:
        result = CanaryPermitStore(engine).issue(permit)
    finally:
        engine.dispose()

    print("\n" + "=" * 78)
    print("  CANARY PERMIT ISSUED — FEEDBACK.2.0 DEMO EXECUTION")
    print("=" * 78)
    print(f"  permit_id                    = {permit.permit_id}")
    print(f"  inserted (new row)           = {result.inserted}")
    print(f"  approved_account_ref         = {approved_account_ref}  (login/server never printed)")
    print(f"  expected_server              = {permit.expected_server}")
    print(f"  canonical_symbol             = {permit.canonical_symbol}")
    print(f"  entry_type                   = {permit.entry_type.value}")
    print(f"  agent_id                     = {permit.agent_id}")
    print(f"  assignment_id                = {permit.assignment_id}")
    print(f"  strategy_artifact_hash       = {permit.strategy_artifact_hash}")
    print(f"  max_requested_risk_fraction  = {permit.max_requested_risk_fraction}")
    print(f"  issued_by                    = {permit.issued_by}")
    print(f"  reason                       = {permit.reason}")
    print(f"  issued_at_utc                = {permit.issued_at_utc.isoformat()}")
    print(f"  valid_until_utc              = {permit.valid_until_utc.isoformat()}")
    print(
        "\n  This permit_id must be passed explicitly to the execution driver's own "
        "canary configuration -- nothing in this platform ever selects one automatically.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
