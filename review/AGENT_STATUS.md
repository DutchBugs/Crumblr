# Agent Integration track — status

**Workstream:** Dev 2 — External Agent Integration
**Owning instructions:** `review/CRUMBLR_DEV2_AGENT_INTEGRATION_INSTRUCTIONS_V3.md`
(supersedes V2 — mandatory workspace/DB isolation, explicit AG-006
direction; see §0 below)
**Formal direction:** `feedback.1.26.md` — "Phase 5: Convergence,
Observability & DEMO Readiness" (supersedes `feedback.1.25.md`; project-wide
`review/FEEDBACK.md`)
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

**Since flagged by review 1.26 §8 as F-064** (global register, Dev-2-owned
— see `review/AGENT_FEEDBACK.md`): explicitly fine for local/shadow use,
explicitly not authorized for public/remote exposure without TLS/mTLS.
No action needed until remote deployment is actually proposed.

---

## 0c. AG-006 resolved — `agent_context_v1` platform-owned evidence, done 2026-09-01

`feedback.1.26.md` §5 arrived and settled AG-006 with a different design
than the one Dev 1 and I were both pursuing (see §0b/§5 below for that
history) — explicitly **not** a universal cross-strategy `compute_features()`.
Implemented the reviewer's actual design the same day.

**What was built:**
- `agent_gateway/evidence.py` (new): `AgentContextEvidence` — a narrow,
  honestly-named `FeatureEvidence` shape (`feature_set_version =
  "agent_context_v1"`), structurally satisfying `trading_agent.base.FeatureEvidence`
  (a `runtime_checkable Protocol`) without importing or changing that
  Dev-1-owned module. `regime` is always `Regime.UNKNOWN` — honest, not a
  guessed technical-analysis classification. Verified via `isinstance(evidence,
  FeatureEvidence) is True` before writing a single test.
- `DecisionContextBundle.feature_snapshot_id: UUID` (new required field,
  `agent_gateway/contracts.py`), included in `content_hash`'s fingerprint
  so the evidence reference cannot be swapped without changing the hash a
  proposal binds to.
- `AgentGateway.publish_context()` (new) — the "platform context
  publication" entry point: builds and durably records the evidence
  *before* constructing the bundle that references it, then issues
  through the same fail-closed path. `issue_context_bundle` itself now
  independently refuses a bundle whose `feature_snapshot_id` does not
  resolve to a real stored snapshot (`UnknownFeatureSnapshotError`) —
  review 1.26's explicit "Gateway refuses an unknown/missing snapshot"
  requirement, enforced regardless of which entry point a caller uses.
- New `FeatureEvidenceStore` Protocol + `InMemoryFeatureEvidenceStore`
  (`agent_gateway/stores.py`). **No schema/migration change** — the real
  `persistence/features.py::FeatureSnapshotStore` (already built for
  `baseline_v1`/`ict_v1`, shared, unmodified) satisfies the Protocol
  directly, exactly matching the review's own claim: "the existing
  feature persistence layer is already intentionally generic... that is
  enough."

**Every requirement review 1.26 §5 named, satisfied by construction:**
snapshot created by Crumblr only (nothing in `evidence.py` accepts agent
input); exists durably before the bundle is issued (`publish_context`'s
own ordering, plus `issue_context_bundle`'s independent check catches any
other caller); immutable/content-addressed (`feature_snapshot_id` is a
content-derived `uuid5`, mirroring `compute_features`'s own derivation —
republishing the same `(symbol, now)` collapses to one snapshot, proven
by test); bundle `content_hash` includes the reference; Gateway refuses
an unknown/missing snapshot; no placeholder UUIDs anywhere in the
construction path; no post-hoc fabrication (evidence is always recorded
strictly before the bundle referencing it exists); external `evidence_refs`
remain completely separate from this platform evidence identity (never
touched by this change).

Evidence: 8 new tests (`tests/unit/test_agent_gateway_contracts.py` +1,
`tests/unit/test_agent_gateway.py::TestFeatureEvidence` +4,
`tests/integration/test_agent_gateway_store.py::TestFeatureEvidenceAgainstRealPostgres`
+3) — full non-integration suite 855→**861 passed**, integration suite
7→**10 passed** against the isolated DB, ruff/mypy clean throughout.
`AG-006` moved to Closed in `review/AGENT_FEEDBACK.md` with full evidence.

**Not done — deliberately out of this slice's scope, next up:**
`TradeProposal → TradeIntent` mapping itself (`TradeIntent.feature_snapshot_id
= bundle.feature_snapshot_id`) — review 1.26 §7 lists this as its own,
separate priority item (#2, after AG-006's #1), not part of AG-006's own
closure.

---

## 0d. `TradeProposal → TradeIntent` mapping — done 2026-09-01 (review 1.26 §7 item 2)

**What was built:** `AgentGateway._build_trade_intent(*, proposal, assignment)
-> TradeIntent` — a pure, deterministic mapping with no dependency on the
current call's `now`. `AgentDecisionOutcomeResult` gained `trade_intent:
TradeIntent | None`, populated only when a `TradeProposal` is accepted
(never for `NO_TRADE`, never for a rejection). `intent_id` is
content-derived (`uuid5` of `proposal_id`) so a replayed identical retry
reconstructs byte-identical content rather than needing separate durable
storage — the same discipline the rest of this track already uses
everywhere. `feature_snapshot_id = bundle.feature_snapshot_id` directly —
AG-006's whole point, carried through. `strategy_id` is a fixed sentinel
(`"external_agent"`, never `baseline_v1`/`ict_v1`); `strategy_version =
assignment.strategy_artifact_hash`. `http.py`'s response body now
surfaces `trade_intent.intent_id`/`decision_hash` (identity only, not the
full contract) — enough for a future external Supervisor to bind its
review to the exact intent, matching `SupervisorReview`'s own design
(ADR-005 §5).

**Self-review (medium) caught two real bugs before this shipped, both fixed:**
- `model_version` was originally sourced from a live `AgentIdentity`
  lookup at mapping time — but identity is a mutable, append-only-latest-
  wins snapshot (`register_identity` can change it any time), so a
  replayed retry after a re-registration could reconstruct a *different*
  `TradeIntent` (different `model_version`, different `decision_hash`)
  than the original, directly contradicting the documented determinism
  guarantee. Fixed by never sourcing anything from identity in the
  mapping — `model_version` is always `None` for now; real per-agent-model
  provenance needs its own immutable, content-addressed home, not a live
  side-channel. Regression test: retries after re-registering the agent
  with a different `model_version` and confirms the reconstructed intent
  is unchanged.
- `TradingAssignment.strategy_artifact_hash` was a bare, unconstrained
  `str`, but it feeds `TradeIntent.strategy_version` (a `VersionTag`,
  1-128 chars) — an assignment registered with an empty hash would
  register fine and then crash the *first* proposal accepted against it
  with an uncaught `pydantic.ValidationError` (an unhandled `500` at the
  HTTP layer, since that exception type isn't in `http.py`'s error map).
  Fixed by tightening the field to `VersionTag` itself, so a malformed
  value fails closed at registration time, not deep inside acceptance.
  Regression test at the contract level.

Also found, fixed, and covered separately: `TradeProposal.reason_codes`
has no non-empty constraint, but `TradeIntent._check_directional_requirements`
requires at least one — a proposal with `reason_codes=()` would have been
accepted by the Gateway and then failed `TradeIntent` construction. New
`AgentRejectionReason.MISSING_REASON_CODES` catches this at evaluation
time, as an ordinary auditable rejection, before `_build_trade_intent` is
ever called.

Evidence: 10 new/changed tests (`tests/unit/test_agent_gateway.py::TestTradeIntentMapping`
×8, `tests/unit/test_agent_gateway_http.py` ×2,
`tests/unit/test_agent_gateway_contracts.py` ×1,
`tests/integration/test_agent_gateway_store.py` ×1 — restart-safety of the
reconstructed intent against real Postgres). Full non-integration suite
861→**871 passed**, integration suite 10→**11 passed**, ruff/mypy clean.

**Not done — deliberately next, review 1.26 §7 items 3+:** wiring the
constructed `TradeIntent` through intent-time Risk, the deterministic
Policy Gate, and `DecisionCapsule` sealing (the "shared no-MT5 integration
path") — this slice only proves the mapping itself exists and is correct;
it does not yet call into any Dev-1-owned execution/risk module.

**Before starting that wiring**, asked Dev 1 directly whether there were
gotchas calling `risk.policies.evaluate()`/sealing a capsule from outside
`application/orchestration.py`'s usual flow. There was a real one — see
**AG-012** in `review/AGENT_FEEDBACK.md`: `PortfolioState.ledger` is
stateful and per-process, held in memory by `LiveDecisionOrchestrator`
across cycles rather than reloaded before every `evaluate()` call. A
Gateway-driven evaluation running as its own independent process would
hold its own independent copy of that same ledger — a lost-update race on
one shared daily-loss/drawdown budget between an internal decision and an
external-agent proposal, invisible to either pipeline on its own. Not a
safety gap today (`order_send` unreachable regardless), but a real
architecture question for before `feedback.2.0` could treat agent-driven
submission as real. Adopted Dev 1's suggested interim approach for this
slice: `risk.session.recover_session()` freshly, immediately before every
Gateway-driven `evaluate()` call, never cached in-process — narrows but
does not eliminate the race, explicitly documented as shadow-mode-only.

---

## 0e. `feedback.1.27` ACK — 2026-09-01

```text
ACK feedback.1.27

current branch/worktree: agent/contracts, .claude/worktrees/agent-dev2
current main SHA fetched: fa0a6b3

DONE since 1.26:
- AG-006 (agent_context_v1 evidence) closed
- TradeProposal -> platform-owned TradeIntent mapping implemented, merged,
  pushed (two self-review findings fixed: AG-010 model_version identity
  drift, AG-011 unconstrained strategy_artifact_hash)
- AG-012 (risk-ledger statefulness, raised by Dev 1) recorded and
  documented with the accepted shadow-only interim mitigation

NEXT: item A (this review's Dev 2 work order) — complete the shared
no-MT5 integration path: TradeIntent -> fresh intent-time Risk ->
deterministic Policy Gate -> DecisionCapsule boundary. Then B-J, the
Static Agent bridge (StaticAgentContextPayload, HTTP client, response
translation, AgentGateway submission, idempotent-replay/failure-mode
proof, synthetic smoke test, first genuine live-shadow decision).

BLOCKED: item A cannot be written yet without deciding where
PortfolioState.account/open_positions (AccountState/PositionState) come
from for a Gateway-driven evaluate() call. Both are documented as "live"
reads (models.py: connected/trade_allowed/expert_allowed/server, all
MT5-terminal-derived) and the only existing source is SimulatedBroker
inside ReplayOrchestrator -- BrokerStateStore exists but nothing
populates it yet (no real broker). Asked Dev 1 directly (SendMessage,
not yet answered) with three candidate options; leaning toward running
the Gateway-driven evaluation against the same replay's SimulatedBroker
state per this review's own EXTERNAL_AGENT_ARCHITECTURE_GUIDE.md Stap B
guidance ("historische replay en live shadow data... de huidige
in-process strategy uitsluitend als vergelijking/twin"), but holding for
Dev 1's answer before writing code rather than guessing and reworking.

NEEDS THE OTHER TRACK: the AccountState/PositionState sourcing decision
above (Dev 1 owns risk/policies.py, risk/session.py,
application/orchestration.py and knows what a real broker connection
will eventually look like); and, per this review's §8/§9, a read-only
Core persistence/query seam if the Static Agent bridge (item B) needs one
that does not yet exist -- not yet known to be needed, flagged in advance
per this review's instruction.
```

---

## 0f. Shared no-MT5 integration path (review 1.26 §7 item 3 / feedback.1.27 §6.A) — core wiring done 2026-09-01

`TradeIntent -> intent-time Risk -> deterministic Policy Gate -> DecisionCapsule`,
stopping there — no `ApprovedOrder`, no `order_check`, no `order_send` anywhere
reachable. New `agent_gateway/decision_path.py`, `evaluate_agent_trade_intent()`.

**Design resolved by the owner-requested reviewer decision**
(`review/INTEGRATION_NOTICES.md`, 2026-09-01, "Record reviewer decision for
agent PortfolioState source") and confirmed with Dev 1 (cross-session message,
who checked `application.broker_state.capture_broker_state()` and
`application/live_decision.py` directly rather than guessing): account/position
state is never read here. A narrow `PortfolioStateProvider` Protocol is
injected instead — a fake is correct for this level of proof; a genuine
LIVE_SHADOW claim needs a Core-adjacent process (sibling of
`application/live_decision.py`, not code inside `agent_gateway/`) backing it
with `capture_broker_state()`. Not built yet — deliberately deferred to item I,
after the wiring itself was proven against a fake first, per the reviewer's own
"tests may use a fake provider; the first genuine live-shadow proof must use
the Core-owned fresh provider."

AG-012's interim mitigation is implemented here, not merely documented: every
call recovers the risk session fresh via `risk.session.recover_session()`,
never caching a ledger across calls — proven by
`TestAG012FreshSessionRecoveryEveryCall` (a loss gate that can only trip if the
store's carried-forward state genuinely participated in that specific call).

Self-review (`/code-review medium`) before the first commit found one real bug
(**AG-014**, HIGH, fixed same day): the function sealed a HALT-verdict capsule
without ever calling `kill_switch.trip()` — `policies.evaluate()`/
`pretrade.evaluate()` only name a HALT, they never trip the switch themselves,
which is the caller's job in every other pipeline in this codebase. Fixed with
a `_trip()` helper mirroring `application/live_decision.py`'s own, called on
both a Risk HALT and a Supervisor HALT.

Also surfaced, not a bug in this module but a real cross-cutting gap
(**AG-013**, MEDIUM, not blocking — see `review/AGENT_FEEDBACK.md`): real
`agent_context_v1` evidence always carries `regime=UNKNOWN` (AG-006, by
design), and the shipped config's `veto_on_unknown_regime=True` means a
directional external-agent proposal can reach Risk `PASS` but can never reach
Supervisor `APPROVE` today — only `VETO` or `NO_TRADE`. Review 1.27 §6 item I
already allows "NO_TRADE is valid proof," so this does not block the next
step, but it means a directional-APPROVE demonstration needs an owner decision
first.

Evidence: 16 new tests, `tests/unit/test_agent_decision_path.py` — NO_TRADE
still seals a capsule, PASS+APPROVE, Risk BLOCK stops before the Policy Gate,
an already-halted kill switch produces `SYSTEM_HALTED`, both HALT verdicts now
trip the switch (AG-014 regression, 3 tests), AG-012 freshness (2 tests),
replay/idempotency (capsule reseals identically for the same `outcome_id`, 2
tests), fail-closed defaults (`incident_status`/`reconciliation_status`
`UNKNOWN`, 2 tests), and the AG-013 proof. ruff/ruff format/mypy clean
throughout. Full gate (unit + integration against `crumblr_test_dev2`) run
twice: once before the AG-014 fix (1071 passed) and once after it, on the
final committed state — **1074 passed**, 3 skipped (pre-existing, unrelated:
filesystem-permission and optional-MT5-import skips), 0 failed.

**Not done yet, deliberately next:** resolving `DecisionContextBundle`
references (`market_snapshot_id`, `feature_snapshot_id`,
`instrument_spec_version`) into the full domain objects
(`MarketSnapshot`/`FeatureEvidence`/`InstrumentSpec`) this function takes as
pre-resolved inputs — this function is intentionally narrow, "everything
pre-observed," matching `risk.policies.evaluate()`'s own philosophy; the
resolution/composition layer is separate work, naturally folding into item B
onward (the Static Agent bridge) or item H (the first synthetic transport
smoke test). The real Core-backed `PortfolioStateProvider` for a genuine
LIVE_SHADOW proof is item I, also not built yet.

---

## 0g. Static Agent bridge (feedback.1.27 §6 item B) — investigation done 2026-09-01, blocked on one design question

Cloned `DutchBugs/crumblr-static-agent-host` (shallow, read-only, scratchpad
only — not committed here) to get ground truth on its actual wire contract
rather than build against feedback.1.27's prose description alone.

Confirmed the real wire protocol: `POST /v1/trader/evaluate` (bearer auth,
`application/json`, 1MB cap), body validated against
`contracts/crumblr-trader-context-1.0.schema.json` ("TraderContext 1.0"),
response shaped by `contracts/crumblr-trader-decision-1.0.schema.json`
("TraderDecision 1.0"). Matches feedback.1.27 §5.2's warning exactly: the
response's `decision_type: TRADE_INTENT` carries its own nested
`trade_intent` object that must never be treated as the platform
`TradeIntent` — untrusted proposal material only, to be translated into a
Crumblr `TradeProposal` and submitted through the existing `AgentGateway` so
it constructs the one authoritative platform `TradeIntent`, exactly as
§5.2 specifies.

**The finding that blocks starting to write code:** the fork's frozen
trader (`crumblr_trader.py::CrumblrStaticTrader.evaluate`) does no
technical analysis of its own. `TraderContext.features.observation` must
already carry a fully-computed, already-confirmed ICT setup —
`event_type` (`SWEEP_DETECTED`/`FVG_CONFIRMED`/`STRATEGY_TRIGGER`/etc.),
`sweep_time_utc`, `fvg_time_utc`, `mss_time_utc`, `direction`, `session`,
`entry_price`, `stop_loss`, `take_profit`, `rr`, `expiry_bars`,
`pivot_level`. `static_trader.py::StaticTrader.decide` only validates and
formats that into an order; it never detects a sweep or an FVG. Whoever
calls this fork must therefore hand it a complete ICT setup, not merely
market/instrument context — `agent_context_v1` (AG-006) is deliberately
the opposite of this (no TA, `regime` always `UNKNOWN`) and is the wrong
source for this specific bridge.

Crumblr already computes exactly this kind of structure internally:
`trading_agent/ict.py` (`ict_v1`, Dev-1/Core-owned) — `IctFeatureSnapshot`
carries `sweep_direction`, `fvg_lower`/`fvg_upper`, `swept_level`, and its
`_evaluate()` already produces a full entry/stop/target `TradeIntent`.
Matches `EXTERNAL_AGENT_ARCHITECTURE_GUIDE.md`'s own framing of the
internal strategy becoming the comparison/twin data source for the
external hop ("Draai de huidige in-process strategy uitsluitend als
vergelijking/twin") — the Static Agent is a deterministic plumbing-proof
fixture (feedback.1.27's own closing framing), not a competing
decision-maker, so feeding it Crumblr's own already-computed ICT
structure to format/validate is consistent with what it's *for*.

Asked Dev 1 directly (SendMessage, not yet answered) whether reusing
`ict_v1`'s structural detection as the source for this bridge's
`features.observation` block is the intended design, and — if so — the
right way to call it read-only from outside
`application/orchestration.py`'s usual flow, since it's their file and
building a parallel ICT detector inside `agent_gateway/` would be exactly
the "reinvent instead of reuse" mistake this session's coordination
pattern exists to catch before it happens. Holding on `StaticAgentContextPayload`
code until that answer lands, same discipline as the item-3
`PortfolioStateProvider` question.

**Update, same session: Dev 1 confirmed** `ict.py::evaluate()` (public,
standalone, no MT5/DB coupling) is the right call, and flagged that
`IctFeatureSnapshot`'s fields don't line up 1:1 with what the fork wants
(no per-event timestamps, no `session`/`rr`/`expiry_bars`/`pivot_level` by
those names). Verified that independently by reading `ict.py` directly —
confirmed, and tractable (`rr`/`expiry_bars` are honestly derivable from
the intent/config, `pivot_level` maps from `swept_level`, the killzone→session
mapping is total for every reachable case since `TRADING_KILLZONES` only
ever permits `LONDON_OPEN`/`NEW_YORK_AM`).

**Then found something bigger while reading the actual translation code
(`crumblr_trader.py::CrumblrStaticTrader.evaluate()`) and the strategy
package's `reason_codes.json` — see AG-015, `review/AGENT_FEEDBACK.md`.**
`features.observation.reason_codes` is validated against a *closed,
fork-specific vocabulary* encoding Jari's particular "ICT Silver Bullet /
Pivot 2.2" state machine (`OUTSIDE_SESSION`, `WAITING_FOR_MSS`,
`PIVOT_2_2_CONFIRMED`, `STATIC_STRATEGY_TRIGGER_VALID`, …) — not a generic
ICT vocabulary, and not something `ict_v1`'s own reason codes
(`market_closed`, `no_liquidity_sweep`, `already_positioned`, …) map onto
honestly beyond a couple of coincidental overlaps. This check runs
*unconditionally*, before the NO_TRADE/STRATEGY_TRIGGER branch, so there is
no way to sidestep it by only ever sending NO_TRADE. This is a real
architectural question, not a naming gap — see AG-015 for the three
options identified (Crumblr genuinely implements enough of the Pivot-2-2
methodology to produce the real vocabulary; the fork gains a second,
Crumblr-facing reason-code set; or the first bridge proves only the
`market_data_health != HEALTHY` path, which `CrumblrStaticTrader.evaluate()`
short-circuits on *before* ever touching `reason_codes` at all — a
genuinely honest, buildable proof of the transport/auth/schema/idempotency
chain that does not require resolving the vocabulary question first).

Not yet raised with Dev 1 as of this entry (about to send). Per
`feedback.1.27` §12's own return-to-reviewer criteria ("the team cannot
resolve the context-materialization... seam without an architectural
decision") this may need to go to the reviewer if Dev 1 doesn't already
know the answer — not escalating unilaterally before giving Dev 1 the
chance to react first.

**Update, same session: it went to the reviewer, and came back resolved.**
`feedback.1.28.md` — "Strategy-neutral Core & external strategy ownership"
— landed within the hour. Full architectural correction, GO verdict.
Confirms AG-015 exactly and goes further: the mistake wasn't the reason-code
vocabulary specifically, it was that "the strategy computation is on the
wrong side of the interface" at all. All three mapping approaches AG-015's
own resolution notes were weighing are explicitly rejected. New project-wide
finding **F-066** ("External-agent production path must be strategy-neutral")
tracks the correction from here; AG-015 is closed, superseded by F-066 (see
`review/AGENT_FEEDBACK.md`).

Revised Dev-2 work order (`feedback.1.28.md` §11): A (finish the
unhealthy-market Static Agent NO_TRADE smoke proof, unaffected by any of
this) — B (stop the `ict_v1`→Pivot-2-2 mapping entirely, confirmed rejected)
— C (`AgentMarketContextV1`, strategy-neutral market data, replacing the
`StaticAgentContextPayload` design that assumed a computed observation) — D
(`DecisionContextBundle`/`content_hash` stays the trusted binding;
`agent_context_v1` stays an audit anchor, not strategy analysis) — E (Gateway
reason-code handling becomes structural/opaque, no global whitelist) — F
(split the external-agent Policy path away from `Regime`/strategy-id/
confidence assumptions) — G (coordinate fork-side strategy-runtime work with
the external Agent Developer) — H (strategy-neutrality tests with two
incompatible vocabularies) — I (first HEALTHY genuine Static Agent shadow
decision, after G) — J (external Supervisor boundary).

**Item F — done, same session.** `agent_gateway/decision_path.py`'s Policy
Gate step no longer calls `evaluator.pretrade.evaluate()` for the
external-agent path at all. New `_evaluate_platform_policy()`: checks only
reconciliation and incident platform-safety health (mirroring
`pretrade.evaluate()`'s own "safety state, checked before anything else"
block), never reads `features`/`Regime`, no strategy-id/model-version
whitelist, no confidence interpretation. Deliberately **not** togglable via
`config.supervisor.enabled` — feedback.1.28 §7 calls these "hard checks" for
the external-agent path, not part of a strategy-envelope switch (self-review
finding, fixed same pass, see below). This directly closes **AG-013**
(`review/AGENT_FEEDBACK.md`): a directional external-agent proposal built
from real production evidence now reaches Supervisor `APPROVE`, proven
against the real evidence builder, not a test double. Also added the F-066
item 8 regression proof at this module's level: an intent with a completely
unrelated `strategy_id` and an arbitrary, made-up reason-code vocabulary
reaches the identical `APPROVE` outcome — nothing here is strategy-specific.

Self-review (`/code-review medium`) on this refactor found one real,
non-blocking issue: the first `_evaluate_platform_policy` docstring
overclaimed exact parity with `pretrade.evaluate()`'s safety block (the
incident check there is gated behind `policy.enabled`; this module's is not,
deliberately). Fixed the docstring to state the divergence and why, and
added a regression test proving `config.supervisor.enabled=False` has no
effect on this path (it isn't even a parameter the function reads).

Evidence: `intents_in_last_hour` parameter removed (no longer used —
platform proposal/rate limits are already enforced upstream by
`AgentGateway._evaluate_proposal` before this function ever sees an intent).
18 tests in `tests/unit/test_agent_decision_path.py` (was 16 before this
pass — `TestAG013RealAgentEvidenceRegimeIsAlwaysUnknown` replaced with
`TestAG013Resolved`, new `TestStrategyNeutrality`, new
`test_config_supervisor_enabled_false_does_not_bypass_platform_safety_checks`).
ruff/ruff format/mypy clean; full gate (unit + integration against
`crumblr_test_dev2`) **1076 passed**, 3 skipped (pre-existing, unrelated),
0 failed.

---

## 0h. Static Agent bridge, item A — unhealthy-market NO_TRADE smoke proof — core wiring done 2026-09-01

New `agent_gateway/static_agent_transport.py`:
`build_unhealthy_market_context()`, the outbound `TraderContext 1.0` wire
payload for exactly the one directional case this track can honestly send
today (`market.market_data_health != "HEALTHY"`, which
`CrumblrStaticTrader.evaluate()` short-circuits on *before* it ever reads
`features.observation.reason_codes` — verified by reading the actual fork
code, not assumed). Refuses (raises) a payload that claims
`market_data_health == "HEALTHY"` — that path reaches the closed
Pivot-2-2 reason-code vocabulary AG-015/F-066 says this track must not
speak. `features.observation` carries only a schema-valid, clearly-labelled
placeholder (`NOT_EVALUATED_MARKET_DATA_UNHEALTHY`) that the fork's own
code never reads on this path.

`compute_input_identity`/`canonical_json`/`canonical_decimal` mirror the
fork's own `crumblr_strategy_agent.identity` algorithm byte for byte — the
fork independently recomputes and compares this value, so this has to
match exactly, not approximately.

**Verified against the real fork, not just its schema.** Cloned
`DutchBugs/crumblr-static-agent-host` a second time with
`-c core.autocrlf=false` (the first, default clone failed the fork's own
`StrategyPackage.verify()` self-check with a source-hash mismatch —
tracked down to Windows `core.autocrlf=true` converting the frozen
`.mq5` source's line endings on checkout, a local clone artifact, not a
real defect in the fork's repository; confirmed by re-cloning with
autocrlf disabled). Generated a payload with this module, fed it to the
fork's own `crumblr_strategy_agent.cli evaluate` command (pure stdlib, no
install needed) against the real, unmodified `ICT_SB_EURUSD_PIVOT2 5.0`
package: validated cleanly, `input_identity` matched the fork's
independent recomputation, and it produced exactly the expected
`decision_type: NO_TRADE`, `reason_codes: ["MARKET_DATA_STALE"]`,
`reason_code_source: "CRUMBLR_INTEGRATION"` decision — a genuine,
honest end-to-end proof of the schema/identity/transport chain, not
merely a shape check against documentation. Not vendored or made a CI
dependency — the fork stays a scratchpad-only clone, per instructions
never importing external strategy code into Crumblr.

Self-review (`/code-review medium`) before commit found two real issues,
both fixed same pass: (1) the placeholder `features.observation` was built
via a shallow copy of a module-level dict, sharing the same mutable
`reason_codes` list object across every call — a caller mutating one
payload's list would have silently corrupted every other call's; (2)
`UtcDatetime`-typed plain dataclass fields/function parameters get none of
Pydantic's UTC coercion for free (that only runs inside model
construction), so a naive or non-UTC datetime was silently accepted and
would have produced a malformed timestamp the fork rejects instead of
failing closed at the Crumblr boundary. Fixed: `_stamp()` now explicitly
checks `tzinfo`/`utcoffset()`; the observation dict is built fresh every
call. Re-ran the real fork round-trip after both fixes — identical
`input_identity` and decision output, confirming the fixes changed nothing
about the actual wire content for a well-formed call.

Evidence: 19 tests in new `tests/unit/test_static_agent_transport.py`
(schema shape, refusals — including the two self-review regressions —
`input_identity` determinism/format, and the canonicalization helpers
against known-good outputs). ruff/ruff format/mypy clean; full gate (unit + integration against
`crumblr_test_dev2`) **1098 passed**, 3 skipped (pre-existing, unrelated),
0 failed.

**Not done yet:** an HTTP client (the fork's real transport is
`POST /v1/trader/evaluate`, bearer-authenticated, 1MB body cap — this
session's verification used the fork's local `cli evaluate` command
directly, not HTTP, since Crumblr's own production code needs a real
client with timeout/size-limit/no-redirect/fail-closed handling, item C
from `feedback.1.27.md` §6, not yet built), the response→`NoTradeDecision`
translation and submission through `AgentGateway` (this proof currently
stops at "the fork validated and returned the expected decision," not yet
"Crumblr accepted that decision as an audited outcome"), items C
(`AgentMarketContextV1` contract), E (opaque Gateway reason-code handling
— `AgentGateway`'s own contracts/validation still need a look for any
Core-side vocabulary assumptions), G (fork-side coordination — not this
track's code to write), and H (a full end-to-end strategy-neutrality proof
through the Gateway/HTTP layer, not just this module).

---

## 0i. Agent Gateway event-conflict hardening — done 2026-09-01 (AG-016)

User-directed priority. `AgentDecisionOutcomeStore.append_event()`
(`agent_gateway/stores.py`, `persistence/agent_gateway.py`) had no
fail-closed conflict check at all — the one method in this package that
did not follow its own module docstring's rule ("the same id with
different content always raises, never silently overwrites"). A
same-`(outcome_id, event_type)` re-append with genuinely different
`reason_codes`/`detail` was silently discarded (`ON CONFLICT DO NOTHING` /
in-memory early-return), never detected.

New `EventConflictError` (`agent_gateway/errors.py`), wired into
`http.py`'s `_CONFLICT_TYPES` so it maps to `409`, not an unmapped `500`.
Both stores now read the already-durable row back on conflict and compare
content rather than storing a new fingerprint column — no schema/migration
change needed.

**Self-review caught a real regression in the fix itself, before commit.**
The first version compared `occurred_at_utc` too. `RECEIVED` is
legitimately re-appended on every resumed-but-unsettled retry
(`AgentGateway.submit_trade_proposal`/`submit_no_trade`'s AG-008 path —
"claimed by an earlier attempt that never recorded a verdict, fall through
and evaluate fresh") with that call's own fresh wall-clock `now`, which the
original attempt's `now` will never match. Including the timestamp in the
comparison would have converted every resumed retry into a permanent `409`
— worse than the original gap, since an interrupted claim would become
unrecoverable rather than merely under-defended. Fixed to compare only
`reason_codes`/`detail`, matching `_claim`'s own precedent (its fingerprint
already excludes `claimed_at_utc` for the same reason). Confirmed the
existing `tests/unit/test_agent_gateway.py
::TestInterruptedClaimIsResumedNotAssumedAccepted` — which calls
`submit_trade_proposal` a second time with a `now` deliberately different
from the original claim's — would have caught this regression on its own
even without the dedicated new tests.

Evidence: `tests/unit/test_agent_gateway_stores.py
::TestAppendEventConflictDetection` (5 tests — identical-content no-op,
different-reason-codes/detail raises, different-`occurred_at_utc`-alone
does *not* raise, a rejected conflict does not corrupt the already-durable
row), `tests/integration/test_agent_gateway_store.py
::TestEventConflictIsDetectedUnderRealPostgres` (3 tests, the same
properties against a real database). Full gate: ruff/ruff format/mypy
clean; full gate (unit + integration against `crumblr_test_dev2`)
**1106 passed**, 3 skipped (pre-existing, unrelated), 0 failed.

---

## 0j. Real HTTP unhealthy NO_TRADE roundtrip — done 2026-09-01

User-directed priority, next after §0i. New
`agent_gateway/static_agent_client.py`: an outbound HTTP client for the
fork's `POST /v1/trader/evaluate`, stdlib-only (`urllib.request`) rather
than a new project dependency — `httpx2` is dev/test-only
(`pyproject.toml`), unavailable in production. Satisfies
`feedback.1.27.md` section 6 item C's four named requirements: strict
timeout, response-size limit, schema validation (JSON object or refuse),
no redirects (refused entirely via a custom `HTTPRedirectHandler`, never
followed), fail-closed handling (`StaticAgentTransportError` subclasses
for every way the call itself can fail; a non-2xx status the server
genuinely answered with is returned as data, never raised — the fork's own
rejection envelope is meaningful JSON a caller must translate).

**Verified against the real, running fork server, not a mock.** Started
`crumblr_strategy_agent.cli serve` from the raw (`autocrlf=false`) clone
with a real bearer token, POSTed a payload built by §0h's
`static_agent_transport.py` over genuine HTTP: **200**, the expected
`NO_TRADE` / `MARKET_DATA_STALE` / `CRUMBLR_INTEGRATION` decision, byte-
identical in shape to the earlier CLI-only proof. Also proved the wrong
bearer token gets a genuine **401 UNAUTHORIZED** through this client's own
response handling, not a raised exception (a non-2xx answer is data).

Self-review (`/code-review medium`) before commit found one real gap,
fixed same pass: `evaluate()`'s fail-closed contract only wrapped
`opener.open()` (the connect/headers phase) — a server that answers
promptly but stalls mid-body-write let a bare `TimeoutError`/`OSError`
escape from `response.read()` instead of the documented typed exception,
which would have crashed any caller that only catches
`StaticAgentTransportError` to fail closed. Fixed with its own
`try`/`except` around the read. Tracked as **AG-017** (closed).

Evidence: 9 tests in new `tests/unit/test_static_agent_client.py`, against
small local `http.server`-based test doubles (no mocking library is a dev
dependency) — 2xx passthrough, bearer token/content-type sent correctly,
a 4xx rejection envelope returned as data, a redirect refused, an
oversized response refused, non-JSON/non-object responses refused, a
connect-phase timeout, and the AG-017 mid-body-stall regression. ruff/ruff
format/mypy clean; full gate (unit + integration against
`crumblr_test_dev2`) **1118 passed**, 3 skipped (pre-existing, unrelated),
0 failed.

**Not done yet:** the response→`NoTradeDecision` translation and
submission through `AgentGateway` (this client proves Crumblr can reach
the fork and get an honest answer back; it does not yet turn that answer
into an audited Crumblr outcome) — the next item on the user's priority
list.

---

## 0k. Response → `NoTradeDecision` → `AgentGateway` — done 2026-09-01

User-directed priority, next after §0j. New
`agent_gateway/static_agent_translate.py`: `translate_no_trade_response()`
+ `submit_static_agent_no_trade()` — the end of the bridge for the
unhealthy-market case. Matches `feedback.1.27.md` section 5.2 exactly:
scope is deliberately `decision_type == "NO_TRADE"` only — a
`TRADE_INTENT` response's nested `trade_intent` object must never be
accepted as the platform `TradeIntent`, and building that translation
stays out of scope until AG-015/F-066's fork-side strategy-runtime
question is resolved.

**Never trusted blindly.** Before constructing a decision: `input_identity`
must match what this bridge actually sent (proves the response answers
*this* request, not a stale or mismatched one), the echoed `strategy`
block must match the known frozen-package identity exactly, and
`executable`/`execution_authority` must both be `false` — any disagreement
refuses outright.

**Proven against the real captured fork response, not a hand-built
fixture.** `REAL_FORK_RESPONSE` in the test file is the exact JSON body
§0j's live HTTP round trip returned — used for the translation tests *and*
a full end-to-end submission through a real `AgentGateway` (in-memory
stores), which comes back `accepted=True`.

Self-review (`/code-review medium`) before commit found one real,
non-obvious gap, fixed same pass — tracked as **AG-018** (closed):
`decided_at_utc` was originally a caller-supplied wall-clock parameter,
but `NoTradeDecision.decision_fingerprint` hashes it, so retrying the
identical response with a fresh "now" would produce the same deterministic
`decision_id` but a *different* fingerprint — `_claim` treats that as a
genuine conflict, not a safe retry. The exact same class of bug AG-016
fixed for `append_event`'s `occurred_at_utc`, reintroduced here via a
different path. Fixed: `decided_at_utc` is no longer a parameter at all —
derived from the response's own `decision_time_utc` (parsed, refuses if
missing/malformed/timezone-naive).

Evidence: 18 tests in new `tests/unit/test_static_agent_translate.py` —
translating the real captured response, `decision_id` determinism, full
retry-safety including `decision_fingerprint` equality (the AG-018
regression), end-to-end Gateway submission, eleven refusal cases (schema
version, wrong decision type, claimed execution authority, an unexpected
`trade_intent`, mismatched `input_identity`, tampered strategy identity,
empty reason codes, missing `decision_id`, and the three `decision_time_utc`
parsing failures), and proof that ordinary Gateway-level semantics
(unknown-assignment rejection, impersonation) still apply unweakened. ruff/
ruff format/mypy clean; full gate (unit + integration against
`crumblr_test_dev2`) **1138 passed**, 3 skipped (pre-existing, unrelated),
0 failed.

**Not done yet:** this closes review 1.27's item D for the NO_TRADE case
only. Item B (`TRADE_INTENT`→`TradeProposal` translation) stays blocked on
the fork-side strategy-runtime work (F-066); nothing wires this bridge into
a real, running process yet (that needs a registered Static Agent
identity/assignment and a script driving `static_agent_client.evaluate()`
→ this module → the Gateway on a schedule, not built).

---

## 0l. `AgentMarketContextV1` — strategy-neutral outbound context — done 2026-09-02

User-directed priority, next after §0k. New `agent_gateway/market_context.py`:
`AgentMarketContextV1` + `build_agent_market_context_v1()`, implementing
`feedback.1.28.md` section 3's architectural correction to review 1.27's
original context-payload wording. Four categories exactly as that review
names them — BINDING/PROVENANCE, MARKET (platform-owned, strategy-neutral:
current bid/ask/spread, a bounded window of confirmed closed bars with
exact source identities, freshness/quality), INSTRUMENT (read-only broker
facts), PLATFORM STATE (session/safety/reconciliation health,
fail-closed-by-construction — no permissive defaults) — and deliberately
none of section 3's negative list (no `liquidity_sweep_detected`,
`FVG_CONFIRMED`, `WAITING_FOR_MSS`, `PIVOT_2_2_CONFIRMED`, OTE, strategy
regime, or strategy reason code anywhere in the schema).

**A Crumblr-owned artifact, not a wire format.** This is not the Static
Agent fork's `TraderContext 1.0` shape — `static_agent_transport.py`
stays the fork-specific adapter (still narrowly scoped to the
unhealthy-market case). A future adapter, for the Static Agent fork or a
second, differently-shaped toy/test agent (F-066 item 8's own regression
proof), consumes this contract; this module knows nothing about any
specific fork.

**No automated self-review this pass — the `/code-review` skill hit a
session usage limit and could not run.** Rather than skip the discipline
this track has relied on all session, did a deliberate manual re-read
instead and found one real bug before committing, tracked as **AG-019**
(closed): the bar-bounding slice `snapshot.bars[-max_bars:]` returns *all*
bars for `max_bars=0`, not zero (`-0 == 0` in Python slicing) — silently
contradicting the "bounded window" requirement `feedback.1.28.md` section
3 names twice. Fixed with an explicit `max_bars > 0` guard and a
negative-value refusal; regression-tested. Noting the gap in the process
plainly rather than presenting this as equivalently reviewed to the other
slices this session, per this project's "report failures plainly" rule.

Evidence: 14 tests in new `tests/unit/test_agent_market_context.py` —
binding/market/instrument/platform-state fields forwarded correctly, bar
bounding (including the AG-019 zero/negative cases), source-bar-id
derivation, a structural strategy-neutrality test (asserts no field name
anywhere in the schema contains any of section 3's forbidden tokens —
fails immediately if a future edit reintroduces a strategy-specific
concept, rather than depending on a reviewer noticing), `extra="forbid"`
and frozen-immutability proofs. ruff/ruff format/mypy clean; full gate
(unit + integration against `crumblr_test_dev2`) **1152 passed**,
3 skipped (pre-existing, unrelated), 0 failed.

**Not done yet:** nothing yet consumes `AgentMarketContextV1` to actually
build a fork-specific wire payload from it (today's `static_agent_transport
.build_unhealthy_market_context()` still constructs its own market/
instrument blocks inline, since the unhealthy-market smoke case does not
need real bar/quote data) — wiring the two together is natural follow-up
once a real driving process exists (§0j/§0k's own "not done yet" note).
Also not done: item E (opaque Gateway reason-code handling — this
contract carries no reason codes at all, so item E's own scope is
untouched by this slice) and item G (fork-side coordination).

---

## 0m. External Supervisor boundary, core evaluation — done 2026-09-02 (AG-003, partial)

User-directed priority, next after §0l. `SupervisorReview`/
`ExternalSupervisorVerdict` have existed since Step A —
`ExternalSupervisorVerdict.UNKNOWN`'s own docstring already said "timeout,
error, or an invalid response -- never approval" — but nothing evaluated a
review against that rule. New `agent_gateway/supervisor_review.py::
evaluate_supervisor_review()` is that missing enforcement: turns a
`SupervisorReview | None` into a safe verdict, `UNKNOWN` with a named
reason on every failure mode — no response at all (timeout/transport
error/never requested), a `proposal_id`/`trade_intent_id`/
`trade_intent_decision_hash` binding mismatch (including the adversarial
case: an outright `APPROVE` for the wrong decision is still discarded, not
downgraded), an expired review (refused exactly at the boundary, not only
past it), or a review that already self-reports `UNKNOWN`. Matches
`feedback.1.28.md` section 6 exactly: "does not replace Risk, does not
size, does not modify the intent, and cannot waive a broker/safety rule" —
this module constructs, sizes and seals nothing; it only ever answers one
question.

**No HTTP client built.** Unlike the Static Agent fork, there is no real,
specified external Supervisor service to build and prove a transport
client against yet — writing one against an unspecified target would be
exactly the kind of speculative code this track's "narrow, real, proven"
discipline (established across §0j's real fork round trip) exists to
avoid. This module is the transport-agnostic evaluation core; wiring it
into `decision_path.py`'s flow and building a real client both wait for
either a concrete target service or an explicit decision to build one
speculatively.

Self-review (`/code-review medium`) ran clean this pass (the skill's
session limit from §0l had reset) — no findings. One bug did surface, but
in the *test fixture*, not the module: an expiry-boundary test tried to
set `expires_at_utc == reviewed_at_utc`, which `SupervisorReview`'s own
`_check_expiry` validator already correctly rejects — caught immediately
by simply running the tests, fixed by backdating `reviewed_at_utc` instead.

Evidence: 11 tests in new `tests/unit/test_supervisor_review.py` — no
response, genuine approve/veto passthrough, all three binding mismatches
individually (plus the adversarial mismatched-APPROVE case), exact-boundary
and one-second-early expiry, self-reported `UNKNOWN`. ruff/ruff format/mypy
clean; full gate (unit + integration against `crumblr_test_dev2`)
**1163 passed**, 3 skipped (pre-existing, unrelated), 0 failed.

**Not done yet:** wiring `evaluate_supervisor_review()` into
`decision_path.py` (where in the Risk → Policy → capsule flow an external
Supervisor's veto should sit is a real design question not yet answered —
build.md's stage order names "Supervisor vetoes" after the deterministic
Policy Gate, but §0f/item F already replaced the internal-strategy
Supervisor with a strategy-neutral platform-safety gate for the external
path, so an *external* Supervisor review needs its own explicit placement
decision relative to that, not an assumed one), and the transport client
once a target service exists. AG-003 stays OPEN, now with its core logic
closed rather than fully open.

---

## 0n. AG-012 (single Risk authority) — design analysis, not implemented, 2026-09-02

User-directed priority, last on the current list. Deliberately **not**
code this session — this is the one item on the list that genuinely
cannot be closed unilaterally, and attempting a one-sided patch would be
worse than the honest interim mitigation already in place.

**Why it needs both sides, concretely.** AG-012's option 2 (implemented,
`decision_path.py`) re-derives the ledger fresh from `risk_session_states`
on every Gateway-driven call. `application/live_decision.py
::LiveDecisionOrchestrator` does the opposite by design: it recovers the
ledger *once* per process lifetime (or on a trading-day rollover) and
holds/mutates it in memory across many `decide_once()` calls, persisting
back only when a worst case changes (`_persist_session()`'s own comment:
"a database call per bar for a value that mostly has not moved" is
deliberately avoided). A Postgres advisory lock added only on the Gateway
side would serialize against nothing — the internal orchestrator's
long-lived in-memory ledger never re-reads the database mid-lifetime, so
it cannot observe or respect a lock the Gateway path takes around its own
read. **Real single-authority correctness requires the internal
orchestrator to give up that caching optimization too** — re-deriving the
ledger fresh under a shared lock on every decision, the same discipline
the Gateway path already accepted. That is a real behavioural/performance
change to a Phase-4-approved, already-reviewed component, not a small
patch — exactly the kind of change this codebase's own process expects to
go through review, not a unilateral fix from either track.

**Proposed design, for Dev 1 / the reviewer to evaluate, not adopted:** a
Postgres advisory transaction lock (`pg_advisory_xact_lock`, the same
primitive `persistence/agent_gateway.py::lock_assignment()` already proves
out for AG-007) keyed on something symbol/account-scoped (e.g.
`hashtext('risk-ledger:' || canonical_symbol)`), held for the full
recover→evaluate→persist critical section, by *both* pipelines:
`LiveDecisionOrchestrator.decide_once()` (would need to stop caching
`self._ledger` across calls and re-recover under the lock every time) and
`agent_gateway/decision_path.py::evaluate_agent_trade_intent()` (already
re-recovers every call; would only need the lock added around the
existing recover-then-evaluate sequence). Whichever pipeline is mid-lock
briefly blocks the other's evaluation rather than racing past it —
narrower blast radius than merging the two into one process, and reuses
an already-proven primitive rather than a new one.

**Not sent to Dev 1 as an implementation request** — they are heads-down
on core critical path item 7 (automatic flatten submission) as of this
entry, and this genuinely is not urgent: AG-012 remains explicitly "not a
shadow blocker" because `order_send` stays unreachable from both
pipelines regardless, so neither can actually consume the real risk
budget this race is about. This design is recorded here for when either
track has bandwidth to take it to the reviewer, not queued as active work.

---

## 0o. Gateway reason-code handling made structural/opaque — done 2026-09-02 (feedback.1.28 §11 item E)

Continued autonomously after Dev 1 wrapped their session — the next item
still open from feedback.1.28's own Dev-2 work order, unblocked and not
waiting on anything. New `agent_gateway/contracts.py::ReasonCodeToken`/
`ReasonCodes`: applied to `TradeProposal.reason_codes`,
`NoTradeDecision.reason_codes` and `SupervisorReview.reason_codes` alike.
Exactly the structural rules `feedback.1.28.md` section 5 authorizes —
non-empty per code, a count ceiling (20), a length ceiling (128 chars),
safe printable-ASCII-only characters (no control characters, no
newlines) — and nothing beyond that: no casing requirement, no known-token
list, no whitelist anywhere. Deliberately **no** tuple-level `min_length`:
an *empty* `reason_codes` must still construct successfully on
`TradeProposal`/`NoTradeDecision` — the Gateway's own
`AgentRejectionReason.MISSING_REASON_CODES` rejection (already built, an
audited outcome) handles that case, not a `pydantic.ValidationError` at
the contract boundary.

Also checked whether "proposal binds to the assigned strategy artifact
hash" (the same section 5 sentence's other named rule) was missing —
it is not: `AgentGateway._build_trade_intent` already sources
`TradeIntent.strategy_version` exclusively from the trusted
`assignment.strategy_artifact_hash`, never from
`TradeProposal.strategy_artifact_hash`'s own (unverified) claim, so the
proposal's claim is already structurally inert regardless of whether it
agrees. Confirmed by re-reading `gateway.py`, not assumed.

Self-review (`/code-review medium`) before commit found one real
cross-module ripple, fixed same pass — tracked as **AG-020** (closed):
adding these bounds meant `static_agent_translate.py
::translate_no_trade_response()`'s final `NoTradeDecision(...)`
construction could now raise a raw `pydantic.ValidationError` for a
response that passed that module's own coarser `isinstance` checks but
violated one of the new contract bounds — contradicting that module's own
documented "always raises `StaticAgentResponseRejectedError`" contract and
every existing test asserting that exact type. Fixed by wrapping the
construction and re-raising as `StaticAgentResponseRejectedError`;
regression-tested with a non-ASCII reason code.

Evidence: 10 new tests in `tests/unit/test_agent_gateway_contracts.py
::TestReasonCodesAreStructurallyBoundedNotWhitelisted` (empty tuple still
constructs, two wildly different made-up vocabularies both accepted,
count/length boundaries on both sides, empty string rejected, a control
character and a newline each rejected, the bound applies to
`SupervisorReview` too), plus the AG-020 regression test in
`tests/unit/test_static_agent_translate.py`. ruff/ruff format/mypy clean;
full gate (unit + integration against `crumblr_test_dev2`)
**1255 passed**, 3 skipped (pre-existing, unrelated), 0 failed.

This closes feedback.1.28 section 11 item E.

---

## 0p. Exact open risk, not a count-based approximation — done 2026-09-02 (Owner Work Order D2.2)

`review/OWNER_WORK_ORDERS_2026-09-02.md` (owner-authored, arrived via Dev 1's
commit `0648e41`) landed with an explicit, prioritized Dev-2 work order,
superseding the informal priority list this session had been working. Its
first directly-actionable item, D2.2, is done: `decision_path.py` no longer
models open risk as `max_risk_per_trade * len(open_positions)` — the owner's
new Risk Policy v1 allows multiple, differently-sized open EUR/USD positions
within one total open-risk budget (`max_open_risk=0.03`), so a count-based
approximation is structurally wrong the moment two positions can carry
different risk.

`PortfolioSnapshot` gained a new **required** field, `open_risk_fraction:
RiskFraction | None`. It must be the caller's own exact total open-risk
figure — the Core-owned exact-open-risk seam D1.4 is supposed to build, once
it exists; nothing under my ownership can honestly compute it (I do not own
account/position state at all, by design — see the module docstring's
"No MT5 anywhere in this module" section). `None` means the caller could not
establish a trustworthy figure. Per D2.2's own instruction ("if exact open
risk cannot be established from trusted state, fail closed"),
`evaluate_agent_trade_intent` now checks this **before** Risk evaluation
runs at all: `None` produces a directly-constructed `RiskDecision`
(`verdict=HALT`, `reason_codes=(SAFETY_STATE_UNKNOWN,)`), trips the kill
switch, seals a capsule with `supervisor_decision=None`, and returns —
never reaching the Policy Gate. There is no silent zero anywhere on this
path.

**This is genuinely partial, not a full fix.** D1.4 (Dev 1's Core-owned
exact-open-risk seam) does not exist yet, so today every real caller of this
module still has nothing honest to pass but `None` — meaning the agent path
is currently HALT-only for any directional intent until D1.4 lands. That is
the correct fail-closed behaviour, not a bug: better an honest refusal than
resurrecting the wrong approximation to keep the shadow path producing
`PASS` verdicts. Flagged to Dev 1 (SendMessage) since `PortfolioSnapshot`
is the `PortfolioStateProvider` Protocol's own return shape, and this add is
a breaking field addition every future implementer (including D1.4 itself)
must satisfy.

Self-review (`/code-review medium`) found no issues — small, well-scoped
change with dedicated fail-closed test coverage.

Evidence: `tests/unit/test_agent_decision_path.py::TestOpenRiskFractionUnknown`
(4 new tests — HALT before the Policy Gate, kill switch tripped, capsule
still sealed, and a regression guard that `Decimal("0")` is *not* treated as
`None`); full existing `FakePortfolioStateProvider`/`Fixture` call sites
updated with a `Decimal("0")` default so prior behaviour is unchanged.
`ruff check`/`ruff format --check`/`mypy` clean; `tests/unit` full suite
**991 passed**, 1 skipped (pre-existing, unrelated MT5-import skip), 0
failed. Full gate (unit + integration against `crumblr_test_dev2`) after:
**1259 passed**, 3 skipped (pre-existing, unrelated), 0 failed.

This is D2.2 done. Checked the rest of the Dev-2 priority order against
current code before moving on:

- **D2.1** ("consume Owner Policy v1; do not duplicate it") — already
  satisfied, verified by grep: no numeric risk limit (`0.02`/`0.03`/`0.04`/
  `0.08` or any `max_*` assignment) appears anywhere under
  `agent_gateway/`. `decision_path.py::_risk_context` forwards
  `PlatformConfig.risk` straight into `risk.policies.evaluate()` — the
  same Core-authoritative function and config object the internal-strategy
  path uses, never an agent-local copy. Note: `config/paper.yaml` itself
  still carries the *old* pre-Owner-Policy-v1 numbers
  (`max_risk_per_trade=0.005`, not `0.02`, etc.) — that is Dev 1's D1.2
  ("implement Owner Risk Policy v1 as versioned configuration"), not a
  Dev-2 gap; this module will pick up the correct values automatically
  once D1.2 updates the config, with no code change needed here.
- **D2.3** ("keep `AgentMarketContextV1` strategy-neutral") — already true,
  done under §0l; no Pivot/FVG/MSS/ICT semantics are computed in
  `market_context.py`.
- **D2.4** (genuine HEALTHY Static Agent path) — blocked on the external
  Agent/strategy-runtime developer turning neutral context into its own
  Pivot-2.2 observation; nothing further to do from this side alone.
- **D2.5** (external Supervisor, full production path) — core evaluation
  logic done (§0m, AG-003 partial); HTTP client and wiring into
  `decision_path.py` still open.
- **D2.6** (AG-012 single Risk authority) — design proposed, not
  implemented (§0n); cross-track, needs Dev 1 agreement.
- **D2.7** (support Dev 3's PAPER_LITE via narrow seams only) —
  informational; no Dev 3 request has arrived yet, no action taken.

D2.5 (wiring) is the next concrete, unblocked Dev-2 item.

---

## 0q. D1.4 landed — real `assess_open_risk` wiring supersedes §0p's interim design, plus an outbound-contract fix (D-054 gap 2) — done 2026-09-03

Dev 1 (`crumblr-68`) shipped D1.4 on `main` (`b2a07a5`/`31c74cf`,
`risk/portfolio_risk.py::assess_open_risk`) and pinged this track per the
handshake §0p asked for. Merged `origin/main` into `agent/contracts`
(`c020051`, one real conflict — both branches had appended a
`review/DEVIATIONS.md` entry at the same location; resolved by keeping
both, D-052 then D-053/D-054 in order) rather than leave a citation
dangling: a self-review pass on an unrelated same-day fix (below) flagged
that my own draft already cited "D-054 gap 2" before that entry existed
on this branch, and the honest fix was to actually bring it in, not
soften the wording around a merge I owed anyway.

**§0p's interim design is now superseded, not merely completed.**
`PortfolioSnapshot.open_risk_fraction` — the required, caller-supplied
field §0p added, with its own `RiskVerdict.HALT` pre-check on `None` — is
gone. D1.4 shipped `assess_open_risk(positions, *, specs, equity) ->
OpenRiskAssessment`, the exact single-authority whole-book computation
`application/live_decision.py` itself now calls; `decision_path.py` calls
it the same way, directly, over `portfolio.open_positions` and
`{spec.broker_symbol: spec}` (correct today under this platform's
single-instrument scope — the same assumption `live_decision.py`'s own
call site relies on), and passes the `Decimal | None` result straight
into `policies.PortfolioState.open_risk_fraction` unmodified. The interim
HALT pre-check was deleted, not kept as a belt-and-braces extra: Core's
own `risk.policies.evaluate()` already fails closed on `None` itself
(`ReasonCode.OPEN_RISK_UNKNOWN`, a `BLOCK` — deliberately not a `HALT`,
`review/DEVIATIONS.md` D-054 gap 1's own reasoning: no in-system
remediation path exists yet for a stopless position, so a HALT would be a
permanent brick, not a safer outcome). Keeping my own parallel HALT-based
check would have been a second, weaker, diverging reimplementation of a
decision Core already owns correctly — exactly what D2.1 ("consume Owner
Policy v1; do not duplicate it") warns against. `PortfolioSnapshot`
itself shrank back to `account`/`open_positions`/`reconciliation_status`
— a `PortfolioStateProvider` only ever answers "what does the
broker/account show", never "what does Risk Policy do with that", which
is one field fewer any future genuine LIVE_SHADOW implementation (Dev 1's
or otherwise) needs to satisfy.

**Separately, D-054 gap 2 (flagged by Dev 1 in the same message):**
`agent_gateway/market_context.py::AgentPlatformState.open_risk_fraction`
was typed `domain.money.RiskFraction`, which requires `gt=0` — a
genuinely flat book (`Decimal("0")`) could never construct, so every
caller had nothing honest to pass but `None`, collapsing "flat" and
"could not be established" into the same wire value on the
agent-visible outbound context. Fixed with a new local type,
`agent_gateway/contracts.py::OpenRiskFraction`
(`Annotated[ExactDecimal, Field(ge=0, le=1)]` — composed from the
existing float-rejecting `ExactDecimal` rather than duplicating that
validator, verified by hand that the composition actually enforces both
constraints before relying on it), applied to both
`AgentPlatformState.open_risk_fraction` and
`build_agent_market_context_v1`'s matching parameter. Nothing populates
this field with a real value yet — outbound context issuance
(`gateway.py::issue_context_bundle` or equivalent) doesn't exist — so
this closes the representational gap only; wiring a real value through
is separate, unblocked future work.

Self-review (`/code-review medium`) ran twice this slice: first pass
correctly caught the dangling `D-054` citation (before the merge) as a
genuine CLAUDE.md §2/§3 violation — three permanent cross-references to
an entry that didn't exist on this branch yet; second pass after the
merge and the `assess_open_risk` rewrite found nothing further.

Evidence: `tests/unit/test_agent_decision_path.py::TestOpenRiskUnknown`
(rewritten from `TestOpenRiskFractionUnknown` — now exercises the real
mechanism: an open position with no protective stop drives
`assess_open_risk` to `None`, proving the resulting `BLOCK`/
`OPEN_RISK_UNKNOWN`, that it does *not* trip the kill switch (a BLOCK,
not a HALT — `_trip()` is only ever called on HALT), that a capsule is
still sealed, that a genuinely flat book reaches `APPROVE`, and that a
position with real stop geometry reaches `APPROVE` too — untrustworthy
geometry fails closed, not risk itself);
`tests/unit/test_agent_market_context.py
::test_a_genuinely_flat_book_constructs_as_zero_not_none`. Full unit
suite (including Dev 1's own D1.4 tests, now merged in) **1010 passed**,
1 skipped (pre-existing, unrelated), 0 failed; ruff/ruff format/mypy
clean (175 source files). Full gate after (unit + integration against
`crumblr_test_dev2`): **1280 passed**, 3 skipped (pre-existing,
unrelated), 0 failed.

`review/DEVIATIONS.md` D-052 marked RESOLVED, D-054 gap 2 marked
RESOLVED (gap 1 remains Dev 1's own deliberate, unrelated design choice).
Replied to Dev 1 confirming no numbering collision and flagging the
`config/paper.yaml` staleness (resolved by their same D1.2 slice, seen
after replying — no further action needed).

This is D2.1 and D2.2 both now fully, not partially, done — the last
open item from Owner Work Order D2's "done condition" list this track
could complete unilaterally.

---

## 0r. Rebase on current main, plus hard `StrategyArtifact` binding enforcement — done 2026-09-03

Explicit owner instruction (Dutch, this session): rebase onto current
`main` before continuing, re-confirm D2.2 uses only Core Risk semantics
(§0p/§0q already did this — verified intact, not re-done), confirm `0 !=
None` on the outbound open-risk contract (§0q already did this — also
verified intact), and — the actual new work this slice — hard-enforce
that a `TradeProposal` is bound to the exact `StrategyArtifact` its
`TradingAssignment` names, not merely consistent with it after the fact.

**Sync, not a literal `git rebase`.** `agent/contracts` is already
pushed and Dev 1 messaged that they won't touch it — but a real `git
rebase` would still rewrite already-pushed commit SHAs and require a
force-push, exactly the operation CLAUDE.md's git-safety section singles
out for extra caution. Used `git merge --no-ff origin/main` instead, the
same non-destructive approach §0q already used successfully — same
result (fully current with `main`), without rewriting shared history.
Two merges landed this slice: `origin/main` had moved twice since §0q
(Dev 3's PAPER_LITE, PR #1, plus Dev 1's D1.5 weekday/weekend session
policy and a CI fix) — merged cleanly, one real conflict both times in
`review/DEVIATIONS.md` (both tracks append entries at the same location;
resolved by keeping every entry, in number order). Full suite green
after each merge before starting new work, not just at the end.

**`agent_gateway/errors.py::AgentRejectionReason.STRATEGY_ARTIFACT_MISMATCH`
(new)** and the check in `gateway.py::_evaluate_proposal`:
`proposal.strategy_artifact_hash != assignment.strategy_artifact_hash`
now rejects before `_build_trade_intent` is ever called — zero
`TradeIntent` constructed, a durably audited `REJECTED` outcome instead
(the same claim→evaluate→settle sequence every other rejection reason
already goes through, nothing new invented). **Why this mattered even
though `_build_trade_intent` already ignored the claim:**
`TradeIntent.strategy_version` has only ever been sourced from
`assignment.strategy_artifact_hash`, the trusted value, never from the
proposal's own (unverified) claim — confirmed true again this slice, so
a mismatch could never have corrupted a constructed `TradeIntent`. But
silently ignoring a wrong claim, rather than rejecting it, meant an agent
could run artifact B, report B's hash in every proposal, and be audited
entirely as artifact A after the fact — nothing durable would ever have
recorded that its own claim disagreed with what was actually assigned.
Rejecting makes the disagreement itself part of the audit trail, closing
that gap.

Evidence: `tests/unit/test_agent_gateway.py::TestStrategyArtifactBinding`
— the exact adversarial shape requested (valid identity, valid
assignment, valid context, valid proposal, wrong
`strategy_artifact_hash` only → rejected, `trade_intent is None`), a
positive control (matching hash still accepts), and a durable-audit
proof (`REJECTED` + `RECEIVED` events both present for the rejected
outcome). Self-review (`/code-review medium`) found the change itself
clean — checked cross-file callers (`paper_lite_toy_agent.py` already
populates the field consistently), all existing test fixtures (uniformly
`"abc123"`, nothing broke), and check ordering — but flagged a real
process gap: no `status.md` §13 entry existed for this change. Resolved
by writing this entry here instead of directly editing `status.md`
myself: `status.md` is Dev 1's actively-edited canonical document (this
slice's own merges pulled in 259 lines of their concurrent edits to it)
and the established, working pattern all session — visible in `status.md`
line 20's own Dev-2 row, which already reflects §0q's `assess_open_risk`
wiring — is that Dev 1 synthesizes a summary from this file into
`status.md`'s tracker rather than both sessions editing the same
document concurrently.

Full gate: ruff/ruff format/mypy clean; `tests/unit` **1059 passed**, 1
skipped (pre-existing, unrelated), 0 failed. Full gate (unit +
integration against `crumblr_test_dev2`, including Dev 3's newly-merged
PAPER_LITE suites): **1331 passed**, 3 skipped (pre-existing, unrelated),
0 failed.

Reaffirmed, not newly built this slice (owner instruction's items 5-7):
External Supervisor wiring is the explicitly-named *next* slice, not
this one (§0m's core logic is done, HTTP client/wiring remain open,
unchanged since §0q). AG-012 stays a cross-track requirement — no
agent-only lock was built (§0n's proposal stands, unimplemented,
awaiting Dev 1). Dev 3 gets narrow, stable seams only
(`AgentMarketContextV1`, translation, `AgentGateway`, the shared
open-risk input, Supervisor contracts) — no PAPER_LITE orchestration
logic exists or was added anywhere under this track's ownership; Dev 3's
own `application/paper_lite*.py` (merged via PR #1, now present after
this slice's merge) was built entirely on their side.

---

## 0s. PL-006 (`recover_session()` loss/drawdown check) merged — two test assertions updated for the new, more-correct timing — done 2026-09-03

Dev 1 shipped PL-006 (`b3068c0`/`8505fd2` on `main`,
`review/adr/ADR-013-restart-recovery-loss-drawdown-check.md`):
`risk/session.py::recover_session()` now checks a recovered session's
`max_drawdown_fraction`/`max_session_loss_fraction` against the
configured `max_drawdown`/`max_daily_loss` thresholds itself, during
recovery — a real gap PAPER_LITE's own test suite surfaced: previously,
an already-breached session that survived a restart was only ever caught
later, inside `policies.evaluate()`'s own live loss-gate leg. `Dev 1`
made the one-line mechanical fix at this track's own call site
(`decision_path.py:243`, two new kwargs,
`max_daily_loss=config.risk.max_daily_loss`/
`max_drawdown=config.risk.max_drawdown` — the same shape `orchestration
.py`/`live_decision.py`/`execution.py` already pass) themselves, with my
explicit go-ahead: `main` was red for PAPER_LITE's own tests over this
today, not a future break on my branch, so waiting for me to touch it
was the wrong call there — confirmed by reading the actual diff after
merging, exactly the two kwargs, nothing else in the file touched.

**Two tests broke, correctly — a timing change, not a defect.** Before
PL-006, an already-breached session's `DAILY_LOSS_LIMIT`/`MAX_DRAWDOWN`
was caught inside `policies.evaluate()`'s own live loss-gate leg, landing
directly in `risk_decision.reason_codes` as `RiskVerdict.HALT`. Now
`recover_session()` catches it first and trips the kill switch during
recovery, before `evaluate()` ever runs — `evaluate()` then runs against
an already-halted switch and reports `RiskVerdict.BLOCK` with
`ReasonCode.SYSTEM_HALTED`, per its own pre-existing "an already-halted
system is enforced as a BLOCK, not a fresh HALT escalation" convention
(`tests/unit/test_risk_engine.py
::test_adr001_7_a_kill_switch_tripped_since_approval_is_refused`) —
reused here, not reinvented. The kill switch itself is still correctly
halted with the real reason, just visible via `kill_switch.active_reasons`
now rather than `risk_decision.reason_codes`. Updated
`tests/unit/test_agent_decision_path.py
::TestAG012FreshSessionRecoveryEveryCall`'s two affected tests to assert
the new, more-correct shape (`kill_switch.is_halted` +
`active_reasons` carries the real breach reason + downstream
`risk_decision.verdict is BLOCK`/`SYSTEM_HALTED`) rather than paper over
the difference.

Self-review (`/code-review medium`) caught one real precision gap before
commit: my first draft asserted `MAX_DRAWDOWN in active_reasons or
DAILY_LOSS_LIMIT in active_reasons` for the second test, but the actual
fixture (`max_drawdown_fraction=0.5`, `max_session_loss_fraction=0.5`,
both far over their configured thresholds) always trips *both* reasons —
confirmed directly from the captured log output
(`"reasons": ["MAX_DRAWDOWN", "DAILY_LOSS_LIMIT"]`), not assumed. An
`or` would have silently stopped catching a regression that dropped
either one; tightened to `and` on both.

Did not touch `test_agent_decision_path.py` before Dev 1 pushed, per our
own agreement (`agent_gateway/**` stays single-owned; I fix my own test
file against real, pushed code rather than either session editing it
blind or concurrently).

Evidence: `tests/unit/test_agent_decision_path.py` — both previously
failing tests pass again (23/23 in the file); full `tests/unit`
**1067 passed**, 1 skipped (pre-existing, unrelated), 0 failed;
ruff/ruff format/mypy clean (185 source files). Full gate after (unit +
integration against `crumblr_test_dev2`): **1339 passed**, 3 skipped
(pre-existing, unrelated), 0 failed.

---

## 0t. Phase 0 convergence — synced with item 9, pushed for review; PR itself blocked on missing GitHub access — 2026-09-03

The owner/reviewer published a major new coordination document,
`review/OWNER_WORK_ORDERS_DEMO_CANARY_2026-09-03.md` — a staged route
(Phases 0 through F) to a one-shot, deliberately constrained real
Pepperstone **DEMO** canary. Its Phase 0 names this track's own next
required action explicitly: merge/rebase latest `main` (including item 9,
ADR-014's broker-side protective-stop verification) into
`agent/contracts`, preserve the already-reviewed D2.2/Core-risk,
0-vs-`None` and `STRATEGY_ARTIFACT_MISMATCH` changes, run the full local
gate, push, and open a PR to `main` — explicitly **not** bundling
Supervisor or new Static Agent work into that PR, keeping it a pure,
reviewable convergence.

Merged `origin/main` (`7ad93a5` — item 9/ADR-014 plus the new work order
document itself) into `agent/contracts`: clean, one usual
`review/DEVIATIONS.md` append-collision (both tracks append entries at
the same location; resolved keeping all entries), no conflict anywhere
in code. Verified §0p/§0q/§0r/§0s's work (`assess_open_risk` wiring,
`OpenRiskFraction`, `STRATEGY_ARTIFACT_MISMATCH`, the PL-006 test fixes)
all survived intact — nothing needed re-doing, exactly as the work order
itself expected ("Preserve the already-reviewed... changes").

**Blocked on the PR step itself, not on anything code-related:** neither
this session nor Dev 1's has `gh` CLI installed or a `GITHUB_TOKEN`/
`GH_TOKEN` available — confirmed independently on both sides. The branch
is pushed and ready; opening the actual PR object needs a human with
GitHub access (the owner, most likely). Dev 1 has agreed to start their
own Phase-0 cross-track review directly against the pushed branch
(`agent/contracts` vs `main`) without waiting on the PR object to exist,
so review is not blocked even though the formal PR artifact is.

Full gate (unit + integration against `crumblr_test_dev2`, on the
dedicated Dev-2 database as the work order specifies): **1358 passed**,
3 skipped (pre-existing, unrelated), 0 failed. ruff/ruff format/mypy
clean.

**Update, same day:** the owner opened and merged PR #2
(`3e87384`, "Merge pull request #2 from DutchBugs/agent/contracts") —
Phase 0 is fully complete. `agent/contracts` fast-forwarded to match
`main` exactly, re-pushed.

---

## 0u. External Supervisor wired into the decision path (AG-003 closed), a deterministic reference implementation — done 2026-09-03

Phase C (`review/OWNER_WORK_ORDERS_DEMO_CANARY_2026-09-03.md`), the task
the user asked to start after confirming Phase 0's PR merged: "Zijn er
in de tussentijd voor jou nog openstaande punten?" — the answer was yes,
and this was the one that did not depend on Dev 1 or the Static Agent
developer.

`agent_gateway/decision_path.py::evaluate_agent_trade_intent` gained two
new optional parameters, `proposal: TradeProposal | None = None` and
`external_supervisor: ExternalSupervisorProvider | None = None` (a new
`Protocol`, injected the same "no HTTP client here, no MT5 here" way
`PortfolioStateProvider` already is). Both default to `None`, so every
existing caller — including PAPER_LITE's two call sites in
`application/paper_lite.py`, confirmed unmodified — sees zero behaviour
change; the full unit suite proves this (no existing test needed a
single edit). When both are supplied, the external Supervisor is asked
**only** after the strategy-neutral Policy Gate itself `APPROVE`s (never
before — asking about an already-refused proposal is pointless, and
"Do not overwrite or relabel the platform Policy decision as the
external Supervisor approval," Phase C's own instruction, means the two
must stay visibly separate always, never merged into one verdict).
`supervisor_review.py::evaluate_supervisor_review()` — built earlier
this session, never wired anywhere until now — does the actual
binding/expiry/self-reported-`UNKNOWN` enforcement.

**`agent_gateway/reference_supervisor.py` (new): a real, deterministic,
in-process Supervisor implementation.** Phase C explicitly permits this
for the first canary: "If no Supervisor service exists yet, a
deterministic reference Supervisor in a separate process is acceptable...
provided it has zero MT5/DB credentials and the exact same
APPROVE/VETO/UNKNOWN authority limits." `ReferenceSupervisor` checks
only what a mechanical stand-in can honestly check without domain
judgment or strategy semantics: that the proposal carries non-empty
`reason_codes` (auditable rationale) and clears an operator-configured
`confidence` floor — both already-defined, strategy-neutral
`TradeProposal` fields. Never reads Risk/Policy state, never sizes,
mutates, waives Risk, resets HALT or executes. **In-process today, not
yet a separate process** — the work order's "separate process" framing
matters for the real DEMO canary trust boundary; an HTTP transport that
lets this run out-of-process (mirroring `static_agent_client.py`) is
deliberately deferred, since nothing yet requires it and writing one
now would be exactly the speculative-code pattern this track avoids.

**Self-review (`/code-review medium`) ran three times this slice and
found four real issues, all fixed before commit:**

1. `_external_supervisor_record()` tried to durably persist
   `ExternalSupervisorReviewRecord` via `recorder.record()` — but that
   type is agent_gateway-owned, and Core's event journal
   (`domain/events.py::EVENT_PAYLOAD_TYPES`) is a closed registry that
   cannot reference it without `domain/` importing from `agent_gateway/`,
   inverting this codebase's one-way dependency direction. Against the
   real `JournalRecorder`, this would have raised `ValueError: ... is
   not a registered event payload` the first time any real caller
   supplied a provider — caught before shipping, not after. Fixed by
   **not** persisting it from this module at all: both
   `external_supervisor_outcome` and the new
   `external_supervisor_record` are returned on
   `AgentDecisionPathResult` instead, for a caller to persist through
   its own mechanism. Tracked as AG-022 (`review/AGENT_FEEDBACK.md`) —
   a real cross-track design question (a generic Core "extension event"
   mechanism, or routing through `AgentDecisionOutcomeStore`), not
   decided unilaterally here.
2. `record_id`'s first draft was keyed on `verdict`/`review_id` only —
   still collided for two different `UNKNOWN` outcomes (e.g.
   `NO_SUPERVISOR_RESPONSE` on a timeout, then `REVIEW_EXPIRED` on a
   retry both share `verdict="UNKNOWN"`/`review_id=None`). Fixed by
   folding `reason_codes` into the key too; regression-tested with two
   deliberately different `UNKNOWN` causes for the identical
   proposal/intent pair.
3. A test named `test_the_review_binds_to_the_exact_risk_and_policy_
   decision_ids` actually only proved this module correctly *passes*
   `risk_decision_id`/`policy_gate_decision_id` to the provider and that
   `ReferenceSupervisor` echoes them back — not that
   `evaluate_supervisor_review()` would *reject* a mismatch (it doesn't
   check those two fields at all). Renamed to
   `test_the_provider_is_passed_the_exact_risk_and_policy_decision_ids`
   and documented the real gap honestly in `supervisor_review.py`'s own
   module docstring rather than silently expanding that module's
   enforcement scope. Tracked as AG-021.
4. (Earlier pass) confirmed no correctness bug in the `intent`
   non-`None` narrowing at the external-Supervisor call site — NO_TRADE
   returns early before any intent-dependent code runs.

Evidence: `tests/unit/test_reference_supervisor.py` (12 tests — config
validation, confidence-floor boundary behaviour, binding fields, never
returns `None`). `tests/unit/test_agent_decision_path.py
::TestExternalSupervisorWiring` (11 tests — skip-when-omitted,
skip-without-a-proposal, approve, veto-does-not-change-Risk/Policy,
missing-response-is-UNKNOWN-not-approval, never-asked-when-Policy-
refuses and never-asked-when-Risk-blocks via a call-counting spy, exact
decision-id binding, two `record_id`-collision regressions). Full
`tests/unit` **1101 passed**, 1 skipped (pre-existing, unrelated), 0
failed; ruff/ruff format/mypy clean (187 source files). Full gate after
(unit + integration against `crumblr_test_dev2`): **1380 passed**, 3
skipped (pre-existing, unrelated), 0 failed.

`review/AGENT_FEEDBACK.md`: AG-003 closed. AG-021 (Supervisor-review
binding gap) and AG-022 (no durable persistence path yet) opened,
both LOW severity — order_send stays unreachable from this path
regardless, so neither is exploitable today.

---

## 0v. Generic neutral-Agent wire envelope moved into Agent Gateway (Phase A item 1) — done 2026-09-04

`review/OWNER_WORK_ORDERS_DEMO_CANARY_2026-09-03.md` §2 Phase A names this
track's first post-Phase-C item explicitly: "Move the reusable neutral
external-Agent HTTP response envelope/adapter into the Agent Gateway
layer. PAPER_LITE must not be the owner of the production wire contract."
Confirmed by reading the actual code, not assumed from the work order's
own description: `application/paper_lite_agent.py::HttpPaperLiteTradingAgent`
(response-envelope parsing/binding-validation for a neutral-context Agent
host) and `PAPER_LITE_AGENT_SCHEMA_VERSION` were defined inside the
PAPER_LITE (Dev-3-owned) application module, not `agent_gateway/` — exactly
the ownership inversion the work order names. `application/paper_lite.py`
itself only depends on the `PaperLiteTradingAgent` `Protocol` (duck-typed:
`agent_id`/`credential_secret`/`decide()`), not on the concrete class, so
the move does not touch that file at all.

New `agent_gateway/neutral_agent_client.py`: `HttpNeutralAgentClient`,
`NeutralAgentResponseError`, `NEUTRAL_AGENT_RESPONSE_SCHEMA_VERSION` — the
Gateway-owned counterpart of `market_context.py`'s outbound
`AgentMarketContextV1` (that module builds the outbound neutral context;
this one parses the inbound response into the same authoritative
`TradeProposal`/`NoTradeDecision` contracts). Transport itself is not
duplicated — reuses `static_agent_client.evaluate()` (timeout/redirect/
size-bound stdlib HTTP client) unchanged.

**Deliberately not a same-session rewrite of PAPER_LITE's file.** The work
order itself splits this: Dev 2 "moves" (creates the canonical, Gateway-
owned version); a separate line item explicitly assigns "Replace paper-only
wire-envelope ownership with Dev 2's generic Agent adapter" to **Dev 3**.
`application/paper_lite_agent.py` and its own test
(`tests/unit/test_paper_lite_agent.py`) are untouched — rewriting a file and
test suite this track does not own, unilaterally, risks exactly the kind of
one-sided cross-track patch this repository's process has repeatedly
avoided elsewhere (AG-012's own §0n is the clearest precedent). The new
module's schema-version string (`"neutral-agent-response-1.0"`) is
therefore deliberately distinct from PAPER_LITE's still-active
`"paper-lite-agent-1.0"` — nothing in production speaks either value today
(F-064: no HTTP transport is deployed anywhere), so there is no live wire
compatibility being broken, and consolidating onto one name is exactly the
substitution Dev 3's own pass makes next.

Also folded in, since a fresh module was the chance to close a coverage gap
the original never had: tests for a `TRADE_PROPOSAL` success parse, an
unsupported `schema_version`, a missing `decision` object, an unsupported
`decision_type`, and a decision payload that violates the Crumblr contract
at the `pydantic.ValidationError` layer — none of these had a direct test
against `HttpPaperLiteTradingAgent` originally (only the `NO_TRADE` success
case, binding mismatches and a non-200 status were covered).

Evidence: `tests/unit/test_neutral_agent_client.py` (11 tests, all new).
`uv run ruff check .` / `ruff format --check .` — clean (213 files).
`uv run mypy` — no issues in 196 source files. Full non-integration suite:
**1202 passed**, 1 skipped (pre-existing, unrelated), 0 failed — no
existing test needed a single edit, confirming the move is additive.
Integration suite launched against the dedicated `crumblr_test_dev2` URL,
as this track's own convention requires. First attempt reported honestly
rather than glossed over: no PostgreSQL was reachable in this session's
environment at that point (234 tests skipped). Docker was available but no
container was running; started the repo's own documented `crumblr-pg`
container (`tests/integration/conftest.py`'s own recipe — a stopped
container from an earlier session already existed under that name, reused
rather than recreated) against the same `crumblr_test_dev2` database this
track already uses. Re-ran for real: **249 passed, 2 skipped** (pre-existing
`test_halt_survives_restart.py` Windows-permission-bits skips, unrelated —
matches every prior session's own note that Windows does not enforce POSIX
permission bits), 0 failed, in 411s.

Full gate now genuinely green (unit + integration + ruff + ruff format +
mypy). Committed `c1f1544`.

---

## 0w. AG-012 closed for real — `RiskLedgerLock` wired into `decision_path.py` (ADR-021, Phase C) — done 2026-09-04

Cross-session coordination with Dev 1 (`crumblr-59`), same day: Dev 1
drafted ADR-021 (a Postgres advisory transaction lock, symbol-keyed,
closing AG-012's original per-process-caching race for real), I reviewed
the actual draft twice — not the summaries — catching one real interface
gap before Dev 1 wrote code (`held()`'s first draft required an external
`connection` neither `LiveDecisionOrchestrator` nor `decision_path.py`
has a natural source for; fixed to open its own transaction and yield the
connection out) and one imprecise supporting claim (its "no orchestrator
code holds an Engine" grep missed `RunRecorder`, which does, privately —
didn't change the design, but the ADR's stated justification is accurate
now instead of overstated).

**A genuine gap found while building my own side, not Dev 1's:** checked
every real caller of `evaluate_agent_trade_intent`, not only this
module's own tests, and found `application/paper_lite.py
::PaperLiteOrchestrator._recover_risk_session()`/`_persist_risk_session()`
is a fourth, unaccounted reader/writer of `risk_session_states` —
unlocked, and real (`scripts/paper_lite.py` constructs
`PostgresRiskSessionStore(engine)`, not an in-memory store). Reported to
Dev 1 immediately rather than sitting on it; Dev 1 independently verified
by reading the same two files and amended ADR-021 §1/§7 same day
(`main` at `2275908`) rather than waiting to bundle it. Tracked here as
AG-023 (`review/AGENT_FEEDBACK.md`) — not fixed in either track's pass,
since `paper_lite.py`'s own recover/persist design is Dev-3-owned and
whether/how it should acquire the lock is that track's call, not
something to bundle into a mechanical Protocol-conformance pass. Not a
live safety gap today by the same reasoning ADR-021 already applies:
PAPER_LITE never reaches `order_send` either.

**The actual wiring, `agent_gateway/decision_path.py`:**
`evaluate_agent_trade_intent()` gained a required `risk_ledger_lock:
RiskLedgerLock` parameter; the existing fresh-every-call
`session_store.load_latest()` now runs inside
`risk_ledger_lock.held(snapshot.symbol)`, with the yielded `connection`
threaded into `load_latest(connection=...)`. The lock is released before
`policies.evaluate()` runs — mirrors `live_decision.py`'s own choice to
protect the durable read/write, not the CPU-bound evaluation against an
already-recovered ledger. This module still never calls `.save()` (AG-012
docstring section rewritten to say so plainly, matching ADR-021 §4) — its
critical section is `recover` only.

**Forced, mechanical downstream fixes** (the same category as Dev 1's own
one-line fix to my test file for the widened `RiskSessionStore` Protocol):
`application/paper_lite.py`'s two `evaluate_agent_trade_intent()` calls
and `PaperLiteOrchestrator.__init__` gained a threaded-through
`risk_ledger_lock` parameter (explicitly documented at the constructor as
*not* covering `_recover_risk_session`/`_persist_risk_session` — AG-023);
`scripts/paper_lite.py` now constructs `PostgresRiskLedgerLock(engine)`
alongside its existing `PostgresRiskSessionStore(engine)`; both
`PaperLiteOrchestrator` construction sites in `tests/unit/test_paper_lite.py`
gained a matching `InMemoryRiskLedgerLock()`.

**New tests**, `tests/unit/test_agent_decision_path.py::TestAG012RiskLedgerLockAcquired`
(4 tests, a `SpyRiskLedgerLock`/`ConnectionCapturingSessionStore` pair):
the lock is acquired for exactly the snapshot's canonical symbol; the
store read runs with the exact connection the lock yielded, not merely
*some* connection; a NO_TRADE evaluation never acquires the lock (mirrors
the existing NO_TRADE/session-store test); the lock is released before
Policy evaluation runs (proven with a non-reentrant fake that asserts if
entered twice). `TestAG012FreshSessionRecoveryEveryCall`'s existing tests
are unchanged and still pass — they proved "fresh every call" before this
change and still do; the new class proves the lock is now the actual
mechanism, not just a habit.

`review/AGENT_FEEDBACK.md`: AG-012 closed (superseded by ADR-021, option
1's real single-authority property achieved via the shared lock rather
than merging the two processes). AG-023 opened (the PAPER_LITE gap
above).

Evidence: `tests/unit/test_agent_decision_path.py` 33→37 passed.
`tests/unit/test_paper_lite.py` 20/20 unchanged. Full non-integration
suite (merged onto `origin/main` at `2275908`, which brought in Dev 1's
own ADR-021 core-side commits): **1217 passed**, 1 pre-existing unrelated
skip, 0 failed. ruff/ruff format/mypy clean (197 source files). Full
integration suite, against real PostgreSQL (`crumblr_test_dev2`, the
same `crumblr-pg` container from §0v): **255 passed, 2 skipped**
(pre-existing `test_halt_survives_restart.py` Windows-permission-bits
skips, unrelated), 0 failed, in 1177s (~20 min — the full suite,
including Dev 1's own new `test_risk_ledger_lock.py` concurrency proof).
Committed `f0a18ed`, merged Dev 1's ADR-021 amendment on top, pushed.

**Self-review, same day:** `/code-review medium` against the actual
`agent/contracts` diff (not requested by anyone — the same "solid test
coverage doesn't rule out a wrong claim in prose" discipline §0a used)
found one real issue: this module's own docstring said ADR-021 "closes
this for real" without qualifying that `PaperLiteOrchestrator` (AG-023)
remains an unlocked party against the same table — a future reader could
mistake this module's own lock participation for a system-wide guarantee.
Fixed same day, `aabc2e4`: the claim is now scoped to the call sites
ADR-021 actually names, with an explicit AG-023 cross-reference. Full
`test_agent_decision_path.py` re-run (37/37) after the docstring-only
change; ruff/mypy clean.

---

## 0x. F-066 item 8 proven through the real Gateway, not only decision_path.py's own arguments — done 2026-09-04

With AG-012 closed and the docstring self-review fixed, checked the
remaining Dev-2 priority list (`review/OWNER_WORK_ORDERS_DEMO_CANARY_2026-09-03.md`
Phase A) for the next fully-unblocked item, since Phase A items 3/4 (the
neutral-context Static Agent proof, the real fork coordination) need the
external Agent Developer's own runtime work — no session for that
developer is available, and no code on this side can substitute for it.

Re-read `feedback.1.28.md` §10's actual 9-condition list rather than
relying on this document's own summary of it (found stale in the
process — see below). `TestStrategyNeutrality`
(`tests/unit/test_agent_decision_path.py`) already proved condition 8
("a second toy/test agent with a deliberately different reason-code
vocabulary can use the same Core path without Core code changes") — but
only at `evaluate_agent_trade_intent`'s own argument level, with a
hand-built `TradeIntent`. That does not prove the *Gateway boundary*
itself is neutral, only this one function.

New `TestStrategyNeutralityThroughTheRealGateway`: onboards two fully
independent agents — distinct `AgentIdentity`, `TradingAssignment`,
`StrategyArtifact` hash, and a completely unrelated reason-code
vocabulary each (`ALPHA_MOMENTUM_BREAK`/`ALPHA_VOLUME_CONFIRM` vs.
`BETA_MEAN_REVERT_SETUP`/`BETA_RSI_DIVERGENCE`) — through the real,
unmodified `AgentGateway.submit_trade_proposal`, then feeds the two
resulting *real* `TradeIntent`s (not hand-built ones) through
`evaluate_agent_trade_intent` and asserts both reach an identical
`RiskVerdict.PASS`/`SupervisorVerdict.APPROVE`, with the sealed capsule
carrying each agent's own real intent through unmodified. Zero code in
either `AgentGateway` or `decision_path.py` branches on which agent
produced the intent.

**Found while doing this: `review/AGENT_FEEDBACK.md`'s F-066 row was
itself stale**, claiming items B/C (`AgentMarketContextV1`, opaque
Gateway reason-code handling) "remain open" after both had actually
shipped (§0l, §0o). Rewritten against the real 9-condition list instead
of a paraphrase of it: 6 of 9 conditions are now confirmed closed from
Crumblr's side (1, 3, 4, 5, 6, 9), condition 8 closes with this entry,
and only conditions 2 and 7 remain — both genuinely fork-dependent (the
external agent owning its own strategy computation; one HEALTHY Static
Agent context reaching an honest decision), neither actionable from this
side alone. This considerably narrows what F-066 was tracking as "several
items open" down to "one external coordination item, nothing left to
build on the Crumblr side."

Evidence: `tests/unit/test_agent_decision_path.py` 37→38 passed. Full
non-integration suite: 1218 passed, 1 pre-existing skip, 0 failed.
ruff/ruff format/mypy clean. Self-reviewed (`/code-review medium`): no
findings — the only clean pass this session that found nothing, recorded
for completeness rather than only recording the passes that caught
something.

---

## 0y. AG-023 closed: `PaperLiteOrchestrator`'s own risk-session read/write now lock-protected — done 2026-09-04

With every genuinely-unblocked Dev-2 item done (§0v-§0x) and no Dev-3 or
Static Agent Developer session available, checked with the user before
crossing a line stated explicitly to Dev 1 ("not fixing this myself —
`paper_lite.py`'s own recover/persist design is Dev-3-owned"). Confirmed:
go ahead.

`PaperLiteOrchestrator._recover_risk_session()`/`_persist_risk_session()`
each now wrap their own `session_store` call in
`self._risk_ledger_lock.held(self._assignment.canonical_symbol)` — the
same constructor parameter §0v/§0w already threaded through for the
Protocol-widening fix, now actually used for its real purpose.

**Verified deadlock-safe before writing anything**, not assumed: re-read
`process()`'s actual body end to end. `_recover_risk_session()` runs near
the top (its lock acquired and released before anything else); a *first*
`_persist_risk_session()` runs shortly after (before any Gateway/decision
evaluation); `evaluate_agent_trade_intent()` — which independently
acquires the same lock on its own connection — only runs after that,
well clear of either PAPER_LITE-side hold; a *second*
`_persist_risk_session()` call (inside `_outcome()`) runs after
`evaluate_agent_trade_intent()` has already released its own lock. None
of these holds ever overlaps another, so there is no nested-acquisition
deadlock risk between PAPER_LITE's own two locks and `decision_path.py`'s
internal one, even though all three ultimately contend for the same
Postgres advisory lock key.

**Deliberately not a full fix, and said so in the code, not just here**:
this closes the "torn read of a concurrent writer's partial state" class
of issue and serializes each individual read/write against the real
Core pipelines' own lock-protected cycles — but `_recover_risk_session()`
and `_persist_risk_session()` remain two separately-locked calls, not one
atomic critical section the way `LiveDecisionOrchestrator`'s own ADR-021
redesign is. A real, narrower lost-update window remains between this
class's own recover and its own later persist. Closing that fully would
mean moving the persist to run immediately after recovery — but unlike
`LiveDecisionOrchestrator`, `PaperLiteOrchestrator`'s `SimulatedBroker`
can fill an order *within the same `process()` call*, so moving the
persist earlier would silently stop capturing a same-cycle fill's P&L
until the next cycle. That is a real behavioral tradeoff for whoever
owns PAPER_LITE's fill-timing design next, not something to decide
unilaterally while closing a locking gap — documented plainly in
`_persist_risk_session()`'s own docstring, not just in this entry.

**Self-review (`/code-review medium`) found one real thing, correctly
scoped**: `RiskLedgerLock.held(...)` acquisition has no exception
handling anywhere in the chain — a transient DB/lock failure propagates
uncaught rather than degrading gracefully the way
`RiskSessionStore.load_latest()`'s own failure mode was designed to.
Checked before accepting this as a finding worth recording: is this new,
or inherited? Grepped `application/live_decision.py` — identical
property, already reviewed and shipped as part of ADR-021 itself; checked
`scripts/live_decision.py`'s main loop — only catches `KeyboardInterrupt`,
so a lock failure there crashes the process exactly the same way. Not a
regression this fix introduced, and not fixed here — recorded as AG-024,
a cross-cutting ADR-021 design question for whoever owns that decision
(most likely Dev 1), not something to patch asymmetrically in only one
of three-plus call sites.

Evidence: `tests/unit/test_paper_lite.py::TestAG023RiskLedgerLockAcquired`
(new — one full cycle acquires the lock at least twice, for exactly the
assignment's canonical symbol). Full `test_paper_lite.py`: 21/21. Full
non-integration suite: 1219 passed, 1 pre-existing skip, 0 failed.
ruff/ruff format/mypy clean. Full integration suite, against real
PostgreSQL (`crumblr_test_dev2`): **255 passed, 2 skipped** (pre-existing
`test_halt_survives_restart.py` Windows-permission-bits skips,
unrelated), 0 failed, in 414s. Committed `dda707c`, pushed.

`review/AGENT_FEEDBACK.md`: AG-023 moved to Closed with full resolution;
AG-024 opened (Open).

---

## 0z. AG-024 mirrored: risk-ledger lock failures fail closed on this side too — done 2026-09-04

Dev 1 decided AG-024's cross-cutting design question (raised in §0y) and
shipped the fix on their side (`live_decision.py`/`execution.py`,
`6d51281`, new ADR-021 §8), then handed the exact shape to mirror: wrap
the entire `with risk_ledger_lock.held(...) as connection: ...` block
(not the lock primitive — a primitive-level fix would also wrongly catch
the caller's own post-yield failures) in a plain `try`/`except
Exception`, log, trip the kill switch with the new
`ReasonCode.RISK_LEDGER_LOCK_UNAVAILABLE`, then refuse/skip through
whatever fail-closed mechanism that call site already has.

**`agent_gateway/decision_path.py::evaluate_agent_trade_intent()`**: on
failure, synthesizes the same halted `SessionRecovery` shape
`risk.session._halt()` itself returns for its own internal failures
(`EquityLedger(starting_equity=portfolio.account.equity)`,
`reason_codes=(ReasonCode.RISK_LEDGER_LOCK_UNAVAILABLE,)`) — the existing
`if recovery.must_halt: _trip(...)` handling below runs completely
unchanged, so this needed no second, parallel halt path. Kept the
module's "always seal a capsule" contract intact (`AgentDecisionPathResult
.capsule` is never optional, unlike `LiveDecisionOutcome`'s own
`capsule=None` skip state) rather than inventing a new result shape.

**`application/paper_lite.py`'s two AG-023 methods**: `_recover_risk_session()`
mirrors the same synthesize-and-fall-through-to-`_trip_risk_session()`
pattern. `_persist_risk_session()` has no `MarketSnapshot` on hand (only
`self._risk_recorded_at`), so `_trip_risk_session()` was factored into a
new `_trip_risk_session_at(reason_codes, *, occurred_at_utc,
correlation_id, detail)` — the original method becomes a one-line
wrapper over it — so both call sites trip through one mechanism instead
of `_persist_risk_session()` needing its own copy.

**Self-review caught a real gap before this shipped**: the first pass
left `paper_lite.py` without the `_log.error(...)` call ADR-021 §8
explicitly prescribes for every site. Reasoned (wrongly) that since
`paper_lite.py` has no logger anywhere else, adding one just for this
would be inconsistent with "local convention" — checked the actual ADR
text before accepting that reasoning, and it explicitly says "wrap ...
in a plain try/except Exception, log, trip the kill switch" as the
shape for all four sites including this one, not a suggestion. Fixed:
`_log = get_logger("paper_lite")` (new for this module), both except
blocks now log `"paper_lite.risk_ledger_lock_failed"` before tripping,
matching `live_decision.py`'s own naming exactly.

New tests: `tests/unit/test_agent_decision_path.py
::TestAG024RiskLedgerLockFailureFailsClosed` (2 — a lock failure trips
the switch and still seals a `BLOCK`/`SYSTEM_HALTED` capsule rather than
raising; NO_TRADE never reaches the lock at all, mirroring
`TestAG012`'s own no-trade case), `tests/unit/test_paper_lite.py
::TestAG024RiskLedgerLockFailureFailsClosed` (1 — a lock failure trips
the switch instead of raising).

Evidence: full non-integration suite **1224 passed**, 1 pre-existing
skip, 0 failed. ruff/ruff format/mypy clean. Full integration suite,
against real PostgreSQL (`crumblr_test_dev2`): **256 passed, 2 skipped**
(pre-existing `test_halt_survives_restart.py` Windows-permission-bits
skips, unrelated), 0 failed, in 478s.

`review/AGENT_FEEDBACK.md`: AG-024 moved to Closed with full resolution.

---

## 1. Where this track actually stands (as of 2026-09-04 — §0v; table below dated 2026-09-01 elsewhere, corrected rows marked)

| Step | Scope | State |
|---|---|---|
| A — design/contracts | ADR-005, threat model, eight contracts, structural tests | **DONE, merged, pushed** (`ba658c5`). |
| B — Agent Gateway in shadow | auth, assignment enforcement, idempotent proposal/NO_TRADE persistence, fail-closed error handling | **DONE, merged, pushed** (`bf18ec5`), self-review hardening merged (`d6a5361`, AG-007/008/009). |
| — HTTP transport | wire boundary for a genuinely separate process | **DONE, merged, pushed** (`a0e380a`). Local/shadow use only — F-064 (open, not blocking) requires TLS/mTLS before any remote exposure. |
| — AG-006 (`feature_snapshot_id`) | platform-owned evidence for external-agent context | **DONE, merged, pushed** (§0c). |
| — `TradeProposal → TradeIntent` mapping | review 1.26 §7 item 2 | **DONE, merged, pushed** (`f9bbceb`). |
| — shared no-MT5 integration path | `TradeIntent` → intent-time Risk → strategy-neutral Policy → capsule boundary | **DONE, merged, pushed** (`475331f`, strategy-neutral Policy Gate `c50312c` — §0f). |
| — Static Agent bridge, unhealthy-market smoke | honest transport/schema/identity/HTTP proof against the real fork | **Core wiring + real HTTP client done, merged, pushed** (`34ddbe6`, HTTP client pending push — §0g/§0h/§0j). Response→`NoTradeDecision`→Gateway submission not yet built. |
| — Agent Gateway event-conflict hardening | `append_event` fail-closed on same-key-different-content | **DONE, merged, pushed** (§0i, this entry). |
| — Phase 0 convergence with `main` | rebase/merge, exact-open-risk, PL-006 fixes, PR review | **DONE — corrected 2026-09-04**: PR #2 merged 2026-09-03 (§0t). Table row below was stale. |
| C — Supervisor boundary | external Supervisor wired in, fail-closed on timeout/error | **Corrected 2026-09-04 — DONE, not committed to `main` yet** (AG-003 closed 2026-09-03, §0u): in-process `ReferenceSupervisor` reference implementation, real transport/AG-012 closure still open. |
| — Phase A item 1: generic neutral-Agent wire envelope | move the response-envelope/adapter out of PAPER_LITE into Agent Gateway | **DONE 2026-09-04** (§0v) — `agent_gateway/neutral_agent_client.py`. PAPER_LITE's own switch-over is a separate, Dev-3-owned step. |
| D — research/training plane | artifact registry, Backtest Requests, Training | **Deliberately not started** — out of scope before MVP. |
| E — first agent-driven canary | full Step B/C bundle + Milestone A requirements | **Not started**, blocked on a HEALTHY genuine Static Agent decision (fork-side strategy-runtime work, F-066) and AG-012 closure. |

**Nothing in `src/crumblr/agent_gateway/` or `src/crumblr/persistence/agent_gateway.py`
is imported by anything outside itself and its own tests** — verified by
grep, most recently 2026-09-01. An HTTP transport exists
(`agent_gateway/http.py`) but nothing deploys/listens anywhere yet. No
agent path can reach execution, MT5, or the platform database outside
this track's own six tables.

### What's proven so far (ADR-005 §8 "first proof target", now exceeded)

> One external Trader consumes one genuine Crumblr decision context and
> returns explicit NO_TRADE or a valid BUY/SELL proposal with SL+TP, and
> Crumblr durably records identity, assignment, context and outcome in
> SHADOW with zero broker execution.

- **Identity/authentication** — interim shared-secret credential
  (`agent_gateway/auth.py`), fails closed on unknown agent, wrong
  credential, or suspended/retired status (AG-001).
- **Assignment authorization** — every proposal/NO_TRADE checked
  server-side against a durably-registered `TradingAssignment`: ownership,
  validity window, requested-risk band, hourly rate limit (AG-004, race
  fixed by AG-007).
- **Context-hash binding + expiry**, now with a **trusted, platform-issued
  evidence reference** — `feature_snapshot_id` (AG-006, §0c) — a proposal's
  `context_hash` must match a `DecisionContextBundle` Crumblr actually
  issued, backed by durably-recorded, non-fabricated evidence.
- **Idempotency / conflicting-retry detection** for both `TradeProposal`
  and `NoTradeDecision` (AG-002), resumable rather than fail-open on an
  interrupted claim (AG-008).
- **Fail-closed audit discipline** — every submission is durably claimed
  before any check runs; a legitimate refusal is a normal, auditable,
  machine-readable outcome, never a silently-dropped attempt.
- **Restart safety** — proven against real PostgreSQL across every store,
  including the evidence layer (§0c).
- **A wire transport** (§0b) — a genuinely separate process can reach the
  Gateway over HTTP with only the two agent-facing operations exposed.
- **`TradeProposal → TradeIntent` mapping** (§0d) — an accepted proposal
  now durably maps to a platform-owned, deterministic `TradeIntent`
  carrying the AG-006 evidence reference.

### What's still open

- **This list is stale as of 2026-09-04 — see §0f/§0u/§0v.** The shared
  no-MT5 integration path (review 1.26 §7 item 3) and the External
  Supervisor boundary (AG-003) are both **done** (§0f, §0u), not "next up."
  Kept here unedited below as the historical record of what was true at
  the time this section was written; do not read it as current.
- **`ProposalWithdrawal` enforcement** — still genuinely open: needs a real
  execution timeline to check its `SUBMISSION_STARTED`-cutoff rule against,
  which does not exist while `order_send` is unreachable.
- AG-012 (single final-Risk authority across the internal and Gateway
  decision paths) remains open — §0n's design analysis, not yet taken to
  Dev 1/the reviewer for sign-off.
- **F-064** — HTTP transport not authorized for remote/public exposure
  yet (not blocking current work).

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
| AG-006 | `TradeIntent.feature_snapshot_id` semantics for agent-originated intents | **CLOSED** — resolved by `feedback.1.26.md` §5, implemented 2026-09-01 (§0c) |
| AG-007 | Proposal-rate-limit check was a check-then-act race under concurrent submission | **CLOSED** (self-review, §0a) |
| AG-008 | Idempotent-retry path defaulted to `accepted=True` for a claimed-but-never-settled outcome | **CLOSED** (self-review, §0a) |
| AG-009 | `TradingAssignment.required_evidence_fields` was defined but never enforced | **CLOSED** (self-review, §0a) |
| F-064 | HTTP transport not authorized for public/remote exposure without TLS/mTLS | OPEN, global register — not a local-shadow blocker |

---

## 4. Merge status — updated 2026-09-01

Step A, Step B, the self-review hardening pass, and the HTTP transport are
all merged and pushed (`a0e380a`). The AG-006 implementation (§0c) and the
`TradeProposal → TradeIntent` mapping (§0d) are both built, tested,
quality-gate clean, **not yet committed** — pending the same per-turn
commit confirmation every other slice this session has gotten.
`agent/contracts` will again be identical to `origin/main` once they land.

---

## 5. Next actions (review 1.26 §7's Dev-2 priority order)

1. ~~Implement the AG-006 decision~~ — **DONE 2026-09-01** (§0c). Not the
   Dev-1 `compute_features()` extraction both tracks were pursuing before
   the review arrived — the reviewer explicitly redirected to a narrower,
   already-sufficient design.
2. ~~Build `TradeProposal → platform-owned TradeIntent` mapping~~ —
   **DONE 2026-09-01** (§0d). Self-review caught and fixed two real bugs
   before it shipped (identity-drift on replay, an unconstrained field
   that could crash acceptance).
3. **Add the shared no-MT5 integration path**: `DecisionContextBundle` →
   proposal/NO_TRADE → Gateway → `TradeIntent` → intent-time Risk →
   deterministic Policy → capsule boundary. Next up — this is the first
   place this track calls into Dev-1-owned/protected modules
   (`src/crumblr/risk/**`), so it needs care about the boundary even
   though no code there should need to change.
4. **Prove one genuine Crumblr context can reach the external Trader path
   in SHADOW with zero broker submission.**
5. **Implement the external Supervisor boundary** (AG-003): `APPROVE`/
   `VETO`/`UNKNOWN`, timeout/error/invalid → `UNKNOWN`, no mutation
   authority.
6. Integrate a real external Trader/Supervisor runtime through the typed
   boundary, once it exists.
7. Replace placeholder code provenance before any agent-driven promotion.

Also authorized, after item 2 (now done) or while waiting on a handshake:
one bounded Dashboard Observability slice (review 1.26 §9) — temporary
exception to normal file ownership, `src/crumblr/dashboard/**` only,
read-only boundary preserved, capped scope (not a redesign cycle).

Do not request formal reviewer input for routine continuation — only per
review 1.26/V3's escalation triggers (material safety/security ambiguity,
required Phase-4 invariant change, unresolved authority dispute,
unexpected agent→execution path, complete agent-driven canary readiness).

---

## 6. Summary for Dev 1 (canonical `status.md`, when next handed over)

> Agent Integration track: Step A, Step B, the self-review hardening pass
> (AG-007/008/009), and the HTTP transport are all merged/pushed to `main`
> (`a0e380a`). **AG-006 is closed** and the **`TradeProposal → TradeIntent`
> mapping is done** (both 2026-09-01, review 1.26 §7 items 1-2) — an
> accepted proposal now durably maps to a deterministic, platform-owned
> `TradeIntent` carrying the AG-006 evidence reference
> (`feature_snapshot_id`), a fixed `strategy_id` sentinel (`"external_agent"`,
> never confusable with `baseline_v1`/`ict_v1`), and `intent_id` derived
> from `proposal_id` so a replay reconstructs byte-identical content.
>
> Self-review caught two real bugs before either shipped: (1) the mapping
> originally sourced `model_version` from a live `AgentIdentity` lookup,
> which could make a replayed retry reconstruct a *different* intent than
> the original if the agent had been re-registered in between — fixed by
> never reading from identity in the mapping at all; (2)
> `TradingAssignment.strategy_artifact_hash` was an unconstrained `str`
> feeding directly into `TradeIntent.strategy_version` (a bounded
> `VersionTag`), so a malformed assignment could crash the first proposal
> accepted against it — fixed by tightening the field itself so it fails
> closed at registration, not deep inside acceptance. Full detail:
> `review/AGENT_STATUS.md` §0c/§0d, `review/AGENT_FEEDBACK.md` AG-006.
> 18 new tests total across both slices, full non-integration suite
> 855→871 passed, integration suite 7→11 passed, ruff/mypy clean. Not yet
> committed — pending the usual per-turn confirmation, will sync onto your
> latest first.
>
> Next on my side: the shared no-MT5 integration path (review 1.26 §7 item
> 3) — the first place this track calls into your Risk/Policy modules. No
> code changes to your side expected, but will raise a handshake
> immediately if that assumption turns out wrong.
