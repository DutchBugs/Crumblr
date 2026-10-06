# Pepperstone DEMO execution boundary

Verified 2026-10-06 against `main` after the Demo Trading Operational Readiness pass.
Everything here is checked in code, pinned by `tests/unit/test_demo_execution_boundary.py`, and
(for the account identity) checked read-only against the real terminal
(`var/acceptance_evidence_2026-10-05/72_demo_boundary_live_readonly_check.json`, not committed).

## 1. What can reach the real `order_send`

Exactly one module calls the MT5 `order_send`: `src/crumblr/mt5_gateway/demo_execution.py`
(`DemoOrderSendMt5Gateway`: `order_send`, `close_position`, `cancel_pending_order`). It is imported by
exactly three scripts, never by `src/`:

| Script | Real broker effect | How it is armed |
|---|---|---|
| `scripts/agent_canary_execution.py` | one entry order | only when `--canary-permit-id` is passed |
| `scripts/close_demo_canary_position.py` | close one named ticket | explicit ticket, run by hand |
| `scripts/cancel_demo_canary_pending_order.py` | cancel one named pending order | explicit ticket, run by hand |

Nothing else can submit: `OrderCheckMt5Gateway` (used by the reader, the dashboard, the preflight path
and every other script) has `order_send` / `close_all_positions` / `cancel_pending_orders` hard-disabled.
The dashboard, the Agent Gateway, Risk, the evaluator and `application/execution.py` never import the
real gateway. The Static Agent has no MT5 and no execution capability (`/health` reports both `false`).
The window driver and the preflight scripts used so far never pass a permit, so they cannot reach it.

## 2. What selects the broker account

- **Credentials:** `CRUMBLR_MT5_LOGIN` / `CRUMBLR_MT5_PASSWORD` / `CRUMBLR_MT5_SERVER`, read from
  Windows Credential Manager (DPAPI, this user) by the supervisor / launch helpers and passed to child
  processes only. None are in YAML, code, logs, the dashboard or HTML. Terminal path:
  `CRUMBLR_MT5_TERMINAL_PATH` (local host setting).
- **Pinned target** (`config/paper.yaml`, `account_guard`): `require_demo_account: true`,
  `expected_server: PepperstoneUK-Demo`, `expected_currency: EUR`, `expected_leverage: 30`. The login is
  not pinned in the guard (`expected_login: null`); it is pinned by fingerprint in the next two items.
- **Approved account reference:** `execution.approved_canary_account_ref` in
  `config/agent_canary_demo.yaml` = `4f857e6a72f9ad30` = `fingerprint({login, server})[:16]` (never the raw login).
- **Permit:** `CanaryPermit.approved_account_ref` + `expected_server`, set by
  `scripts/issue_canary_permit.py --login ... --server ...`.
- **Environment:** `Environment.PAPER`. There is no `config/live.yaml`; `load_config(LIVE)` raises
  `PermissionError` without `CRUMBLR_ALLOW_LIVE=1`, and `LIVE` additionally needs
  `live_trading_acknowledged: true` and a recorded human promotion.

## 3. Proof the configured target is DEMO, not LIVE (2026-10-06, read-only)

- The real account, read from the terminal: server `PepperstoneUK-Demo`, `is_demo = true`
  (`account_info().trade_mode == DEMO`, the terminal's own statement), EUR, leverage 30, masked login `***706`.
- Its account reference is `4f857e6a72f9ad30`, equal to `approved_canary_account_ref`; recomputing the
  fingerprint from the stored credentials gives the same value; the credential server equals `expected_server`.
- The same real account, checked against deliberately wrong pins, is refused each time:
  server `...-Live`, currency `USD`, leverage `500`, login `...456` -> `AccountGuardError`.

## 4. Fail-closed behaviour

1. Every `DemoOrderSendMt5Gateway` call starts with `self.account()` -> `_verify_account`: refuses a
   non-demo account or any server / currency / leverage / login mismatch before building a request.
2. `order_send` refuses an order without a FINAL Risk decision id (`MissingFinalRiskDecisionError`).
3. `SubmissionGate` needs ten simultaneous conditions: DEMO-only environment; demo account; connected;
   reconciliation MATCHED; fresh GOOD tick; safety RUNNING; approved risk-config version; `submission_enabled`;
   terminal AlgoTrading on; `feedback_2_0_approved`; and the observed account equals the approved reference.
   `submission_enabled`, `feedback_2_0_approved`, `flatten_submission_enabled` and
   `approved_canary_account_ref` ship closed/unset; any false or unknown leg closes the gate.
4. The permit is scoped and one-shot: account, server, symbol (EUR/USD), entry type, agent, assignment,
   StrategyArtifact hash, max requested risk fraction, validity <= 24 h. The run also pins the one capsule it
   just sealed (any other capsule is refused before the permit is read), and the permit is consumed
   atomically, committed *together with* `SUBMISSION_STARTED`, before `order_send` is called.
5. A crash after `SUBMISSION_STARTED` is resolved by read-only recovery that never resubmits.

## 5. What an operator must do (nothing happens by default)

All four are explicit, separate acts; none is implied by another:

1. Issue a one-shot permit: `scripts/issue_canary_permit.py` with login, server, agent, assignment, artifact
   hash, max risk fraction, validity window, operator identity and reason.
2. Run `agent_canary_execution.py` with **both** `--apply-canary-config` (opens `submission_enabled`,
   `feedback_2_0_approved`, the approved account ref and risk-config version for that process only) **and**
   `--canary-permit-id <uuid>`. `flatten_submission_enabled` stays closed.
3. Terminal AlgoTrading must already be enabled (it currently reports trade allowed; nothing is toggled to pass).
4. This session's tool permission layer denied the earlier PAPER_LITE start that used
   `--confirm-paper-incident-clear`; a permit-armed broker run should be expected to need the same explicit,
   in-session owner authorization and must not be worked around.

## 6. Consequence for using the first genuine proposal as the canary

- **Without a permit, a real proposal cannot be traded afterwards.** In `agent_canary_execution.py`'s
  preflight-only mode there is no activation watermark, so eligibility returns `DECISION_PREDATES_EXECUTION_ACTIVATION`; the orchestrator
  has by then claimed the request (`execution_requests` is write-once per intent), so a later pass never
  re-attempts it. The preflight driver therefore *consumes* a genuine proposal for execution purposes.
- **A permit issued after the decision does not help either:** the watermark is the permit's issue time and
  eligibility requires `capsule.occurred_at >= watermark`. The permit must exist *before* the decision.
- So a real proposal can become the first canary only if the permit is issued and the run is armed
  (`--apply-canary-config --canary-permit-id`) **before** the window; in that state the run submits
  automatically if every gate passes. "Stop and report before broker execution" and "trade that same
  proposal" cannot both hold; the human gate is the permit itself (scope, risk fraction, short validity).

## 7. The canary window driver (`scripts/canary_window_driver.py`, built, NOT armed)

Decision logic: `src/crumblr/application/canary_window.py` (unit-tested with fakes). The script only supplies
real I/O. It never issues, edits or consumes a permit (it only reads `permit_for` / `consumption_for`); the
permit is consumed atomically by the orchestrator inside `agent_canary_execution.py`, the only code that can
reach `order_send`.

**It refuses to start (exit 2) unless all of these hold:** the permit exists, is unconsumed and unexpired;
it equals what the operator restated on the command line (account fingerprint, server, agent, assignment,
artifact hash, entry type, max risk fraction, all exact; a looser restatement is refused, not accepted);
it was issued at most 60 min before the window start and not after it, and is valid through the window end
but at most 10 min beyond it; the window is at most 60 min and not already over; `config/agent_canary_demo.yaml`
contains exactly the four approved lines (no flatten flag, no other key) with the same account fingerprint and
the current risk-config version; the latest Reader broker snapshot (<= 150 s old) shows the same fingerprint and
server; and the running Static Agent reports the same artifact hash. It also refuses a dirty tracked tree, so the
`code_commit` recorded in the capsule is true.

**While running:** one cycle per newly closed M5 bar, only for bars whose decision falls inside the window (a bar
closing exactly at the window end is excluded), a bar noticed more than 90 s late is skipped, never twice for the
same bar. Before every cycle it re-reads the permit (consumed or expired stops it) and re-checks Reader heartbeat
<= 30 s, tick <= 30 s, broker snapshot <= 150 s with COMPLETE position and pending-order sets, dashboard CONNECTED /
HEALTHY, Agent READY with the exact artifact; anything stale or unreadable stops it (exit 4).

**It stops (never retries):** immediately after the first submission attempt, accepted or rejected (permit consumed
or `SUBMISSION_STARTED` recorded; exit 10); after any proposal that was not submitted, for human review (exit 3);
after a crashed cycle, which is inspected once first (exit 4 or 10).

**Evidence:** `var/canary_evidence/<utc>_<permit8>/window_log.jsonl` plus one raw log per cycle: freshness readings,
outcome and reason codes, Risk and Policy verdicts, every execution event with payload (the broker result follows
`SUBMISSION_STARTED`), and the permit consumption. `--check-only` evaluates the start conditions and exits without
running a cycle.

### Operator-chosen permit fields (nothing is defaulted)

| Field | First-canary intent |
|---|---|
| `--login`, `--server` | the real DEMO login, `PepperstoneUK-Demo` (becomes fingerprint `4f857e6a72f9ad30`) |
| `--agent-id` / `--assignment-id` / `--strategy-artifact-hash` | `760e93be-117c-48a3-b997-f258055ec29b` / `f98c0396-dd13-4a99-b1e9-b83ea0f15ed7` / `81894d6a9c44ddb0433c15f9779fb0a2e25c0e1eccee232e2fd145d72cb498c5` |
| `--entry-type` | one only. The Static Agent proposes `LIMIT`, so a `MARKET` permit would never match |
| `--max-requested-risk-fraction` | operator's choice; never defaulted. Must equal the driver's `--max-requested-risk-fraction` |
| `--valid-for-minutes` | from issue time to at most 10 min past the window end |
| `--issued-by`, `--reason`, optional `--permit-id` | operator identity, why, and an id chosen in advance |

### Commands (not run)

    # 1. operator issues the one-shot permit shortly before the window (CRUMBLR_DATABASE_URL = crumblr_soak)
    uv run python scripts/issue_canary_permit.py --login <login> --server PepperstoneUK-Demo       --agent-id 760e93be-117c-48a3-b997-f258055ec29b --assignment-id f98c0396-dd13-4a99-b1e9-b83ea0f15ed7       --strategy-artifact-hash 81894d6a9c44ddb0433c15f9779fb0a2e25c0e1eccee232e2fd145d72cb498c5       --entry-type LIMIT --max-requested-risk-fraction <chosen> --valid-for-minutes <N>       --issued-by "<operator>" --reason "<why>" --permit-id <uuid>

    # 2. dry check (read-only): prints the refusals, runs nothing
    uv run python scripts/canary_window_driver.py --check-only <flags of step 3>

    # 3. the armed run
    uv run python scripts/canary_window_driver.py --canary-permit-id <uuid> --apply-canary-config       --agent-id 760e93be-117c-48a3-b997-f258055ec29b --assignment-id f98c0396-dd13-4a99-b1e9-b83ea0f15ed7       --strategy-artifact-hash 81894d6a9c44ddb0433c15f9779fb0a2e25c0e1eccee232e2fd145d72cb498c5       --expected-account-ref 4f857e6a72f9ad30 --expected-server PepperstoneUK-Demo       --entry-type LIMIT --max-requested-risk-fraction <chosen>       --window-start <UTC ISO> --window-end <UTC ISO>
