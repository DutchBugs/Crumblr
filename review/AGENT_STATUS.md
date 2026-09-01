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

**Not done yet:** items C (`AgentMarketContextV1` contract), E (opaque
Gateway reason-code handling — `AgentGateway`'s own contracts/validation
still need a look for any Core-side vocabulary assumptions), G (fork-side
coordination — not this track's code to write), H (a full end-to-end
strategy-neutrality proof through the Gateway/HTTP layer, not just this
module), and A (the unhealthy-market smoke proof itself, still not started).

---

## 1. Where this track actually stands (as of 2026-09-01, Phase 5 / `feedback.1.26.md`)

| Step | Scope | State |
|---|---|---|
| A — design/contracts | ADR-005, threat model, eight contracts, structural tests | **DONE, merged, pushed** (`ba658c5`). |
| B — Agent Gateway in shadow | auth, assignment enforcement, idempotent proposal/NO_TRADE persistence, fail-closed error handling | **DONE, merged, pushed** (`bf18ec5`), self-review hardening merged (`d6a5361`, AG-007/008/009). |
| — HTTP transport | wire boundary for a genuinely separate process | **DONE, merged, pushed** (`a0e380a`). Local/shadow use only — F-064 (open, not blocking) requires TLS/mTLS before any remote exposure. |
| — AG-006 (`feature_snapshot_id`) | platform-owned evidence for external-agent context | **DONE, merged, pushed** (§0c). |
| — `TradeProposal → TradeIntent` mapping | review 1.26 §7 item 2 | **DONE, implemented and tested 2026-09-01** (§0d) — not yet committed, see §4. |
| — shared no-MT5 integration path | `TradeIntent` → intent-time Risk → deterministic Policy → capsule boundary | **NEXT**, not started (review 1.26 §7 item 3). |
| C — Supervisor boundary | external Supervisor wired in, fail-closed on timeout/error | **Not started** (AG-003), blocked behind the integration path above per review 1.26 §7's ordering. |
| D — research/training plane | artifact registry, Backtest Requests, Training | **Deliberately not started** — out of scope before MVP. |
| E — first agent-driven canary | full Step B/C bundle + Milestone A requirements | **Not started**, blocked on the integration path + C. |

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

- **The shared no-MT5 integration path** — review 1.26 §7 item 3: wire
  the constructed `TradeIntent` through intent-time Risk, the
  deterministic Policy Gate, and `DecisionCapsule` sealing. Next up.
- **`ProposalWithdrawal` enforcement** — needs the integration path above
  first (its `SUBMISSION_STARTED`-cutoff rule needs a real execution
  timeline to check against).
- **External Supervisor boundary** (AG-003) — review 1.26 §7 item 5,
  after the integration path.
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
