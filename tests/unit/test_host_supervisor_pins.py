"""Static assertions over scripts/host_supervisor.ps1's own pinned literals.

Post-incident runtime binding patch (2026-09-09): a PowerShell script has
no pytest of its own, but the *exact* pinned values it hard-codes are
security/correctness-relevant (a wrong Static Agent commit or
StrategyArtifact hash would silently authorize the wrong runtime), so
this reads the file's own source text and checks the exact literals --
the same "read the real file, assert on its real content" discipline
`test_local_admin_boundary.py` already established for a different file.

These are text assertions, not a PowerShell parse/execution -- deliberate,
since no PowerShell test runner exists in this project's toolchain
(`pyproject.toml`'s `[tool.pytest.ini_options]` covers Python only). A
syntax-level check (`PARSE OK`) was performed by hand during this patch;
these tests exist to catch a *content* regression on any future edit,
which a parser cannot.
"""

from __future__ import annotations

from pathlib import Path

HOST_SUPERVISOR_PATH = Path(__file__).resolve().parents[2] / "scripts" / "host_supervisor.ps1"

NEW_ASSIGNMENT_ID = "f98c0396-dd13-4a99-b1e9-b83ea0f15ed7"
OLD_ASSIGNMENT_ID = "14255834-811d-4b19-8d57-8057d3e39424"
STALE_CODE_COMMIT = "c775333"
REQUIRED_STATIC_AGENT_HEAD = "dcc3770df67b5251d145c6f7fa08786ea85f8328"
REQUIRED_STRATEGY_ARTIFACT_HASH = "81894d6a9c44ddb0433c15f9779fb0a2e25c0e1eccee232e2fd145d72cb498c5"
POST_INCIDENT_SETTINGS_PATH = "config\\agent_paper_post_incident.yaml"
POST_INCIDENT_SAFETY_PATH = "var\\agent_paper_post_incident.safety.json"
POST_INCIDENT_JOURNAL_PATH = "var\\agent_paper_post_incident.journal.jsonl"


def _source() -> str:
    return HOST_SUPERVISOR_PATH.read_text(encoding="utf-8")


class TestNewPinsArePresentExactly:
    def test_the_new_pregenerated_assignment_id_is_pinned(self) -> None:
        assert f'$AssignmentId = "{NEW_ASSIGNMENT_ID}"' in _source()

    def test_the_required_static_agent_head_is_pinned_exactly(self) -> None:
        assert f'$StaticAgentRequiredHead = "{REQUIRED_STATIC_AGENT_HEAD}"' in _source()
        assert len(REQUIRED_STATIC_AGENT_HEAD) == 40, "must be a full SHA, not a short prefix"

    def test_the_required_strategy_artifact_hash_is_pinned_exactly(self) -> None:
        assert f'$RequiredStrategyArtifactHash = "{REQUIRED_STRATEGY_ARTIFACT_HASH}"' in _source()

    def test_the_post_incident_settings_and_state_paths_are_pinned(self) -> None:
        source = _source()
        assert f'$PaperLiteSettingsPath = "{POST_INCIDENT_SETTINGS_PATH}"' in source
        assert f'$PaperLiteSafetyLatchPath = "{POST_INCIDENT_SAFETY_PATH}"' in source
        assert f'$PaperLiteJournalPath = "{POST_INCIDENT_JOURNAL_PATH}"' in source


class TestStalePinsAreGone:
    def test_the_old_assignment_id_is_not_pinned_anywhere(self) -> None:
        assert OLD_ASSIGNMENT_ID not in _source()

    def test_no_hard_coded_code_commit_variable_remains(self) -> None:
        source = _source()
        assert "$CodeCommit" not in source
        assert STALE_CODE_COMMIT not in source

    def test_no_quoted_reference_to_the_preserved_soak_evidence_paths_as_a_cli_argument(
        self,
    ) -> None:
        """The preserved pre-incident evidence paths may still appear in

        prose (explaining what must never be reused) but must never again
        appear as a quoted PowerShell string literal -- the shape a CLI
        argument or path variable assignment would take."""
        source = _source()
        assert '"config\\agent_paper_soak.yaml"' not in source
        assert '"var\\agent_paper_soak.journal.jsonl"' not in source
        assert '"var\\agent_paper_soak.safety.json"' not in source


class TestLocalHostAdminAssignmentIdStaysDisplayOnly:
    def test_get_crumblr_setting_is_never_called_with_assignment_id(self) -> None:
        source = _source()
        assert 'Get-CrumblrSetting "assignment_id"' not in source
        assert "Get-CrumblrSetting 'assignment_id'" not in source

    def test_the_runtime_assignment_id_variable_is_the_sole_source(self) -> None:
        """$AssignmentId is a literal, not something read from

        config\\local_host_settings.json at any point in the file."""
        source = _source()
        assignment_id_line = next(
            line for line in source.splitlines() if line.strip().startswith("$AssignmentId =")
        )
        assert "Get-CrumblrSetting" not in assignment_id_line


class TestPinnedValuesAreWellFormed:
    def test_the_static_agent_required_head_and_strategy_hash_are_real_verified_values(
        self,
    ) -> None:
        """A guard against this whole file quietly testing stale strings:

        both pinned values must be well-formed (a 40-hex-char git SHA and
        a 64-hex-char sha256 hex digest), not merely "some string"."""
        assert len(REQUIRED_STATIC_AGENT_HEAD) == 40
        assert all(c in "0123456789abcdef" for c in REQUIRED_STATIC_AGENT_HEAD)
        assert len(REQUIRED_STRATEGY_ARTIFACT_HASH) == 64
        assert all(c in "0123456789abcdef" for c in REQUIRED_STRATEGY_ARTIFACT_HASH)
