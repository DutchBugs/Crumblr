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

**Update:** Dev 1 found and fixed the same gap independently, same day,
pushed to `main`. Confirmed clean on this side too (grepped this track's
own integration test for the same anti-pattern — no hits, it only ever
uses the `engine` fixture).

---

## 0a. Self-review hardening pass — done 2026-08-28

Ran `/code-review high` against the whole `agent_gateway` package (code,
persistence, tests) between the Step A/B merge and starting on AG-006 —
not requested by anyone, done because solid test coverage doesn't rule out
logic bugs a second look catches. Found five real issues, all fixed same
day; three were genuine correctness bugs, not style. Full detail and
evidence: `review/AGENT_FEEDBACK.md` AG-007/AG-008/AG-009 (the two closed
without a new AG number — a stale package docstring and duplicated
validation logic — are folded into the commit, not separately tracked).

- **AG-007 (rate-limit TOCTOU race, HIGH).** Reading the proposal-rate-
  limit count and claiming in separate transactions let concurrent
  proposals for one assignment each observe a stale below-limit count and
  all get accepted. Fixed with a Postgres advisory transaction lock
  (`pg_advisory_xact_lock`) serializing the whole claim→count→evaluate→
  settle sequence per `assignment_id`. Verified the fix is real, not
  incidental: manually disabled the lock, confirmed the new concurrency
  test (10 real threads, real PostgreSQL) actually fails (10/10 or 7/10
  accepted instead of the configured 3), then restored it and re-confirmed
  green.
- **AG-008 (fail-open retry replay, HIGH).** The idempotent-retry path
  read "no `REJECTED` event" as proof of acceptance, so an outcome claimed
  but never settled (a crash between claim and verdict) would make every
  future retry silently report `accepted=True` without ever running
  authorization. Fixed by making an unsettled claim resume evaluation with
  fresh inputs rather than assuming a verdict — genuinely fail-closed, not
  merely refusing forever (a raise-based fix was considered and rejected
  as unrecoverable). Event ids also switched to content-derived, so a
  resumed attempt's re-appended `RECEIVED` event can't duplicate.
- **AG-009 (unchecked `required_evidence_fields`, MEDIUM).** A Step-A
  contract field with no enforcement anywhere — a proposal with zero
  evidence was accepted even against an assignment that names required
  evidence. Fixed with a conservative check (some evidence must be cited
  when required; content-level verification is explicitly out of scope,
  same as AG-005).

Evidence: 5 new/changed tests (`tests/unit/test_agent_gateway.py::TestInterruptedClaimIsResumedNotAssumedAccepted`
×2, `::TestRequiredEvidence` ×3), plus 1 new real-concurrency integration
test (`tests/integration/test_agent_gateway_store.py::TestRateLimitIsAtomicUnderRealConcurrency`).
Full re-verification after the fixes: 24→29 unit tests still pass
unchanged (no regressions from the transaction/connection-threading
refactor), 6→7 integration tests pass, full non-integration suite
839 passed/1 skipped, ruff/mypy clean.

---

## 0b. HTTP transport — done 2026-08-31

Checked in with Dev 1 first (2026-08-31, three days after the last
exchange): no new commits on `main`, `compute_features()` extraction not
started yet (Dev 1 confirmed: `trading_agent/features.py::compute_features()`
is real but `baseline_v1`-specific; `ict_v1` has its own structurally
different `IctFeatureSnapshot`; no cross-strategy entry point exists yet;
no ETA). AG-006/Step E genuinely still blocked — not a shortcut available,
confirmed by reading the code myself before asking, same as before.

Dev 1 explicitly said no need to wait idle if there's other Gateway work
available. Asked the owner before building anything, since a wire
transport is a new externally-reachable surface, not an internal fix —
approved to proceed.

Built `agent_gateway/http.py`: a FastAPI app (`create_app(*, gateway,
clock)`) exposing exactly two routes, `POST /agent/proposals` and
`POST /agent/no-trade` — the only two operations `gateway.py`'s own
module docstring calls agent-facing. Administrative operations
(`register_identity`/`issue_assignment`/`issue_context_bundle`) get no
route at all, checked structurally
(`tests/unit/test_agent_gateway_http.py::TestNoAdministrativeRouteExists`,
mirrors `test_dashboard.py`'s own "no mutation route" pattern) — a
docstring promise is not a guarantee. Kept under `agent_gateway/`, not
`src/crumblr/api/` — `build.md`'s architecture diagram already earmarks
`api/` for Core's own Control API (HALT reset, operator controls), a
different authority boundary than this shadow-mode ingestion surface.

Authentication: the same interim shared-secret mechanism (AG-001), carried
as two headers (`X-Agent-Id`, `X-Agent-Credential`) rather than folded
into one `Authorization` value. Error mapping is deliberately coarse:
unknown agent / wrong credential / suspended agent all collapse to one
`401`, matching `AuthenticationError`'s own "never help enumerate agent
ids" discipline; impersonation is `403`; a structural content conflict
(idempotent-claim fingerprint mismatch) is `409`; malformed JSON or a
contract validation failure is `400`. A **rejected** proposal is still
`200 OK` with `"accepted": false` in the body — the same "a refusal is a
normal, fully-audited outcome, not a transport error" principle
`gateway.py` already establishes internally.

Found and fixed one real bug via the test suite itself, not by
inspection: `pydantic.ValidationError.errors()` can carry the raw
exception object in a validator's `ctx` (e.g. `TradeProposal`'s own
`_check_stop_and_target_direction`), which plain `json.dumps`
(`JSONResponse`'s encoder) cannot serialize — a domain-validator
rejection was coming back as an unhandled `500` instead of the intended
`400`. Fixed with `error.errors(include_url=False, include_context=False)`.
Regression test: `TestSubmitProposal::test_a_domain_validator_rejection_returns_400_not_500`.

Evidence: `tests/unit/test_agent_gateway_http.py` (new, 16 tests — accept/
reject, all four auth-failure/conflict status codes, malformed JSON,
malformed contract, idempotent replay, conflicting retry, both routes,
structural route/docs checks). Full non-integration suite 839→**855
passed**, 1 skipped, ruff/mypy clean. No dependency added — FastAPI/
`TestClient` were already in use by `dashboard/app.py`.

Not done: no docs page, no OpenAPI schema exposed (`docs_url`/`redoc_url`/
`openapi_url=None`, matching `dashboard/app.py`'s own convention), no
actual deployment/process wiring (`uvicorn` invocation, port, TLS
termination) — this proves the boundary exists and is safe, not that
anything is listening anywhere yet. No admin-facing transport either;
`register_identity`/`issue_assignment`/`issue_context_bundle` stay
Python-only, deliberately.

---

## 1. Where this track actually stands

| Step | Scope | State |
|---|---|---|
| A — design/contracts | ADR-005, threat model, eight contracts, structural tests | **Complete, merged to `main`/pushed to `origin/main`** (rebased+re-hashed as `ba658c5`, 2026-08-28 — original commit was `cc16e4f`, 2026-08-27, before rebasing onto Dev 1's F-049 work). |
| B — Agent Gateway in shadow | auth, assignment enforcement, idempotent proposal/NO_TRADE persistence, fail-closed error handling, HTTP transport | **Ingestion + audit layer complete, tested, merged/pushed** (`bf18ec5`), self-review hardening merged/pushed (`d6a5361`), HTTP transport built 2026-08-31 (§0b, not yet committed — see §4). `TradeProposal → TradeIntent` mapping deliberately NOT built — see AG-006 below, still blocked as of 2026-08-31. |
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

## 2. Evidence, at the Step A/B merge point (2026-08-28)

**Superseded by §0a's counts after the self-review hardening pass** (5
more tests, one more integration test file). Kept as-is below as the
historical record of what was true at the merge itself.

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
| AG-006 | `TradeIntent.feature_snapshot_id` semantics for agent-originated intents — blocks `TradeProposal → TradeIntent` mapping | OPEN — handshake in progress with Dev 1 (see §5) |
| AG-007 | Proposal-rate-limit check was a check-then-act race under concurrent submission | **CLOSED** (self-review, §0a) |
| AG-008 | Idempotent-retry path defaulted to `accepted=True` for a claimed-but-never-settled outcome | **CLOSED** (self-review, §0a) |
| AG-009 | `TradingAssignment.required_evidence_fields` was defined but never enforced | **CLOSED** (self-review, §0a) |

---

## 4. Merge status — updated 2026-08-31

Step A, Step B, and the self-review hardening pass are merged and pushed
(`d6a5361`). The HTTP transport (§0b) is built, tested, quality-gate clean,
**not yet committed** — pending the same per-turn commit confirmation
every other slice this session has gotten. `agent/contracts` will again be
identical to `origin/main` once it lands, per V3 §6's "not a long-lived
divergent branch" instruction. `agent/tradeintent-mapping` (V3 §6/§7) is
still not created — still correctly waiting on D below, not started early.

---

## 5. Next actions (V3 §18 sequence, steps D onward — A/B/C already done)

1. **D — raise AG-006 with Dev 1. Still in progress, checked in again
   2026-08-31** (three days after the first exchange, my own initiative —
   no new commits/status entries appeared, so I verified nothing had
   changed before assuming it had). Dev 1 confirmed the gap is real and
   specifically ruled out the shortcut I'd found on my own
   (`trading_agent/features.py::compute_features()` — real, but
   `baseline_v1`-specific; `ict_v1` has its own structurally different
   `IctFeatureSnapshot`; no cross-strategy entry point exists for either).
   Extraction not started, no ETA. Explicitly told me not to wait idle —
   see §0b, which is what I built instead while this stays blocked.
2. **E — implement `TradeProposal → TradeIntent` mapping** on a new
   `agent/tradeintent-mapping` branch, once D lands.
3. **F — run the shared integration path** (V3 §15) once the mapping
   exists — not available yet, correctly not attempted this pass.
4. **G/H/I** (external Trader against genuine shadow context, Supervisor
   boundary, agent-driven shadow proof) — blocked on E/F, in that order,
   per V3 §18. The HTTP transport (§0b) is the piece G will actually need
   once E/F land — built ahead of time since it did not itself depend on
   AG-006.
5. Do not request formal reviewer input for routine continuation — only
   per V3 §19's five triggers (material safety/security ambiguity,
   required Phase-4 invariant change, unresolved authority dispute,
   unexpected agent→execution path, complete agent-driven canary
   readiness).

---

## 6. Summary for Dev 1 (canonical `status.md`, when next handed over)

> Agent Integration track, Step A + Step B (Agent Gateway ingestion+audit
> layer, ADR-005 §8's "first proof target") are merged to `main`/`origin/main`
> (`ba658c5`, `bf18ec5`, rebased onto your F-049 work first, clean). A
> same-day self-review then caught and fixed three real bugs, not style:
> a proposal-rate-limit check-then-act race under concurrent submission
> (fixed with a Postgres advisory transaction lock serializing claim→
> count→evaluate→settle per assignment — verified by confirming the new
> concurrency test actually fails with the lock disabled, then passes with
> it restored), an idempotent-retry path that defaulted to `accepted=True`
> for a claimed-but-never-settled outcome (fixed by resuming evaluation
> with fresh inputs instead of assuming a verdict), and an unenforced
> `required_evidence_fields` contract field. Full detail: `review/AGENT_FEEDBACK.md`
> AG-007/008/009. 35 new/changed tests total, full non-integration suite
> 839 passed/1 skipped, migration suite green, nothing outside this
> track's own files imports any of it.
>
> **AG-006 handshake is live** (direct cross-session messages, not just
> this document) — Dev 1 found a real gap in my original proposal
> (`compute_features()` doesn't exist standalone yet, only fused into
> each strategy's `evaluate()`) and is extracting it; not blocking their
> current execution-activation work, they'll ping me when it's ready.
> Checked in again 2026-08-31 (three days, no new activity visible) — still
> genuinely blocked, no ETA, explicitly told not to wait idle.
>
> **New since then (2026-08-31): HTTP transport.** `agent_gateway/http.py`
> — a FastAPI app exposing only `POST /agent/proposals`/`POST /agent/no-trade`,
> nothing administrative reachable, kept out of `api/` deliberately (your
> Control API's territory, different authority boundary). 16 new tests,
> full suite now 855 passed/1 skipped. One real bug found by the tests
> themselves and fixed: a domain-validator rejection's `ValidationError.errors()`
> carried a raw exception object `json.dumps` couldn't serialize, turning
> an intended `400` into an unhandled `500` — fixed with
> `include_context=False`. Not yet committed, same as everything else —
> pending the usual per-turn confirmation.
>
> **Already resolved, no action needed:** the `DEFAULT_TEST_URL`-bypasses-
> `CRUMBLR_DATABASE_URL` gap I flagged — you found and fixed it
> independently the same day, confirmed clean on my end too.
