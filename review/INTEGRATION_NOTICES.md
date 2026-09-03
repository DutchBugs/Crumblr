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

---

```text
2026-08-28 — DEV1
Changed: Two additions, both from "durable execution-activation wiring"
(core critical path item 2 — the first real caller of
evaluate_submission_gate()). (1) domain/enums.py: two new
ExecutionEventType members, SUBMISSION_GATE_PASSED /
SUBMISSION_GATE_BLOCKED. (2) config.py: PlatformConfig.config_version
now excludes three governance/approval fields
(risk.approved_config_version, execution.submission_enabled,
execution.feedback_2_0_approved) from what it hashes — fixing F-062, a
self-referential bug found while building this: those fields compared
themselves against a hash that included themselves, so the comparison
could never be made to match by construction (full reproduction in
review/adr/ADR-006-submission-gate.md §5, review/FEEDBACK.md F-062).
Impact: both files are shared-contract territory per DEV1/DEV2
instructions section 4. (1) is additive-only — confirmed via grep,
nothing in src/crumblr/agent_gateway/ references either new name. (2)
changes what bytes config_version hashes, but not its type (still
str) or its meaning for any existing caller — no shipped config file
sets any of the three excluded fields, so every config_version any
existing code has ever actually seen is numerically unchanged by this
fix; only a config that also set one of those three fields (none did,
anywhere) would see a different value than before. Confirmed via grep:
agent_gateway/ does not reference config_version anywhere.
Action required: none expected. If Agent Gateway work ever needs to
reference PlatformConfig.config_version directly (e.g. binding a
DecisionContextBundle to a specific config), be aware it now
deliberately excludes those three fields — ask Dev 1 if a Gateway-side
need for the pre-fix (whole-config) hash ever comes up, since excluding
fields from an identity hash is exactly the kind of design decision
this log exists to surface before it causes cross-track confusion.
Relevant commit: (this commit)
```

---

```text
2026-09-01 — DEV1
Changed: No code change. Architecture guidance recorded ahead of Dev 2
starting review 1.26 section 7 item 3 (wiring the Gateway's constructed
TradeIntent through intent-time Risk -> deterministic Policy -> capsule
boundary) after Dev 2 asked whether risk.policies.evaluate()/CapsuleStore
.seal() are safe to call from outside application/orchestration.py's
usual flow.
Impact: Found while reading application/live_decision.py that
risk.policies.evaluate()'s PortfolioState.ledger (EquityLedger) is
stateful and per-process, not re-derived fresh on every call.
LiveDecisionOrchestrator recovers it once per process (risk.session
.recover_session(), on first decide_once() or a trading-day rollover),
holds it in memory, and periodically persists it back to
risk_session_states — it is not reloaded from the database before every
evaluate() call. Two independent processes each recovering their own
copy of this ledger (one for internal strategies, one for external-agent
proposals) would produce two independent in-memory views of one
daily-loss/drawdown budget, each blind to what the other just decided —
a lost-update race on risk_session_states, invisible to either pipeline
on its own. This reads as a genuine conflict with the "one Risk engine"
invariant feedback.1.26.md closes on ("Agent proposes. Risk engine
constrains...").
Not a defect today: order_send is unreachable from both pipelines, so no
real position can ever result from this race regardless — this is
architecture guidance to settle before feedback.2.0 could ever treat
agent-driven submission as real, not a live safety gap.
CapsuleStore.seal() and evaluator.pretrade.evaluate() were checked too
and are NOT gotchas: seal() is content-derived-ID/ON-CONFLICT-safe from
any caller, and evaluate()'s intents_in_last_hour check is currently
uncalibrated (max_intents_per_hour: null in every shipped config, F-024)
so it is a no-op today regardless of how it is counted across pipelines.
Action required: Dev 2 to decide, when building the shared integration
path, either (a) route external-agent proposals into the same running
instance that holds the canonical ledger rather than a separate process
reimplementing recover_session(), or (b) if a separate process is
architecturally necessary, re-recover the ledger fresh immediately
before every evaluate() call instead of caching it — not fully
race-free without real DB-level locking, but avoids the worst
staleness. No Dev-1 code change needed either way.
Relevant commit: (this commit — documentation only)
```

---

```text
2026-09-01 — DEV1
Changed: pyproject.toml [tool.ruff] gained extend-exclude = ["review/"]
and force-exclude = true (F-065, review/feedback.1.27.md SS3). Fixes
hosted CI: ruff format --check was rewriting Python code fences inside
immutable historical reviewer Markdown (feedback.1.22/23/24.md).
Impact: pyproject.toml is shared/handshake territory per DEV1/DEV2
instructions section 4. Formatter-scoping only, not a dependency change
(uv sync --locked re-verified clean, no lockfile mismatch) and not a
lint-rule change (ruff check .'s selected rules are unaffected; reviewer
Markdown was never a lint target, only a format target, so nothing that
was actually being checked is now skipped). Confirmed: agent_gateway/
and its tests are untouched by this exclude (they live under src/tests,
not review/), and ruff check ./mypy both still report the same file
counts as before (150 source files).
Action required: none expected. Note for accuracy: review/AGENT_STATUS.md
and review/AGENT_FEEDBACK.md are also under review/, so this exclude
covers them too — checked directly (grep), neither currently contains a
```python fence, so nothing about their formatting actually changes
today. If Dev 2 ever wants ruff to keep formatting Python examples
inside those two specific files (unlike the immutable feedback.X.Y.md
reviewer artifacts, they're Dev 2's own living documents), say so and a
narrower exclude can replace the blanket review/ one.
Relevant commit: (this commit)
```

---

```text
2026-09-01 — REVIEWER (owner-requested cross-track decision)
Changed: Resolved Dev 2's open question from review/AGENT_STATUS.md §0e
about the source of `PortfolioState.account` / `open_positions` for the
Gateway-driven Risk -> Policy -> DecisionCapsule shadow path. No safety
invariant is changed. The Agent Gateway remains the authenticated proposal
ingress and constructs the platform-owned TradeIntent; broker-state sourcing
belongs behind that boundary in Core application integration, not inside the
external agent and not as an Agent Gateway authority.
Impact: For a genuine LIVE_SHADOW evaluation, `PortfolioState.account` and
`PortfolioState.open_positions` MUST come from a Core-owned, read-only,
fresh broker-state provider. The current preferred concrete source is one
coherent `application.broker_state.capture_broker_state()` observation (or an
equivalent future Core seam): use its `account_state` and `position_states`
for Risk and record the corresponding durable broker snapshot as normal. Do
not perform a second, unrelated broker read for the same evaluation. Do not
reconstruct missing Risk fields from `BrokerAccountSnapshot` with guessed
or permissive defaults; if the fresh Core observation cannot supply the
required account/position state, fail closed. The external Static Agent must
never receive MT5 access, broker credentials or Crumblr DB access. The
Agent Gateway package itself must not become the owner of MT5 reads. For
synthetic/unit/integration smoke tests, Dev 2 may inject a deterministic fake
`PortfolioStateProvider`; `SimulatedBroker` is acceptable only for such
synthetic/replay proof, not as the source for a claim labelled genuine
LIVE_SHADOW. AG-012 remains unchanged: for the current shadow-only
multi-process design, recover the Risk session/ledger fresh immediately
before every Gateway-driven `risk.policies.evaluate()` call and do not cache
an independent long-lived ledger. This mitigation is still not sufficient
for agent-driven submission at feedback.2.0; a single serialized/shared Risk
authority remains required before such promotion. `order_send` remains
NO-GO throughout this work.
Action required: Dev 2 is unblocked and should proceed now with the shared
no-MT5 `TradeIntent -> Risk -> deterministic Policy -> DecisionCapsule`
wiring against a narrow provider interface. Tests may use a fake provider;
the first genuine live-shadow proof must use the Core-owned fresh provider.
Dev 1 should expose/approve the smallest read-only Core adapter around the
existing broker-state capture if composition cannot already inject it
cleanly; no agent-specific logic belongs in Core. If the eventual runtime
must keep the agent-integration orchestrator in a process that cannot access
the Core read-only broker gateway, stop and define a Core read-only service
or persist a complete risk-ready state contract — do not give the external
agent direct broker/DB access and do not invent absent fields.
Relevant commit: this documentation commit; implements the owner-requested
reviewer clarification following feedback.1.27, with no `order_send`
authorization.
```

---

```text
2026-09-01 — DEV1
Changed: domain/enums.py::ExecutionEventType.SUBMISSION_STARTED moved
out of the "Reserved for M5" block and is now real — emitted by
application/execution.py::_start_submission() (new), called from
_process() only when SUBMISSION_GATE_PASSED, carrying the complete
serialized ApprovedOrder as its payload and becoming the run's reported
outcome (core critical path item 3, review 1.26 SS6 / review 1.27 SS8).
Impact: this is exactly the marker agent_gateway/contracts.py
::ProposalWithdrawal already names by name as the withdrawal-cutoff
boundary (ADR-005) - it was reserved-but-inert when that contract was
written, and is real as of this commit. It remains unreachable in every
shipped config today (the same three SubmissionGate approval fields
still default closed - F-049/ADR-006 unchanged), so nothing in any real
deployment changes. order_send is NOT called by this change - explicit
ordering rule from both reviews, see review/adr/ADR-006-submission-gate.md
SS6 for the full reasoning. Grep-confirmed: no agent_gateway/ code
depends on SUBMISSION_STARTED having actually fired yet (only the one
docstring reference).
Action required: none expected today. If Dev 2's withdrawal-cutoff test
suite wants to exercise a real fired SUBMISSION_STARTED event (rather
than a fake/mocked one) for a genuine end-to-end proof, it can now do so
against a test-only fully-approved config exactly like
tests/integration/test_execution_orchestrator.py
::test_a_fully_approved_config_reaches_submission_started does -
never a shipped one. ExecutionEventStore.events_for(order_request_id)
already exists as the query surface; ask if something narrower turns
out to be needed.
Relevant commit: (this commit)
```

---

```text
2026-09-01 — DEV1
Changed: No shared-contract file touched (persistence/execution.py and
application/execution.py are Dev-1-only, not on the shared/handshake
list) - this notice exists to flag a finding, not a change requiring
action. Hardened ExecutionEventStore.append() against same-id/
different-content conflicts (core critical path item 4, review 1.23 SS7
/ review 1.26 SS6 / review 1.27 SS8): mirrors ExecutionRequestStore
._claim()'s exact pattern - a retried event with matching content
converges silently, different reason_codes/detail/payload raises a new
ExecutionEventConflictError instead of being silently dropped by
ON CONFLICT DO NOTHING.
Impact: While researching this (to mirror the existing pattern
faithfully), found src/crumblr/persistence/agent_gateway.py
::AgentDecisionEventStore.append_event() (line 360) has the IDENTICAL
unhardened gap: _event_id_for(outcome_id, event_type) derives from
identity only, on_conflict_do_nothing, no .returning(...), no readback,
-> None. Same class of gap this notice's own change just closed on the
Core side, found as a direct byproduct of that research rather than a
deliberate audit of agent_gateway/.
Action required: none from Dev 1 - this is Dev-2-owned code, not touched
here. Flagging only. The pattern to mirror, if useful:
ExecutionEventStore.append()/ExecutionEventConflictError in
persistence/execution.py (this commit) - add .returning(event_id) to
the insert, read back reason_codes/detail/payload (or whatever
AgentDecisionEventStore's own append_event carries) on a loss, compare
via domain.hashing.fingerprint() on both sides, raise on mismatch.
Relevant commit: (this commit)
```

---

```text
2026-09-02 — DEV1
Changed: New Alembic migration, migrations/versions/20260902_cc35e55b3f92
_flatten_requests_and_events.py, revises d4b6e2f81a37 (the current head at
the time this was created - confirmed via `alembic heads`). Adds two new
tables, flatten_requests/flatten_events (core critical path item 7,
automatic flatten submission, review/adr/ADR-009-automatic-flatten
-submission.md) - structurally parallel to execution_requests/
execution_events but with no FK into decision_capsules: a flatten is
policy-driven, not proposal-driven. Confirmed with Dev 2 before creating
the revision (no migration in flight on their side).
Impact: persistence/schema.py is shared/cross-cutting infrastructure.
This addition is purely additive - two new tables, no existing table
touched, no existing FK or index changed. Confirmed via
tests/integration/test_migrations.py (all 8 tests pass, including
schema/migration-agreement) and a full-suite run.
Action required: current Alembic head is now cc35e55b3f92 - the next
migration on either side must chain from there.
Relevant commit: (this commit)
```

---

```text
2026-09-02 — DEV1
Changed: domain/models.py gains two new contracts, FlattenInstruction and
FlattenPlan (core critical path item 7, ADR-009 SS2.5) - the flatten
analogue of ApprovedOrder, deliberately not ApprovedOrder itself (that
type rejects Side.FLAT and requires intent/risk-decision/supervisor-
decision ids a policy-driven close has no honest value for). Also two
new ReasonCode members (POSITION_BOOK_INCOMPLETE, FLATTEN_NOT_REQUIRED,
FLATTEN_SUBMISSION_NOT_ENABLED - three, not two) and a new FlattenEventType
enum, both in domain/enums.py.
Impact: domain/enums.py and domain/models.py are shared-contract
territory per DEV1/DEV2 instructions section 4. Both additions are
purely additive - no existing model's shape changed, no existing enum
member renamed or removed. Confirmed via grep: zero references to any
of FlattenInstruction/FlattenPlan/FlattenEventType/the three new
ReasonCode members anywhere in src/crumblr/agent_gateway/.
Action required: none expected. If external-agent-driven flatten
handling is ever needed (not currently planned - a flatten stays a
Core-internal, policy-driven action per ADR-004 SS5.1, never agent-
proposed), these are the contracts to reuse rather than inventing
parallel ones.
Relevant commit: (this commit)
```

---

```text
2026-09-02 — DEV1
Changed: New Alembic migration, migrations/versions/20260902_03df83b062a6
_execution_events_type_time_index.py, revises cc35e55b3f92 (the head at
the time this was created - confirmed via `alembic heads`, and confirmed
with Dev 2 no migration was in flight). Index-only: adds
Index("ix_execution_events_type_time", "event_type", "occurred_at_utc")
on execution_events (core critical path item 8, post-fill reconciliation,
review/adr/ADR-010-post-fill-reconciliation.md). Serves the new
ExecutionEventStore.request_ids_with_event() seam and retroactively
serves count_events_since()'s existing unindexed filter, which FINAL
Risk already calls every _process() pass.
Impact: persistence/schema.py is shared/cross-cutting infrastructure.
Index-only - no table, column, FK, or existing index changed. Confirmed
via tests/integration/test_migrations.py (all 8 tests pass) and a full-
suite run.
Action required: current Alembic head is now 03df83b062a6 - the next
migration on either side must chain from there.
Relevant commit: (this commit)
```

---

```text
2026-09-02 — DEV1
Changed: application/reconciliation.py::ExpectedState gains one new
field, undetermined_reasons: tuple[str, ...] = () (core critical path
item 8, ADR-010 SS2). Non-empty means reconcile() returns UNKNOWN -
symmetric with the existing expected_spec_version=None leg (F-055).
ExpectedState.flat() never sets it; a new classmethod,
from_durable_exposure(), does.
Impact: application/reconciliation.py is not itself on the shared/
handshake list, but ExpectedState is a dataclass any caller could
construct - noting this defensively. Purely additive: a new field with
a default, a new classmethod alongside (not replacing) flat(), one new
early-return leg in reconcile() placed before any existing leg's logic
runs, unreachable for any caller that never sets the new field
(confirmed by tests/unit/test_reconciliation.py
::test_flat_never_sets_undetermined_reasons - flat() produces
identical results before and after this change).
Action required: none expected. If agent_gateway/ code ever needs to
construct an ExpectedState directly (grep-confirmed: it does not today),
be aware of the new field and prefer flat() unless durable execution
history is genuinely being read.
Relevant commit: (this commit)
```

---

```text
2026-09-03 — DEV1
Changed: New ReasonCode.OPEN_RISK_UNKNOWN (owner risk policy v1, D1.4,
review/adr/ADR-011-owner-risk-policy-v1.md SS2.5). SYMBOL_EXPOSURE_EXISTS
is retired (O-004 withdrawn by review/OWNER_POLICY_V1.md SS2) - kept as
an enum member only because ReasonCode(code) reconstructs values from
persisted rows in persistence/execution.py, persistence/flatten.py, and
persistence/safety_state.py; no code path emits it any more.
Impact: purely additive on the ReasonCode side - no existing member
renamed or removed. Confirmed via grep: zero references to
OPEN_RISK_UNKNOWN anywhere in src/crumblr/agent_gateway/ today.
Action required: none expected. If external-agent code ever surfaces
BLOCK/HALT reason codes to an operator or model, OPEN_RISK_UNKNOWN is
now a live code that can appear; SYMBOL_EXPOSURE_EXISTS can still appear
on old persisted rows and should render as retired/historical, not as a
current rule.
Relevant commit: (this commit)
```

---

```text
2026-09-03 — DEV1
Changed: risk/policies.py::PortfolioState.open_risk_fraction widens from
Decimal (defaulting to ZERO) to Decimal | None, with no default (owner
risk policy v1, D1.4). None means the platform could not establish real
open risk (an open position with untrustworthy stop geometry) and must
never be treated as zero - see risk/portfolio_risk.py::assess_open_risk,
the new function that replaces the old count-based approximation
(max_risk_per_trade * Decimal(len(open_positions))) at every internal
call site.
Impact: agent_gateway/decision_path.py:230-236 constructs a
PortfolioState with an explicit open_risk_fraction kwarg already
(confirmed by direct read before this slice) - the widened type is
source-compatible with that call site with no edit required, though its
own count-based approximation is unchanged by this slice (that is Dev
2's own D2.2, tracked separately below). Confirmed via grep: zero edits
under src/crumblr/agent_gateway/ in this slice.
Action required: Dev 2's D2.2 - when agent_gateway/decision_path.py is
updated to consume real open-risk accounting instead of its own
count-based approximation, prefer risk/portfolio_risk.py::assess_open_risk
over reimplementing the arithmetic; ping DEV1 if the signature needs
anything the agent-gateway side does not already have in scope (specs,
equity). Separately: agent_gateway/market_context.py
::AgentPlatformState.open_risk_fraction: RiskFraction | None cannot
currently distinguish a flat book from an unestablished one - both
serialize as None, since RiskFraction is constrained gt=0. Not fixed in
this slice (see review/DEVIATIONS.md D-054 gap 2); worth resolving
alongside D2.2 rather than separately.
Relevant commit: (this commit)
```

---

```text
2026-09-03 — DEV1
Changed: New Alembic migration,
migrations/versions/20260903_d3b2e828b5b0_risk_session_open_risk_fraction
_nullable.py, revises 03df83b062a6 (the head at the time this was
created - confirmed via `alembic heads`, and confirmed with Dev 2 no
migration was in flight on agent/contracts). Nullable-only: makes
risk_session_states.open_risk_fraction nullable (owner risk policy v1,
D1.4). recover_session() never reads this field (confirmed by direct
read) - recovery behaviour is unaffected.
Impact: persistence/schema.py is shared/cross-cutting infrastructure.
Nullability-only - no table, column removal, FK, or existing index
changed. Confirmed via tests/integration/test_migrations.py.
Action required: current Alembic head is now d3b2e828b5b0 - the next
migration on either side must chain from there.
Relevant commit: (this commit)
```
