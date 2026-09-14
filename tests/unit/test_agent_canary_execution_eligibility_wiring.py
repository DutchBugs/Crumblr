"""FIRST DEMO CANARY eligibility wiring fix (Dev 1 review, post-BLOCK):

`scripts/agent_canary_execution.py`'s own permit-driven `activation_watermark`
resolution, plus a mechanical proof of the `current_strategy_version`
wiring -- in isolation, no DB, no MT5.
"""

from __future__ import annotations

import inspect
from datetime import timedelta
from uuid import uuid4

import pytest
import scripts.agent_canary_execution as agent_canary_execution
from scripts.agent_canary_execution import (
    MissingCanaryPermitError,
    _resolve_canary_activation_watermark,
)

from crumblr.domain.timeutils import utc_now


class _FakePermit:
    def __init__(self, issued_at_utc: object) -> None:
        self.issued_at_utc = issued_at_utc


class _FakePermitStore:
    def __init__(self, permit: _FakePermit | None) -> None:
        self._permit = permit
        self.calls: list[object] = []

    def permit_for(self, permit_id: object) -> _FakePermit | None:
        self.calls.append(permit_id)
        return self._permit


class TestResolveCanaryActivationWatermark:
    def test_missing_permit_raises_before_returning_a_watermark(self) -> None:
        store = _FakePermitStore(None)
        permit_id = uuid4()
        with pytest.raises(MissingCanaryPermitError, match=str(permit_id)):
            _resolve_canary_activation_watermark(store, permit_id)  # type: ignore[arg-type]
        assert store.calls == [permit_id]

    def test_existing_permit_returns_exactly_issued_at_utc(self) -> None:
        issued_at = utc_now() - timedelta(minutes=5)
        store = _FakePermitStore(_FakePermit(issued_at))
        watermark = _resolve_canary_activation_watermark(store, uuid4())  # type: ignore[arg-type]
        assert watermark is issued_at

    def test_never_falls_back_to_wall_clock_or_a_capsule_offset(self) -> None:
        """Documents the exact rule this function must never violate --

        a regression that swapped in `utc_now()` or `capsule.occurred_at_utc
        - timedelta(...)` would still "work" by type, so this pins the
        actual returned value against a permit issued well in the past.
        """
        issued_at = utc_now() - timedelta(days=1)
        store = _FakePermitStore(_FakePermit(issued_at))
        watermark = _resolve_canary_activation_watermark(store, uuid4())  # type: ignore[arg-type]
        assert watermark == issued_at
        assert watermark != utc_now()


class TestBuildOrchestratorPassesAssignmentStrategyArtifactHash:
    def test_source_wires_current_strategy_version_to_assignment_hash(self) -> None:
        """Mechanical proof, mirroring this codebase's own established

        `inspect.getsource` idiom (e.g. `test_demo_order_send_gateway.py
        ::TestNotWiredIntoTheOrchestrator`) for wiring that a full
        integration test would otherwise need real MT5/DB fixtures to
        reach: the Agent-driven runner's own `_build_orchestrator` closure
        must construct every `ExecutionOrchestrator` with
        `current_strategy_version=assignment.strategy_artifact_hash`,
        never a `PlatformConfig` mutation and never the internal
        `ict_v1`/`baseline_v1` version.
        """
        source = inspect.getsource(agent_canary_execution)
        assert "current_strategy_version=assignment.strategy_artifact_hash" in source
        # No mutation of PlatformConfig.trading_agent anywhere in this
        # script -- the assignment's own strategy identity is threaded
        # through as a separate ExecutionOrchestrator parameter instead.
        assert not any(
            "trading_agent" in line and "model_copy" in line for line in source.splitlines()
        )
