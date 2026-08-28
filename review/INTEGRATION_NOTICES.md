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
Relevant commit: (this commit)
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
