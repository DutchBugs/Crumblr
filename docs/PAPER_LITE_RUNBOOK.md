# Crumblr PAPER_LITE runbook

## Status and safety boundary

PAPER_LITE consumes the existing real, read-only Pepperstone EUR/USD feed and
creates positions only in `SimulatedBroker`. The runner has no real MT5
execution-adapter dependency. All real-submission configuration flags are
validated `false` at startup.

This is currently a plumbing/integration milestone, not final PAPER_LITE
acceptance. The exact open-risk and new Friday/weekend Core seams are still in
flight on Dev 1, and the genuine HEALTHY Pivot-2.2 runtime remains Dev-2/external
Agent work. See `review/PAPER_LITE_DEV3_WORKLOG.md` PL-001 through PL-004.

## Prerequisites

- Use branch `lite/paper-orchestrator` in `.claude/worktrees/paper-lite`.
- PostgreSQL database `crumblr_test_dev3` exists and is reachable from both the
  Windows MT5 reader and the PAPER_LITE process.
- Apply migrations to that database before starting any process.
- A Windows host has the existing Pepperstone DEMO terminal and the normal MT5
  credentials available through the existing secret environment variables.
- Never set `CRUMBLR_ALLOW_LIVE`, never create `config/live.yaml`, and do not
  enable any Crumblr real-submission field.

Set the dedicated database explicitly in every shell:

```bash
export CRUMBLR_DATABASE_URL='postgresql+psycopg://USER:PASSWORD@HOST:55432/crumblr_test_dev3'
uv run alembic upgrade head
```

The URL is an example shape only. Keep the real value in the operator's secret
store or shell environment; never commit it.

## 1. Start the read-only Pepperstone feed

Run this on the Windows MT5 host from the same code revision, pointing it at
`crumblr_test_dev3`:

```bash
uv run python scripts/mt5_live_reader.py \
  --environment PAPER \
  --canonical-symbol EUR/USD \
  --timeframe M5 \
  --json var/paper_lite_reader_health.json
```

This existing process owns MT5 access. Its gateway is read-only and persists
ticks, confirmed bars and instrument specifications to PostgreSQL. PAPER_LITE
reads those records and never receives MT5 credentials.

Confirm its health JSON says `HEALTHY`, the latest bar advances, and the latest
instrument spec matches the approved pin in `config/paper.yaml`. PAPER_LITE
will refuse startup or stop if the spec is missing or its semantic version
changes.

## 2. Provision the first toy/external Agent contract

Choose stable UUIDs for the Agent, assignment and toy strategy artifact. Put the
Gateway credential in an environment variable, not an argument:

```bash
export CRUMBLR_PAPER_LITE_GATEWAY_CREDENTIAL='from-your-secret-store'

uv run python scripts/setup_paper_lite_agent.py \
  --agent-id AGENT_UUID \
  --assignment-id ASSIGNMENT_UUID \
  --strategy-artifact-id ARTIFACT_UUID \
  --strategy-artifact-hash toy-orbital-v1 \
  --valid-from 2026-09-02T00:00:00+00:00 \
  --valid-until 2026-10-02T00:00:00+00:00 \
  --symbol EUR/USD \
  --timeframe M5
```

The assignment is immutable. Reusing its UUID with different content fails as a
conflict instead of overwriting prior authority.

## 3. Start the local toy Agent

The toy Agent is an external HTTP process using the exact neutral Crumblr input
and proposal/no-trade contracts. It is not Pivot-2.2 and must never be reported
as the final Static Agent acceptance test.

```bash
export CRUMBLR_PAPER_LITE_TOY_TOKEN='another-secret-from-your-store'

uv run python scripts/paper_lite_toy_agent.py \
  --agent-id AGENT_UUID \
  --mode NO_TRADE \
  --host 127.0.0.1 \
  --port 8788
```

Use `--mode BUY` or `--mode SELL` only for a deliberate directional paper test.
The toy Agent returns its own deliberately different strategy-local reason
codes. Crumblr treats them as opaque evidence rather than a global strategy
vocabulary.

## 4. Start PAPER_LITE

Use the same secrets in the PAPER_LITE shell, under their separate purposes:

```bash
export CRUMBLR_PAPER_LITE_GATEWAY_CREDENTIAL='from-your-secret-store'
export CRUMBLR_PAPER_LITE_AGENT_TOKEN="$CRUMBLR_PAPER_LITE_TOY_TOKEN"

uv run python scripts/paper_lite.py \
  --agent-id AGENT_UUID \
  --assignment-id ASSIGNMENT_UUID \
  --agent-url http://127.0.0.1:8788 \
  --code-commit COMMIT_SHA \
  --confirm-paper-incident-clear \
  --initialize-paper-safety \
  --operator YOUR_OPERATOR_ID \
  --incident-note 'initial PAPER_LITE safety-state activation'
```

`--initialize-paper-safety` is an explicit, audited operator action for a new
Dev-3 database/latch. Do not use it on ordinary restarts. Without it, startup
requires both the database safety record and local paper latch to already say
`RUNNING`. A HALT is never reset automatically.

`--confirm-paper-incident-clear` is also deliberate. The current repository has
no durable incident register to query, so platform Policy receives `UNKNOWN`
and vetoes by default. This flag is the operator's explicit assertion that the
limited PAPER_LITE integration scope has no active incident; it does not disable
Policy and it cannot reset HALT.

For an ordinary restart, omit the three initialization/operator arguments:

```bash
uv run python scripts/paper_lite.py \
  --agent-id AGENT_UUID \
  --assignment-id ASSIGNMENT_UUID \
  --agent-url http://127.0.0.1:8788 \
  --code-commit COMMIT_SHA \
  --confirm-paper-incident-clear
```

`config/paper_lite.yaml` contains the explicit simulated starting balance and
Owner Risk Policy v1 values. Changing the starting balance against an existing
journal fails closed; start a consciously separate journal for a separate
portfolio instead of silently rewriting history.

## Evidence and interpretation

The runner emits compact JSON lines with paper balance/equity, open/closed
position counts and the outcome stage. The paper read model also exposes exact
current stop-risk amount/fraction separately from the originally authorized
risk amount. Detailed evidence lives in:

- PostgreSQL: real market observations, Agent identity/assignment/context and
  outcomes, feature evidence, decision capsules and Core audit events;
- `var/paper_lite.journal.jsonl`: hash-chained paper observations, accepted
  paper order instructions, Friday flatten requests and explicit audit facts;
- `var/paper_lite.safety.json`: the independent PAPER_LITE safety latch.

The existing Core risk-session record is updated after paper state transitions,
so 4% daily loss and 8% high-water-mark drawdown do not reset on restart. If a
journal contains exposure while the durable risk-session record is absent or
unreadable, Lite trips HALT as `SAFETY_STATE_UNKNOWN`; only an operator can
resolve/reset that state.

PAPER_LITE also halts when a recovered session's persisted worst daily loss or
drawdown has already reached the configured limit. This temporary conservative
guard is tracked as PL-006 until the shared Core evaluation consumes its own
recovered maxima directly.

Expected audit fact on a paper fill:

```text
SUPERVISOR_SKIPPED_PAPER_MODE
```

This means only the external Supervisor was omitted. A fill is reachable only
after AgentGateway acceptance, Core Risk `PASS` and strategy-neutral platform
Policy `APPROVE`. No fake `SupervisorReview(APPROVE)` is created.

Paper fills approximate entry/stop execution with the existing simulator's
spread/slippage rules. They are useful product evidence and are not evidence of
Pepperstone execution quality.

## Known fail-closed behavior pending shared seams

- A first directional paper fill can complete when the paper book is flat.
- A further directional proposal while a paper position is open returns
  `EXACT_OPEN_RISK_UNAVAILABLE`; it does not use the forbidden
  `position_count × 2%` approximation.
- The Friday-only application guard blocks entries from T-15m and flattens the
  paper book from T-5m using Core's New York market clock. This remains a
  temporary Lite guard until Dev 1 replaces Core's obsolete daily-flatten
  policy.
- Claiming a decision window is at-most-once and durable. A crash after the
  claim but before the Agent answer intentionally loses that one window rather
  than risking a duplicate decision; the event remains visible for review.
- Genuine HEALTHY Static Agent evidence remains outstanding. The toy Agent is
  only the approved first plumbing milestone.
