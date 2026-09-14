"""`_canary_permit_scope_mismatches` (FEEDBACK.2.0 DEMO EXECUTION) in

isolation — a pure function, no DB, no MT5. Covers every leg named in
the owner work order, including `canonical_symbol`/`entry_type`, which
`CanaryPermit`'s own model validator already pins to `EUR/USD`/`MARKET`
for any permit constructible through the public model today (`"the
first canary is EUR/USD only"`/`"the first canary is MARKET-entry
only"`) — exercised here via `model_construct()` (bypassing that
validator) as a deliberate defense-in-depth check: this function must
independently refuse a mismatch even if a future relaxation of that
model validator ever allowed one through.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

from crumblr.application.execution import (
    CanaryEntrySubmissionConfig,
    _canary_permit_scope_mismatches,
)
from crumblr.domain.enums import EntryType, ReasonCode
from crumblr.domain.models import CanaryPermit
from tests.conftest import FIXED_NOW, make_account_state, make_approved_order, make_intent

_ACCOUNT = make_account_state(login=62_137_706, server="PepperstoneUK-Demo")
_CANARY_SYMBOL = "EUR/USD"
_ORDER = make_approved_order(entry_type=EntryType.MARKET)
_INTENT = make_intent(requested_risk_fraction=Decimal("0.005"))


def _permit(**overrides: Any) -> CanaryPermit:
    fields: dict[str, Any] = {
        "permit_id": uuid4(),
        "approved_account_ref": _ACCOUNT.login_hash,
        "expected_server": _ACCOUNT.server,
        "canonical_symbol": _CANARY_SYMBOL,
        "entry_type": EntryType.MARKET,
        "agent_id": None,
        "assignment_id": None,
        "strategy_artifact_hash": None,
        "max_requested_risk_fraction": Decimal("0.01"),
        "issued_by": "owner",
        "reason": "unit test",
        "issued_at_utc": FIXED_NOW - timedelta(minutes=5),
        "valid_until_utc": FIXED_NOW + timedelta(hours=1),
    }
    fields.update(overrides)
    return CanaryPermit.model_construct(**fields)


def _config(**overrides: object) -> CanaryEntrySubmissionConfig:
    fields: dict[str, object] = {"permit_id": uuid4(), "capsule_id": uuid4()}
    fields.update(overrides)
    return CanaryEntrySubmissionConfig(**fields)  # type: ignore[arg-type]


class TestPermitNotFound:
    def test_none_permit_returns_not_found_only(self) -> None:
        reasons = _canary_permit_scope_mismatches(
            None,
            account=_ACCOUNT,
            canonical_symbol=_CANARY_SYMBOL,
            order=_ORDER,
            intent=_INTENT,
            canary_config=_config(),
            now=FIXED_NOW,
        )
        assert reasons == (ReasonCode.CANARY_PERMIT_NOT_FOUND,)


class TestExactMatchPasses:
    def test_a_fully_matching_permit_and_config_returns_no_mismatches(self) -> None:
        reasons = _canary_permit_scope_mismatches(
            _permit(),
            account=_ACCOUNT,
            canonical_symbol=_CANARY_SYMBOL,
            order=_ORDER,
            intent=_INTENT,
            canary_config=_config(),
            now=FIXED_NOW,
        )
        assert reasons == ()


class TestEachScopeLegBlocksIndependently:
    def test_expired_permit(self) -> None:
        permit = _permit(valid_until_utc=FIXED_NOW - timedelta(seconds=1))
        reasons = _canary_permit_scope_mismatches(
            permit,
            account=_ACCOUNT,
            canonical_symbol=_CANARY_SYMBOL,
            order=_ORDER,
            intent=_INTENT,
            canary_config=_config(),
            now=FIXED_NOW,
        )
        assert ReasonCode.CANARY_PERMIT_EXPIRED in reasons

    def test_account_mismatch(self) -> None:
        permit = _permit(approved_account_ref="not-the-real-account-ref")
        reasons = _canary_permit_scope_mismatches(
            permit,
            account=_ACCOUNT,
            canonical_symbol=_CANARY_SYMBOL,
            order=_ORDER,
            intent=_INTENT,
            canary_config=_config(),
            now=FIXED_NOW,
        )
        assert reasons == (ReasonCode.CANARY_PERMIT_ACCOUNT_MISMATCH,)

    def test_server_mismatch(self) -> None:
        permit = _permit(expected_server="Some-Other-Server")
        reasons = _canary_permit_scope_mismatches(
            permit,
            account=_ACCOUNT,
            canonical_symbol=_CANARY_SYMBOL,
            order=_ORDER,
            intent=_INTENT,
            canary_config=_config(),
            now=FIXED_NOW,
        )
        assert reasons == (ReasonCode.CANARY_PERMIT_SERVER_MISMATCH,)

    def test_symbol_mismatch(self) -> None:
        """Bypasses `CanaryPermit`'s own "EUR/USD only" validator via

        `model_construct()` — see this module's own docstring for why."""
        permit = _permit(canonical_symbol="BTC/USD")
        reasons = _canary_permit_scope_mismatches(
            permit,
            account=_ACCOUNT,
            canonical_symbol=_CANARY_SYMBOL,
            order=_ORDER,
            intent=_INTENT,
            canary_config=_config(),
            now=FIXED_NOW,
        )
        assert reasons == (ReasonCode.CANARY_PERMIT_SYMBOL_MISMATCH,)

    def test_entry_type_mismatch(self) -> None:
        """Bypasses `CanaryPermit`'s own "MARKET-entry only" validator via

        `model_construct()` — see this module's own docstring for why."""
        permit = _permit(entry_type=EntryType.LIMIT)
        reasons = _canary_permit_scope_mismatches(
            permit,
            account=_ACCOUNT,
            canonical_symbol=_CANARY_SYMBOL,
            order=_ORDER,
            intent=_INTENT,
            canary_config=_config(),
            now=FIXED_NOW,
        )
        assert reasons == (ReasonCode.CANARY_PERMIT_ENTRY_TYPE_MISMATCH,)

    def test_agent_mismatch(self) -> None:
        agent_id, assignment_id, strategy_hash = uuid4(), uuid4(), "a" * 64
        permit = _permit(
            agent_id=uuid4(), assignment_id=assignment_id, strategy_artifact_hash=strategy_hash
        )
        reasons = _canary_permit_scope_mismatches(
            permit,
            account=_ACCOUNT,
            canonical_symbol=_CANARY_SYMBOL,
            order=_ORDER,
            intent=_INTENT,
            canary_config=_config(
                agent_id=agent_id,
                assignment_id=assignment_id,
                strategy_artifact_hash=strategy_hash,
            ),
            now=FIXED_NOW,
        )
        assert reasons == (ReasonCode.CANARY_PERMIT_AGENT_MISMATCH,)

    def test_assignment_mismatch(self) -> None:
        agent_id, assignment_id, strategy_hash = uuid4(), uuid4(), "a" * 64
        permit = _permit(
            agent_id=agent_id, assignment_id=uuid4(), strategy_artifact_hash=strategy_hash
        )
        reasons = _canary_permit_scope_mismatches(
            permit,
            account=_ACCOUNT,
            canonical_symbol=_CANARY_SYMBOL,
            order=_ORDER,
            intent=_INTENT,
            canary_config=_config(
                agent_id=agent_id,
                assignment_id=assignment_id,
                strategy_artifact_hash=strategy_hash,
            ),
            now=FIXED_NOW,
        )
        assert reasons == (ReasonCode.CANARY_PERMIT_ASSIGNMENT_MISMATCH,)

    def test_strategy_artifact_hash_mismatch(self) -> None:
        agent_id, assignment_id, strategy_hash = uuid4(), uuid4(), "a" * 64
        permit = _permit(
            agent_id=agent_id, assignment_id=assignment_id, strategy_artifact_hash="b" * 64
        )
        reasons = _canary_permit_scope_mismatches(
            permit,
            account=_ACCOUNT,
            canonical_symbol=_CANARY_SYMBOL,
            order=_ORDER,
            intent=_INTENT,
            canary_config=_config(
                agent_id=agent_id,
                assignment_id=assignment_id,
                strategy_artifact_hash=strategy_hash,
            ),
            now=FIXED_NOW,
        )
        assert reasons == (ReasonCode.CANARY_PERMIT_STRATEGY_ARTIFACT_MISMATCH,)

    def test_risk_fraction_exceeded(self) -> None:
        permit = _permit(max_requested_risk_fraction=Decimal("0.001"))
        intent = make_intent(requested_risk_fraction=Decimal("0.005"))
        reasons = _canary_permit_scope_mismatches(
            permit,
            account=_ACCOUNT,
            canonical_symbol=_CANARY_SYMBOL,
            order=_ORDER,
            intent=intent,
            canary_config=_config(),
            now=FIXED_NOW,
        )
        assert reasons == (ReasonCode.CANARY_PERMIT_RISK_FRACTION_EXCEEDED,)


class TestMultipleFailingLegsAreAllReported:
    def test_two_mismatches_both_appear(self) -> None:
        permit = _permit(
            approved_account_ref="wrong", expected_server="wrong-server-too-long-enough"
        )
        reasons = _canary_permit_scope_mismatches(
            permit,
            account=_ACCOUNT,
            canonical_symbol=_CANARY_SYMBOL,
            order=_ORDER,
            intent=_INTENT,
            canary_config=_config(),
            now=FIXED_NOW,
        )
        assert set(reasons) == {
            ReasonCode.CANARY_PERMIT_ACCOUNT_MISMATCH,
            ReasonCode.CANARY_PERMIT_SERVER_MISMATCH,
        }
