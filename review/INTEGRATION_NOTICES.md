# Integration notices — canonical cross-track log

**Owner:** Dev 1 (`CRUMBLR_DEV1_CORE_EXECUTION_INSTRUCTIONS.md` §7). Kept
small and append-only. A Dev-2 change with Core impact is recorded here
once merged; Dev 1's own shared-contract changes are logged directly.

Format:

```text
YYYY-MM-DD — DEV1|DEV2
Changed:
Impact:
Action required:
Relevant commit:
```

---

```text
2026-08-28 — DEV1
Changed: Added four new ReasonCode members to domain/enums.py
(RISK_POLICY_NOT_APPROVED, EXECUTION_NOT_EXPLICITLY_ENABLED,
ALGOTRADING_DISABLED, FEEDBACK_2_0_NOT_APPROVED) as part of shipping the
real SubmissionGate (F-049, review/adr/ADR-006-submission-gate.md).
Removed the now-inaccurate SUBMISSION_GATE_NOT_IMPLEMENTED member (the
gate is no longer a stub) — confirmed via grep it had exactly one
non-definition reference, in this session's own tests/unit/test_execution_gates.py,
updated in the same change.
Impact: domain/enums.py is shared-contract territory per DEV1/DEV2
instructions section 4. This is additive-only — no existing ReasonCode
member was renamed, and the one removed member
(SUBMISSION_GATE_NOT_IMPLEMENTED) had no reference anywhere in
src/crumblr/agent_gateway/ or its tests at the time of this change
(confirmed by grep before removing it). Also added three new config
fields (RiskConfig.approved_config_version, ExecutionConfig
.submission_enabled, ExecutionConfig.feedback_2_0_approved), all
optional/defaulted, not required by any existing constructor call.
Action required: none expected — additive change, no shipped config
file touched, no existing call site broken (confirmed: full suite green,
1014 passed / 3 skipped, including every agent_gateway test). If a
future agent-track change needs one of the four new ReasonCode members
(e.g. a Gateway-side submission-readiness check), reuse these rather
than adding parallel ones.
Relevant commit: a1a2770
```

---

```text
2026-08-28 — DEV1
Changed: Process note, not a code change. Dev 1's SubmissionGate commit
was first created while "agent/contracts" (Dev 2's own topic branch, per
DEV2 instructions section 11's own naming example) was the checked-out
branch in this shared working tree — both sessions operate on the same
physical .git, and neither had switched branches explicitly before
starting work. The commit landed stacked on top of Dev 2's two commits
instead of on main. Caught before pushing (nothing was on origin yet).
Fixed by: creating core/submission-gate from main, cherry-picking the
Dev-1 commit onto it (clean, no conflicts — file sets are fully
disjoint from Dev 2's), force-moving agent/contracts back to Dev 2's own
tip (2f7c921, dropping the misplaced commit from it), then
fast-forwarding main onto core/submission-gate and pushing. No commit
was rewritten or lost — the original stacked commit's content is
identical to what merged, only its ancestry/branch placement changed.
Impact: none on either track's actual code. Recorded so the git history
around this date is legible to a later reader — agent/contracts
briefly, locally, contained a Dev-1 commit that was never pushed.
Action required: both sessions should explicitly confirm/switch to the
intended branch before starting work in this shared working tree,
rather than trusting whatever was last checked out.
Relevant commit: a1a2770 (final, on main); agent/contracts restored to
2f7c921
```

---

```text
2026-08-28 — DEV2
Changed: (recorded here on Dev 1's behalf, reconstructed from the
already-merged commits — Dev 2 committed directly to main before this
log existed) cc16e4f "ADR-005, threat model and Step A external-agent
contracts", 2f7c921 "Agent Gateway ingestion + audit layer (ADR-005 Step
B)" — new src/crumblr/agent_gateway/ package (contracts, auth, errors,
events, gateway, stores), src/crumblr/persistence/agent_gateway.py, and
additions to src/crumblr/persistence/schema.py.
Impact: persistence/schema.py is shared/cross-cutting infrastructure. A
new migration, migrations/versions/20260828_d4b6e2f81a37_agent_gateway_step_b.py,
correctly chains after Phase 4's own head (c9e1d5a3f286) — confirmed by
git log; no divergent/parallel head exists. Dev 1 has not reviewed the
schema addition in detail as of this notice — no overlap with any file
Dev 1 touched in the same window (confirmed via git show --stat), and
the full suite is green (1014 passed / 3 skipped) including the new
agent_gateway integration tests.
Action required: current Alembic head is now d4b6e2f81a37 — Dev 1's
next migration (if any) must chain from there, per instructions section
8.
Relevant commit: cc16e4f, 2f7c921
```

---

```text
2026-08-28 — DEV1
Changed: CRUMBLR_DEV1_CORE_EXECUTION_INSTRUCTIONS_V2.md /
CRUMBLR_DEV2_AGENT_INTEGRATION_INSTRUCTIONS_V3.md arrived, mandating
workspace isolation (separate worktree/branch/Python env/test DB per
track) — a direct response to the branch-mixup incident logged above.
Set up per V2 section 1: entered a dedicated git worktree
(.claude/worktrees/core, its own venv, branch prefix core/*). Per V2
section 2, created a dedicated crumblr_test_dev1 PostgreSQL database
(same server, separate database from the shared crumblr one) and
pointed CRUMBLR_DATABASE_URL at it for this workspace.
While verifying integration tests actually respect that isolation,
found and fixed a real, pre-existing bug (not just a naming gap):
tests/integration/test_run_survives_restart.py's child-process helper
and four other integration test files
(test_orchestrator_persistence.py, test_market_data_store.py,
test_live_decision.py, test_migrations.py) all imported
crumblr.persistence.engine.DEFAULT_TEST_URL and used the literal
constant directly, bypassing the CRUMBLR_DATABASE_URL environment
override the shared engine fixture (conftest.py) already respects via
database_url(DEFAULT_TEST_URL). Every affected call site now resolves
a module-level TEST_URL = database_url(DEFAULT_TEST_URL) once, honouring
the override like the fixture does. test_migrations.py's pg_dump/psql
subprocess calls also hardcoded the literal database name "crumblr" —
fixed to use TEST_DB_NAME (parsed from TEST_URL via
sqlalchemy.engine.make_url) instead.
Impact: test-infrastructure only, no production code touched. Without
this fix, setting CRUMBLR_DATABASE_URL per workspace (exactly what V2/V3
section 2 mandates) would have silently not isolated roughly half of
this project's integration tests — they would have kept writing to
whatever DEFAULT_TEST_URL resolves to (the shared crumblr database)
regardless of the env var, defeating the isolation both tracks were
just told to set up.
Action required: Dev 2 should apply the same fix if
tests/**/agent_gateway/** integration tests import DEFAULT_TEST_URL
directly rather than going through database_url(DEFAULT_TEST_URL) —
worth a quick grep before assuming crumblr_test_dev2 isolation actually
holds.
Relevant commit: (this commit)
```

---

```text
2026-08-28 — DEV1
Changed: Acknowledging AG-006 (TradeIntent.feature_snapshot_id must stay
required, not optional) as recorded identically in both
CRUMBLR_DEV1_CORE_EXECUTION_INSTRUCTIONS_V2.md section 5 and
CRUMBLR_DEV2_AGENT_INTEGRATION_INSTRUCTIONS_V3.md sections 4/5 — both
instruction documents already state the same resolution, so there is
no outstanding disagreement to settle. Confirmed no code change is
needed on the Dev-1 side: domain/models.py::TradeIntent
.feature_snapshot_id is already a required field (UUID, no default) —
it always has been, since before this session's Phase-4 work. Dev 2's
own next step (V3 section 4/section 18.D) is to add the trusted,
platform-issued feature_snapshot_id to their own
DecisionContextBundle contract (src/crumblr/agent_gateway/contracts.py,
Dev-2-owned) and coordinate the Gateway's TradeIntent-construction
mapping — nothing for Dev 1 to build for that.
Impact: none — no shared-contract file changed by this entry.
Action required: none from Dev 1 right now. Dev 1 should review Dev 2's
DecisionContextBundle field-addition proposal when it arrives, per the
section 4 handshake, before Dev 2 merges it.
Relevant commit: (this commit)
```
