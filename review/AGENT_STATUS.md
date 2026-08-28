# Agent Integration track — status

**Workstream:** Dev 2 — External Agent Integration
**Owning instructions:** `review/CRUMBLR_DEV2_AGENT_INTEGRATION_INSTRUCTIONS_V3.md`
(supersedes V2 — mandatory workspace/DB isolation, explicit AG-006
direction; see §0 below)
**Formal direction:** `feedback.1.25.md` (project-wide `review/FEEDBACK.md`)
**This document is Dev-2-owned.** Canonical `status.md` and
`review/FEEDBACK.md` remain Dev-1-owned; only a short consolidated summary
gets handed to Dev 1 at a meaningful merged milestone (instructions §17).

---

## 0. Workspace isolation (V3 §1/§2) — done 2026-08-28

Working directory: dedicated worktree at `.claude/worktrees/agent-dev2`,
branch `agent/contracts`, own `.venv` (created automatically on first
`uv run`). Test database: dedicated `crumblr_test_dev2` (same Postgres
instance, port 55432, isolated by name) — selected via the existing
`CRUMBLR_DATABASE_URL` env var, e.g.:

```text
CRUMBLR_DATABASE_URL=postgresql+psycopg://crumblr:crumblr@localhost:55432/crumblr_test_dev2 uv run pytest ...
```

**Integration notice for Dev 1 — partial harness isolation gap found:**
`tests/integration/conftest.py::engine` (and anything using it) respects
`CRUMBLR_DATABASE_URL` correctly via `persistence.engine.database_url()`.
But five integration test files construct their own runtime with the raw
`DEFAULT_TEST_URL` constant instead of going through `database_url()`,
so they silently ignore the env var and always hit the literal default
database regardless of isolation intent:
`tests/integration/test_live_decision.py`,
`tests/integration/test_market_data_store.py`,
`tests/integration/test_migrations.py`,
`tests/integration/test_orchestrator_persistence.py`,
`tests/integration/test_run_survives_restart.py`. Not fixed here — these
are Dev-1-owned test files and this is exactly the "coordinate the
parameterization with Dev 1" case V3 §2 anticipates, not something to
patch unilaterally. This track's own integration suite
(`tests/integration/test_agent_gateway_store.py`) and
`tests/integration/test_migrations.py` (which happened to still pass
cleanly run alone) were both re-verified against the isolated DB and are
unaffected either way.

---

## 1. Where this track actually stands

| Step | Scope | State |
|---|---|---|
| A — design/contracts | ADR-005, threat model, eight contracts, structural tests | **Complete, merged to `main`/pushed to `origin/main`** (rebased+re-hashed as `ba658c5`, 2026-08-28 — original commit was `cc16e4f`, 2026-08-27, before rebasing onto Dev 1's F-049 work). |
| B — Agent Gateway in shadow | auth, assignment enforcement, idempotent proposal/NO_TRADE persistence, fail-closed error handling | **Ingestion + audit layer complete, tested, merged to `main`/pushed to `origin/main`** (rebased+re-hashed as `bf18ec5`, 2026-08-28 — original commit was `2f7c921`). `TradeProposal → TradeIntent` mapping deliberately NOT built this pass — see AG-006 below. |
| C — Supervisor boundary | external Supervisor wired in, fail-closed on timeout/error | **Not started** (AG-003). |
| D — research/training plane | artifact registry, Backtest Requests, Training | **Deliberately not started** — out of scope before MVP per instructions §10. |
| E — first agent-driven canary | full Step B/C bundle + everything Milestone A (Crumblr Execution Proof) already requires | **Not started**, blocked on B/C. |

**Why the commit hashes changed:** `agent/contracts` was created off `main`
at `86873a6`, before Dev 1's F-049 `SubmissionGate` (`a1a2770`, `f0fd167`)
landed. Per V3 §6's merge policy, this branch was rebased onto current
`main` (clean, no conflicts — the two tracks' files are fully disjoint,
confirmed by Dev 1 independently too), re-verified fully green in
isolation (§2 below), then fast-forward-pushed straight to `origin/main`
(`f0fd167..bf18ec5`) — nothing force-pushed, nothing rewritten on `main`
itself, `main` only ever moved forward.

**Nothing in `src/crumblr/agent_gateway/` or `src/crumblr/persistence/agent_gateway.py`
is imported by anything outside itself and its own tests.** Verified by
grep 2026-08-28. No transport (HTTP/gRPC/queue) exists yet for an external
process to reach the Gateway at all — everything proven so far is proven
by calling `AgentGateway` directly from tests. No agent path can reach
execution, MT5, or the database outside this package's own six tables.

### What Step B actually proves (ADR-005 §8 "first proof target")

> One external Trader consumes one genuine Crumblr decision context and
> returns explicit NO_TRADE or a valid BUY/SELL proposal with SL+TP, and
> Crumblr durably records identity, assignment, context and outcome in
> SHADOW with zero broker execution.

Built and tested against this exact bar — `AgentGateway`
(`agent_gateway/gateway.py`) plus six new PostgreSQL tables
(`agent_identities`, `agent_credentials`, `agent_trading_assignments`,
`agent_decision_context_bundles`, `agent_decision_outcomes`,
`agent_decision_events`; migration `d4b6e2f81a37`, off Dev-1's confirmed
head `c9e1d5a3f286`):

- **Identity/authentication** — interim shared-secret credential
  (`agent_gateway/auth.py`, salted SHA-256, constant-time compare), fails
  closed on unknown agent, wrong credential, or suspended/retired status.
  Not the final mTLS/SPIFFE mechanism `service_identity` is named for
  (AG-001, closed to a first-draft-acceptable degree, not to the threat
  model's ultimate target).
- **Assignment authorization** — every proposal/NO_TRADE is checked
  server-side against a durably-registered `TradingAssignment`: ownership,
  validity window, requested-risk band, hourly rate limit. Never trusts a
  proposal's own description of its scope.
- **Context-hash binding + expiry** — a proposal's `context_hash` must
  match a `DecisionContextBundle` Crumblr actually issued, for the correct
  assignment, not yet expired.
- **Idempotency / conflicting-retry detection** — `TradeProposal.proposal_fingerprint`
  and a new `NoTradeDecision.decision_fingerprint` (added this pass,
  mirroring the proposal one) are claimed via the same
  `INSERT ... ON CONFLICT DO NOTHING RETURNING` primitive
  `persistence/execution.py` already proves for internal execution
  requests. An identical retry replays the original result with no new
  side effect; a conflicting retry raises `DecisionConflictError`.
- **Fail-closed audit discipline** — every submission is durably claimed
  (`RECEIVED`) *before* any authorization check runs, so a legitimate
  refusal is a normal, auditable, machine-readable outcome
  (`AgentRejectionReason`), never a silently-dropped attempt.
- **Explicit `NO_TRADE`** — structurally independent of `TradeProposal`
  (proven at Step A); this pass adds the same idempotent-claim and
  audit-trail treatment NO_TRADE needs to actually flow through the
  Gateway, not only exist as a contract.
- **Restart safety** — proven against real PostgreSQL: a second,
  independently-constructed `AgentGateway` (simulating a crashed-and-
  restarted process) replays an identical retry safely and still fails
  closed on a conflicting one, because no Gateway method caches identity,
  assignment, or claim state in memory between calls.

### What Step B deliberately does not do yet

- **No `TradeProposal → TradeIntent` mapping.** `TradeIntent` requires a
  non-optional `feature_snapshot_id: UUID`; an externally-originated
  proposal has no computed feature snapshot. This is a shared-contract
  semantic question (instructions §4: stop and raise rather than force it
  alone) — tracked as **AG-006** in `review/AGENT_FEEDBACK.md`, not
  silently worked around. V3 §5 has since given explicit direction on the
  shape of the fix (see §5 below) — still pending a Dev-1 handshake on the
  exact field. ADR-005's own Step B description doesn't actually require
  this mapping either — Risk/Policy Gate/`DecisionCapsule` sealing is Step
  C territory (§9).
- **No `ProposalWithdrawal` enforcement.** The contract exists (Step A);
  wiring its `SUBMISSION_STARTED`-cutoff rule needs the intent-mapping
  above to exist first, since there is no execution timeline to check
  against yet.
- **No wire transport.** Everything is proven by calling `AgentGateway`
  directly. "Malformed input rejected" (ADR-005 §8) is proven at the
  contract level (Step A's 31 tests — pydantic validation makes an invalid
  `TradeProposal` unconstructible) but not yet at a wire-deserialization
  boundary, because no such boundary exists yet.

---

## 2. Evidence, this session (2026-08-28)

- `uv run pytest tests/unit/test_agent_gateway_contracts.py -q` — **29
  passed** (was 27 at Step A; +2 for the new `decision_fingerprint`
  computed field's own tests, added this pass for AG-002).
- `uv run pytest tests/unit/test_agent_gateway.py -q` — **24 passed**
  (new this session — the full ADR-005 §7 test matrix against in-memory
  stores).
- `uv run pytest tests/integration/test_agent_gateway_store.py -q -m
  integration` — **6 passed** (new this session — real PostgreSQL,
  restart-safety, concurrent-claim atomicity).
- `uv run pytest tests/integration/test_migrations.py -q -m integration`
  — **8 passed** (proves the new migration and `persistence/schema.py`
  agree exactly, run in isolation — see the note on shared-database
  contention below).
- `uv run ruff check` / `ruff format --check` on every new/changed file —
  clean.
- `uv run mypy` (project-wide, via the configured invocation) — **no
  issues in 147 source files**.
- `uv run pytest -m "not integration" -q` (full non-integration suite) —
  **834 passed, 1 skipped** (unrelated pre-existing Windows/MT5-importability
  skip), proving Step B did not disturb Phase 4 or Step A.

**Observed, not a defect in this work:** a full-suite run including
integration tests intermittently failed with `relation "..." already
exists` / `relation "..." does not exist` errors unrelated to any specific
table. Root-caused to test/database contention, not a code defect —
`tests/integration/conftest.py::engine` drops and recreates the entire
schema per test against a single fixed database URL
(`persistence.engine.DEFAULT_TEST_URL`), and this session's own evidence
(`review/FEEDBACK.md`/`status.md` changing on disk mid-session, see the
git-log entries around 2026-08-27/28) points to a concurrent Dev-1 session
running its own integration suite against the same shared local Postgres
instance at the same time. Every test in this track's own scope passes
cleanly and repeatably when run without that collision (`tests/integration/test_agent_gateway_store.py`
run alone: 6/6, twice). Not raised as a formal finding — it is a shared
test-infrastructure property, not specific to this track, and instructions
§18 doesn't name this as an escalation trigger — but worth Dev 1 knowing
about if it recurs during any coordinated CI push.

---

## 3. Local finding register (AG-###)

See `review/AGENT_FEEDBACK.md` for the full register with evidence. Summary:

| ID | Summary | Status |
|---|---|---|
| AG-001 | `service_identity` authentication mechanism | **CLOSED** (interim shared-secret, not final mTLS/SPIFFE) |
| AG-002 | Idempotent-claim persistence for `proposal_fingerprint`/`decision_fingerprint` | **CLOSED** |
| AG-003 | Supervisor response-handling / fail-closed-on-timeout | OPEN — Step C |
| AG-004 | Assignment-scope server-side enforcement | **CLOSED** |
| AG-005 | Evidence/news ingestion path (SSRF mitigation unproven, no exposure yet) | OPEN — deferred to Step D by design |
| AG-006 | `TradeIntent.feature_snapshot_id` semantics for agent-originated intents — blocks `TradeProposal → TradeIntent` mapping | OPEN — needs a Dev-1 handshake (shared contract) |

---

## 4. Merge status — done 2026-08-28

Step A + Step B are merged and pushed. `agent/contracts` is now identical
to `origin/main` at its tip (`bf18ec5`) — not a long-lived divergent
branch, per V3 §6's explicit instruction. The next slice of work opens a
fresh short-lived branch off this point: `agent/tradeintent-mapping`
(V3 §6/§7), for AG-006 resolution + the `TradeProposal → TradeIntent`
mapping. Not yet created — see §5.

---

## 5. Next actions (V3 §18 sequence, steps D onward — A/B/C already done)

1. **D — raise AG-006 with Dev 1.** V3 §5 already gives explicit
   direction (do not make `feature_snapshot_id` optional; `DecisionContextBundle`
   should carry a trusted platform-issued `feature_snapshot_id` that the
   Gateway copies into the `TradeIntent` it constructs) — but the exact
   field addition to `DecisionContextBundle` and its mapping tests still
   need Dev-1 acknowledgment per the shared-contract handshake (instructions
   §4): identify exact need (done, this section) → propose the exact
   schema/field change → Dev 1 acknowledges → both suites updated → merge.
2. **E — implement `TradeProposal → TradeIntent` mapping** on a new
   `agent/tradeintent-mapping` branch, once D is acknowledged.
3. **F — run the shared integration path** (V3 §15) once the mapping
   exists — not available yet, correctly not attempted this pass.
4. **G/H/I** (external Trader against genuine shadow context, Supervisor
   boundary, agent-driven shadow proof) — blocked on E/F, in that order,
   per V3 §18.
5. Do not request formal reviewer input for routine continuation — only
   per V3 §19's five triggers (material safety/security ambiguity,
   required Phase-4 invariant change, unresolved authority dispute,
   unexpected agent→execution path, complete agent-driven canary
   readiness).

---

## 6. Summary for Dev 1 (canonical `status.md`, when next handed over)

> Agent Integration track, Step A + Step B (Agent Gateway ingestion+audit
> layer, ADR-005 §8's "first proof target") are merged to `main` and
> pushed to `origin/main` (`ba658c5`, `bf18ec5` — rebased from the
> original `cc16e4f`/`2f7c921` onto your F-049 work first, clean, no
> conflicts). Identity/credential authentication, assignment authorization,
> context-hash binding + expiry, idempotent proposal/NO_TRADE claiming
> with conflict detection, fail-closed audit trail. Six new PostgreSQL
> tables via migration `d4b6e2f81a37` (off your confirmed head
> `c9e1d5a3f286`) — `agent_identities`, `agent_credentials`,
> `agent_trading_assignments`, `agent_decision_context_bundles`,
> `agent_decision_outcomes`, `agent_decision_events`, all in
> `APPEND_ONLY_TABLES`. 30 new/changed tests (24 unit + 6 integration),
> plus 29 Step-A contract tests. Full non-integration suite (834 tests)
> and the migration-equivalence suite both green, re-verified again
> post-rebase in an isolated worktree/DB; nothing outside this track's own
> files imports any of it. **One open item needs your input: AG-006** —
> see §5 above; V3 already gave me direction on the shape of the fix
> (`DecisionContextBundle` carries the trusted `feature_snapshot_id`), I
> just need your acknowledgment on the exact field/schema before touching
> `TradeIntent`'s construction path. **Also flagged (§0 above):** five of
> your integration test files bypass `CRUMBLR_DATABASE_URL` via a
> hardcoded `DEFAULT_TEST_URL`, which will keep causing cross-session
> database collisions until parameterized — not fixed here since those
> are your files.
