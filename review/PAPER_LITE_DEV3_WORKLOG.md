# PAPER_LITE Dev 3 worklog

**Track:** Dev 3 — PAPER_LITE integration/orchestration
**Branch:** `lite/paper-orchestrator`
**Worktree:** `.claude/worktrees/paper-lite`
**Test database:** `crumblr_test_dev3` (required for database-backed tests)
**Started:** 2026-09-02
**Baseline:** `origin/main` at `0648e41`
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

### PL-001 — exact open-risk input is not yet available in the shared agent path

**Status:** OPEN / fail-closed in PAPER_LITE
**Owner:** shared seam requires Dev 1 + Dev 2 coordination; Dev 3 will not silently
change the shared semantics.

At baseline, `agent_gateway/decision_path.py::PortfolioSnapshot` contains only
`account`, `open_positions` and `reconciliation_status`. The implementation then
sets `PortfolioState.open_risk_fraction` to:

```text
config.risk.max_risk_per_trade * len(open_positions)
```

This is exactly the approximation prohibited by Owner Policy v1 and the Lite
work order. PAPER_LITE therefore must not send a directional proposal into this
path while any open paper position exists unless an exact open-risk seam has
been supplied. The planned Lite portfolio derives exact remaining stop risk from
the simulated position volume, entry/current executable price, stop and trusted
instrument specification. If that calculation is impossible or ambiguous, the
run refuses the new entry rather than guessing.

### PL-002 — current Core Risk still enforces one exposure per symbol

**Status:** OPEN / owned by Dev 1
`risk/policies.py::MAX_EXPOSURES_PER_SYMBOL = 1` still implements the superseded
O-004 rule. PAPER_LITE will not fork or weaken Core Risk. Multiple-position
acceptance tests remain blocked until Dev 1 lands the owner-policy replacement;
the Lite test suite will prove fail-closed behavior meanwhile and will expose
this dependency explicitly.

### PL-003 — current intraday Core policy still models daily flatten

**Status:** OPEN / owned by Dev 1
The owner work order assigns replacement of the old daily intraday behavior with
Friday/weekend semantics to Dev 1. PAPER_LITE will consume that Core policy once
available and will not invent a second calendar. Until then, weekday-overnight
and Friday/weekend acceptance evidence cannot honestly be claimed complete.

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
each simulated state transition. It records exact current stop risk from the
simulator's bid/ask, position volume, stop and trusted tick facts. Existing
paper exposure with no durable risk-session record trips the existing KillSwitch
as `SAFETY_STATE_UNKNOWN`; Lite never silently manufactures a fresh budget.

### PL-006 — recovered Core risk maxima are not used by the shared gate

**Status:** OPEN in shared Core / fail-closed guard present in PAPER_LITE
**Owner:** Dev 1/shared Risk seam; Dev 3 will not alter shared Risk on this branch

`recover_session()` preserves `max_session_loss_fraction` and
`max_drawdown_fraction`, but `risk.policies.evaluate()` compares the ledger's
current `session_loss_fraction` and `drawdown_fraction`. When current equity has
recovered, a previously reached maximum can therefore cease to gate a new
trade. PAPER_LITE checks the persisted maxima against the same configured 4%
and 8% thresholds during recovery and trips the existing KillSwitch with
`DAILY_LOSS_LIMIT`/`MAX_DRAWDOWN`. This is conservative composition glue, not a
new Risk rule; the shared semantics still need owner-track review.

### PL-007 — no durable incident register is available to PAPER_LITE

**Status:** OPEN platform seam / explicit operator assertion required
**Owner:** Core/control-plane integration

The strategy-neutral platform Policy requires `IncidentStatus`, but this
repository exposes no durable incident-register reader to the Lite process.
The orchestrator therefore defaults to `UNKNOWN`, which vetoes. The standalone
runner only supplies `CLEAR` when the operator passes
`--confirm-paper-incident-clear` after checking the limited paper integration
scope. This assertion cannot bypass Risk, cannot bypass Policy, cannot reset
HALT and cannot enable real submission. Replace it with a durable read when the
shared incident seam exists.

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

## 6. Reviewer/supervisor handoff

- **Dev 1 / Core:** review PL-001, PL-002, PL-003, PL-006 and PL-007. PAPER_LITE is
  deliberately fail-closed for a second position until the exact open-risk
  field is accepted by shared Risk, and its Friday guard remains temporary.
- **Dev 2 / external Agent:** run the final acceptance with a genuine HEALTHY
  Static Agent response using the same neutral HTTP contract. The toy Agent is
  plumbing evidence only.
- **Operator/owner:** supply the dedicated database URL, Gateway credential,
  Agent bearer token, stable IDs and Windows read-only MT5 feed described in the
  runbook. First safety activation/reset must include operator identity and an
  incident note.
- **Promotion status:** local implementation milestone only. Do not call the
  final PAPER_LITE goal complete until the real read-only Pepperstone feed and
  genuine HEALTHY Static Agent evidence have run with zero broker writes.
- **Git status:** the first reviewer said "do not merge yet" but explicitly
  authorized committing and pushing `lite/paper-orchestrator` so the actual
  source can be reviewed. This log entry records that scope: branch push only,
  no merge to `main`.
