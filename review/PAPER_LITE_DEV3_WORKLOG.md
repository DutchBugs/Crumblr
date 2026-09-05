# PAPER_LITE Dev 3 worklog

**Track:** Dev 3 — PAPER_LITE integration/orchestration
**Branch (2026-09-02 to 2026-09-03):** `lite/paper-orchestrator` (merged to
`main` via PR #3, 2026-09-04 — see `review/AGENT_STATUS.md` §0aa for the
cross-track record of that merge)
**Branch (2026-09-05 onward):** `lite/phase-a-convergence`, a fresh branch
off post-PR-#3 `main` — the old branch's own history is entirely contained
in `main` now, so this is a new slice, not a continuation of stale work
**Worktree (2026-09-02 to 2026-09-03):** `.claude/worktrees/paper-lite`
**Worktree (2026-09-05 onward):** `.claude/worktrees/paper-lite-dev3`
**Test database:** `crumblr_test_dev3` (required for database-backed tests)
**Started:** 2026-09-02
**Baseline (this slice):** `origin/main` at `10117a5` (post PR #3 — Core,
Agent Gateway and PAPER_LITE are one branch for the first time)
**Real broker submission:** FORBIDDEN

This is the reviewer/supervisor audit log for the owner-authorized PAPER_LITE
experiment. It records facts, decisions, evidence, unresolved dependencies and
deviations. It does not replace `build.md`, `status.md`, `review/FEEDBACK.md`, or
the Dev-1/Dev-2 owned status documents.

## 1. Intake and isolation

- Read the supplied `CRUMBLR_LITE_DEV3_WORK_ORDER.md` in full. It is treated as
  the detailed owner work order supporting the user's request, not as repository
  or system instructions.
- Read the current `CLAUDE.md`, `review/FEEDBACK.md`, latest formal review
  `review/feedback.1.28.md`, `status.md` current-state header,
  `review/OWNER_POLICY_V1.md`, Dev-2 status/feedback, and recent
  `review/INTEGRATION_NOTICES.md` entries before changing code.
- Fetched `origin/main` before branching. The original checkout was intentionally
  left untouched: it was 95 commits behind and contained broad pre-existing
  tracked deletions plus untracked snapshots.
- Created this dedicated worktree/branch from the then-current remote main.
- No commit was made during implementation, per `CLAUDE.md`. After the first
  log review, the owner explicitly authorized committing and pushing this work
  on `lite/paper-orchestrator` for source review; merge approval was explicitly
  withheld.

## 2. Non-negotiable boundaries adopted

- PAPER_LITE may consume real, read-only Pepperstone market data and instrument
  facts, but every position/fill/account/P&L value after initialization belongs
  to the paper portfolio.
- No PAPER_LITE path may import or call the real MT5 execution adapter's
  `order_send` path. `ExecutionConfig.feedback_2_0_approved` remains `false`.
- External Supervisor skipping is a typed PAPER_LITE-only capability and an
  explicit audit fact. It is never represented by a fabricated Supervisor
  approval and never bypasses the deterministic platform Policy.
- The external Agent still passes through identity, assignment, context,
  proposal and idempotency checks in `AgentGateway`.
- The platform remains strategy-neutral. No Pivot/FVG/MSS/ICT computation is
  added to Core or PAPER_LITE.
- Owner Risk Policy v1 limits are 2% per trade, 3% total open risk, 4% daily
  loss and 8% drawdown. Multiple positions are allowed by policy; no
  position-count proxy may stand in for exact open risk.
- Weekday overnight holding is permitted. New Friday entries stop at close
  minus 15 minutes, the book must be confirmed flat by close minus 5 minutes,
  and weekend holding is forbidden. HALT reset remains human/operator-only.

## 3. Baseline findings and cross-track dependencies

### PL-001 — Core risk exists; shared Agent-path consumption remains pending

**Status:** CLOSED 2026-09-05 (Dev-3 Phase-A convergence, section 2)
**Closed by:** Dev 2 (D1.4/D2.2, prior session) shipped `agent_gateway
/decision_path.py::evaluate_agent_trade_intent()` calling `risk
.portfolio_risk.assess_open_risk()` directly — `PortfolioSnapshot` no longer
carries a position-count approximation at all, confirmed by reading the
current source, not assumed from an older description. This session removed
PAPER_LITE's own `if positions: EXACT_OPEN_RISK_UNAVAILABLE` guard (§5
2026-09-05 entry) now that the seam this finding was waiting for genuinely
exists. Regression: `tests/unit/test_paper_lite.py
::TestPaperLiteFlow::test_a_second_full_size_position_exceeding_the_open_risk_budget_is_blocked`
(>3% blocks on `OPEN_RISK_LIMIT`, not on position count),
`::test_several_small_positions_under_the_open_risk_budget_are_not_blocked_by_count`
(<3% combined does not block), `::test_flat_portfolio_open_risk_is_exact_zero_not_unknown`.

Original finding, kept for the historical record: after the 2026-09-03
rebase, `PortfolioSnapshot` still fed `PortfolioState.open_risk_fraction` as
`config.risk.max_risk_per_trade * len(open_positions)` — the exact
approximation Owner Policy v1 and this work order forbid — so PAPER_LITE
fail-closed on any second directional proposal rather than accept an
approximation. That approximation is gone from the current source.

### PL-002 — current Core Risk still enforces one exposure per symbol

**Status:** CLOSED by current Core; no PAPER_LITE shared-Core edit
The rebase includes the owner-policy replacement that withdrew O-004. The
remaining multi-position blocker is PL-001's shared Agent-path input seam, not a
PAPER_LITE risk rule.

### PL-003 — current intraday Core policy still models daily flatten

**Status:** CLOSED 2026-09-05 (Dev-3 Phase-A convergence, section 1)
**Closed by:** Dev 1 shipped the weekly Friday/weekend redesign
(`risk/trading_window.py`, D1.5/ADR-012) in the intervening period —
confirmed by reading the module directly: `SessionPhase`/`phase_at()`/
`IntradayPolicy` are the exact weekly-close-relative shape this finding was
waiting for, already consumed by `LiveDecisionOrchestrator`/
`ExecutionOrchestrator`. This session removed PAPER_LITE's own duplicate
`PaperLiteSessionPhase`/`paper_lite_session_phase()` and switched
`platform_config()`'s `IntradayConfig.enabled` from `False` to `True`, so
PAPER_LITE now consumes the one canonical calendar authority instead of a
second definition that could drift from it. Regression:
`tests/unit/test_paper_lite.py::TestSessionPolicy` (rewritten to prove the
config→policy wiring, not re-test Core's own already-tested arithmetic),
`::TestPaperLiteFlow::test_friday_deadline_flattens_remaining_paper_exposure`/
`::test_weekend_exposure_raises_instead_of_carrying_silently` (end-to-end,
unchanged, still pass — they test the orchestrator's behavior, not the
removed function).

### PL-004 — genuine HEALTHY Static Agent strategy runtime

**Status:** OPEN / owned by Dev 2 + external Agent developer
The current Crumblr side has the neutral context and an unhealthy-market Static
Agent smoke path. A genuine HEALTHY Pivot-2.2 decision remains agent-side work
under F-066. Dev 3 can prove the identical Crumblr contracts with a deterministic
toy agent, but will not label that the final Static Agent proof.

### PL-005 — risk-session persistence in the shared Agent decision path

**Status:** CLOSED in the Lite composition; shared path unchanged
**Owner:** Dev 3 for PAPER_LITE composition; shared Agent path remains unchanged

The shared `evaluate_agent_trade_intent()` recovers `RiskSessionStore` state but
does not save a new state. Using it directly in a long-running paper process
would therefore refill the apparent daily-loss/drawdown budget on every call.
PAPER_LITE now recovers and persists the existing Core `RiskSessionState` around
each simulated state transition. It records Core's entry-geometry allocation
risk from paper positions, trusted instrument facts and paper equity. Existing
paper exposure with no durable risk-session record trips the existing KillSwitch
as `SAFETY_STATE_UNKNOWN`; Lite never silently manufactures a fresh budget.

### PL-006 — recovered Core risk maxima are not used by the shared gate

**Status:** CLOSED — shared Core fix landed in the intervening period
(unrelated commit series also numbered "PL-006" in the owner work order,
confirmed the same underlying gap by reading the code, not assumed from
name collision alone)
`risk.session.recover_session()` now checks the recovered
`max_drawdown_fraction`/`max_session_loss_fraction` against the *configured*
thresholds itself (`risk/session.py` lines ~315-320) and halts during
recovery if either is already exhausted — exactly this finding's ask, now
owned by the one shared function every pipeline (`LiveDecisionOrchestrator`,
`decision_path.py`, and PAPER_LITE) calls. PAPER_LITE's own
`_recover_risk_session()` already removed its duplicate hand-rolled check in
a prior session once this shipped (see that method's own docstring); nothing
further needed here.

### PL-007 — no durable incident register is available to PAPER_LITE

**Status:** OPEN platform seam / explicit operator assertion required
**Owner:** Core/control-plane integration

The strategy-neutral platform Policy requires `IncidentStatus`, but this
repository exposes no durable incident-register reader to the Lite process.
The orchestrator therefore defaults to `UNKNOWN`, which vetoes. The standalone
runner only supplies `CLEAR` when the operator passes
`--confirm-paper-incident-clear` with operator identity and a reason after
checking the limited paper integration scope. The assertion, UTC timestamp and
context are durably journalled as `PAPER_LITE_INCIDENT_CLEAR_ASSERTED`. This
cannot bypass Risk or Policy, reset HALT, or enable real submission. Replace it
with a durable incident read when the shared seam exists.

## 4. Implementation plan

1. Add a typed PAPER_LITE-only execution capability and audit vocabulary in a
   Dev-3-owned application module, with no real adapter dependency.
2. Adapt trusted live observations into the existing `GeneratedTick` input
   expected by `SimulatedBroker`; do not duplicate its fill engine.
3. Wrap `SimulatedBroker` with an append-only, replayable paper journal so
   requests and observations reconstruct positions/P&L after restart and retain
   `order_request_id` idempotency.
4. Add a paper portfolio provider/read model with exact open-risk calculation
   or a fail-closed `unknown` result.
5. Compose neutral context publication, external-agent response ingestion,
   `AgentGateway`, Core Risk + strategy-neutral Policy, PAPER_LITE Supervisor
   skip audit, paper-only approved instruction and simulated fill.
6. Add a dedicated runner/runbook and tests. Real Pepperstone and genuine
   HEALTHY Static Agent evidence will be recorded separately from deterministic
   local acceptance evidence.

## 5. Evidence log

Evidence is appended here as work proceeds. Failed/skipped checks are recorded
as plainly as passed checks.

### 2026-09-02 — implementation milestone

- Added `PaperLiteOrchestrator`, explicit `PAPER_LITE` settings and a runner that
  reads only the existing PostgreSQL market/spec stores and requires database
  name `crumblr_test_dev3`.
- Added a concrete `DurablePaperBroker` which can construct only
  `SimulatedBroker`; it has no injectable real broker port and keeps a
  hash-chained, append-only replay journal.
- Reused the existing simulator for fills, spread/slippage, SL/TP, P&L and
  idempotent `order_request_id` behavior. No second fill engine was introduced.
- Added the exact external Agent HTTP boundary, a deterministic local toy Agent,
  and provisioning CLI using the existing identity/credential/assignment stores.
- Added a paper portfolio view for balance, equity, realized/unrealized P&L,
  authorized risk and exact current stop-risk amount/fraction.
- Added a concise operator runbook. Safety-state initialization is explicit and
  audited; ordinary startup cannot auto-reset HALT.
- Targeted verification after the risk-session hardening: Ruff passed, mypy
  passed, and 35 PAPER_LITE unit tests passed. These include persisted 4%/8%
  loss limits, weekday overnight retention, Friday flattening and explicit
  weekend exposure escalation, plus default incident-UNKNOWN veto behavior.
- Real Pepperstone/Static-Agent integration was not executed locally: no
  operator credentials, Windows MT5 reader or genuine HEALTHY Static Agent were
  available in this worktree. The first live evidence milestone therefore
  remains an operator/reviewer run, not a claimed result.

### 2026-09-02 — final local quality gate

- Dedicated PostgreSQL database `crumblr_test_dev3` was created in the existing
  local `crumblr-pg` test container. No shared `crumblr` database was targeted.
- `uv run ruff check .`: passed.
- `uv run ruff format --check .`: 199 files formatted.
- `uv run mypy .`: passed, 193 source files.
- `uv run pytest` against `crumblr_test_dev3`: 1,282 passed, 0 failed, 0
  skipped.
- Determinism: two `scripts/run_replay.py --bars 600` runs produced identical
  MD5 `7513db8de3ac1dbb691d842c3afaf27d`.
- CLI `--help` smoke checks passed for the existing read-only MT5 reader, Agent
  provisioning tool, toy Agent and PAPER_LITE runner.
- No real Pepperstone connection, MT5 execution call or external service call
  was made during this gate.
- Refetched `origin/main` after the gate: both the Lite branch base and
  `origin/main` remain at `0648e41` (ahead/behind `0/0`). No rebase or merge was
  needed and no shared tracked file was changed.

### 2026-09-03 — requested review corrections

- Fetched and rebased the branch onto current `origin/main` `dd2106c`; this
  includes Core owner-risk commit `b2a07a5`. No merge to `main` was performed.
- Replaced PAPER_LITE's quote-based open-risk calculation with Core
  `assess_open_risk(position state, trusted spec, paper equity)`. The result is
  used by the neutral Agent context, paper read model and durable risk session.
- Prevented a previously observed closed bar's high/low from being replayed
  after a later entry. Repeated observations of the same source bar now advance
  stop/TP handling with the current executable quote only; the full closed bar
  remains available to the Trading Agent. The journal records and validates
  whether that historical range was applied.
- Centralized the exact `crumblr_test_dev3` database guard and applied it to the
  runner and provisioning CLI before engine construction.
- Made incident-CLEAR assertions operator-bound and durable with operator,
  timestamp, reason/context and audit fact
  `PAPER_LITE_INCIDENT_CLEAR_ASSERTED`. Default `UNKNOWN` still vetoes and this
  capability neither resets HALT nor changes execution authority.
- Included Friday T-5 simulated closures in `PaperLiteOutcome.closed_trades` and
  verified the outcome against the broker's closed-trade ledger.
- Added regression coverage for all points above. Targeted PAPER_LITE tests:
  29 passed.
- Final gate after the corrections: `ruff check .` passed; all 201 files passed
  `ruff format --check .`; mypy passed for 195 source files; the full suite
  passed with 1,304 tests against only `crumblr_test_dev3`.
- Two 600-bar deterministic replays produced identical MD5
  `7d767883dc43a0f6527b9d6348dcc5fc`.
- This gate made no Pepperstone connection, MT5 execution call, real broker
  write or external Agent claim. Genuine HEALTHY Static Agent acceptance remains
  outstanding under PL-004.

### 2026-09-05 — Phase-A convergence (owner work order, sections 1-3 + 7)

New worktree/branch off post-PR-#3 `main` (`10117a5`) — the prior
`lite/paper-orchestrator` branch's entire history is now inside `main`
itself, so this is a fresh slice, not a rebase of stale work. Removed the
temporary integration glue the owner work order named, now that the shared
seams it was waiting on genuinely exist (confirmed by reading the current
source before removing anything, not assumed from this worklog's own older
description of the gap):

- **Section 1 (session-policy duplication).** Removed
  `PaperLiteSessionPhase`/`paper_lite_session_phase()`. PAPER_LITE now
  enables Core's own shared `risk.trading_window` (D1.5/ADR-012) via
  `platform_config()`'s `IntradayConfig.enabled=True`, and
  `PaperLiteOrchestrator.process()` calls `trading_window.phase_at()`
  directly. Closes PL-003.
- **Section 2 (single-position open-risk guard).** Removed the
  `if positions: EXACT_OPEN_RISK_UNAVAILABLE` block —
  `agent_gateway/decision_path.py::evaluate_agent_trade_intent()` already
  calls Core `assess_open_risk()` directly (shipped by Dev 2 in the
  intervening period), so the seam this guard was waiting for exists.
  Closes PL-001. `PaperLiteOutcomeType.EXACT_OPEN_RISK_UNAVAILABLE` removed
  as dead code.
- **Section 3 (generic neutral-Agent adapter).** Retired
  `application/paper_lite_agent.py` (`HttpPaperLiteTradingAgent`,
  `PAPER_LITE_AGENT_SCHEMA_VERSION`) entirely — `scripts/paper_lite.py` now
  constructs `agent_gateway.neutral_agent_client.HttpNeutralAgentClient`
  instead, and `application/paper_lite_toy_agent.py`'s response envelope
  now speaks `NEUTRAL_AGENT_RESPONSE_SCHEMA_VERSION`. Every caller was
  migrated before deletion, per the work order's own instruction — checked
  by grep, not assumed. `tests/unit/test_paper_lite_agent.py` deleted; its
  `TestToyAgent` class (still needed — nothing else covers
  `create_toy_agent_app`) moved to a new `tests/unit/test_paper_lite_toy_agent.py`
  with the schema-constant import updated; its `TestHttpPaperLiteTradingAgent`
  class was not preserved, since `tests/unit/test_neutral_agent_client.py`
  (Dev 2's own test file, already existed) covers the same envelope more
  thoroughly.
- **Section 4 (AG-012/023/024)** — verified untouched: grepped
  `risk_ledger_lock`/`RISK_LEDGER_LOCK_UNAVAILABLE` across the diff,
  confirmed the lock-protected recover/persist pair and its fail-closed
  exception handling are exactly as the prior session shipped them. No
  code changed here.
- **Section 5 (Supervisor skip)** — verified already correct: PAPER_LITE's
  own call into `evaluate_agent_trade_intent()` never passes `proposal=`/
  `external_supervisor=`, so the external-Supervisor step is skipped
  structurally, not by a bypass; `SUPERVISOR_SKIPPED_PAPER_MODE` is only
  recorded after both Core Risk PASS and platform Policy APPROVE. Added
  `TestTypedPaperOnlyBoundary::test_the_supervisor_skip_cannot_activate_outside_paper_mode`
  proving the orchestrator itself refuses to construct against a non-PAPER
  config or a non-PAPER assignment — the real reason this can never
  activate outside paper mode, not merely an assertion that it doesn't
  today.
- **Section 7 (regressions).** Rewrote `TestSessionPolicy` against the
  shared `trading_window` wiring (not re-testing Core's own already-tested
  arithmetic — see `tests/unit/test_trading_window.py`). Added: a
  >3%-budget block test, a <3%-combined not-blocked-by-count test, a
  flat-portfolio-exact-zero test, the paper-only-Supervisor-skip
  construction-guard test above, and a parametrized static-source check
  (`test_no_real_demo_order_send_reference_anywhere_in_the_paper_path`)
  across `paper_lite.py`/`paper_lite_toy_agent.py`/`scripts/paper_lite.py`
  for `demo_execution`/`DemoOrderSendMt5Gateway`/`order_send` references.

**Section 6 (real Phase-A product proof) not attempted** — PL-004 (a
genuine HEALTHY Static Agent) remains open, owned by Dev 2 and the external
Agent Developer; no session for that developer has been available to
coordinate with. Nothing on the Crumblr side blocks it once that runtime
exists — `scripts/paper_lite.py`/`create_toy_agent_app` now speak the one
canonical wire contract (section 3) a real Static Agent would need to
match.

**Self-reviewed** (`/code-review medium`) before this entry: one finding
(this status update itself was missing) — no code defects found. Verified
independently, not only trusted: read `risk/session.py` directly to
confirm PL-006's closure claim before writing it, grepped for every
remaining reference to removed symbols repo-wide (zero hits outside test
files already updated), and read `decision_path.py`'s actual
`assess_open_risk()` call site before claiming PL-001 closed rather than
assuming it from an older description.

Evidence: full non-integration suite **1225 passed**, 1 pre-existing skip,
0 failed. Full integration suite, against the dedicated `crumblr_test_dev3`
database (created fresh this session — did not previously exist):
**256 passed, 2 skipped** (pre-existing `test_halt_survives_restart.py`
Windows-permission-bits skips, unrelated), 0 failed, in 407s. `ruff check`/
`ruff format --check`/`mypy` all clean (196 source files). No Pepperstone
connection, MT5 execution call, real broker write, or external Agent claim
made during this gate.

## 6. Reviewer/supervisor handoff

**Updated 2026-09-05 — most of this section was stale after the Phase-A
convergence pass; only PL-004/PL-007 and the real product proof remain
genuinely open.**

- **PL-001, PL-003, PL-006 — CLOSED this session**, see the 2026-09-05
  evidence-log entry above. No further Dev-1 review requested for these
  specifically; flag only if the closure reasoning above looks wrong.
- **PL-002, PL-005 — already CLOSED**, unchanged.
- **PL-007 (durable incident register) — still OPEN**, owned by Core/
  control-plane integration; the operator-assertion workaround is
  unchanged and still the only path to `IncidentStatus.CLEAR`.
- **Dev 2 / external Agent Developer:** PL-004 is the one remaining real
  blocker — a genuine HEALTHY Static Agent response is still needed for
  the actual Phase-A product proof (section 6 of the 2026-09-05 owner work
  order). The toy Agent now speaks the same canonical neutral-Agent wire
  contract (`agent_gateway/neutral_agent_client.py`) a real Static Agent
  would need to match, so there is no known remaining Crumblr-side gap —
  this is a pure "run it against the real thing" checkpoint now.
- **Operator/owner:** supply the dedicated database URL, Gateway
  credential, Agent bearer token, stable IDs and Windows read-only MT5
  feed described in the runbook, once a genuine HEALTHY Static Agent
  exists to point at. First safety activation/reset must include operator
  identity and an incident note.
- **Promotion status:** local implementation milestone only. Do not call
  the final PAPER_LITE goal complete until the real read-only Pepperstone
  feed and genuine HEALTHY Static Agent evidence have run with zero broker
  writes.
- **Git status:** committing/pushing `lite/phase-a-convergence` is this
  session's own continuation of the prior explicit authorization (the
  original `lite/paper-orchestrator` branch it superseded was itself
  already merged to `main` via PR #3, 2026-09-04) — branch push only, no
  merge to `main` performed by this session.
