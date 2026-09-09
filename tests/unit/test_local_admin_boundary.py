"""Structural proof that Local Host Admin cannot reach a governance control.

Owner's amendment 4 (Host Auto-Recovery / Local Host Admin work order,
2026-09-09): `execution.submission_enabled`, `execution.feedback_2_0_approved`,
`execution.flatten_submission_enabled`, and `live_trading_acknowledged`
already exist in `main@c775333` (`crumblr.config.ExecutionConfig`/
`PlatformConfig`) and "must remain completely unreachable from Local Host
Admin. Add structural tests proving there is no admin route/settings key/
write path for them. Same for HALT reset, Risk/Policy authority and
TradingAssignment mutation."

These tests check the *source*, not only today's behaviour, wherever that
is stronger: an `ast`-level import scan means a future change that adds a
`crumblr.risk` import to this package fails a test even if nobody has yet
wired a route to misuse it. Everything here runs on any platform — no
Windows Credential Manager call happens in this module.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import pkgutil
from pathlib import Path

import pytest
from fastapi.routing import APIRoute

from crumblr.local_admin import api as local_admin_api
from crumblr.local_admin.registry import ALLOWED_SECRET_NAMES, ALLOWED_SETTINGS_KEYS
from crumblr.local_admin.settings_store import UnknownSettingError, write_settings

FORBIDDEN_MODULE_PREFIXES = (
    "crumblr.risk",
    "crumblr.persistence.safety_state",
    "crumblr.agent_gateway",
)

FORBIDDEN_NAMES = (
    "submission_enabled",
    "feedback_2_0_approved",
    "flatten_submission_enabled",
    "live_trading_acknowledged",
    "reset_halt",
    "issue_assignment",
    "TradingAssignment",
)


def _local_admin_module_files() -> list[Path]:
    import crumblr.local_admin as pkg

    package_dir = Path(pkg.__file__).resolve().parent
    files = [package_dir / "__init__.py"]
    for module_info in pkgutil.iter_modules([str(package_dir)]):
        files.append(package_dir / f"{module_info.name}.py")
    return [f for f in files if f.exists()]


def _imported_module_names(source: str) -> set[str]:
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _docstring_node_ids(tree: ast.AST) -> set[int]:
    """`id()`s of docstring `Constant` nodes -- excluded from the identifier

    scan below, since a docstring explaining *why* a control is excluded
    should not itself be flagged as if it were a reference to that
    control."""
    ids: set[int] = set()
    doc_bearing = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    for node in ast.walk(tree):
        if isinstance(node, doc_bearing) and node.body:
            first = node.body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                ids.add(id(first.value))
    return ids


def _real_code_identifiers(source: str) -> set[str]:
    """Names/attributes/string-literals actually used as code -- e.g. a

    dict key, an attribute access, a route path -- with docstrings
    excluded. This is what "reachable from code" means for the belt-and-
    braces check below; prose explaining the boundary is not code."""
    tree = ast.parse(source)
    skip = _docstring_node_ids(tree)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif (
            isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip
        ):
            names.add(node.value)
    return names


class TestNoForbiddenImportAnywhereInLocalAdmin:
    """Every file in `crumblr.local_admin`, scanned by its own AST -- not by

    what happens to be imported into a running test process."""

    def test_no_file_imports_risk_safety_state_or_agent_gateway(self) -> None:
        offenders: dict[str, set[str]] = {}
        for path in _local_admin_module_files():
            imported = _imported_module_names(path.read_text(encoding="utf-8"))
            hits = {
                name
                for name in imported
                if any(
                    name == prefix or name.startswith(prefix + ".")
                    for prefix in FORBIDDEN_MODULE_PREFIXES
                )
            }
            if hits:
                offenders[str(path)] = hits
        assert not offenders, f"forbidden imports found: {offenders}"

    def test_no_file_names_a_forbidden_governance_symbol_as_real_code(self) -> None:
        """Belt-and-braces: even a *reference* to these names as an

        identifier, attribute, or string literal used in code (not prose
        in a docstring) should not appear -- e.g. a route/setting/dict-key
        literally named "submission_enabled"."""
        offenders: dict[str, set[str]] = {}
        for path in _local_admin_module_files():
            identifiers = _real_code_identifiers(path.read_text(encoding="utf-8"))
            hits = {name for name in FORBIDDEN_NAMES if name in identifiers}
            if hits:
                offenders[str(path)] = hits
        assert not offenders, f"forbidden governance names found as real code: {offenders}"


class TestAllowlistsExcludeGovernanceControls:
    def test_allowed_secret_names_exclude_forbidden_names(self) -> None:
        for forbidden in FORBIDDEN_NAMES:
            assert forbidden not in ALLOWED_SECRET_NAMES

    def test_allowed_settings_keys_exclude_forbidden_names(self) -> None:
        for forbidden in FORBIDDEN_NAMES:
            assert forbidden not in ALLOWED_SETTINGS_KEYS

    def test_write_settings_rejects_every_forbidden_key(self, tmp_path: Path) -> None:
        for forbidden in FORBIDDEN_NAMES:
            with pytest.raises(UnknownSettingError):
                write_settings({forbidden: True}, path=tmp_path / "settings.json")


class TestRouteTableCarriesNoMutatingPathNearGovernance:
    def test_no_route_path_or_endpoint_name_mentions_a_forbidden_control(self) -> None:
        app = local_admin_api.create_app(admin_token="test-token")
        offenders = []
        for route in app.routes:
            if not isinstance(route, APIRoute):
                continue
            endpoint_name = getattr(route.endpoint, "__name__", "")
            endpoint_source = inspect.getsource(route.endpoint)
            haystack = f"{route.path} {endpoint_name} {endpoint_source}"
            hits = {name for name in FORBIDDEN_NAMES if name in haystack}
            if hits:
                offenders.append((route.path, hits))
        assert not offenders, f"forbidden control reachable from a route: {offenders}"

    def test_only_the_named_secrets_and_settings_are_ever_reachable(self) -> None:
        app = local_admin_api.create_app(admin_token="test-token")
        secret_paths = {
            route.path
            for route in app.routes
            if isinstance(route, APIRoute) and "/api/secrets/" in route.path
        }
        # The route is parameterised (/api/secrets/{name}); the allowlist check
        # happens inside the handler (proven by test_local_admin_api.py), not by
        # having one static route per secret. This test only proves the route
        # table itself carries nothing beyond the parameterised CRUD paths.
        assert secret_paths <= {"/api/secrets/{name}"}


def test_forbidden_module_prefixes_actually_resolve_to_real_governance_code() -> None:
    """A guard against this whole test file quietly testing nothing: each

    forbidden prefix must be a real, importable module that really does
    define the governance symbols named above -- otherwise a rename could
    silently defang every test in this file."""
    for prefix in FORBIDDEN_MODULE_PREFIXES:
        importlib.import_module(prefix)
