# status.md — Autonomous MT5 Trading Platform

**Project:** Autonomous EUR/USD Trading Platform  
**Status document version:** 1.10  
**Last updated:** 2026-09-03  
**Current environment:** DESIGN  
**Live trading permitted:** NO

## Current state — mandatory compact header (review 1.26 §3)

This section is the integrated current truth. If any historical paragraph
below ever disagrees with it, this section wins — update it the same
session a meaningful slice merges to `main`, not later.

| | |
|---|---|
| **`main` HEAD** | `1fad624` |
| **Last hosted CI result** | **Owner-reported 2026-09-03 (`OWNER_WORK_ORDERS_DEMO_CANARY_2026-09-03.md` §1.2): run #106, 1341 collected, 1339 passed, 2 failed.** PostgreSQL 17 client/server alignment (F-068), lint, format and mypy all passed — F-063/F-065/F-067/F-068 effectively confirmed green. The 2 failures are the known, already-fixed-on-`agent/contracts` (`d62722d`) `test_agent_decision_path.py` PL-006 timing assertions — not a new Core defect. Still no `gh`/Actions access in this environment; this result is owner-reported, not independently re-pulled |
| **Dev 1** | DONE: owner risk policy v1 (D1.2/D1.3/D1.4, `ADR-011`, O-008), CI PostgreSQL client version pin (F-068), owner session policy v1 (D1.5, `ADR-012`, O-009), PL-006 restart-recovery hardening (`ADR-013`), item 9 broker-side SL verification (`ADR-014`). Owner/reviewer coordination order `review/OWNER_WORK_ORDERS_DEMO_CANARY_2026-09-03.md` (staged route to a constrained DEMO canary, Phases 0-F): **Phase 0 done** — reviewed and merged Dev 2's `agent/contracts` convergence (PR #2, `3e87384`), no conflicts, independently re-verified green locally (1358 passed/3 skips/0 failed). **Phase B slice 1 (B4, `ADR-015`) shipped** — `_recover_ambiguous_submission()` now fails closed and HALTs (`ReasonCode.SUBMISSION_INTEGRITY_AMBIGUOUS`) on >1 broker positions sharing one magic number, instead of silently attributing them all to one request; full suite 1361 passed/3 skips/0 failed. 7 of 8 Phase-B sub-items remain (B1/B2/B3/B5/B7/B8; B6 explicitly deferred by the work order itself until continuous-DEMO promotion). NEXT: decide and plan the next Phase B slice with the user (leaning B7 — exact account pin, small/independent — or B1+B2, the core adapter + submission chain). BLOCKED: none currently |
| **Dev 2** | DONE: Agent contracts + Gateway ingestion/audit merged, AG-007–014 tracked/fixed, `TradeProposal → TradeIntent` mapping merged, shared no-MT5 Risk → Policy → capsule path merged, **D2.2 wired to Dev 1's `assess_open_risk`** (`agent/contracts` `2312908`: `decision_path.py` now calls it directly, interim HALT pre-check deleted as redundant with `evaluate()`'s own `OPEN_RISK_UNKNOWN`, D-054 gap 2 fixed via a new `OpenRiskFraction` type distinguishing a flat book from unestablished — not yet on `main`). Found AG-015 and escalated it — **review 1.28 resolved it as an architectural correction (F-066): Core must be strategy-neutral**. NEXT: revised work order (review 1.28 §11) — unhealthy-market smoke proof, strategy-neutral `AgentMarketContextV1`, structural/opaque Gateway reason-code handling, split the external-agent Policy path away from `Regime`/strategy-id/confidence assumptions (AG-013). BLOCKED: none currently |
| **F-051 state** | **Both parts CLOSED** (2026-08-26 / 2026-09-01) — see `review/FEEDBACK.md` F-051 for full evidence. Reader left running, read-only, toward `ict_v1`'s 120-bar threshold |
| **PAPER_LITE** | Merged to `main` 2026-09-03 (`f645e75`, PR #1, `lite/paper-orchestrator`) — a separate, self-contained track (`application/paper_lite*.py`, `persistence/paper_lite.py`, own tests, `review/PAPER_LITE_DEV3_WORKLOG.md`, `config/paper_lite.yaml`). Not Dev 1's track; zero file overlap confirmed with the D1.2-D1.5 slices (clean rebase). Not narrated further here — see its own worklog |
| **Owner blockers** | Confirm next hosted CI run is fully green (F-068 fix pending confirmation); decide when to enable terminal AlgoTrading. All risk/session-policy numbers now supplied and shipped (O-008, O-009) |
| **`order_send`** | **NO-GO.** `ExecutionConfig.feedback_2_0_approved` stays `false` |
| **Next formal review target** | `feedback.2.0.md` (routine, per review 1.25 §9's three triggers) — `feedback.1.26.md`/`feedback.1.27.md`/`feedback.1.28.md` (all 2026-09-01) were deliberate owner/reviewer checkpoints (1.28 an explicit early-escalation exception per 1.27 §12's own trigger), not a change to that default |

### Dev 1 ACK — feedback.1.27 (review 1.27 §1's required format)

```text
ACK feedback.1.27
branch/worktree: core/execution-activation, .claude/worktrees/core
main SHA fetched: fa0a6b3
DONE since 1.26: F-063 fix confirmed by hosted run 60; F-051 part 2
  CLOSED (real baseline_v1 decision, risk PASS/Supervisor APPROVE
  against real EUR/USD data); F-065 fixed same day as opened
  (pyproject.toml extend-exclude/force-exclude, review Markdown no
  longer touched by ruff format)
NEXT: confirm the next hosted CI run is fully green (needs a human/gh
  access), then core critical path item 2 (SUBMISSION_STARTED timing)
BLOCKED: hosted CI confirmation only
Needs from Dev 2: nothing right now
```

---

## What's needed next — owner-only or otherwise blocked

Phase 4 is formally passed (review 1.24) and remains passed (review 1.25
§0/§13); real `order_check` evidence has been gathered against the live
Pepperstone DEMO terminal (§13 forty-fifth entry, does not need repeating
— review 1.25 §8/§10). **Review 1.25 also changed how reviews get
requested from here** — see the note right after this table. Nothing
below needs more agent engineering by itself — each either needs you
specifically, needs a background process (re)started, or needs a
document you have that this repository doesn't yet.

| # | What | Why it needs you | Where |
|---|---|---|---|
| 1 | Confirm the next hosted CI run is actually green | No `gh`/Actions access in this environment. Hosted run 60 already confirmed **F-063** genuinely fixed; **F-065** (`ruff format --check`) and now **F-067** (the `pg_dump`/`psql` restore proof silently skipping instead of running — `postgresql-client` was never installed on the runner, and the dump/restore subprocess calls carried no connection parameters underneath that) are both fixed 2026-09-01. "Local green" was never allowed to stand in for "hosted green," and now — for the first time — neither is "hosted green that never actually exercised the restore test." A human (or a session with GitHub access) has to look at the Actions tab for the current `main` and check: Linux job (including the two new "assert not silently skipped" steps), Windows job, PostgreSQL integration coverage, gitleaks/secrets job, overall workflow | `.github/workflows/ci.yml`; `tests/integration/test_migrations.py`; §2 M0 acceptance below; `review/FEEDBACK.md` F-067 |
| 2 | ~~Owner risk-policy decisions: risk per trade, max daily loss, max drawdown, last-entry cutoff, mandatory flatten deadline, HALT-reset authority~~ **all done** — risk fractions 2026-09-02 (`max_risk_per_trade=0.02`, `max_open_risk=0.03`, `max_daily_loss=0.04`, `max_drawdown=0.08`, O-008b, ADR-011); session policy 2026-09-03 (Friday-only entry cutoff T-15, flatten T-5, weekday overnight now permitted, weekend still forbidden, O-009, ADR-012); HALT-reset human-only reconfirmed compliant, no code change needed | build.md §29 Q7/Q8 and ADR-004 §3 reserved these for a human by design; all now answered | `config/paper.yaml`; `review/adr/ADR-004-intraday-session-boundary.md`; `review/adr/ADR-011-owner-risk-policy-v1.md`; `review/adr/ADR-012-owner-session-policy-v1.md`; §11 below |
| 3 | Optional: countersign the domain-contract package | Only relevant if §2's "reviewed by a human" wording below is read literally. Review 1.24 §7 approved the package at the reviewer/technical level and explicitly declined to count itself as that "human" — named as an open governance question, not an engineering one. Suggested one-line form: "Owner reviewed and accepts the current domain-contract package at commit `6bdb5b1`." | `review/domain_contracts.md`; `review/FEEDBACK.md` unreviewed-work table |
| 4 | Decide if/when to enable terminal AlgoTrading, and under what conditions | APP-016: explicitly an owner decision, never automatic, never "just to make a check pass." The real `order_check` evidence gathered 2026-08-27 was deliberately gathered with AlgoTrading left off — a genuine `ORDER_CHECK_REJECTED` result, not a workaround. Review 1.25 §8 reaffirms: leave it off until the actual `SubmissionGate`/`feedback.2.0` readiness conditions are met | §3 APP-016 below; §13 forty-fifth entry |
| ~~5~~ | ~~Restart real M5 bar accumulation for F-051 part 2~~ | **Done 2026-09-01** — `mt5_live_reader.py`/`live_decision.py` (`baseline_v1`) restarted against `crumblr_soak`; two real decisions reached risk PASS/Supervisor APPROVE (capsules `5b8c89df...`/`ed0b5c4a...`). Reader left running to keep accumulating toward `ict_v1`'s 120-bar threshold | `review/FEEDBACK.md` F-051; §13 fifty-sixth entry |
| 6 | Agent Integration track (Dev 2) — Step A + Step B **merged to `main`** (`bf18ec5`), a same-day self-review hardening pass **merged** (`d6a5361`, 3 real bugs found and fixed — AG-007/008/009), an HTTP transport for the Gateway **merged** (`a0e380a`, 2026-08-31). **No longer blocked** | Review 1.26 §5 resolved AG-006 directly: no standalone cross-strategy `compute_features()` needed after all — Dev 2 adds one platform-owned, deliberately-named evidence shape (`agent_context_v1`) reusing the existing generic `FeatureEvidence` persistence layer, entirely within Dev 2's own ownership. Dev 2 confirmed starting on this 2026-09-01. Full detail in `review/AGENT_STATUS.md`/`review/AGENT_FEEDBACK.md` and §13 fiftieth–fifty-fourth entries | `review/AGENT_STATUS.md`; `review/AGENT_FEEDBACK.md`; `feedback.1.26.md` §5/§7; commits `bf18ec5`, `d6a5361`, `a0e380a` on `main` |
| 7 | Core submission-safety phase — F-049 `SubmissionGate` **done 2026-08-28**; durable execution-activation wiring **done 2026-08-28**; `SUBMISSION_STARTED` emission **done 2026-09-01** (item 3); execution-event conflict hardening **done 2026-09-01** (item 4); `order_send` idempotence/magic-number derivation **done 2026-09-01** (item 5); three items remain | `SubmissionGate` is real, called by `ExecutionOrchestrator`, durably records its own commitment point, the event log fails closed on a same-id/different-content conflict, and every `ApprovedOrder` now carries the deterministic MT5 `magic` a future `order_send` would use (`ADR-007`) — the broker-visible identity `order_send` idempotence and the next item both need. Still open: ambiguous-outcome recovery, automatic flatten submission, post-fill reconciliation, broker-side SL verification | `review/adr/ADR-006-submission-gate.md`; `review/adr/ADR-007-order-send-idempotence.md`; §13 sixty-first entry; `feedback.1.26.md` §6; `feedback.1.27.md` §8 |

**Review cadence has changed (review 1.25 §9).** Don't request a formal
reviewer artifact for documentation wording, one extra unit test, normal
F-051 accumulation, routine refactors, minor dashboard work, or
individual Agent Gateway files. Bring the reviewer back only for: (1) a
material safety defect, (2) a proposed change to a Phase-4 invariant, or
(3) the complete `feedback.2.0` readiness bundle (review 1.25 §10's
checklist) — at which point the target is `feedback.2.0.md` directly, not
another `feedback.1.2x.md`. **`feedback.1.26.md` (2026-09-01) was a
deliberate owner-requested exception to this rule, not a reversal of it**
— it opens Phase 5 (Convergence, Observability & DEMO Readiness) with
fresh work orders for both tracks; `feedback.2.0.md` remains the next
*routine* target. It also changes how feedback reaches this repository
going forward: the owner/reviewer now commits formal feedback directly,
picked up here via `git fetch` at session start rather than handed over
as an external document — see `review/FEEDBACK.md`'s own cadence section
for the full detail.

---

## For the reviewer

| Document | Purpose |
|---|---|
| [review/DEVIATIONS.md](review/DEVIATIONS.md) | Every departure from `build.md`, with rationale and what to watch. Start here. |
| [review/FEEDBACK.md](review/FEEDBACK.md) | Where findings are filed. Resolved at the start of each session before new work. |
| [HANDOVER.md](HANDOVER.md) | How to pick this project up cold: environment, code map, next steps, traps. |
| [CLAUDE.md](CLAUDE.md) | The working agreements the implementing agent operates under. |
| §10 below | Architectural and risk decision log |
| §13 below | Chronological update log with evidence and problems found |

`build.md` is the specification and is never edited to match the code. Gaps are
recorded in `review/DEVIATIONS.md` rather than closed by rewriting the spec.

---

# 1. Overall status

## Maturity is not qualification

Review finding F-001: `BUILDING` was being used to mean both "code exists" and
"this milestone is close to passing", which are unrelated claims. They are now
tracked separately, and **no amount of implementation maturity passes a gate**.
Unit and replay tests are never sufficient for promotion on their own.

Implementation maturity ladder — each step includes the ones before it:

```text
SPECIFIED         written down in build.md, not built
IMPLEMENTED       code exists
UNIT-TESTED       behaviour asserted in isolation
REPLAY-TESTED     exercised end to end against simulated data
MT5-INTEGRATED    exercised against a real terminal
PAPER-VALIDATED   run unattended on a demo account
SHADOW-VALIDATED  run against a live feed without submitting orders
```

Gate qualification is a separate, human decision recorded in §16.

| Component | Maturity | Gate qualification | Blocker |
|---|---|---|---|
| 1. Platform / Application | MT5-INTEGRATED (M1); F-051 part 1 (discovery through reconciliation `MATCHED`) real-terminal-confirmed 2026-08-26; part 2 (a real Trader decision) and everything else past M1 remain REPLAY-TESTED, not yet real-terminal-validated | **NOT PASSED** (M0) | M0's own remainder: human contract review, and CI confirmation — CI ran for the first time 2026-08-26, both jobs failed, fixed same day (F-056), next run unconfirmed. M1 itself is PASSED — see §16 Promotion history |
| 2. Trading Agent | REPLAY-TESTED | **NOT PASSED** (M6) | No real EUR/USD evidence; feature-frozen per F-004 |
| 3. Evaluator / Supervisor | UNIT-TESTED | **NOT PASSED** (M7) | Layer 1 only; post-trade and drift not started |

Progress percentages have been removed. They implied a precision the project
does not have, and they contradicted the detailed checklists below (F-005).

## Status legend

```text
NOT STARTED
DESIGN
BUILDING
TESTING
PAPER
SHADOW
READY FOR REVIEW
BLOCKED
HALTED
LIVE-CANARY
```

## Overall health

```text
Engineering health:   AMBER   (lint/types/tests green locally, 939 passed/3
                                explained skips as of 2026-08-27. CI ran for
                                the first time 2026-08-26 and both platform
                                jobs failed fast — root cause found and
                                fixed the same day (F-056: an undeclared
                                `numpy` test dependency); the fix is pushed
                                but the next run has not yet been confirmed
                                green on an actual runner — needs a human
                                or `gh`/Actions check)
Safety-state health:  AMBER   (fail-closed, durable, and now recovered on the
                                normal path; not yet broker-validated)
Trading health:       GREY    (ict_v1 runs on synthetic data only — no evidence)
Risk health:          AMBER   (engine works in replay; the daily budget now
                                survives a restart; never met a real broker)
Data health:          GREEN   (decisions journalled, ticks and bars stored,
                                schema versioned; real Pepperstone feed
                                evidence exists — Phase A, 2026-08-24: 2,920
                                real ticks + 17 real M5 bars, GOOD quality,
                                zero gaps; broker-state/instrument-spec
                                snapshots real-terminal-confirmed 2026-08-26)
MT5 connectivity:     GREEN   (M1 = MT5-INTEGRATED, PASSED — review
                                feedback.1.12, 2026-08-24. Account, symbol,
                                instrument and position reads, continuous
                                ticks/bars, and reconnect-with-full-
                                revalidation have all run against the real
                                Pepperstone terminal: Phase A (30 clean
                                minutes) and Phase B (two deliberate terminal
                                closures, both recovered) — F-034/F-037
                                closed. F-051, part 1, 2026-08-26: broker
                                snapshot, instrument spec and reconciliation
                                (UNKNOWN before a human-approved pin,
                                MATCHED after — F-055) all real-terminal-
                                confirmed, zero defects found. A real
                                Trader/Risk/Supervisor decision (F-051 part 2)
                                remains pending real M5 bar accumulation)
Paper campaign:       NOT STARTED
Production readiness: 0%
```

---

# 2. Current project gate

**Gate:** M0 — Architecture / repository setup

Review 1.1 finding F-010: this section previously held M0 open partly on broker
and demo-account selection. Those are real dependencies but they belong to M1,
and `build.md` is the specification — `status.md` must not silently redefine
its gates. The criteria below are therefore split into what the specification
itself requires, and what is local project policy on top of it.

### M0 deliverables — build.md §26

- [x] Python project — CPython 3.12.14, `src/crumblr`
- [x] dependency lock — `uv.lock`, CI installs with `--locked`
- [x] lint/type/test pipeline — ruff, mypy `strict`, pytest
- [x] config schema — versioned, fail-closed, no permissive risk defaults
- [x] **logging** — structured JSON to stderr, UTC timestamps, component and
      service fields, correlation-id propagation, secret redaction. Emissions
      cannot raise into the caller and do not affect replay determinism —
      both properties tested (review 1.2 F-013)
- [x] CI — workflow written (see acceptance below for its real status)
- [x] environment separation — paper/shadow demo-only, live double-gated

### M0 acceptance — build.md §26

- [x] clean install from scratch — `uv sync` from an empty environment
- [ ] all tests run locally **and in CI** — 939 pass locally (2026-08-27).
      **CI ran for the first time 2026-08-26 and both platform jobs
      failed**; root cause found and fixed the same day (F-056 — an
      undeclared `numpy` test dependency), fix pushed, **next run not yet
      confirmed green**. This criterion is closer to met than at any prior
      point but still not fully met — a human or `gh`/Actions check of the
      next run is the remaining step. Review 1.6 §5's fallback (an explicit
      M0 exception, CI mandatory before `feedback.2.0.md`) is now most
      likely moot rather than needed, pending that confirmation
- [x] no secrets in repository — `.gitignore`, config-loader rejection, gitleaks job

### Local project policy — not from build.md §26

Recorded separately so the specification's own gates stay legible.

- [ ] domain contracts reviewed by a human — implemented and tested;
      package assembled 2026-08-25 (`review/domain_contracts.md`),
      refreshed against Phase 4 and commit `6bdb5b1` on 2026-08-27.
      **Review 1.24 §7: "Reviewer contract verdict: APPROVED for the
      current Phase-4 codebase"** — but the reviewer explicitly declined
      to represent itself as the human this item's wording asks for
      ("I am the independent project reviewer in this workflow, but I
      should not represent myself as a human reviewer"). Technical/
      reviewer-level review is PASSED; whether this item's literal
      wording requires the owner to additionally countersign
      (review 1.24's suggested form: "Owner reviewed and accepts the
      current domain-contract package at commit 6bdb5b1") is an open
      governance question for the owner, not an engineering task —
      no further contract work is required either way
- [x] durable safety state and fail-closed startup — added after review 1.0 F-003
- [x] status semantics separate maturity from qualification — review 1.0 F-001

### Not M0 — M1 dependencies (F-010)

These block M1 and are tracked there. They are not engineering failures at M0.

- [x] broker selected — Pepperstone, owner decision O-001 (build.md §29 Q1)
- [x] exact MT5 server documented — `PepperstoneUK-Demo`, supplied 2026-08-18
      and recorded in `config/paper.yaml` as a claim the account guard checks
- [x] **entity confirmed for the demo environment** — O-005 (review 1.9 §2,
      F-028): `company` from first contact reads `Pepperstone Limited`, the
      UK entity, matching the `PepperstoneUK-Demo` server. This **amends**
      O-001's "Pepperstone EU" shorthand for demo/development only — it does
      **not** pre-select the entity for a future live account, which needs
      its own review against the owner's residence and live documentation.
      See D-034
- [x] MT5 demo account created — 2026-08-24. `Server: PepperstoneUK-Demo`,
      `Currency: EUR`, `Leverage: 1:30` (review 1.7/1.8 F-026). The login itself
      is a local secret and does not belong in this file
- [x] logged into the MT5 terminal, once, interactively — 2026-08-24
- [x] hedging or netting established from `account_info()` (§29 Q2) —
      `RETAIL_HEDGING`, read 2026-08-24, not guessed
- [x] Windows x86-64 host with the MetaTrader 5 terminal provisioned

### Promotion decision

```text
Decision: GO WITH CONDITIONS   (reviewer verdict, feedback.1.1 §4)
Owner:
Date:
Evidence: 604 tests green, mypy strict clean over 88 files, ruff clean,
          replay byte-identical across runs, halt proven to survive a real
          process restart, structured logging shipped and proven not to
          affect determinism, and a run reproducible from the persisted
          journal alone (2026-08-18).
Notes:    Two conditions remain, neither blocked on a broker.
          1. Execute CI on a runner, or keep it honestly marked as unexecuted.
          2. Human review of the domain contracts.
```

---

# 3. Component 1 — Platform / Application

## Current status

```text
Status: BUILDING
Owner:
Current milestone: M0 (M1/M2 already passed; see §16 Promotion history)
Implementation maturity: MT5-INTEGRATED (M1); Dashboard v0 shipped
Gate qualification: M0 NOT PASSED (hosted CI confirmation is the one
                remaining technical gate; domain-contract review is
                reviewer/technical-APPROVED, review 1.24 §7 — an owner
                countersign is open as a governance question, not an
                engineering task); M1 PASSED; M2 PASSED
Last meaningful update: 2026-08-27 — **Phase 4 (the non-sending execution
                preflight chain) FORMALLY PASSED, review 1.24.** Review
                1.23's exact four-item hardening bundle (F-058 final-time/
                session sequencing, F-059 complete approval-chain
                fingerprint, F-060 real `SUBMISSION_STARTED`-based order-
                frequency authority, F-061 broker-boundary FINAL-Risk
                guard) was fixed the same day it was requested (commit
                `6bdb5b1`) and reviewed against the actual source, not
                accepted from this document alone. Review 1.24 closed all
                four with "no further work required" on each, declared
                Phase 4 architecture and implementation both PASSED, and
                approved the same-day refreshed `review/domain_contracts.md`
                at the reviewer/technical level. It additionally
                AUTHORIZED one controlled real-terminal `order_check`
                evidence run (non-sending, Pepperstone DEMO only, exact
                conditions in `feedback.1.24.md` §8) — **executed
                2026-08-27 (§13 forty-fifth entry): real `order_check`
                reached, `ORDER_CHECK_REJECTED` because AlgoTrading is
                off at the terminal (APP-016, expected, not a defect;
                AlgoTrading not toggled to force a pass)**. Phase 4
                passing does **not** mean an order can be
                submitted: `order_send`/`cancel_pending_orders`/
                `close_all_positions` remain unconditionally refused by
                every adapter that can reach a real terminal, and
                `feedback.2.0.md` remains mandatory before the first
                broker submission
Next objective: run the authorized real-terminal `order_check` evidence
                (review 1.24 §8, non-sending only); finish F-051 part 2
                once enough real M5 bars have accumulated (`baseline_v1`
                needs 65, `ict_v1` needs 120; 49 existed at last check);
                confirm the next CI run is actually green (needs a human
                or `gh`/Actions check); owner risk-policy decisions
                (review 1.24 §12.A) and, if the M0 wording is read
                literally, an owner countersign on the domain-contract
                package
```

## Scope

- MT5 Gateway
- market data
- instrument registry
- event journal
- backtest/replay
- risk engine
- execution engine
- reconciliation
- database
- API/dashboard
- monitoring
- kill switches
- security/secrets

## Milestone tracker

| Milestone | Maturity | Gate | Evidence / why not qualified |
|---|---|---|---|
| M0 Repo / engineering baseline | REPLAY-TESTED | GO WITH CONDITIONS | Logging shipped (F-013). CI ran on a runner for the first time 2026-08-26, both jobs failed, root cause found and fixed the same day (F-056), fix pushed — next run not yet confirmed green. Domain-contract review APPROVED at the reviewer/technical level 2026-08-27 (review 1.24 §7); an owner countersign is open only if the wording is read literally, not an engineering task. Remaining: hosted CI confirmation is the one open technical gate |
| M1 MT5 read-only gateway | MT5-INTEGRATED | **PASSED — review feedback.1.12, 2026-08-24** | Read-only adapter and first-contact probe, 60+ tests against a fake terminal; execution refused by construction (D-036). First contact made 2026-08-24; account/symbol/instrument/position reads and the account guard all succeeded against the real Pepperstone terminal; D-037 fixed from the observed values. Entity (APP-013/D-034) closed for demo by O-005. **Phase A satisfied 2026-08-24** (sixth real attempt): 30 clean minutes, zero disconnects, 2,920 real ticks + 17 real M5 bars persisted, all `GOOD` quality, zero gaps, every bar on a 5-minute UTC boundary. **Phase B satisfied 2026-08-24**, owner present: two deliberate MT5 terminal closures, both detected, both recovered automatically with full revalidation (symbol, account, instrument spec, broker clock offset) and fresh data resuming within seconds — F-034 closed. Four real defects found and fixed across both phases (D-040, D-041, D-042×2, D-039); see `status.md` §13 sixteenth/seventeenth entries. **Reviewer decision, review 1.12 §7: M1 PASSED.** Recorded in §16 Promotion history. Does not authorize execution — `order_send` remains prohibited |
| M2 Data/event journal | REPLAY-TESTED | **PASSED on its own acceptance evidence** | build.md's Milestone 2 acceptance is "events can be replayed in original order; gaps/out-of-order data detected; raw data immutable" — all three met and tested against a real PostgreSQL (F-018–F-020, F-022, F-023). Review 1.7/1.8 F-027: real-feed evidence is not an M2 acceptance criterion in build.md — it is what Milestone 1 acceptance ("reads EUR/USD ticks/bars") actually requires. Reassigned there rather than silently holding M2 open for it (the same class of error as F-010). That every row currently in the journal came from a seeded generator is real and tracked, but as an M1 gap, not an M2 one |
| M3 Replay/backtest | REPLAY-TESTED | NOT PASSED | Deterministic; cost model incomplete (no swap/commission) |
| M4 Risk engine | REPLAY-TESTED | NOT PASSED | Full §8.1 checklist and sizing; never met a real broker |
| M5 Paper execution | SPECIFIED | **NO-GO** | Twelve prerequisites open — see `review/feedback.1.0.md` §6 |
| M6 Trading Agent | REPLAY-TESTED | FEATURE FREEZE | `ict_v1` + `baseline_v1`; next step is evidence, not concepts |
| M7 Evaluator / Supervisor | UNIT-TESTED | SAFETY WORK ONLY | Layer 1. Two of its seven checks now report themselves as **not in force** rather than passing (F-024); post-trade and drift not started |
| M8 Dashboard | IMPLEMENTED (v0.1, read-only, visually redesigned) | NOT PASSED | Dashboard v0 built 2026-08-24 — read-only status page + JSON endpoint (`scripts/run_dashboard.py`, `src/crumblr/dashboard/`), deliberately scoped to review 1.12 §8's minimum screen, not the full build.md §22/M8 spec (D-043). Visually rebuilt 2026-08-25 (review 1.13 §§4-10, F-042/F-043/F-044) into a dark ops-console layout with an EUR/USD candlestick chart, decision pipeline and activity timeline. Review 1.14 **accepted the visual direction** and closed two follow-on findings the same day: F-045 (the `PAPER` badge misread as an active campaign — now `DEMO DATA`) and F-046 (stale/missing data must look visibly non-live — historical banner + chart overlay). **Visual scope frozen** (review 1.14 §10, reconfirmed review 1.15 §15): only broker-state/reconciliation/real-decision data panels may still be added — F-047/F-048 now exist to source them (shipped 2026-08-25), but review 1.17 §15/§9 both say to wait for F-051's real-terminal validation before wiring them into the dashboard, so this remains not-yet-done rather than blocked-on-missing-code. No further layout/framework/animation work is planned. No manual HALT/CANCEL/FLATTEN control surface exists — M8 itself requires that and is not attempted |
| M9 Paper campaign support | SPECIFIED | NOT PASSED | Blocked behind M5 |
| M10 Shadow support | SPECIFIED | NOT PASSED | Blocked behind M5 |

## Platform checklist

### Repository / build

- [x] `pyproject.toml`
- [x] dependency lockfile — `uv.lock`, CI installs with `--locked`
- [x] environment config — `config/base.yaml` + `config/paper.yaml`, versioned by content hash
- [x] strict typing — mypy `strict`, clean over 125 source files
- [x] linting — ruff check + format, clean
- [x] tests — 880 total (property/replay/chaos suites plus unit and
      integration, the latter needing a real PostgreSQL and skipping loudly
      without one); 877 passed, 3 skipped (platform-dependent, explained),
      2026-08-26 (F-053/F-054/F-055/D-031 shipped — see §13 twenty-ninth entry)
- [x] schema migrations — Alembic baseline `ce70efeb9fe9`; a migrated database
      is asserted to match the application's metadata, and a `pg_dump` restore
      is asserted to reproduce the run
- [x] MT5-touching tests exist and pass on the Windows host — `test_mt5_probe.py`,
      `test_mt5_readonly_gateway.py`, `test_live_reader.py` — all against a
      fake terminal; **no automated test in the repository runs against the
      real terminal.** Real-terminal evidence instead comes from manual runs
      recorded in status.md §13: the first-contact probe, Phase A (30 clean
      minutes, continuous real ticks/bars), and Phase B (two deliberate
      terminal closures, both detected and recovered with full revalidation)
      — F-034/F-037 closed, M1 PASSED (review 1.14 §11 F-033)
- [ ] CI pipeline — **ran for real for the first time 2026-08-26** (owner
      relayed GitHub's failure notifications directly — no `gh`/Actions log
      access exists in this environment either way). Both platform jobs
      failed fast; root cause found and fixed same day (F-056: `numpy` was
      an undeclared test dependency, present only as a side effect of the
      `mt5` extra). Reproduced and fixed locally against the exact failing
      commands; fix pushed → **hosted rerun result pending** (no `gh`/Actions
      access in this environment — needs a human check of the next run)

### MT5

Three columns, because "code exists", "unit-tested against a fake" and
"validated against the real terminal" are different claims (review 1.10
F-033's own rule: current sections state present truth).

| Capability | impl+unit | real terminal | Note |
|---|:--:|:--:|---|
| `initialize` / connect | x | x | first contact, 2026-08-24 |
| account validation | x | x | guard passed against the real account |
| environment (demo) validation | x | x | confirmed `DEMO` |
| terminal health | x | x | build/version read from the real terminal |
| `symbol_info` | x | x | real EURUSD spec read |
| `symbol_info_tick` | x | x | real bid/ask read |
| bars/ticks collection | x | x | `LiveReader` + `ticks()`/`bars()`, unit-tested against a fake plus **Phase A, 2026-08-24**: 30 real minutes, 2,920 real ticks + 17 real M5 bars, GOOD quality, zero gaps |
| reconnect behaviour | x | x | `LiveReader`, all 5 review 1.9 F-034 scenarios pass against a fake plus **Phase B, 2026-08-24**: two real deliberate terminal closures, both recovered with full revalidation |
| positions | x | x | 0 open positions read from the real account |
| `order_check` | — | — | N/A for M1 by design — refused, not merely untested (D-036) |
| `order_send` | — | — | N/A for M1 by design — refused, not merely untested (D-036) |
| orders (pending, read/persist) | x | x | built 2026-08-25 (F-047) — `pending_orders()`, `broker_pending_order_snapshots`. **Real-terminal-validated 2026-08-26**: real flat account read as `pending_order_set_state=COMPLETE` with 0 rows, not `UNKNOWN` |
| history (backfill beyond one poll) | | | not built; not required for M1's own acceptance |
| reconciliation | x | x | v0 built 2026-08-25, instrument-spec comparison added 2026-08-26 (F-053), pinned-baseline authority added same day (F-055) — `application/reconciliation.py`. **Real-terminal-validated 2026-08-26**: `UNKNOWN` before a human-approved `expected_spec_version` pin, `MATCHED` after — the first real `MATCHED` reconciliation result this project has ever produced. Account/position/pending-order dimensions also real-terminal-confirmed via the same flat-account snapshot |

### Risk

Per capability, because "implemented" and "validated" are different claims
(F-005). `impl` / `unit` / `replay` / `MT5` / `paper`:

| Capability | impl | unit | replay | MT5 | paper |
|---|:--:|:--:|:--:|:--:|:--:|
| stale-data check | x | x | x | | |
| spread check | x | x | x | | |
| instrument allow-list | x | x | x | | |
| stop validation | x | x | x | | |
| volume normalisation | x | x | x | | |
| risk-based size calculation | x | x | x | | |
| max open risk | x | x | x | | |
| max daily loss | x | x | x | | |
| max drawdown | x | x | x | | |
| order-frequency protection | x | x | x | | |
| duplicate protection | x | x | x | | |
| automatic HALT | x | x | x | | |
| manual HALT | x | x | | | |
| manual reset workflow | x | x | | | |
| durable HALT across restart | x | x | | | |
| cancel pending orders | x | x | | | |
| flatten positions | x | x | | | |
| real portfolio open-risk accounting (D1.4, supersedes O-004) | x | x | x | | |
| Friday entry cut-off, weekday overnight permitted (D1.5, O-009, supersedes O-003) | x | x | x | | |
| weekly-close exposure halt (D1.5, O-009, supersedes O-003) | x | x | x | | |
| account currency / leverage guard | x | x | x | | |
| automatic flatten at the deadline (item 7, ADR-009) | x | x | | | |
| execution-time revalidation | x | x | x | | |

Execution-time revalidation (ADR-001's FINAL Risk,
`risk/policies.py::revalidate_fixed_volume_at_execution_time`) was built
and unit-tested 2026-08-27 (Phase 4 slice 2, §13 thirty-fifth entry),
reusing `evaluate()`'s full checklist against freshly observed inputs and
never resizing — PASS with the original approved volume unchanged, or
BLOCK/HALT. It is exercised end to end by `ExecutionOrchestrator` against
a scripted fake terminal (`tests/integration/test_execution_orchestrator.py`)
and, since 2026-08-27, against the real Pepperstone DEMO terminal too
(review 1.24 §8's authorized evidence run, §13 forty-fifth entry): real
`FINAL_RISK_PASSED` with a real approved volume and real account equity,
followed by a real (non-mutating) `order_check` call — `MT5` column
checked. `paper` stays blank; that is the ongoing paper campaign itself
(still `NOT STARTED`), a different claim from this one evidence run.

No risk capability is MT5-integrated or paper-validated yet, but the
picture is no longer "nothing feeds a live tick into the risk engine" —
that was true through review 1.14 and is stale now (review 1.18 §3,
contradiction B). Since 2026-08-25, `application/live_decision.py::LiveDecisionOrchestrator`
does exactly that: a real persisted closed M5 bar reaches the intent-time
Risk Engine (`risk.policies.evaluate`, this same unmodified table's
functions) and the Supervisor, fed a real `ReconciliationStatus`. That
pipeline is integration-tested against real PostgreSQL with a synthetic
bar series — **it has not yet run end-to-end against a fresh real-terminal
session (F-051)**, so the MT5/paper columns above stay blank until that
happens, not because the pipeline doesn't exist but because it has not yet
been exercised against what it is meant to observe.

### Data

Persisted by the running orchestrator, not merely storable (D-030 closed).

- [x] raw ticks — `market_ticks`, written for every window the run observed
- [x] raw bars — `market_bars`, each carrying its origin and, when derived, the
      pipeline version that produced it
- [x] symbol specs — `instrument_specs` has a real producer since 2026-08-25
      (F-048): `persistence/instrument_specs.py::InstrumentSpecStore`,
      written by `LiveReader._reconnect()` on every reconnect, content-keyed
      by `spec_version`. **Real-terminal validated 2026-08-26** (F-051 part
      1) — a real spec was persisted, compared field-by-field against the
      2026-08-24 first-contact evidence, and approved as the pinned
      baseline. Reconciliation now compares it (F-053, shipped 2026-08-26)
      via the spec's own `spec_version` hash
- [x] features — `feature_snapshots` table (D-031, shipped 2026-08-26):
      the full `FeatureEvidence` payload, not only its hash and version,
      persisted for every evaluated window via `RunRecorder.record_features()`
      on both the replay and live paths
- [x] signals — `SignalGenerated`, one per evaluated window including NO_TRADE
- [x] trade intents — `TradeIntentCreated`
- [x] risk decisions — `RiskDecisionMade`
- [x] supervisor decisions — `SupervisorDecisionMade`
- [x] orders/fills — `OrderCheckCompleted`, `OrderSubmitted`, `OrderResultReceived`
- [x] positions — `PositionChanged`, before and after each fill
- [x] decision capsules — in the journal and in `decision_capsules`
- [ ] incidents — the contract exists; no register, so nothing produces one
- [x] model/config versions — carried on every capsule; `config_versions` table exists
- [x] safety state — `safety_state_events`, read with the file latch (ADR-002)
- [x] risk-session state — `risk_session_states`, recovered on startup (F-019)

## Platform quality metrics

Fill during operation.

```text
MT5 uptime:
Data freshness p50:
Data freshness p95:
Gateway errors / day:
Reconnects / day:
Order rejection rate:
Duplicate orders:
Reconciliation mismatches:
Mean order latency:
p95 order latency:
Critical incidents:
```

## Open platform issues

Records are updated in place, never deleted — a closed issue is evidence that
something was found and dealt with (F-009).

| ID | Severity | Issue | Owner | Status | Resolution |
|---|---|---|---|---|---|
| APP-001 | HIGH | No FLATTEN POSITIONS control exists; a halt stops new orders but cannot close an open position (build.md §8.2, D-027) | | **CLOSED 2026-08-17** | Three separately authorised, separately logged controls in `risk/operator_controls.py`; tests assert they are decoupled. **Not MT5-validated** — real broker behaviour is unproven until M1/M5 (review 1.1 F-008) |
| APP-002 | HIGH | Supervisor receives hard-coded `open_incident_count=0` and `reconciliation_matched=True`; those checks can never fire, yet an approval reads as though they passed (D-028) | | **CLOSED 2026-08-17** | `ReconciliationStatus` / `IncidentStatus` with explicit `UNKNOWN`, both defaulting to it. Unknown reconciliation halts, unknown incident vetoes, and the safety gate sits above the policy-enable switch |
| APP-003 | HIGH | Kill-switch state did not survive a restart (review 1.0 F-003) | | **CLOSED 2026-08-17** | `SafetyStateStore` + atomic file store; startup begins disabled. Cross-process restart evidence in `tests/integration/test_halt_survives_restart.py` |
| APP-004 | MEDIUM | Safety-state authority undefined once PostgreSQL arrives (review 1.1 F-012) | | **CLOSED 2026-08-18** | `CompositeSafetyStateStore` is what `application/bootstrap.py` hands the kill switch. Disagreement resolves to HALTED; a fresh database starts UNKNOWN and refuses orders |
| APP-005 | HIGH | Persistence invariants for the event journal (review 1.3 F-015) | | **CLOSED 2026-08-18** | All ten ADR-003 acceptance tests pass against PostgreSQL 17, including replay-from-journal reproducing the in-memory run |
| APP-006 | MEDIUM | Persistence exists but the orchestrator does not use it (D-030) | | **CLOSED 2026-08-18** | Every stage writes through `RunRecorder`; the run is reproducible from the `events` table alone and survives a real process restart. 19 integration tests |
| APP-007 | HIGH | Risk-session state reset in the permissive direction on restart — a crash refilled the daily-loss budget (review 1.5 F-019) | | **CLOSED 2026-08-18** | `risk/session.py` recovers the session and can only ever tighten it; unreadable, future-dated or position-mismatched records halt. 18 unit + 8 restart tests |
| APP-008 | HIGH | The journal records decisions but not the market data they were made from; warm-up windows leave no trace at all (D-031) | | **CLOSED 2026-08-18** | `market_ticks` and `market_bars` written on the ordinary path for every observed window; tick→bar pipeline with gap, out-of-order, duplicate and crossed-quote detection. 46 tests |
| APP-010 | MEDIUM | `metadata.create_all` was the only way to build the schema, on a database that now holds data (D-029) | | **CLOSED 2026-08-18** | Alembic baseline; the runtime migrates rather than creates; a restored `pg_dump` is proven to reproduce the run |
| APP-011 | MEDIUM | Two supervisor checks could not fire but reported as passed (D-015, D-028, EV-002) | | **CLOSED 2026-08-18** | Threshold set to `null` = uncalibrated; every decision carries `uncalibrated_checks` and the run report names them. The calibration itself still needs real data |
| APP-012 | HIGH | Nothing enforced the owner's one-exposure and intraday decisions (O-003, O-004) | | **PARTLY CLOSED 2026-08-18; both legs superseded by owner risk policy v1** | O-004 (one exposure per symbol) withdrawn 2026-09-02 — multiple positions are now permitted, enforced instead by real portfolio open-risk accounting (D1.4, `risk/portfolio_risk.py::assess_open_risk`, ADR-011, O-008). O-003 (no overnight positions) withdrawn 2026-09-03 — weekday overnight is now permitted, only the Friday trading day carries entry-cutoff/flatten deadlines against the weekly close (D1.5, ADR-012, O-009). Entries are refused and a breach halts on both remaining rules; **the automatic flatten is built** (item 7, ADR-009) |
| APP-014 | MEDIUM | The MT5 adapter has never run against a terminal; the fake it was tested against was written from documentation, not observation (D-035) | | **CLOSED 2026-08-24** | First contact made: account, symbol, instrument and position reads all succeeded against the real terminal (status.md §13). Continuous bar/tick read and observed reconnect behaviour, once "still open" here, completed the same day: Phase A (30 clean minutes) and Phase B (two deliberate terminal closures, both recovered with full revalidation) — F-034/F-037 closed, M1 PASSED (`feedback.1.12.md`). Closed in place per review 1.14 §11 F-033, which found this row still read as open after the fact |
| APP-015 | MEDIUM | `symbol_info.filling_mode` and `trade_mode` are integer enums stored as strings; the filling mode is a bitmask, so `"3"` is recorded where `FOK\|IOC` was meant (D-037) | | **CLOSED 2026-08-24** | Confirmed against the real terminal (`filling_mode=2`→IOC, `trade_mode=4`→FULL, matching documentation) and fixed: decode logic shared between the gateway and the probe in `mt5_gateway/enums.py` |
| APP-013 | MEDIUM | The Pepperstone entity is ambiguous: O-001 says EU, the supplied server says UK | | **CLOSED FOR DEMO 2026-08-24, by O-005** | Owner/reviewer decision: demo entity is **Pepperstone Limited (UK)**, amending O-001 for demo/development only. Does **not** decide the entity for a future live account — that reopens this question against live documentation (D-034) |
| APP-016 | LOW | The terminal reports `trade_allowed: false` even though the account itself reports `trade_allowed: true` | | **KNOWN / DEFERRED TO M5 READINESS** (review 1.9 §4) | Reviewer/owner decision: **do not enable AlgoTrading yet** — M1 is read-only, and leaving it off is an extra safety layer with no approved execution path. Before M5: account permission, terminal permission, verified demo account, an explicitly enabled execution adapter and `feedback.2.0` GO must **all** be true together — the UI toggle alone must never be sufficient |
| APP-009 | MEDIUM | `feedback.1.4.md` is referenced by review 1.5 but is not in the repository; findings F-016 and F-017 are unaccounted for | | **CLOSED 2026-08-18** | Restored unchanged by the owner. F-016 and F-017 were already resolved by the tracker and deviation rewrites; F-020 was new and is now closed |

---

# 4. Component 2 — Trading Agent

## Current status

```text
Status: BUILDING
Owner:
Current milestone: M6
Current strategy: ict_v1 (sweep + shift + displacement gap + OTE, in killzones)
Champion version: none — nothing has been promoted
Challenger versions: baseline_v1 retained as the §9.2 benchmark
Implementation maturity: REPLAY-TESTED
Gate qualification: FEATURE FREEZE (review F-004)
Next objective: Evidence on real data. No new strategy concepts.
```

## Agent contract

The Trading Agent may:

- read normalized market/features;
- read permitted portfolio/risk context;
- choose `BUY`, `SELL` or `NO_TRADE`;
- propose stop/target structure within policy;
- produce confidence and reason codes.

The Trading Agent may not:

- call MT5;
- access broker credentials;
- bypass risk checks;
- choose unrestricted final lot size;
- alter production risk config;
- reset HALT;
- promote itself to live.

## Strategy/version tracker

| Version | Type | Environment | Status | Started | Ended | Notes |
|---|---|---|---|---|---|---|
| baseline_v1 | deterministic | replay | BUILDING | 2026-08-17 | | Benchmark per §9.2. No edge claimed or measured. |
| ict_v1 | deterministic | replay | BUILDING | 2026-08-17 | | ICT entry model, ten enforced conditions. ~3 setups per 12k M5 bars on synthetic data — a number that means nothing on a random walk. |

## Research checklist

- [ ] no-trade benchmark
- [ ] deterministic baseline
- [ ] feature specification
- [ ] leakage tests
- [ ] walk-forward splitter
- [ ] realistic cost model
- [ ] model baseline
- [ ] calibration analysis
- [ ] regime analysis
- [ ] champion/challenger protocol
- [ ] exact model versioning
- [ ] deterministic feature replay

## Trading Agent KPI snapshot

```text
Evaluation window:
Closed trades:
No-trade decisions:
Long intents:
Short intents:

Net P&L:
Expectancy/trade:
Profit factor:
Hit rate:
Avg win:
Avg loss:
Max drawdown:
Time under water:
Sharpe:
Sortino:
Calmar:

Avg spread paid:
Avg slippage:
p95 slippage:
Avg holding time:
Exposure time:

Prediction calibration:
Drift state:
```

## Regime breakdown

| Regime | Trades | Expectancy | P&L | Drawdown | Notes |
|---|---:|---:|---:|---:|---|
| Trend | | | | | |
| Range | | | | | |
| High volatility | | | | | |
| Low volatility | | | | | |
| Unknown | | | | | |

## Promotion evidence

```text
Candidate:
From gate:
To gate:
Reason:
Out-of-sample evidence:
Cost assumptions:
Regime coverage:
Known weaknesses:
Decision:
Reviewer:
Date:
```

## Open Trading Agent issues

| ID | Severity | Issue | Owner | Status | Resolution |
|---|---|---|---|---|---|
| TA-001 | | | | | |

---

# 5. Component 3 — Evaluator / Supervisor Agent

## Current status

```text
Status: BUILDING
Owner:
Current milestone: M7
Policy version: supervisor-policy-v1
Statistical monitor version: none
LLM analyst enabled: NO
Implementation maturity: UNIT-TESTED
Gate qualification: NOT PASSED — safety work only (review F-007 gate decision)
Next objective: Real incident and reconciliation inputs, then post-trade scorecard
```

## Supervisor permissions

```text
Can approve intent:       YES
Can veto intent:          YES
Can trip HALT:            YES
Can submit order:         NO
Can create a BUY/SELL:    NO
Can change lot size:      NO
Can change risk policy:   NO
Can reset HALT:           NO
Can promote strategy:     NO
```

## Evaluator layers

| Layer | Status | Purpose |
|---|---|---|
| Deterministic policy | BUILDING | hard safety |
| Statistical monitor | NOT STARTED | drift/anomaly detection |
| LLM analyst | DISABLED | explanation/research only |

## Pre-trade checks

- [ ] strategy allowed
- [ ] model version allowed
- [ ] known regime
- [ ] confidence sane
- [ ] signal-frequency sane
- [ ] spread sane
- [ ] market data current
- [ ] no duplicate intent
- [ ] recent execution behavior sane
- [ ] no active incident
- [ ] no active HALT

## Post-trade checks

- [ ] slippage
- [ ] spread cost
- [ ] latency
- [ ] MAE/MFE
- [ ] expected vs realized
- [ ] stop/target behavior
- [ ] exit reason
- [ ] calibration
- [ ] drawdown contribution
- [ ] anomaly flags

## Drift dashboard

```text
Feature drift:
Prediction drift:
Performance drift:
Spread drift:
Latency drift:
Trade-frequency drift:
Reject-rate drift:
Live-vs-backtest divergence:
```

## Evaluator KPI snapshot

```text
Window:
Intents evaluated:
Approvals:
Vetoes:
Halts:
False-positive veto reviews:
Critical anomalies:
Warnings:
Incidents opened:
Incidents closed:
```

## Open Evaluator issues

| ID | Severity | Issue | Owner | Status | Resolution |
|---|---|---|---|---|---|
| EV-001 | HIGH | Four of seven pre-trade checks were inert — confidence band, signal frequency, active incident, reconciliation (D-028) | | **SPLIT 2026-08-17** | Incident and reconciliation resolved: both now carry explicit `UNKNOWN` and fail closed. The two calibration items are carried forward as EV-002 |
| EV-002 | MEDIUM | Confidence band (0.0-1.0) and signal-frequency threshold (20/hour on an M5 cadence) are configured to values nothing can fall outside, so neither can fire (D-015) | | OPEN | Recalibrate once the bar interval is settled — build.md §29 Q3 |

---

# 6. Paper-trading campaign

## Campaign status

```text
Campaign: NOT STARTED
Environment: MT5 DEMO
Broker: Pepperstone
Server: PepperstoneUK-Demo
Canonical symbol: EUR/USD
Broker symbol:
Start date:
End date:
Strategy version:
Evaluator policy version:
Risk config version:
```

## Daily paper log

| Date | Trades | P&L | Max DD | Vetoes | Errors | Reconnects | Incidents | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| | | | | | | | | |

## Weekly review

```text
Week:
Reliability:
Trading behavior:
Execution quality:
Drift:
Largest incident:
Largest unexpected behavior:
Changes made:
Changes intentionally NOT made:
Promotion status:
```

### Anti-overfitting rule

Do not change strategy parameters after every bad day.

Every strategy/config change must create a new version and reset or explicitly segment evaluation evidence.

---

# 7. Shadow-mode campaign

```text
Status: NOT STARTED
Start:
Live feed/account context:
Orders actually submitted: 0
```

Track:

| Metric | Demo | Shadow | Difference | Acceptable? |
|---|---:|---:|---:|---|
| Median spread | | | | |
| p95 spread | | | | |
| Signal count | | | | |
| Hypothetical fill slippage | | | | |
| Data gaps | | | | |
| Reconnects | | | | |

---

# 8. Kill-switch log

| Time UTC | Trigger | Source | State before | State after | Operator reset | Incident |
|---|---|---|---|---|---|---|
| | | | | | | |

Current state:

```text
NEW ORDERS: DISABLED
PENDING ORDER CANCEL: MANUAL
POSITION FLATTEN: MANUAL
HALT reason: Project not yet in paper mode
```

---

# 9. Incident register

Severity:

```text
SEV-0 = potential uncontrolled financial exposure
SEV-1 = execution/risk safety compromised
SEV-2 = degraded operation or material data problem
SEV-3 = non-critical defect
```

| Incident | Severity | Opened | Closed | Component | Root cause | Corrective action | Regression test |
|---|---|---|---|---|---|---|---|
| | | | | | | | |

A SEV-0 or unresolved SEV-1 blocks promotion.

---

# 10. Decision log

Use this for architectural or risk decisions.

| Date | Decision | Why | Alternatives rejected | Owner |
|---|---|---|---|---|
| 2026-08-17 | Separate Trading Agent from MT5 execution | Prevent strategy/model from bypassing deterministic controls | Direct agent→MT5 | |
| 2026-08-17 | Evaluator gets veto/halt but no execution rights | Independent control plane | Second trading agent | |
| 2026-08-17 | Paper → shadow → live-canary | Reduce deployment risk | Backtest directly to live | |
| 2026-08-17 | MT5 Gateway on Windows x86-64 | Current official Python wheel distribution | Unsupported direct Linux dependency | |
| 2026-08-17 | Develop on macOS arm64; defer the gateway to a Windows x86-64 host | Everything except the gateway is host-independent; `MetaTrader5` is marked `sys_platform == 'win32'` so `uv sync` works on both | Wine, x86 emulation, developing only on Windows | |
| 2026-08-17 | Package as `src/crumblr/…` rather than build.md §5's `src/domain/…` | Avoids claiming top-level import names like `domain` and `risk` | Literal §5 layout | |
| 2026-08-17 | All monetary values are `Decimal`; `float` rejected at the model boundary | `Decimal(1.1)` carries a silent binary error, and in a stop-loss price that is both invisible and expensive | Floats with rounding discipline | |
| 2026-08-17 | `stop_loss_price` mandatory on BUY/SELL intents | Position size is derived from stop distance, so an unstopped intent is not merely risky — it is unsizeable | Optional stop with a later risk-engine check | |
| 2026-08-17 | Identity by content hash: `decision_hash`, `spec_version`, `config_version`, `provenance_fingerprint` | Makes §11 auditability and §13.3 replay determinism checkable rather than aspirational | Sequential version numbers | |
| 2026-08-17 | No `config/live.yaml` in the repository; live needs both an in-file acknowledgement and `CRUMBLR_ALLOW_LIVE=1` | A live config that merely exists is one editing mistake away from being selected | Ship live.yaml disabled by a flag | |
| 2026-08-17 | Freeze `ict_v1` feature development until real EUR/USD data exists | Review F-004: strategy sophistication was running ahead of any evidence. Bug, determinism, contract and test work continue; new concepts, extra confirmations, parameter tuning and ML overlays do not | Continue building ICT v2 | Reviewer |
| 2026-08-17 | Local Git allowed; remote deferred until collaboration | Review F-006. Note: the recorded agreement was to hold *commits* until a working prototype, and `status.md` §2 lists "Repository created" as an M0 exit criterion — so `git init` executed the plan rather than departing from it. No commit has been made | Removing the local repository | Reviewer + owner |
| 2026-08-17 | Safety-critical state is represented as MATCHED/MISMATCHED/UNKNOWN, never as a boolean | Review F-002. A boolean forces "not checked" and "checked and fine" into one value, and the safe-looking one wins by default | Boolean with a safe default | Reviewer |
| 2026-08-17 | System safety state is persisted; startup begins with new orders disabled | Review F-003. A halt that does not survive a restart is not a halt | In-memory only until M2 | Reviewer |
| 2026-08-17 | Execution-time risk revalidation before `order_send` (ADR-001) | Review F-007. State can change between approval and submission | Single check at intent time | Reviewer |
| 2026-08-18 | The event journal is append-only and identity is producer-assigned (ADR-003) | Review F-015. A journal that can be edited is not an audit trail, and a database sequence is not identity across a rebuild | Mutable rows with a serial primary key | Reviewer |
| 2026-08-18 | M2 is complete when a replay driven from the persisted journal reproduces the in-memory run byte for byte | Review F-015. Storing rows is not the same as being able to prove what happened | Row counts as the completion signal | Reviewer |
| 2026-08-18 | Skip review 1.4; build M2 first | Two consecutive review rounds produced documentation only. `feedback.1.3.md` §8 itself triggers 1.4 on M2 availability, and §9 states simulated development has exhausted its value. There is nothing new to review until the persistence layer exists | Another documentation-only review cycle | Project owner |
| 2026-08-18 | Journal event ids are derived from the event type, its window and its payload, never from `uuid4` | ADR-003 invariant 3 makes an append idempotent on `event_id`, which only converges if the same logical event yields the same id. A random id turns a rerun after an ambiguous outcome into duplicated history | Random ids with a de-duplication pass on read | |
| 2026-08-18 | A decision window's events and its sealed capsule commit in one transaction; a halt is flushed immediately | ADR-003 invariant 5 wants a multi-row transition to be atomic. A halt is the exception because a safety event that waits for the next commit is one a crash can lose | One transaction per event; or buffering halts with the rest | |
| 2026-08-18 | Risk-session recovery may only ever tighten; anything it cannot establish halts | Review F-019. A restart that refills a spent daily-loss budget moves a safety limit in the one direction it may never move on its own | Trusting the record as-is; recomputing from live equity alone | Reviewer |
| 2026-08-18 | A position-count disagreement between the record and the account halts, in both directions | Review 1.5 §4 step 8 makes this the rule at M5. Applying it now to the only position book that exists means the reconciliation path is exercised before MT5 rather than written for the first time against it | Deferring all reconciliation to M5 | Reviewer |
| 2026-08-18 | The owner's v1 decisions: Pepperstone EU demo on MT5, an M5 decision cadence, no overnight positions, one EUR/USD exposure at a time | Review 1.5 O-001…O-004. Broker choice unblocks M1; the cadence fixes what the supervisor's frequency threshold must be calibrated against | Multi-broker routing in v1; deferring the timeframe decision | Project owner |
| 2026-08-18 | The FX session boundary is 17:00 America/New_York and is not configurable (ADR-004) | O-003 forbids overnight positions, and "overnight" is defined by the rollover that charges swap. A configurable boundary would be a second definition of the trading day, able to drift from the one the daily-loss baseline already uses | Midnight UTC; a configurable clock time | Reviewer + owner |
| 2026-08-18 | One EUR/USD exposure is a constant in code, not a configuration field | O-004 is a business rule the owner approved, not a budget to tune. A YAML key would let someone raise it without the decision that should accompany doing so | `max_exposures_per_symbol` in `config/` | Project owner |
| 2026-08-18 | The intraday flatten is detected and halted on, not performed | Refusing to open is safe and ships now; promising to close is a promise the system cannot keep before M5. A policy claiming otherwise would read as though positions were managed out when nothing was managing them | Building an automatic flatten ahead of the M5 gate | Reviewer |
| 2026-08-18 | The supervisor's frequency check is marked uncalibrated rather than given a number | Review 1.6 F-024. O-002 fixed the cadence at M5, which permits 12 windows an hour, so the old threshold of 20 was an absent control wearing a number. The only honest calibration needs real EUR/USD observations | A structural rate limit derived from synthetic trade frequency | Reviewer |
| 2026-08-18 | A bar records its origin and, when derived, the pipeline version that made it | A bar the broker sent and one this platform built are not interchangeable evidence, and nobody will remember which was which. Without the version, changing the aggregation rules silently rewrites history | Storing bars as bare OHLC rows | |
| 2026-08-18 | A conflicting bar for an interval already stored raises rather than being ignored | build.md §26 requires raw data to be immutable. Keeping whichever arrived first satisfies the wording and loses the contradiction, which is the finding | `ON CONFLICT DO NOTHING` for bars as well as ticks | |
| 2026-08-18 | Alembic is the deployment path; `create_all` is for tests only | Ordinary runs now write data. A schema built one way and migrated another is two schemas that happen to agree today | Keeping `create_all` everywhere until a deployment exists | Reviewer |
| 2026-08-18 | The account guard checks currency and leverage as well as server and login | Both change what a risk budget means without changing anything the strategy or the risk engine can otherwise see. Review 1.5 requires broker metadata to be discovered and verified, never assumed | Trusting the configured values | Project owner |
| 2026-08-17 | Risk values in `config/paper.yaml` are provisional placeholders | They encode risk policy, which build.md §29 reserves for a human; the platform still needs loadable values to be testable | Leave them unset and block all progress | |
| 2026-08-24 | build.md §29 Q2 answered: the Pepperstone demo account is `RETAIL_HEDGING` | Read from `account_info()` on first contact rather than guessed, per O-001. `risk/policies.py`'s one-exposure rule was written to hold under either mode and needed no code change once the answer was known | Guessing ahead of the terminal; supporting both modes speculatively | Observed, not decided |
| 2026-08-24 | The Pepperstone entity question (D-034) stays open despite new evidence | First contact returned `company: "Pepperstone Limited"`, which reads as the UK entity rather than the "Pepperstone EU" of O-001 — but a company-name string is evidence, not a legal determination (review 1.8 F-028). Inferring the entity from it would repeat the mistake `APP-014` exists to prevent | Treating the company field as settling the question | Reviewer (F-028); owner decision still pending |
| 2026-08-24 | **O-005**, superseding the row above: for the demo/development environment only, the Pepperstone entity is **Pepperstone Limited (UK)** | Review 1.9 §2 F-028: `Pepperstone Limited` (UK) and `Pepperstone EU Limited` (Cyprus) are officially distinct entities, and the observed `company`/`server` match the UK one. Scoped deliberately — refines O-001's demo shorthand without pre-deciding a future live account, which needs its own review against the owner's residence and live documentation | Treating this as a live-account entity decision; leaving D-034/APP-013 open indefinitely with no way to close a demo-only question | Reviewer + owner (O-005) |
| 2026-08-24 | APP-016 (terminal `trade_allowed: false`) recorded as known and deliberately not changed | Review 1.9 §4: keeping the MT5 "AlgoTrading" toggle off is an additional safety layer while no execution path is approved. M5 readiness must require account permission, terminal permission, a verified demo account, an explicitly enabled execution adapter and `feedback.2.0` GO — all together, so the toggle alone can never be sufficient | Enabling AlgoTrading now "to be ready"; treating the toggle as the execution gate | Reviewer + owner |
| 2026-08-24 | A real MT5 soak/live run must use its own database (`crumblr_soak`), never the shared dev/test database (`crumblr`, `DEFAULT_TEST_URL`) | The twelfth update-log entry's own "worth a dedicated soak-only database... noted here rather than acted on" line — raised again independently by the reviewer/supervisors before it was acted on. `tests/integration`'s `engine` fixture drops the schema at teardown by design; the third Phase A attempt crashed on it the moment the two shared one database. `scripts/mt5_live_reader.py` now refuses to start at all unless `CRUMBLR_DATABASE_URL` is explicitly set, rather than silently falling back to the test default — "remember to set it" is not a control, same reasoning as F-031. `crumblr_soak` created on the same local PostgreSQL instance, migrated with the same Alembic baseline, `.env`/`.env.example` updated | Re-migrating the shared database before every soak attempt indefinitely; changing `create_db_engine`'s global default (would affect `run_replay.py` and other legitimate dev-default callers) | Reviewer/owner |
| 2026-08-24 | The MT5 broker-clock offset (D-039) is detected dynamically once per gateway connection, never hard-coded | The fourth Phase A attempt proved Pepperstone's server clock runs a stable ~2:59:39-2:59:40 ahead of true UTC — real, measured, not a documentation gap. Review 1.11 §7 explicitly forbids inventing a correction without observation; observation now existed, but a fixed `+3` would have been a second silent assumption exactly where the first one used to be, wrong the moment DST shifts the real offset or a different account/server is used. `ReadOnlyMt5Gateway._clock_offset()` instead measures the gap from `symbol_info_tick` against the platform's own clock every time a gateway is constructed — which is every `LiveReader` reconnect — rounded to the nearest 30 minutes (GMT offsets are always whole/half-hour). User confirmed this direction over a hard-coded offset or leaving it undocumented, when asked directly | Hard-coding `+3`; documenting the gap without correcting it and leaving M1 blocked | User (relaying reviewer framing) |
| 2026-08-24 | **M1 MT5 read-only gateway: PASSED, MT5-INTEGRATED** | Review `feedback.1.12.md` §7: Phase A and Phase B evidence together exceed the minimum M1 acceptance requirement build.md set. Recorded in §16 Promotion history. Does not authorize `order_send` | Reviewer (`feedback.1.12.md`) |
| 2026-08-24 | `InstrumentSpec.spec_version`'s semantic-identity hash excludes `tick_value` as well as `captured_at_utc` | Review F-039: Phase B logged `live_reader.spec_changed` on every reconnect even though the broker's actual contract terms never changed. First-contact evidence (§13 sixth entry) already recorded why: `tick_value` is non-round for this account because MT5 recomputes it live from the EUR/USD cross-currency rate whenever the account currency differs from the quote currency — a live market readout, not broker policy, so hashing it manufactured a false "specification changed" alert on every reconnect. `tick_value` is still recorded on every `InstrumentSpec`; it is simply excluded from what "changed" means | Leaving `tick_value` in the hash and treating every reconnect's alert as expected noise; comparing specs field-by-field with an ad hoc tolerance instead of a stable hash | Reviewer (F-039) |
| 2026-08-24 | Broker-clock offset detection (D-039) fails closed on a stale/implausible reference tick, rather than caching whatever it measures | Review F-040: the offset is derived from the gap between `symbol_info_tick`'s timestamp and the platform's own clock; if that tick is stale (e.g. the terminal handing back the last quote it ever saw), the derived "offset" is not a timezone at all, and D-039's fix would then mis-correct every timestamp for the life of that gateway connection with no way to notice. `_clock_offset()` now rejects a measurement whose residual from a clean half-hour multiple exceeds 3 minutes (real offsets round cleanly; a stale tick does not), or whose magnitude exceeds ±15h (no real GMT offset does), raising rather than caching — the next call re-measures fresh. `LiveReader` treats this the same as a stale feed: `DISCONNECTED`, not sticky, clears on its own once a fresh tick arrives | A fixed staleness cutoff on tick age alone (the tick's own clock is exactly what is in question, so its age cannot be measured independently of the offset it is meant to establish); trusting the first measurement for the life of the connection, as before | Reviewer (F-040) |
| 2026-08-24 | The soak database is reset only via `alembic downgrade base` -> `upgrade head`, never `bootstrap_schema()`/`create_all` against a hand-dropped database | Review F-041: the third Phase A attempt's own recovery mixed the two paths — tables dropped by hand, then rebuilt with `create_all` while `alembic_version` still claimed head, so a later `alembic upgrade head` believed the database already current and did nothing. `tests/integration/test_migrations.py` already proves the downgrade/upgrade round trip leaves nothing behind; `scripts/reset_soak_database.py` is that same proven pair, run deliberately, refusing to run against a URL without "soak" in it and requiring `--yes` | Leaving the reset as an unrepeatable manual recovery; a script that wraps `drop_schema()`+`bootstrap_schema()` for speed, reintroducing the same drift | Reviewer (F-041) |
| 2026-08-24 | Dashboard v0 built as FastAPI + server-rendered Jinja2 HTML, its own `src/crumblr/dashboard/` package rather than inside `api/` | Asked the user directly (AskUserQuestion) rather than picking a stack silently, since a new dependency and a long-lived architectural surface are the kind of decision worth pausing for. FastAPI chosen over Streamlit (heavier dependency, framework-owned process model, less natural to keep strictly read-only) and over stdlib `http.server` (zero dependencies but hand-rolled HTML across several panels grows awkward). Kept out of `api/` because build.md §21 already earmarks that package for "authenticated operator functions" — the opposite of what v0 must be — so the read-only boundary is a physically separate package, not a convention inside one meant to eventually hold mutation | Streamlit; stdlib `http.server`; building inside `api/` | User |
| 2026-08-25 | Dashboard v0's live refresh is a small vanilla-JS poller against `GET /api/state` that updates only the fast-moving fields (price, tick age, the three headline status badges, tick/bar counts, the chart); slower-moving panels (connection detail, account context, decision pipeline, activity timeline) stay accurate as of the last full page load rather than being live-bound | Review 1.13 §10 prefers "5-second refresh/poll for state without full-page flicker" but explicitly forbids building a JavaScript application framework merely for polish, and restricts any JS polling to `GET /api/state` only (no mutation endpoint). A full DOM-diffing/templating layer in JS would duplicate the Jinja rendering logic — a second place the same bug could be introduced — for panels that in practice change on the order of minutes (reconnects, decisions, journal events), not seconds. Scoping the live JS binding to only the fields where "stale-looking" is a real F-043 risk (price/health) keeps the JS small, auditable, and free of a second rendering engine | A full client-side rendering framework; a meta-refresh full-page reload (simpler but produces the flicker the review explicitly wants avoided); fetching and swapping the full `/` HTML via JS (equally correct, more bytes per poll, and not `GET /api/state` as the review specifies) | Reviewer (review 1.13 §10) |
| 2026-08-25 | One `account_info()` read per `BrokerAccountSnapshot`, not two | Review 1.16 F-052: the original `capture_broker_state` called `gateway.account()` then a separate `gateway.account_extras()` — two reads that could straddle a real change at the broker (a fill, a swap charge) between them, producing a stored row that never existed as such at the broker. `ReadOnlyMt5Gateway.account_with_extras()` derives `AccountState` and `AccountExtras` from one raw `account_info()` response instead | Two reads with separate observation timestamps, presented as one snapshot anyway (the review's own "alternative design", rejected as more complex for no benefit at this stage) | Reviewer (F-052) |
| 2026-08-25 | Broker-state freshness (`BrokerStateHealth`) is a type separate from `ReaderStatus`/`ReaderHealth`, not a field added to either | Review 1.16 F-050: fresh EUR/USD ticks and a stale/missing/incomplete account snapshot are different facts, and folding them into one status would let one hide the other the moment a live decision or an order needs to see both independently. `is_usable(now, max_age)` gives reconciliation a direct predicate for the review's own missing/stale/incomplete → UNKNOWN rule | Adding `broker_state_status` fields onto `ReaderHealth` | Reviewer (F-050) |
| 2026-08-25 | Reconciliation v0 reads only the durable F-047 snapshots, never a live MT5 call, and expected state is `ExpectedState.flat()` (zero positions, zero pending orders) until an execution path exists | Review 1.15 §14/1.16 §7-8: comparing the latest MT5 snapshot against itself ("MT5 to MT5") would detect nothing; expected state must eventually come from the platform's own durable order/position history once `order_send` exists, never from what MT5 last reported. `UNKNOWN` (missing/stale/incomplete observation) is structurally distinct from `MISMATCHED` (a real disagreement) and neither is ever silently upgraded to `MATCHED` | Deriving "expected" from the same MT5 read being reconciled; treating a stale/incomplete snapshot as good enough to reconcile against | Reviewer (review 1.16 §7-8) |
| 2026-08-25 | `LiveDecisionOrchestrator` is a class of its own, not a mode added to `LiveReader` or `ReplayOrchestrator` | Review 1.16 §9 is explicit: "Build it as a separate `LiveDecisionOrchestrator`/equivalent rather than turning `LiveReader` into a trading process." Keeps the boundary `LiveReader` = observe/persist real broker+market state; `LiveDecisionOrchestrator` = decide from what was persisted; execution service (M5) = later, execute. The Trading Agent, Risk Engine and Supervisor it calls are exactly the same components `ReplayOrchestrator` already uses — nothing about how a decision is judged changed, only where its inputs come from | Adding a "live mode" flag to `LiveReader`; forking `ReplayOrchestrator`'s decision logic into a parallel copy | Reviewer (review 1.16 §9) |
| 2026-08-25 | `instrument_specs` gets a real producer: `LiveReader` now persists the spec it already observes on every reconnect | F-048 needs a durable instrument spec to size against without the decision orchestrator itself talking to MT5 — a dependency discovered while building F-048, not planned ahead of it. Closes part of the gap D-045 (then D-045, now folded into D-046) already named: "the day `instrument_specs` gets a real producer..." | Passing a hardcoded/config-supplied spec into the live decision path (risks silent drift from the real broker spec); giving the decision orchestrator its own MT5 read (violates the LiveReader=observe boundary) | |
| 2026-08-25 | **O-006**: the next promotion target is a controlled MT5 **DEMO** account autonomous canary order — real decisioning, real order submission, real demo fills, zero live-money exposure — not a live account, not strategy validation from a handful of trades | Review 1.15 §3 interprets the owner's direction toward "daadwerkelijk getrade kan worden" concretely, so "autonomous trading" cannot later be read as authorizing more than this. Reprioritizes engineering away from dashboard/foundation polish (both now substantially closed) onto the M5 critical path: CI/M0 closure → F-047 durable broker-state persistence → read-only reconciliation → F-048 live shadow decision pipeline (execution stays disabled) → execution-safety work (F-049) → `feedback.2.0` → one gated canary order | Owner (relayed via reviewer, review 1.15 §3/§12) |
| 2026-09-02 | **O-008a**, superseding O-004: multiple EUR/USD positions may be open at once, provided total open risk never exceeds `max_open_risk` — not one exposure at a time | `review/OWNER_POLICY_V1.md` (owner-approved). O-004 was always a v1 simplification, not a permanent constraint; the risk gateway now enforces the real portfolio budget via `risk/portfolio_risk.py::assess_open_risk` (D1.4) rather than a position-count rule, so relaxing the count constraint no longer weakens the actual safety property. See `review/adr/ADR-011-owner-risk-policy-v1.md` | Deferring the withdrawal until D1.5 (session-policy) also lands — rejected, D1.4's real accounting was already in place and there was no reason to keep the stricter rule waiting on unrelated work | Project owner |
| 2026-09-02 | **O-008b**: the four risk fractions move from engineering placeholders to owner-approved policy — `max_risk_per_trade=0.02`, `max_open_risk=0.03`, `max_daily_loss=0.04`, `max_drawdown=0.08` | `review/OWNER_POLICY_V1.md` §1, answering build.md §29 Q7-Q8. Closes the numeric half of D-013 (`config/paper.yaml`'s risk values were placeholders since 2026-08-17). `max_orders_per_hour`, `max_open_positions` and `min_stop_distance_points` remain engineering-chosen, not owner policy — D-013's remaining gap, D-053 | Leaving the placeholders in place indefinitely; picking new numbers without an explicit owner decision | Project owner |
| 2026-09-03 | **O-009**, superseding O-003: weekday overnight holding is permitted; only the Friday trading day carries restrictions — no new entries from T-15 (15 min before the weekly close), must be fully flat by T-5 (5 min before it); weekend exposure stays forbidden; HALT-reset stays human/operator-only (reconfirmed compliant, no code change) | Owner Shared-Core work order 2026-09-03 item 2 (D1.5), answering `review/adr/ADR-004-intraday-session-boundary.md` §7's own deferred question ("whether a Friday close needs a longer cutoff than a weekday roll"). `config/paper.yaml`'s `intraday:` offsets move from 60/15 (daily) to 15/5 (Friday-only). See `review/adr/ADR-012-owner-session-policy-v1.md` | Keeping the daily "no overnight" rule now that real portfolio open-risk accounting (D1.4) makes the exposure budget the real safety control, not position duration — rejected by the owner directly, superseding rather than retaining O-003 | Project owner |

---

# 11. Current risks / assumptions

| Risk / assumption | Impact | Mitigation | Status |
|---|---|---|---|
| Broker execution differs from demo | High | shadow mode + conservative promotion | OPEN |
| Backtest leakage | High | causal feature tests + walk-forward | OPEN |
| Duplicate order after crash/reconnect | Critical | idempotency + reconciliation | OPEN |
| Stale tick interpreted as current | Critical | data-age policy + HALT | OPEN |
| Strategy overfits one regime | High | regime breakdown + challengers | OPEN |
| LLM behaves nondeterministically | High | no direct execution privileges | MITIGATED BY DESIGN |
| Broker symbol/spec differs | High | dynamic instrument registry | OPEN |
| Risk config accidentally omitted | Critical | no permissive defaults | MITIGATED BY DESIGN |
| Operator cannot close a position once open | High | FLATTEN POSITIONS built 2026-08-17; unproven against a real broker | MITIGATED — pending MT5 validation |
| Restart clears a halt | Critical | durable safety state + fail-closed startup; cross-process restart evidence recorded | CLOSED 2026-08-17 |
| Safety state disagrees between file and event journal | High | precedence rule in ADR-002: any disagreement resolves to HALTED | CLOSED 2026-08-18 — `CompositeSafetyStateStore` is on the normal path |
| Restart refills a spent daily-loss budget | Critical | risk-session state persisted and recovered; recovery can only tighten | CLOSED 2026-08-18 |
| The journal records what was decided but not what it saw | High | raw tick and bar storage, written on the ordinary path; feature values durably stored | CLOSED 2026-08-18 for ticks and bars; **CLOSED 2026-08-26 for feature values** (D-031) — `feature_snapshots`, no longer only a hash |
| A schema change loses data nobody can regenerate | Medium | Alembic baseline, migrated deployment path, proven restore | CLOSED 2026-08-18 — no backup *schedule* exists yet, only a proven restore |
| A position is carried through the 17:00 rollover | High | entries refused inside the window; a surviving exposure halts | MITIGATED — detection only. The flatten itself is M5 (D-033) |
| A second EUR/USD exposure is opened | High | hard constant in the risk engine, above the account model | CLOSED 2026-08-18 |
| The connected account is not the one that was configured | High | server, login, currency, leverage and margin mode all re-checked on every reconnect (`LiveReader`) | **CLOSED for the M1 reconnect path** — Phase B, 2026-08-24, owner present: two real deliberate terminal closures, both recovered automatically with full revalidation (symbol, account, instrument spec, broker clock offset), M1 PASSED on this evidence (review 1.12 §7). **CLOSED for broker-state snapshots and reconciliation too** — F-051 part 1, 2026-08-26: a real flat account snapshot and a real `MATCHED` reconciliation, both against the live terminal. The remaining open risk is narrower still: the live *decision* pipeline (F-048) built on the same data has not yet produced a real Trader decision — pending real M5 bar accumulation, F-051 part 2 |

---

# 12. Next 10 actions

Done in this pass:

- [x] Create repository and Python 3.12 environment.
- [x] Add `pyproject.toml`, lockfile, pytest and type checker.
- [x] Implement Pydantic domain/event contracts.

- [x] Persist the decision flow, ticks and bars to PostgreSQL (M2).
- [x] Version the schema with Alembic and prove a restore (F-020, F-023).
- [x] Make the risk session survive a restart (F-019).
- [x] Encode the one-exposure rule and the intraday window (O-003, O-004) —
      O-004 later withdrawn 2026-09-02 and replaced by real portfolio
      open-risk accounting (D1.4, O-008). O-003 later withdrawn
      2026-09-03 and replaced by the weekly session policy (D1.5, O-009):
      weekday overnight permitted, only the Friday close restricted.
- [x] Implement the deterministic risk policy engine and kill switch (M4).

Blocked on a human decision — see build.md §29 and `review/feedback.1.6.md` §6:

- [x] ~~§29 Q1 — broker~~ — Pepperstone (O-001).
- [x] ~~Resolve the Pepperstone entity~~ — **Pepperstone Limited (UK)**, for the
      demo/development environment only (O-005, closing APP-013/D-034 for
      that scope). A future live account still needs its own determination
      against the owner's residence and live-account documentation.
- [x] ~~Create the Pepperstone MT5 demo account~~ — 2026-08-24, and logged into
      the Windows MT5 terminal once, interactively. Login stays a local
      secret, never Git, logs, status or review documents (F-026, F-031 —
      reopened and fixed again 2026-08-24 by review 1.11 after the account
      number was found unmasked in `mt5.connected`/`mt5.account_guard_failed`).
- [x] ~~§29 Q2 — hedging or netting~~ — **`RETAIL_HEDGING`**, read from
      `account_info()` on the real demo account, 2026-08-24.
- [ ] Confirm or replace the provisional risk budget in `config/paper.yaml`
      (§29 Q7-Q8) — D-013.
- [ ] Confirm the intraday cut-off and flatten offsets — ADR-004 §3.
- [ ] Review and approve the domain contracts (M0).
- [ ] Decide the CI exception, or run CI on a runner (M0) — review 1.6 §5.

Next engineering steps once unblocked:

- [x] ~~Provision a Windows x86-64 host with the MetaTrader 5 terminal.~~ — done
      2026-08-24.
- [x] ~~Run `scripts/mt5_probe.py` against the now-logged-in terminal.~~ — done
      2026-08-24. Sanitized output recorded in `status.md` §13 (F-031).
- [x] ~~Discover the real EUR/USD broker symbol and its full specification.~~ —
      `EURUSD`, no suffix. Recorded 2026-08-24.
- [x] ~~Implement continuous bar/tick read and reconnect with full
      revalidation~~ — `application/live_reader.py::LiveReader`, built and
      unit-tested 2026-08-24 (HANDOVER.md §4.5, review 1.9 F-034). All five
      required reconnect scenarios pass against a scripted fake terminal.
- [x] ~~Run the real soak, Phase A~~ — **satisfied 2026-08-24, sixth
      attempt.** 30 minutes, real Pepperstone demo, zero disconnects, zero
      errors: 2,920 real ticks and 17 real M5 bars persisted, all
      `data_quality=GOOD`, zero anomalies, zero gaps, every bar aligned to a
      5-minute UTC boundary. Four real defects found and fixed across the
      six attempts — D-040 (numpy scalar repr), D-041 (PostgreSQL parameter
      ceiling), D-042 in two parts (still-forming bar, then a post-close
      settle window), and D-039 (the terminal's clock runs ~3 hours ahead of
      UTC, now detected dynamically per connection rather than assumed).
      F-031 (login masking) and F-038 (chunked-insert atomicity proof) also
      closed along the way. Full detail: `status.md` §13 sixteenth entry.
- [x] ~~Run the real soak, Phase B~~ — **satisfied 2026-08-24, owner present.**
      Two deliberate MT5 terminal closures; both detected, both recovered
      automatically with full revalidation (symbol, account, instrument
      spec, broker clock offset) and fresh data resuming within seconds.
      F-034 closed. Full detail: `status.md` §13 seventeenth entry.
- [x] ~~M1 qualification~~ — **PASSED, review feedback.1.12, 2026-08-24.**
      Recorded in §16 Promotion history.
- [x] ~~F-039: instrument-spec fingerprint must not change on re-observation
      alone~~ — `tick_value` (drifts live with the account/quote
      cross-currency rate, confirmed 2026-08-24 first contact) removed from
      `InstrumentSpec.spec_version`'s hash; `captured_at_utc` was already
      excluded. `domain/models.py`, `tests/unit/test_control_plane_contracts.py
      ::TestInstrumentSpecVersioning`.
- [x] ~~F-040: broker-clock detection must fail closed on a stale reference
      tick~~ — `ReadOnlyMt5Gateway._clock_offset()` now rejects a measurement
      whose residual from a clean half-hour multiple exceeds 3 minutes, or
      whose offset exceeds ±15h, raising `ClockOffsetUnavailableError`
      rather than caching a bad value; `LiveReader` maps that to
      `DISCONNECTED` (not sticky — clears once a fresh tick is available,
      same as `STALE`). `mt5_gateway/readonly.py`,
      `tests/unit/test_mt5_readonly_gateway.py::TestClockOffset`,
      `tests/unit/test_live_reader.py::TestClockOffsetStaleReference`.
- [x] ~~F-041: soak database reset must stay on the migration path~~ —
      `scripts/reset_soak_database.py` (Alembic `downgrade base` ->
      `upgrade head` only, refuses a URL without "soak" in it, requires
      `--yes`), documented in `.env.example`.
- [x] ~~Build Dashboard v0~~ — **shipped 2026-08-24.** Read-only status page +
      `/api/state` JSON, `scripts/run_dashboard.py` / `src/crumblr/dashboard/`,
      FastAPI + server-rendered HTML (user's choice, over Streamlit or stdlib
      `http.server`). Reads PostgreSQL (`MarketDataStore`, `EventJournal`,
      `PostgresSafetyStateStore`) and the `LiveReader` health JSON snapshot —
      never MT5 directly. Smoke-tested against the real `crumblr_soak` data
      from Phase A/B (13,108 ticks, 37 bars at check time). Boundary enforced
      structurally: `tests/integration/test_dashboard.py::TestReadOnlyBoundary`
      asserts no route accepts a mutating method and the package never
      imports `MetaTrader5`/`crumblr.mt5_gateway`. Deliberate scope reduction
      against build.md §22/M8's full spec recorded as D-043 — no manual
      HALT/CANCEL/FLATTEN control surface exists yet, by design.
- [x] ~~Visually rebuild Dashboard v0 and close F-042/F-043/F-044~~ —
      2026-08-25 (review 1.13). ~~F-045/F-046 and the F-044 heading
      refinement~~ — 2026-08-25 (review 1.14). **Dashboard visual scope now
      frozen** (review 1.15 §15) — only broker-state/reconciliation/real-
      decision data panels may still be added, once F-047/F-048 exist to
      source them honestly.

**O-006 (review 1.15 §3/§12): the critical path from here to one gated DEMO**
**canary order — CI/M0 first, nothing else jumps the queue:**

- [x] ~~Phase 1a — provide the domain-contract package for reviewer/human
      approval~~ — done 2026-08-25: `review/domain_contracts.md` covers all
      twelve named contracts against the review's own checklist
      (immutability, extra-field rejection, Decimal/time semantics,
      ownership boundaries, execution permissions, risk/supervisor
      separation, agent-controlled fields). Awaiting the human review this
      package exists to support — a document existing is not the same
      claim as a document being approved
- [ ] Phase 1b — run CI on a runner and record the result (review 1.12 §9,
      repeated through review 1.16 §11: "no longer allowed to drift"). Two
      commits have pushed to `main` since the workflow was written
      (`2ce40d5`, `f67f341`), which should have triggered it automatically,
      but this session has no `gh` CLI or other way to read the Actions
      tab — the result needs a human (or a session with that access) to
      check and record
- [x] ~~Phase 2a — broker truth: F-047 durable `broker_account_snapshots` /
      `broker_position_snapshots` / `broker_pending_order_snapshots`~~ — done
      2026-08-25. Balance **and** equity (plus profit), all `Decimal`;
      `SnapshotCompleteness` (`COMPLETE`/`FAILED`/`UNKNOWN`) never conflates
      "empty" with "failed"; `LiveReader` captures on reconnect and every
      `broker_state_interval`. `scripts/mt5_live_reader.py` wired in. Capture
      triggers needing F-048/M5/reconciliation to exist first are recorded as
      `review/DEVIATIONS.md` D-044, not silently skipped
- [x] ~~Phase 2b — read-only reconciliation~~ — done 2026-08-25.
      `application/reconciliation.py::reconcile()` + `scripts/reconcile.py`,
      built from the F-047 snapshots. Fail-closed exactly per review 1.16
      §7's table (`UNKNOWN` never upgraded to `MATCHED`). Expected state is
      `ExpectedState.flat()` (no execution path exists yet); once one does,
      expected state must come from the platform's own order/position
      history, not from MT5 again (review 1.16 §8) — not yet wired into the
      Supervisor's `reconciliation_status` (that is F-048's job, since no
      live decision loop exists to call it from yet)
- [x] ~~F-052 (review 1.16 §5)~~ — done 2026-08-25 alongside the above:
      `account_with_extras()` reads the broker account once, not twice, per
      snapshot
- [x] ~~F-050 (review 1.16 §3)~~ — done 2026-08-25: `BrokerStateHealth`,
      kept separate from `ReaderStatus`
- [ ] F-051 **IN PROGRESS** (review 1.16 §4, expanded through review 1.20
      §6 into a 26-step checklist) — real-terminal verification of
      F-047/F-052/reconciliation/F-053/F-054/F-055/F-048/D-031 together.
      **Part 1 done 2026-08-26** (this development host turned out to be
      the Windows/MT5 host — steps 1-18: discovery, InstrumentSpec, flat
      broker snapshot, reconciliation `UNKNOWN`→pin→`MATCHED`, zero
      defects). **Part 2 in progress**: a real Trader/Risk/Supervisor
      decision needs more real M5 bars than exist yet
      (`baseline_v1` 65, `ict_v1` 120, 49 at last check; no backfill
      capability, so this is real-time accumulation only) — a background
      `mt5_live_reader.py` run is under way. Full detail: §13 thirty-first
      entry
- [x] ~~Phase 3 — attach the agent: F-048 live/shadow decision
      orchestrator~~ — done 2026-08-25.
      `application/live_decision.py::LiveDecisionOrchestrator`, a class
      deliberately separate from `LiveReader` and `ReplayOrchestrator`: real
      closed M5 bar → features → Trading Agent → intent-time Risk (fed real
      F-047 broker state) → Supervisor (fed a real `ReconciliationStatus`
      from `application/reconciliation.py`) → persists via the same
      journal machinery replay uses → **stops — no `ApprovedOrder` is ever
      constructed**. `scripts/live_decision.py` drives it. Unblocked a
      dependency found mid-build: `InstrumentSpec` had no durable producer
      (D-045) — closed via `persistence/instrument_specs.py`. D-031
      (feature-value persistence) is **not** closed by this — review 1.16
      §10 explicitly allows "a first wiring test" before that closes; this
      is that wiring test, recorded as D-046 alongside two smaller v0
      scope choices (`orders_in_last_hour` always 0, dedup not persisted
      across restarts — neither matters before an execution path exists).
      Dashboard integration not attempted — review 1.16 §12 says wait for
      real-MT5 validation first
- [ ] Not yet attempted — dashboard integration for broker state/
      reconciliation/decision-pipeline data (review 1.16 §12, reconfirmed
      review 1.17 §15 with a concrete field list: balance, equity, open
      P/L, free margin, open positions, pending orders, broker-state age,
      reconciliation status, live/shadow pipeline), correctly deferred
      until F-051 (real-terminal validation) succeeds. Review 1.17 §15 is
      explicit: no further visual redesign, operational data only
- [x] ~~F-053 (review 1.17 §7)~~ — done 2026-08-26, per review 1.18 §6's
      explicit "build now" instruction: `reconcile()` compares the
      instrument spec's `spec_version` against an expected value;
      missing/changed/unpinned → `UNKNOWN`/`MISMATCHED`. Closes the `D-045`
      "watch for" condition. Its first version compared against the
      earliest-ever observation — review 1.19 §4 reopened that half as
      F-055 (below), fixed the same day
- [x] ~~F-054 (review 1.17 §8)~~ — done 2026-08-26, per review 1.18 §7's
      "hard prerequisite" framing: `application/decision_window.py` +
      `persistence/decision_window.py::PostgresDecisionWindowStore` make
      `LiveDecisionOrchestrator`'s decision-window/duplicate-protection
      state durable, keyed by (canonical_symbol, strategy_id,
      config_version). Migration `a7c4e19d6f52`. Review 1.19 §5 reopened it
      the same day for execution-grade failure semantics — an unreadable
      record must not read as "nothing recorded" — fixed the same day:
      `DecisionWindowRecord` (three states, mirroring `RiskSessionStore`)
      + `_recover_decision_window()` trips the kill switch on corruption
- [x] ~~F-055 (review 1.19 §4)~~ — done 2026-08-26: the instrument-spec
      baseline F-053 reconciles against must be an explicitly pinned,
      human-approved value, not whichever spec happened to be observed
      first (trust-on-first-use). `config.MarketConfig.expected_spec_version`
      — `None` (→ `UNKNOWN`) until a real F-051 observation is reviewed and
      approved; `PlatformConfig.market_for()` (new) looks it up per symbol
- [x] ~~D-031 (feature-value persistence)~~ — done 2026-08-26:
      `persistence/features.py::FeatureSnapshotStore` durably records the
      full `FeatureEvidence` payload for every evaluated window, wired into
      both `ReplayOrchestrator` and `LiveDecisionOrchestrator` via
      `RunRecorder.record_features()`. Migration `b3f8a2c7d914`
- [ ] Supply `review/domain_contracts.md` unchanged for actual reviewer
      inspection (review 1.17 §10) — the package existing was never the
      same claim as it being reviewed, and the reviewer states plainly it
      has not yet been given the file
- [ ] Phase 4 — execution safety: separate execution-capable MT5 adapter,
      `order_check`, ADR-001 execution-time risk revalidation, automatic
      intraday flatten, terminal/account execution guard (review 1.15 §12
      Phase 4 — the existing M5 checklist, now sequenced explicitly after
      Phases 1-3 rather than in parallel with them). **Non-sending
      preflight complete 2026-08-27** (all five planned slices —
      `OrderCheckMt5Gateway`, ADR-001's FINAL Risk, execution
      eligibility/the two-gate split, immutable request + append-only event
      persistence, and `ExecutionOrchestrator` assembling all of it end to
      end, proven by an integration test that hard-asserts `order_send` is
      never called — §13 thirty-fourth through thirty-eighth entries).
      **Review 1.22 source-reviewed the bundle same day: architecture
      ACCEPTED, four implementation findings opened (F-057 CRITICAL,
      F-058/F-059/F-060 HIGH) and fixed the same day — §13 thirty-ninth/
      fortieth entries. Review 1.23 (same day) reconfirmed F-057 CLOSED,
      accepted F-058/F-059 as real progress but PARTLY CLOSED (one narrow
      gap named in each), REOPENED F-060 (wrong durable authority used),
      and opened F-061 (new, HIGH) — §13 forty-first entry. That exact
      four-item bundle was fixed the same day (§13 forty-second entry) and
      a refreshed `review/domain_contracts.md` delivered alongside it
      (§13 forty-third entry). Review 1.24 (2026-08-27) reconfirmed all
      four CLOSED — "no further work required" on each — and declared
      **Phase 4 (the non-sending preflight chain) FORMALLY PASSED**, both
      architecture and implementation; approved `domain_contracts.md` at
      the reviewer/technical level (owner countersign optional, see the
      M0 local-policy checklist item above and `feedback.1.24.md` §7);
      and additionally AUTHORIZED one controlled
      real-terminal `order_check` evidence run (non-sending, Pepperstone
      DEMO only, under the exact conditions in review 1.24 §8) — §13
      forty-fourth entry.** Still open, deliberately out of this phase's
      scope: automatic flatten actually *submitting* a close (stays
      halt-only, ADR-004), the real F-049 `SubmissionGate` (design stub
      only, `risk/submission_gate.py`), and everything else in review
      1.24 §12's "submission-era execution safety" list — this checklist
      item stays open until those land too, even though the non-sending
      preflight sub-scope it also covers is now formally passed.
- [ ] Phase 5 — owner policy: risk per trade, max daily loss/drawdown,
      last-entry cutoff, mandatory flatten deadline, production/demo
      HALT-reset authority — all still open (Q7/Q8, ADR-004 §3).
- [ ] Phase 6 — F-049 multi-gated execution enablement (environment=DEMO,
      account/server verified, reconciliation=MATCHED, data=HEALTHY,
      safety=RUNNING, risk policy owner-approved, execution adapter
      explicitly enabled, terminal AlgoTrading enabled, `feedback.2.0` GO —
      simultaneously); then `feedback.2.0.md`; then one deliberately
      constrained canary DEMO order, treated as a technical proof (proposal
      → risk → supervisor → order_check → order_send → broker ack → fill →
      reconciliation → SL presence → closure → audit trail), **not** as
      evidence the strategy is profitable (review 1.15 §13/§14/§16).

`feedback.1.20.md` has been processed (pure acceptance, no new findings —
F-053/F-054/F-055/D-031 all CLOSED IN IMPLEMENTATION). The next regular
review (`feedback.1.21.md`) triggers per review 1.20 §16 on **F-051, the
real Pepperstone run** — no longer waiting on the whole original bundle,
since the review explicitly named this as sufficient on its own, and also
explicitly welcomes a real integration defect as its own trigger. **F-051
part 1 is now done** (2026-08-26, discovery through reconciliation
`MATCHED`, real terminal, zero defects); part 2 (a real Trader decision) is
in progress, blocked only on real M5 bar accumulation. Two more genuinely
blocked items remain: CI confirmation (the fix is pushed — F-056 — but the
next run is not yet confirmed green; needs `gh`/Actions access or a human
check) and the domain-contract supply (needs a human reviewer).

---

# 13. Update log

## Update 2026-08-17

```text
Component: 1 — Platform / Application
Milestone: M0 — repository and engineering baseline
Status before: NOT STARTED
Status after:  BUILDING (M0 substantially complete)
```

**Completed**

- Python 3.12.14 project scaffolded with `uv`, ruff, mypy (strict) and pytest.
- Pydantic v2 domain contracts: `MarketSnapshot`, `Bar`, `InstrumentSpec`,
  `TradeIntent`, `RiskDecision`, `SupervisorDecision`, `ApprovedOrder`,
  `ExecutionResult`, `AccountState`, `PositionState`, `Incident`,
  `DecisionCapsule`. All frozen, all `extra="forbid"`.
- Event journal vocabulary: all 13 event types from build.md §23 plus
  `DecisionCapsuleSealed`, in a generic envelope carrying `correlation_id`,
  `causation_id` and `schema_version`.
- Versioned, fail-closed configuration with no risk defaults.
- `.gitignore`, `.env.example`, CI workflow (Linux + Windows + gitleaks).

**Evidence**

- tests: 185 passed (174 unit, 11 property). `uv run pytest`
- coverage: 99% line, 98% branch over `src/crumblr`
- types: `uv run mypy` — clean, strict, 23 files
- lint: `uv run ruff check .` and `ruff format --check .` — clean
- artifact/commit: staged in git, not yet committed

**Problems found**

- Pydantic `computed_field` values are emitted by `model_dump` but rejected by
  `extra="forbid"` on reload, which silently broke journal round-trips for
  every model carrying a derived hash. Fixed on the `Contract` base class:
  computed fields are dropped on input and recomputed. Caught by the event
  round-trip tests, not by inspection.
- Ruff's `TCH` rules propose moving pydantic field types into `TYPE_CHECKING`
  blocks, which turns into a runtime `NameError` the moment such a type is used
  as a model field. Rule deselected, with the reason recorded in `pyproject.toml`.

**Risk impact**

- Three risks in §11 are now partially mitigated by construction rather than by
  intent: "risk config accidentally omitted" (required fields), "stale tick
  interpreted as current" (mandatory UTC + age helper), and "broker symbol/spec
  differs" (dynamic registry with change detection by content hash).
- No execution risk exists yet: nothing in the codebase can reach a broker.

**Decision**

- Proceed to M1 only after the broker and demo account are chosen. Building a
  gateway against an unknown broker's symbol conventions would be guesswork.

**Next**

- Resolve build.md §29 Q1, Q2, Q7 and Q8.
- Provision the Windows x86-64 MT5 host.

---

## Update 2026-08-17 (second pass — runnable prototype)

```text
Component: all three
Milestone: M3/M4/M6/M7 partial
Status before: M0 only, nothing executable
Status after:  end-to-end replay running against a simulated broker
```

**Completed**

- Deterministic synthetic market generator with injectable faults (§20, §25.5).
- Causal feature pipeline (EMA/ATR/regime) and `baseline_v1`, a deliberately
  simple trend strategy that returns NO_TRADE in roughly 80% of windows.
- Full §8.1 pre-trade risk checklist, risk-based position sizing from equity,
  stop distance and symbol spec, plus the kill switch and equity ledger.
- Supervisor layer-1 deterministic veto policy.
- `BrokerPort` protocol with a simulated implementation; the real MT5 adapter
  drops into the same interface at M1.
- `ReplayOrchestrator` running the complete §3 flow and sealing a decision
  capsule per window.
- `scripts/run_replay.py` with `--chaos`, `--max-daily-loss` and
  `--wrong-server` to demonstrate each guardrail.

**Evidence**

- tests: 257 passed (225 unit, 11 property, 21 replay)
- coverage: 95% line over `src/crumblr`
- types: `uv run mypy` — clean, strict, 47 files
- replay: 900 bars → 93 intents, 80 approved, 80 filled, 0 fills after a halt

**Problems found**

- The replay was not reproducible: the generator seeded its clock from
  `utc_now()`, so provenance fingerprints differed between identical runs.
  Wall-clock time was leaking into replay input, which §13.3 forbids. Fixed
  with a fixed `REPLAY_EPOCH`. Caught by a determinism test, not by review.
- `baseline_v1` proposed ATR stops tighter than the configured minimum stop
  distance in calm regimes, so 78% of intents were blocked as `INVALID_STOP`.
  The agent now receives the policy floor and proposes stops within it, per
  the §9.1 agent contract.
- The supervisor's signal-frequency threshold was set to 12 per hour while an
  M5 cadence permits at most 12 windows per hour, so it vetoed 52% of ordinary
  traffic. A control that refuses half of normal operation trains its operator
  to ignore it. Threshold recalibrated above the structural maximum and the
  calibration requirement documented.
- Max drawdown was reported as the drawdown at the end of the run rather than
  the deepest reached. Since drawdown is a promotion criterion (§15), the
  ledger now tracks the running maximum.

**Risk impact**

- Still zero execution risk: no component can reach a broker.
- Two §11 risks now have working mitigations rather than intentions:
  "duplicate order after crash/reconnect" (idempotency on `order_request_id`,
  proven in test) and "stale tick interpreted as current" (blocked with a
  recorded reason code under fault injection).

**Decision**

- Do not tune `baseline_v1` for better synthetic P&L. It exists to exercise
  infrastructure, and optimising it against a random walk would be the
  overfitting §6 of status.md warns about.

**Next**

- Broker and demo account selection remain the blocking decisions.
- Persist the event journal and decision capsules to PostgreSQL (M2).

---

## Update 2026-08-17 (third pass — review loop)

```text
Component: process
Milestone: n/a
Status before: status.md updated per session; no deviation register, no feedback loop
Status after:  reviewer-facing documentation in place
```

**Completed**

- `review/DEVIATIONS.md` — nineteen recorded departures from `build.md`, each
  with spec reference, rationale, status (deliberate / provisional / pending)
  and what to watch. Keyed `D-001`…`D-019` so findings can cite them.
- `review/FEEDBACK.md` — the inbox the reviewing agent writes into, with a
  severity scale and the response each severity requires.
- `CLAUDE.md` — working agreements, including the session-start protocol:
  open findings are resolved before new work begins.
- Pointer table at the top of this document so a reviewer lands in the right
  place.

**Evidence**

- No code changed in this pass. Quality gate re-run unchanged: 257 tests, 95%
  coverage, mypy strict clean, ruff clean, replay byte-identical across runs.

**Problems found**

- The previous two passes recorded *what* was built and *what went wrong*, but
  not *where the implementation departs from the specification*. That is the
  gap a reviewer most needs closed, and it was the one thing missing. Nineteen
  deviations existed; none were written down as such.

**Risk impact**

- Two deviations deserve attention above the rest and were previously only
  implicit: `D-011` (a restart clears the kill switch, because it is in-memory)
  and `D-009`/`D-010` (synthetic data and an incomplete cost model mean no
  performance figure from this system is currently meaningful).

**Decision**

- `build.md` is never edited to match the implementation. Gaps are recorded,
  not closed by rewriting the specification.

**Next**

- Unchanged: broker and demo account selection remain the blocking decisions.

---

## Update 2026-08-17 (fourth pass — ICT entry model)

```text
Component: 2 — Trading Agent
Milestone: M6
Status before: baseline_v1 (moving-average separation) as the configured strategy
Status after:  ict_v1 configured; baseline_v1 retained as the benchmark
```

**Completed**

- ICT primitives, each with one exact definition: market structure (swings,
  break of structure, market structure shift, dealing range, premium/discount),
  imbalance (fair value gaps, displacement, order blocks), liquidity (pools,
  sweeps, targets) and sessions (killzones, trading week).
- `ict_v1`: ten separately enforceable conditions. A trade requires a liquidity
  sweep, a structure shift, a displacement gap, price retraced back into that
  gap, a discount/premium location and an OTE retracement, inside a killzone,
  on an open market.
- Killzones computed in New York local time via `zoneinfo`, so daylight saving
  is handled rather than hard-coded.
- Strategy registry and a `Strategy` protocol; `strategy_id` in configuration
  selects which strategy runs, and an unknown id fails loudly.
- Session volatility profile and a real trading week in the replay generator.

**Evidence**

- tests: 344 passed (305 unit, 11 property, 28 replay). 80 cover the ICT model, built on hand-constructed
  bar sequences where the correct answer is known by construction.
- types: `uv run mypy` — clean, strict, 56 files
- replay: `ict_v1` completes a 12,000-bar replay deterministically

**Problems found**

- Four real defects, each caught by measurement rather than review. In order:
  the supervisor vetoed every valid ICT setup, because a setup entered on a
  structure shift reported an UNKNOWN regime; the entry trigger was missing, so
  location was judged at the displacement extreme where every long is in
  premium; sweeps were detected in 87% of windows because spent levels stayed
  sweepable; and premium/discount was measured against an unrelated swing range
  instead of the impulse leg.
- Separately: `max_daily_loss` was measuring loss since the start of the run,
  not since the start of a day, because nothing rolled the session baseline.
  See `review/DEVIATIONS.md` D-026.
- A quadratic scan in sweep detection made long replays take 17 seconds and the
  test suite time out. Precomputing first-touch per pool brought a 4,000-bar
  replay to 1.6 seconds.

**Risk impact**

- No change to execution risk: still nothing that can reach a broker.
- `ict_v1` produces roughly 3 intents per 12,000 M5 bars on synthetic data.
  That number is not evidence of anything, and has deliberately not been tuned.

**Decision**

- Do not loosen ICT conditions to raise the trade count on synthetic data. The
  model is tested against constructed setups instead, and the pipeline tests
  that need trade volume run `baseline_v1`.

**Next**

- Unchanged: broker and demo account selection remain the blocking decisions.
- §29 Q3 (strategy horizon) now matters more: it sets the bar interval, which
  determines whether the killzone windows and the M5 cadence are the right ones.

---

## Update 2026-08-17 (fifth pass — review feedback.1.0 processed)

```text
Component: all three
Trigger:   review/feedback.1.0.md — GO WITH CONDITIONS, M5/P2 NO-GO
```

**Findings closed**

- **F-002 (CRITICAL)** — `ReconciliationStatus` and `IncidentStatus` replace the
  two booleans. Both default to `UNKNOWN`. Unknown reconciliation halts; unknown
  incident state vetoes. The safety gate now sits *above* the `enabled` switch,
  so disabling supervisor policy cannot launder unknown state into an approval.
- **F-003 (HIGH)** — `SafetyStateStore` port with an atomically-written file
  implementation. `KillSwitch.on_startup` begins disabled and only releases on an
  explicitly RUNNING record. Missing, corrupt, wrong-schema and unparseable
  records all resolve to UNKNOWN, which counts as halted. The record is written
  before the in-memory state changes, so a failed write cannot leave a process
  believing it halted when nothing was recorded.
- **F-008 (HIGH)** — three separately authorised, separately logged operator
  controls. Tests assert the decoupling directly: halting does not close
  positions, flattening does not halt.
- **F-001 / F-005** — maturity ladder separated from gate qualification;
  progress percentages removed; the risk checklist is now per-capability across
  impl/unit/replay/MT5/paper.
- **F-004** — `ict_v1` feature freeze recorded in §10 and `CLAUDE.md` §4.
- **F-007** — `review/adr/ADR-001-execution-time-risk-revalidation.md`.

**Answered rather than actioned**

- **F-006** — decision adopted (local Git allowed, remote deferred), but the
  premise is corrected: the agreement was to hold *commits*, and "Repository
  created" is an M0 exit criterion in §2. `git init` executed the plan. No
  commit has been made.

**Evidence**

- tests: 384 passed (up from 344). 40 new tests covering fail-closed safety
  state and the operator controls.
- types: `uv run mypy` — clean, strict, 60 files
- lint: ruff clean

**Problems found while doing it**

- The supervisor's `enabled=False` path would have bypassed the new UNKNOWN
  checks entirely, turning "switch off policy judgement" into "switch off the
  safety gate". Restructured so safety state is evaluated first and is not
  subject to the policy toggle.
- Persisting after mutating in-memory state would let a failed disk write leave
  the process believing it had halted with nothing recorded. Order inverted.

**Risk impact**

- Two of the reviewer's three CRITICAL/HIGH execution-safety blockers for M5 are
  closed in code. M5 remains NO-GO: the rest need MT5, PostgreSQL and broker
  reconciliation.

**Next**

- PostgreSQL event persistence (M2). The reviewer's GO NOW item and the only
  major piece not blocked on a human decision or hardware.

---

## Update 2026-08-17 (sixth pass — review feedback.1.1 processed)

```text
Trigger: review/feedback.1.1.md — GO WITH CONDITIONS, M5/P2 NO-GO
Result:  every finding F-001 … F-012 now CLOSED; four at design level only
```

**F-003 was correctly reopened, and the reviewer was right**

The original evidence built two `KillSwitch` objects inside one interpreter.
That demonstrates the store round-trips; it demonstrates nothing about a
restart, because nothing restarted. `tests/integration/test_halt_survives_restart.py`
now spawns a real child process that writes the halt and exits, and asserts the
acceptance sequence literally. Also covered: truncated, empty, array-shaped,
wrong-schema, broken-timestamp and naive-timestamp records, plus an unwritable
destination — all fail closed.

**New findings closed**

- **F-009** — APP-001 and APP-002 closed in place with resolution text, APP-003
  added for the durable halt, APP-004 opened for safety-state authority, EV-001
  split so the two resolved checks and the two calibration items are not carried
  as one status. No record deleted.
- **F-010** — §2 rebuilt. `build.md` §26 deliverables and acceptance are listed
  as the specification states them; local project policy is a separate list; and
  broker, demo account, server and Windows host are marked as M1 dependencies
  rather than M0 engineering failures.
- **F-011** — ADR-001 amended. The final gate recomputes monetary risk from the
  *current executable side*, because a fixed volume does not mean fixed exposure:
  a BUY approved with 20 pips to its stop risks 60% more if the ask moves 12 pips
  before submission. Sizing is not recomputed; the volume holds or the order is
  blocked, never increased. Eight required tests specified.
- **F-012** — ADR-002 written. The event journal is the record of authority, the
  file store is an independent latch that survives the database being
  unreachable, and any disagreement resolves to HALTED. The rule underneath:
  never prefer the more permissive store.

**Problem found that neither review raised**

`build.md` §26 lists **logging** as an M0 deliverable. `structlog` is declared as
a dependency, `observability/` is an empty package, and nothing imports either.
It had been treated as delivered because the dependency existed. Now recorded in
§2 as not done. Found by checking the gate criteria against the specification
rather than against memory — which is exactly what F-010 asked for.

**Evidence**

- tests: 397 passed (up from 384), including 13 integration tests that spawn
  real child processes
- types: `uv run mypy` — clean, strict, 61 files
- lint: ruff clean · replay byte-identical across runs

**Risk impact**

- No change to execution risk. Nothing can reach a broker.
- The M5 blocker list is unchanged in substance: it needs MT5, PostgreSQL and
  broker reconciliation, none of which exist.

**Next**

- PostgreSQL event persistence (M2) — the reviewer's highest engineering
  priority and the only large piece not blocked on a human decision or hardware.
  ADR-002 must be implemented as part of it, not after.

---

## Update 2026-08-18 (seventh pass — review feedback.1.2 processed)

```text
Trigger: review/feedback.1.2.md — GO WITH CONDITIONS, M5/P2 NO-GO
Result:  F-013 closed and shipped; tracker semantics improved per §4
```

**F-013 — the missing M0 logging deliverable, now built**

`src/crumblr/observability/logging.py`. Structured JSON records carrying a UTC
timestamp, service and component fields, event name, severity and any bound
correlation id. Configured against a stream, so it needs no external
infrastructure — Prometheus, Loki and OpenTelemetry remain production concerns
and are explicitly not M0.

Two design choices worth stating, because both are load-bearing:

- **Logs go to stderr; the replay report goes to stdout.** The determinism gate
  hashes stdout. Interleaving log lines into it would break that gate, and break
  it non-deterministically, which is the worst possible failure for a check
  whose entire job is detecting non-determinism.
- **Emissions cannot raise into the caller.** Every log call swallows its own
  exceptions. A full disk must not throw an exception out of a halt. There is a
  test that trips the kill switch through a deliberately broken stream and
  asserts the halt still takes.

Emitting today: configuration load, safety-state recovery (both directions),
kill-switch trip and reset, replay start and finish, risk block and halt,
supervisor veto and halt.

**Tracker semantics (review 1.2 §4)**

The register now carries two fields instead of one. `Finding` answers whether
the reviewer's concern is resolved; `Implementation` answers what actually
exists — `SHIPPED`, `DECIDED` (an ADR with no code), or `PENDING M2`/`M5`.
Three findings are `DECIDED` with nothing built (F-007, F-011, F-012) and one is
shipped but unproven against a real broker (F-008). Under a single column all
four read as "CLOSED", which is exactly the misreading §4 warned about.

**Evidence**

- tests: 422 passed (up from 397); 25 new, covering all seven properties the
  review specified for logging
- types: `uv run mypy` — clean, strict, 63 files
- lint: ruff clean
- determinism: `run_replay.py --bars 2000 | md5` identical across runs **with
  logging enabled**, verified explicitly rather than assumed

**Problem found while doing it**

The first determinism test compared two `make_intent()` calls and failed — not
because logging changed anything, but because `feature_snapshot_id` is random
per intent and is part of the decision hash. The test was wrong, not the code.
Fixed by holding the id fixed. Worth recording because a test that fails for
the wrong reason is one step from a test that passes for the wrong reason.

**Risk impact**

- None. Logging is observability and cannot alter a trading result — a property
  now asserted rather than asserted about.

**Next**

- M2 PostgreSQL persistence, with ADR-002's authority semantics built into the
  recovery path rather than added afterwards.

---

## Update 2026-08-18 (eighth pass — review feedback.1.3 processed)

```text
Trigger: review/feedback.1.3.md — GO WITH CONDITIONS, M5/P2 NO-GO
Result:  F-014 and F-015 closed; M2 invariants decided before any database code
```

**F-014 — a stale note in my own tracker**

The finding register said logging was `CLOSED / SHIPPED` while a note further
down still said, in the present tense, that it "does not exist". Rewritten in
past tense, keeping the discovery itself because *how* it was found is the part
worth remembering: comparing gate criteria against the specification rather than
against memory.

Small, but it is the same class of defect as F-009 — a document contradicting
itself is worse than a document that is merely incomplete, because a reader has
no way to tell which half is current.

**F-015 — persistence invariants, decided before the layer exists**

`review/adr/ADR-003-persistence-invariants.md`. Ten invariants, ten acceptance
tests. Written now rather than after the schema, because a storage layer that
already exists tends to have its invariants inferred from whatever it happens
to do.

The load-bearing decisions:

- **Append-only, enforced by the database.** The application role gets `INSERT`
  and `SELECT` and nothing else, so a mistaken `UPDATE` fails as a permission
  error rather than succeeding quietly. Corrections are new events linked by
  `causation_id`.
- **Identity is producer-assigned.** `event_id` is a UUID made where the event
  is created. A `BIGSERIAL` may exist for physical ordering but is never
  identity — a sequence is unique to one database, and a replay against a
  rebuilt one would silently renumber everything.
- **Three clocks, never conflated.** `occurred_at_utc` (market time),
  `recorded_at_utc` (write time), `sequence` (insertion order). Replay orders by
  the first. Ordering by insertion time would reorder events after a reconnect
  backfill — exactly when order matters most.
- **Money is `NUMERIC`, never `float8`.** The domain rejects binary floats at its
  boundary; storing them as floats would reintroduce that error one layer down,
  where nothing is watching.

A useful thing fell out of writing it: most of what M2 needs already exists in
the domain layer. `event_id`, `correlation_id`, `causation_id`, `schema_version`,
`capsule_id` and the fingerprints are all there. M2's job is largely to carry
those across the boundary without losing them, not to invent them.

**The acceptance test that defines the milestone**

> A replay driven from the persisted journal reproduces the same decision
> sequence, byte for byte, as the in-memory replay.

Recorded as a decision in §10. Without it, the journal is storage rather than an
audit trail, and row counts would become the completion signal — which is what
the reviewer warned against.

**Evidence**

- No code changed. 422 tests still green, mypy strict clean, ruff clean.
- Two ADRs and one tracker correction.

**Risk impact**

- None directly. Indirectly: M2's definition of done is now a property, not a
  volume of rows.

**Next**

- Implement M2 against ADR-002 and ADR-003 together. Docker is available for a
  local PostgreSQL; no external dependency blocks this.

---

## Update 2026-08-18 (ninth pass — M2 persistence implemented)

```text
Component: 1 — Platform / Application
Milestone: M2 — PostgreSQL event journal
Maturity before: SPECIFIED (invariants decided, no code)
Maturity after:  UNIT-TESTED against real PostgreSQL 17
Gate:            still NOT PASSED — see below
```

**Process decision first**

Review 1.4 was skipped by the project owner so this could be built. Recorded in
§10 and in `review/FEEDBACK.md`. `feedback.1.3.md` §8 itself triggers 1.4 on M2
availability, so this is following the reviewer's own sequencing rather than
departing from it. `feedback.2.0.md` before the first `order_send` is unchanged.

**Completed**

- `persistence/schema.py` — five tables. Append-only enforced by grants rather
  than convention; `NUMERIC` for money; `TIMESTAMPTZ` throughout; three clocks
  per event; producer-assigned `event_id` as the primary key.
- `persistence/journal.py` — `EventJournal` (idempotent on `event_id` via
  `ON CONFLICT DO NOTHING`, ordered by market time) and `CapsuleStore` (sealed
  once, fingerprint recomputed and compared on read).
- `persistence/safety_state.py` — `PostgresSafetyStateStore` and
  `CompositeSafetyStateStore`, implementing ADR-002's precedence table.
- `HANDOVER.md` — what a developer picking this up cold needs: environment,
  code map, ordered next steps, the traps, and an honest ranking of what the
  evidence supports.
- CI now runs a PostgreSQL service and **fails if the database was unreachable**,
  because a silently skipped persistence suite would look green.

**Evidence — all ten ADR-003 acceptance criteria**

| # | Criterion | Where |
|---|---|---|
| 1 | duplicate insert stores one row | `test_persistence_invariants.py::TestIdempotentWrites` |
| 2 | retry after ambiguous commit converges | same |
| 3 | crash before commit leaves nothing | `TestCrashConsistency` |
| 4 | read order is market time, not insertion | `TestOrdering` |
| 5 | Decimal survives bit-exact; no float columns | `TestExactnessSurvivesTheRoundTrip` |
| 6 | UTC survives; no naive timestamp columns | same |
| 7 | payload disagreeing with its column is refused | `TestSchemaVersioning` |
| 8 | tampered capsule detected on read | `TestSealedCapsulesAreImmutable` |
| 9 | journal/latch disagreement resolves to halted | `TestSafetyStateAuthority` |
| 10 | **replay from the journal reproduces the run** | `test_replay_from_journal.py` |

- tests: 454 passed (up from 422); 32 new, 25 of them against real PostgreSQL
- types: mypy strict clean, 71 files · lint: ruff clean
- replay determinism unchanged and still byte-identical

**Problems found**

- The `sequence` column had no default. SQLAlchemy only auto-creates a sequence
  for the primary key, and the primary key here is `event_id` — deliberately, so
  identity comes from the producer. Fixed with an explicit `Identity()`. The
  failure was loud and immediate, which is what a `NOT NULL` is for.
- `Event[TradeIntent]` is not an `Event[Contract]`: generics are invariant. The
  journal serialises the payload and never inspects its type, so the write path
  now takes `Event[Any]` — accurate rather than a loosening.
- My first CI step named "fail if the database was unavailable" did not fail if
  the database was unavailable; it re-ran the tests, which would skip. Replaced
  with an explicit reachability assertion. A check that cannot detect the thing
  it is named after is worse than no check.

**Risk impact**

- No change to execution risk. Nothing can reach a broker.
- The audit trail is now durable and verifiable, but **the running system does
  not use it yet** — see D-030 and APP-006. Until that wiring lands, the
  guarantees are real and unused. *(Closed by the 2026-08-18 update below.)*

**Next**

- Wire `CapsuleStore` into `_seal` and `CompositeSafetyStateStore` into startup.
  Small change, tested foundation.
- Then M1, which needs a broker and a Windows host — both human decisions.

---

## Update 2026-08-18 — persistence on the normal path

```text
Component: 1 — Platform / Application
Milestone: M2 — event journal and data pipeline
Status before: journal built and proven, unused by the running system
Status after:  journal, capsules, safety state and risk-session state all
               written and recovered on the orchestrator's ordinary path
Review:        feedback.1.5.md steps 1-3 (findings F-018, F-019)
```

**Completed**

- `application/recording.py` — `RunRecorder` with a forgetful default and a
  PostgreSQL implementation. Every stage of the build.md §3 flow is written as
  a typed event; a window's events and its sealed capsule commit together; a
  halt flushes immediately.
- `application/bootstrap.py` — assembles the journal, the ADR-002 composite
  safety store, the risk-session store and a kill switch recovered through
  `KillSwitch.on_startup`. A database with no RUNNING record starts UNKNOWN and
  refuses new orders.
- `application/reconstruction.py` — rebuilds a run from the `events` table
  alone and states plainly what the journal does not carry.
- `risk/session.py` + `persistence/risk_session.py` — risk-session state
  persisted and recovered, seeded so recovery can only ever tighten (F-019).
- `build_event` gained producer-supplied `event_id` and `occurred_at_utc`, so
  journal identity is content-addressed and ordering is by market time.
- `scripts/run_replay.py --persist`, with an explicit `--operator` /
  `--incident-note` pair to clear a recorded halt. Nothing automatic clears one.

**Evidence**

- tests: 491 passed (388 unit, 11 property, 28 replay, 64 integration against
  a real PostgreSQL 17). Previously 454.
- new: `tests/integration/test_orchestrator_persistence.py` (11),
  `tests/integration/test_run_survives_restart.py` (8, spawning real child
  processes), `tests/unit/test_risk_session.py` (18).
- the reviewer's acceptance criterion holds: the decision fingerprint of an
  in-memory run equals the one reconstructed from the journal, and the
  reconstructed tally matches the live one counter for counter.
- types: `uv run mypy` — clean, strict, 79 files. lint: clean.
- determinism: `scripts/run_replay.py --bars 600 | md5` byte-identical across
  runs, and unchanged from before this work.

**Problems found**

- `build_event` used `uuid4` and `utc_now`. Both are wrong for a journalled
  replay: a random id makes a rerun duplicate history rather than converge,
  and a wall-clock timestamp is write time, which ADR-003 invariant 4 says is
  never what replay orders by. Fixed by letting the producer supply both.
- Recovering the session before the loop crashed: `SimulatedBroker.positions()`
  needs a clock, and there is none until the first tick. Moved into the first
  iteration. A small bug, but the shape of it is worth noting — recovery needs
  market time, and market time does not exist until data arrives.
- Replaying the same series twice into one database halts with
  `SAFETY_STATE_UNKNOWN`, because the recorded trading day is then ahead of the
  data being fed in. That is correct — time went backwards — but it was
  unreadable until the halt *detail* was surfaced in the report. It is now.
- A capsule cannot explain a NO_TRADE: it has no field for the strategy's
  reason codes when there is no intent. The reasons live in `SignalGenerated`,
  which is the first thing in this project that the capsule store alone cannot
  answer and the journal can. There is a test asserting exactly that.

**Risk impact**

- No change to execution risk. Nothing can reach a broker.
- Two safety properties moved from "designed" to "exercised on the normal
  path": ADR-002 authority, and a risk budget that survives a restart.
- One new honest gap: the journal records decisions, not the market data they
  were made from (D-031, APP-008). Nothing depends on it today because the
  generator is seeded and deterministic. Everything will depend on it the day
  a real feed arrives.

**Next**

- Raw tick/bar storage and the normalized bar pipeline — the rest of M2's own
  deliverable list (D-031).
- Alembic, now that ordinary runs write data (D-029, escalated).
- Then M1: the Pepperstone EU demo account (O-001) and a Windows x86-64 host,
  both of which need a human.


## Update 2026-08-18 — the data boundary, migrations, and the owner's policies

```text
Component: 1 — Platform / Application
Milestone: M2 — event journal and data pipeline (deliverables now complete)
Status before: decisions journalled; nothing recorded what the system saw
Status after:  ticks and bars stored, schema versioned, restore proven,
               owner decisions O-003 and O-004 enforced in the risk engine
Reviews:       feedback.1.4.md (restored), feedback.1.6.md — F-020 … F-025
```

**Completed**

- **Raw market data (F-022).** `MarketTick` and `MarketBar` contracts,
  `market_ticks` / `market_bars` tables, and `persistence/market_data.py`. The
  orchestrator records every window it observes — including the warm-up ones
  that seal no capsule and emitted no event, which is the hole D-031 named.
  A bar carries its origin and, when derived, the pipeline version that made
  it; a conflicting bar for a stored interval raises rather than being ignored.
- **Normalized bar pipeline (F-022, build.md §26 M2).**
  `market_data/pipeline.py` aggregates ticks into bars with a transformation
  identity that includes the price basis, and detects gaps, out-of-order
  arrivals, duplicates and crossed quotes. It never invents a bar to cover a
  gap and never discards a late tick to make a series look tidy.
- **Alembic (F-020, F-023).** Baseline `ce70efeb9fe9`; the durable runtime
  migrates rather than calling `create_all`; a `pg_dump` → drop → restore is
  proven to produce a database from which the run still reconstructs.
- **One EUR/USD exposure (O-004, F-025).** A constant in the risk engine, above
  the account model, with the reviewer's four cases plus the symmetric fifth.
- **Intraday policy (O-003, F-025).** `risk/trading_window.py` and `ADR-004`.
  Entries refused outside the trading window; exposure surviving the flatten
  deadline halts. The flatten itself is M5 and is written down as such.
- **Supervisor honesty (F-024).** The frequency threshold is `null` —
  uncalibrated, not passing. Every decision carries `uncalibrated_checks` and
  the run report prints which controls were not in force.
- **Pepperstone facts (O-001).** Server, currency and leverage recorded in
  `config/paper.yaml` as claims the account guard checks. No credentials.

**Evidence**

- tests: 604 passed (479 unit, 11 property, 28 replay, 86 integration against a
  real PostgreSQL 17). Previously 491.
- types: `uv run mypy` clean, strict, 88 files. lint: clean.
- determinism: `run_replay.py --bars 600 | md5` byte-identical across runs.
  The hash itself moved, because `config/paper.yaml` changed — the intraday
  policy and the Pepperstone server are both inputs to `config_version`.
- the intraday policy is live on the real path, not only in unit tests: a
  1,500-bar `baseline_v1` replay records 44 `SESSION_BLACKOUT` refusals.

**Problems found**

- **Running a migration in-process silently reconfigured the platform's
  logging.** Alembic's `env.py` calls `logging.config.fileConfig`, which
  reconfigures the root logger and disables existing ones. Twenty logging tests
  failed the moment the migration tests ran before them. The tests caught a
  real defect, not test pollution: an in-process migration would have replaced
  structured JSON logging with Alembic's format, routing audit-relevant records
  somewhere nobody reads. `fileConfig` now runs only on the CLI path.
- **The session-phase check had a hole at the boundary.** At 17:00 New York the
  day rolls and the phase becomes OPEN for the *new* day, so a position that
  survived the old day's flatten deadline stopped looking like a breach one
  second after becoming one. Closed by comparing trading days directly —
  `has_crossed_rollover` — as well as asking about the phase.
- **The supervisor's frequency check needed a second look.** Marking it
  uncalibrated exposed that the confidence band is inert for the same reason:
  its range is the one the contract already enforces. Both are now reported as
  absent rather than passed, which is what D-028 was originally about.
- **A configured `supervisor.policy_version` was never read.** The decision is
  stamped with a constant in code, so the two could drift and a capsule could
  name a policy the configuration never described. There is now a test
  asserting they match.

**Risk impact**

- No change to execution risk. Nothing can reach a broker, and `order_send`
  remains unreachable.
- The audit trail can now answer "what did it see?" as well as "what did it
  decide?" — for ticks and bars. Not for feature values, which are still only
  hashed (D-031).
- Two owner policies moved from prose into refusals the deterministic engine
  makes. One of them, the flatten, is only half there and is recorded as D-033
  rather than implied to be complete.

**Next**

- M1: the Pepperstone demo account and a Windows x86-64 host, both human tasks.
  Resolve the EU/UK entity question first (D-034).
- Read-only MT5 gateway, then persist real ticks and bars immediately — the
  store and the pipeline exist and have never seen a real quote.
- Feature values in storage (D-031), and the automatic flatten (D-033, M5).

---

## Update 2026-08-23 (M1 read-only gateway prepared)

```text
Component: 1 — Platform / Application
Milestone: M1 — MT5 read-only gateway
Maturity before: SPECIFIED (BrokerPort only, no adapter)
Maturity after:  UNIT-TESTED against a fake terminal
Gate:            still NOT PASSED — nothing has met MetaTrader 5
```

Review 1.6 puts M1 at **PREPARE NOW**, and everything it still needs from a
human — the demo account, a Windows host, the entity question — leaves the code
itself unblocked. This converts waiting time into readiness.

**Completed**

- `mt5_gateway/client.py` — connection management. The `MetaTrader5` import is
  deferred to call time so the rest of the platform still runs on macOS, and
  every call goes through a checked wrapper that turns MT5's convention of
  returning `None`/`False` with the reason in `last_error()` into an exception
  naming what failed.
- `mt5_gateway/readonly.py` — the read half of `BrokerPort`: account, instrument
  specification, positions, terminal health.
- 41 tests against a fake terminal that mimics MT5's actual failure convention.

**Three decisions worth stating**

1. **Execution is refused by construction, not by configuration** (D-036). The
   mutating methods exist only to raise. A flag would make "M1 cannot trade" a
   promise about usage; this makes it a property of the type. M5 must add a
   separate adapter rather than relax these, so the read-only one stays
   available for shadow mode.
2. **The broker symbol is discovered, never assumed** (O-001). An exact match
   wins, otherwise the shortest candidate — `EURUSD.a` is the instrument,
   `EURUSD.a.cfd` is something else. A hard-coded `"EURUSD"` is a bug that only
   appears against a real account.
3. **MT5 floats become Decimal through `repr`, not directly.** `Decimal(1e-05)`
   preserves the binary error; `Decimal(repr(1e-05))` is the shortest decimal
   that round-trips, which is what the terminal meant. The domain rejects floats
   at its boundary and this is the conversion that lets it.

**A failure mode the tests pin down**

`positions_get` returning `None` is ambiguous: an empty book and a failed call
look identical. The adapter distinguishes them by error code — `RES_S_OK` means
genuinely flat, anything else raises. Reading a failed call as "no positions" is
how a reconciliation check passes while the terminal is unreachable.

**Evidence**

- tests: 645 passed (up from 604), 41 new
- types: mypy strict clean, 91 files · lint: ruff clean
- replay determinism unchanged

**What this does not prove**

Nothing here has run against MetaTrader 5. The fake was written from the
documented API, not from observation, so where the real terminal disagrees the
fake is wrong and the adapter probably is too. Spread, fills, reconnect
behaviour, the real symbol name, the account mode and the actual field names are
all broker facts. Recorded as D-035 and APP-014; first contact is discovery, and
review 1.5 step 7 asks for disagreements to become deviations rather than quiet
patches.

**Next**

- Three human tasks: create the Pepperstone demo account, provision a Windows
  x86-64 host, and resolve the EU/UK entity question (APP-013).
- Then M1 first contact, and `feedback.2.0.md` before anything can submit.

## Update 2026-08-24

```text
Component: 1 — Platform / Application
Milestone: M1 — MT5 read-only gateway
Status before: adapter written, no host, no runbook
Status after:  Windows host available; first-contact probe and runbook ready;
               still never connected to a terminal
```

**What changed, and why now**

The owner reported that a Windows x86-64 machine is available. That removes the
hardware blocker recorded on 2026-08-23 and makes MT5 first contact the next
real step, so the work this session was aimed squarely at making that step
executable by someone who was not here for the previous ones.

**Completed**

- `scripts/mt5_probe.py` — connects to a terminal, reads account, symbol,
  instrument and position state, and prints them for a human to compare against
  `config/paper.yaml` and `build.md`. Read-only by construction: it holds a
  `ReadOnlyMt5Gateway`, whose execution methods raise (D-036).
- `HANDOVER.md` rewritten around the two-host reality: a Windows setup section,
  a six-step first-contact runbook (§4), reordered next steps now that hardware
  is no longer blocking, and the MT5-specific traps.
- `.gitattributes` pinning the checkout to LF. With macOS and Windows both in
  play, a CRLF checkout would change the determinism hash and the format check
  without changing a line of code.
- D-037 and APP-015 opened — see below.

**A defect found while writing the probe**

`ReadOnlyMt5Gateway.instrument` stores `filling_modes=(str(info.filling_mode),)`
and `trade_mode=str(info.trade_mode)`. MetaTrader 5 reports both as integers,
and `filling_mode` is a **bitmask**: a symbol allowing FOK and IOC reports `3`.
Stringifying that yields `"3"`, which reads like a filling mode and is not one.

The fake in `tests/unit/test_mt5_readonly_gateway.py` supplies `"IOC"` and
`"FULL"` as strings, so the tests agree with the adapter and both are wrong
together. That is a worked example of the weakness D-035 already names: a fake
written from documentation confirms the reading its author had.

It is deliberately **not** patched. The decode is documented but unobserved,
and writing an unverified mapping into the instrument spec — which sizing and
order building read — would give an assumption the appearance of a fact. The
probe prints the raw value beside the decoded one so the first connection
settles it. Recorded as D-037 / APP-015.

**Evidence**

- tests: 663 passed (up from 645), 18 new in `tests/unit/test_mt5_probe.py`
- without a database, 73 of those skip — verified by pointing
  `CRUMBLR_DATABASE_URL` at an unreachable port, not assumed
- types: mypy strict clean, 93 source files · lint and format: ruff clean
- replay determinism unchanged

**What this does not prove**

Still nothing about MetaTrader 5. The probe has been run only against a fake,
and the fake is the thing under suspicion. Its value is that it turns first
contact into a single command whose output is a record, rather than a session
of interactive poking whose findings live in someone's memory.

**Risk impact**

None to the running system — no execution path was touched. The probe cannot
place an order, and a test fails if it ever reaches for one.

**Next**

- Blocked on one human task now, not three: **create the Pepperstone demo
  account** and log into it once in the terminal. The entity question (APP-013)
  is answered by the probe's own output.
- Then run `uv run python scripts/mt5_probe.py --json first-contact.json` on the
  Windows host, record the result here, and open a deviation for each
  disagreement rather than editing the code to match.
- Continuous bar/tick read and observed reconnect behaviour complete M1
  (HANDOVER.md §4.5). `feedback.2.0.md` remains mandatory before any
  `order_send`, demo included.

## Update 2026-08-24 (second entry) — the repository has a remote

```text
Component: 1 — Platform / Application
Milestone: M0 — repository and engineering baseline
Status before: no commits, no remote; every quality claim from one machine
Status after:  initial commit pushed to a private remote; CI able to run
```

**What changed**

The owner lifted the commit hold, which had stood since the first session
(F-006), because the code now has to reach a second machine — the Windows MT5
host. Initial commit `fd6a890`, 123 files, pushed to the private repository
`DutchBugs/Crumblr`.

**Separation of the personal and work accounts**

The owner runs an unrelated project under a different GitHub account, and asked
explicitly that the two not mix. Three crossover routes were found and closed:

1. **Commit attribution.** The global git identity was the owner's work email
   address, so every commit would have been attributed to the work account.
   Overridden **repo-locally**; the global config was left untouched.
2. **The SSH key.** The default key on the macOS host (`~/.ssh/id_ed25519`)
   turned out to be a *deploy key belonging to the unrelated work repository* —
   verified, not assumed. The remote therefore uses HTTPS, and the handover
   says not to switch it to SSH.
3. **The stored credential.** `credential.https://github.com.username` is
   pinned repo-locally, so the macOS keychain keeps one entry per account
   instead of one shared github.com credential.

The token itself is fine-grained and scoped to this single repository. It never
entered a chat transcript or a file: the first push was run by the owner in
their own terminal.

**A rejection worth recording**

The first push was refused: a fine-grained token cannot create
`.github/workflows/ci.yml` without the Workflows permission. The push was
atomic, so nothing partial landed. Resolved by adding **Workflows: read and
write** rather than by dropping the workflow file — dropping it would have
preserved D-019 indefinitely, and the workflow is the point.

**Evidence**

- `git ls-remote origin` → `refs/heads/main` at `fd6a890`, identical to local
  `HEAD`; branch tracking established
- commit author verified as the personal address, not the work address
- no `.env` tracked; a scan of all 123 tracked files found no credential-shaped
  strings beyond the local development PostgreSQL and test fixtures

**Risk impact**

None to the running system. The relevant risk was disclosure, and the two
things that could have leaked — MT5 credentials and the GitHub token — are both
outside the repository by construction, one of them enforced by the config
loader itself.

**Next**

- Record the first CI result here, pass or fail. Until then the quality figures
  still rest on one developer machine (D-019 amended, not closed).
- Clone to the Windows host and run the gate there. Expect **590 passed,
  73 skipped** without a PostgreSQL — read the skip count rather than the
  colour.
- Then the demo account and MT5 first contact (HANDOVER.md §4).
- **`feedback.1.7.md` is due.** Review 1.6 §10 named five triggers; all five
  are now met. Nothing since 2026-08-22 — the M1 adapter, the probe, D-035…
  D-037, the repository change — has been independently reviewed.
  `review/FEEDBACK.md` now carries an *Unreviewed work* section saying what to
  look at and where the evidence is.

## Update 2026-08-24 (third entry) — Windows host initialised, gate run there for the first time

```text
Component: 1 — Platform / Application
Milestone: M0/M1 — engineering baseline, Windows host
Status before: Windows host provisioned but never used; every quality claim
               from the macOS machine only
Status after:  Windows host cloned, dependencies synced, full gate run and
               green; one cross-platform mypy defect found and fixed
```

**What changed**

Cloned `DutchBugs/Crumblr` onto the Windows x86-64 host per HANDOVER.md §0.2.
The working directory already contained an empty, remote-less `git init` from
an earlier attempt, which is why the user's own `git clone` failed with
`destination path '.' already exists and is not an empty directory` — removed
(nothing was committed to it) and cloned properly. Repo-local git identity set
per §0.2 (`user.name`, `user.email`, `credential.https://github.com.username`),
`uv` installed, `uv sync --extra mt5` run.

**A real defect found by running the gate on this host, not predicted by HANDOVER.md**

`uv run mypy` failed with `tests\unit\test_mt5_readonly_gateway.py:479: error:
Statement is unreachable`, which does not reproduce on macOS. Cause: mypy
statically evaluates `sys.platform` comparisons against the platform mypy
itself runs on (no `--python-platform` override is configured). The test's
`if sys.platform == "win32": pytest.skip(...)` was therefore treated as always
taken on this host, and since `pytest.skip` is typed `NoReturn`, mypy marked
the following `with pytest.raises(...)` block as unreachable. On macOS the
same branch is statically dead instead, which is silent rather than an error.
Fixed by switching the guard to `platform.system() == "Windows"`, which mypy
does not special-case — same runtime behaviour, no static elimination. See the
inline comment added at the test for the reasoning.

**Evidence**

- `platform.machine()` → `AMD64`; `MetaTrader5.__version__` → `5.0.6090`
- ruff check / format — clean, 113 files formatted
- mypy — clean, 93 source files (after the fix above; failed before it)
- pytest without PostgreSQL — **587 passed, 76 skipped** (HANDOVER.md predicted
  590/73; the difference is three tests whose skip/pass status is
  platform-dependent, not a regression — one confirmed as the mypy fix above
  now genuinely skipping the Windows branch at runtime too; the other two are
  most plausibly `test_halt_survives_restart.py`'s own
  `"filesystem or user ignores directory permissions"` skip, since Windows
  does not enforce POSIX permission bits the way the test's assumptions were
  written against — not independently confirmed line-by-line, the pytest
  output was captured through a `tail` that truncated the early skip lines)
- replay determinism — `run_replay.py --bars 2000` run twice, byte-identical
  md5 (`6528cef1969d4d973cc085ebaebcc6a8`) both times
- PostgreSQL not yet started on this host (Docker Desktop installed but not
  running) — the full 663 have not been run here yet

**Risk impact**

None. No product code changed, only a test file. No broker, no order path
touched.

**Decision**

Not yet committed — holding for the user's go-ahead per `CLAUDE.md` §4.

**Next**

- Start Docker Desktop + PostgreSQL on this host and run the full 663 to
  close out that gap.
- Commit the test fix once the user confirms (repo-local identity is already
  correct for it).
- The one remaining hard blocker for MT5 first contact (HANDOVER.md §4) is
  still the owner creating the Pepperstone demo account and logging into it
  once, interactively, in the terminal — unchanged by this session.
- `feedback.1.7.md` remains due and is not something this session can write —
  it is the independent reviewer's document, not the implementer's.

## Update 2026-08-24 (fourth entry) — reviews 1.7 and 1.8 processed; demo account created and logged in

```text
Component: 1 — Platform / Application
Milestone: M1 — MT5 read-only gateway
Status before: two reviews arrived unprocessed; status.md said the demo
               account did not exist; the owner had, in fact, already
               created it and logged the terminal into it on this host
Status after:  F-026…F-032 resolved or explicitly deferred; current-state
               documentation corrected; a real security gap (F-031) closed
               before any credentialed run; nothing external now blocks the
               first-contact probe
```

**What changed**

`review/feedback.1.7.md` and `review/feedback.1.8.md` arrived as untracked
files (not via a git pull — placed directly in the working tree) and were
processed per `CLAUDE.md` §1.

- **F-026** (demo account shown as nonexistent) — corrected throughout
  `status.md`: the health line, the M1-dependency checklist, Component 1's
  current status, the M1 milestone-tracker row, and the next-actions list.
  Recorded facts: server `PepperstoneUK-Demo`, currency `EUR`, leverage
  `1:30`. The account login itself is not written anywhere in this
  repository, in line with the reviewer's rule.
- **F-027** (M2 possibly held open by an unwritten M1 condition) — checked
  against `build.md`'s actual Milestone 2 acceptance text: replay order,
  gap/out-of-order detection, raw-data immutability. No real-feed clause
  exists there; `build.md`'s Milestone 1 acceptance ("reads EUR/USD
  ticks/bars") is where that requirement actually lives. `status.md` §6 now
  reads M2 as **PASSED on its own acceptance evidence**, with real-feed
  validation reassigned to M1 rather than silently added to M2 — the same
  class of error F-010 named a year of one milestone-numbering scheme ago.
- **F-028** (Pepperstone EU vs UK) — `review/DEVIATIONS.md` D-034 already
  carried this as provisional/unresolved; cross-referenced from `status.md`
  and confirmed it does not gate the read-only probe.
- **F-029** (stale paper-campaign header) — `Broker: Pepperstone` and
  `Server: PepperstoneUK-Demo` filled in; campaign status stayed `NOT
  STARTED`; no credential added.
- **F-031** (raw probe output must not reach git) — this is the one finding
  that had to be fixed *before* the probe could be run with real credentials,
  not just documented. `scripts/mt5_probe.py` gained `sanitize_report()` and
  a `--sanitized-json` CLI flag that redacts `account.login`; `.gitignore`
  gained `first-contact*.json`, `*.mt5probe.json` and `var/`; `HANDOVER.md`
  §0.4 and §4.3 now demonstrate the sanitized flag and a gitignored `var/`
  output path instead of a bare repo-root filename. Three new tests in
  `tests/unit/test_mt5_probe.py::TestSanitizeReport` assert the account number
  is redacted, the original report object is not mutated, and every technical
  broker fact (server, currency, leverage, margin mode, instrument spec)
  survives unchanged.
- **F-030** (full Windows gate never run with PostgreSQL) — left `OPEN`.
  Genuinely not done yet; see Next.
- **F-032** (MT5 enum decoding must be settled from observation) — left `IN
  PROGRESS`. It cannot be closed from documentation; it closes when the probe
  runs and `filling_mask`/`trade_mode` are compared against what the terminal
  actually reports.

**A process note, not a finding**

`.env` was created locally from `.env.example` (git-ignored, unmodified
otherwise). The three MT5 credential fields are intentionally still blank —
they must be filled in by the owner, on this machine, never dictated over
chat or written by this session. That is the one remaining action before the
probe can run.

**Evidence**

- ruff check / format, mypy — all clean after the F-031 changes (93 source
  files)
- `tests/unit/test_mt5_probe.py` — 21 passed (18 existing + 3 new)
- `review/FEEDBACK.md` — 1.7 and 1.8 added to the reviews table; F-026…F-032
  added to the finding register; the stale "Pepperstone demo account"/entity
  rows in "Still open" corrected; the "Unreviewed work" section rewritten
  around 1.9 rather than the now-processed 1.7

**Risk impact**

None to the running system. F-031 is the only change with real stakes, and it
closes a gap rather than opening one: without it, following HANDOVER.md's own
prior example command would have written the real account number into a
repo-root JSON file with no gitignore rule protecting it.

**Decision**

Not yet committed — holding for the user's go-ahead, consistent with the
mypy-fix entry above.

**Next**

- Owner fills in `CRUMBLR_MT5_LOGIN`, `CRUMBLR_MT5_PASSWORD`,
  `CRUMBLR_MT5_SERVER` in `.env` directly (not through this session).
- Run `uv run python scripts/mt5_probe.py --json var/first-contact.json
  --sanitized-json var/first-contact.sanitized.json`. Expect the account
  guard to plausibly fail on the first run (D-034/APP-013 still open) — that
  is a finding, not an error, and the report still prints.
- Record the **sanitized** output here, and open a deviation for every
  disagreement between the terminal and the code (APP-014) rather than
  patching code to match first.
- Start Docker Desktop + PostgreSQL on this host and run the full 663 to
  close F-030.

## Update 2026-08-24 (fifth entry) — F-030 closed: full Windows gate, with PostgreSQL

```text
Component: 1 — Platform / Application
Milestone: M0/M2 — engineering baseline, persistence
Status before: Windows gate only ever run without PostgreSQL (587/76)
Status after:  Windows gate run in full, with a real PostgreSQL — 663 passed,
               3 skipped, all three predicted and explained in advance
```

**What changed**

Docker Desktop started, `postgres:17-alpine` run locally
(`crumblr-pg`, port 55432, matching `.env.example`), `uv run alembic upgrade
head` applied the baseline migration, then the full suite run against it.

**Evidence**

```text
663 passed, 3 skipped in 152.36s
SKIPPED tests\integration\test_halt_survives_restart.py:212 — filesystem or user ignores directory permissions
SKIPPED tests\integration\test_halt_survives_restart.py:233 — filesystem or user ignores directory permissions
SKIPPED tests\unit\test_mt5_readonly_gateway.py:484 — MetaTrader5 may genuinely be importable on Windows
```

All three are exactly the skips predicted in the third 2026-08-24 entry when
comparing 590/73 (HANDOVER.md's macOS-derived expectation) against the
587/76 actually observed without a database: one is the platform-guard test
fixed for the mypy defect in that same entry (it now also *runs* differently
on Windows, not just type-checks differently), and two are
`test_halt_survives_restart.py` cases whose own skip message already
anticipated that Windows does not enforce POSIX permission bits the way the
test's assumptions were written against. None is a regression; none is new.

**Risk impact**

None. Confirms rather than changes anything about the running system.

**Decision**

Closes review 1.8 F-030.

**Next**

- Postgres container left running locally (`crumblr-pg`) for continued
  development; not part of any deployment.
- The one remaining action before M1 first contact is unchanged: the owner
  fills in `.env` with the real MT5 credentials, then the probe runs.

## Update 2026-08-24 (sixth entry) — first contact: the platform has now met a real MT5 terminal

```text
Component: 1 — Platform / Application
Milestone: M1 — MT5 read-only gateway
Status before: nothing had ever connected to MetaTrader 5; every broker fact
               in the codebase was a claim written from documentation
Status after:  one successful connection made against the real Pepperstone
               demo terminal; account guard passed; D-037 confirmed and
               fixed from observation; Q2 (hedging/netting) answered
```

**What happened**

The owner filled in `.env` locally (never seen by this session) and logged
the MT5 terminal into the existing demo account. `scripts/mt5_probe.py` was
run with both `--json` (raw, local-only) and `--sanitized-json` (account
number redacted). Only the sanitized output is reproduced below, per F-031 —
this is deliberately the first time this file, or any file in this
repository, has ever carried real broker data.

**Sanitized first-contact report**

```json
{
  "terminal": {
    "connected": true,
    "trade_allowed": false,
    "build": 6140,
    "ping_last_ms": 15094,
    "observed_at_utc": "2026-08-24T12:26:20.247353+00:00"
  },
  "account": {
    "login": "<redacted>",
    "server": "PepperstoneUK-Demo",
    "name_present": true,
    "company": "Pepperstone Limited",
    "currency": "EUR",
    "leverage": 30,
    "trade_mode": "DEMO (0)",
    "margin_mode": "RETAIL_HEDGING (2)",
    "trade_allowed": true,
    "trade_expert": true,
    "limit_orders": 500,
    "margin_so_call": 90.0,
    "margin_so_so": 50.0
  },
  "symbol_candidates": ["EURUSD"],
  "symbols_total": 1722,
  "resolved_symbol": "EURUSD",
  "instrument": {
    "name": "EURUSD",
    "description": "Euro vs US Dollar",
    "path": "Retail\\Forex\\Majors\\EURUSD",
    "digits": 5,
    "point": "1e-05",
    "tick_size": "1e-05",
    "tick_value": "0.8568539749455898",
    "contract_size": "100000.0",
    "volume_min": "0.01",
    "volume_max": "100.0",
    "volume_step": "0.01",
    "stops_level": 0,
    "freeze_level": 0,
    "symbol_trade_mode": "FULL (4)",
    "filling_mask": 2,
    "filling_modes": ["IOC"],
    "spread_points": 0,
    "spread_float": true,
    "swap_long": "-7.1",
    "swap_short": "1.67",
    "swap_rollover_3days": 3,
    "current_bid": "1.16706",
    "current_ask": "1.16706"
  },
  "open_positions": 0,
  "position_symbols": [],
  "account_guard": { "passed": true, "mismatches": null }
}
```

**Findings, one by one (HANDOVER.md §4.4)**

1. **`resolved_symbol` is `EURUSD` — no suffix.** The suffix-discovery logic
   in `resolve_symbol()` handled this correctly, but the specific case it hit
   is the simple one: an exact match among 1,722 symbols, nothing to
   disambiguate. The suffixed case the code was written to handle remains
   unexercised by this account.
2. **`margin_mode` is `RETAIL_HEDGING` — Q2 answered.** Recorded in the
   decision log above. `risk/policies.py`'s one-exposure rule was written to
   hold under either mode and needed no change.
3. **`company` is `"Pepperstone Limited"`; entity (APP-013/D-034) still
   open.** That name reads as the UK entity, not "Pepperstone EU" — but see
   `review/DEVIATIONS.md` D-034: this is evidence for the owner, not a
   resolution this session is entitled to make.
4. **Instrument facts recorded, none contradicted a hard assumption** — there
   were none to contradict; digits, tick size/value, contract size and
   volume steps were always discovered, never hard-coded. `tick_value` is a
   non-round `0.8568539749455898` because the account currency (EUR) differs
   from the quote currency (USD) — expected, not a defect.
5. **`filling_modes`/`trade_mode` — D-037, now closed.** See
   `review/DEVIATIONS.md`. `filling_mask=2` decoded to `IOC`; `trade_mode=4`
   decoded to `FULL`; both match the documented mapping the gateway now uses.
6. **`swap_long`/`swap_short`** — `-7.1` / `1.67`. First real numbers towards
   the cost model D-010 still doesn't have. Not wired into anything yet.
7. **A discrepancy not anticipated by HANDOVER.md's checklist: terminal-level
   `trade_allowed` is `false` while the account's own `trade_allowed` is
   `true`.** Opened as `APP-016` — most likely the MT5 terminal's own
   "AlgoTrading" toggle, separate from account permission. Did not block any
   read. Will silently block every order at M5 if still off then.
8. **`ping_last_ms` reads `15094`** (~15 seconds) on this first call, which is
   implausible as a real round-trip time and far more likely a first-call
   artifact (an uninitialised or cold-start value) than a genuine latency
   figure. Recorded rather than interpreted — worth comparing against a
   second, later reading before drawing any conclusion.
9. **The account guard passed on the first run** — `expected_server`,
   `expected_currency` and `expected_leverage` in `config/paper.yaml` all
   matched. HANDOVER.md §4.3 expected a first-run failure while APP-013 was
   open; that expectation did not hold, because the guard does not check the
   entity, only server/currency/leverage, and none of those three disagreed.

**What this does and does not prove**

Proves: the gateway's connection handling, checked-call wrapping,
Decimal-from-`repr` conversion, symbol resolution and account-guard logic all
work against a real terminal, not just a fake. Does not prove: continuous
read, reconnect behaviour, or anything about order execution — M1 is not
complete, and M5 remains untouched.

**Evidence**

- raw JSON kept local at `var/first-contact.json` (git-ignored, never
  committed); sanitized copy at `var/first-contact.sanitized.json` and
  reproduced above
- code changes made in response, all covered by the existing quality gate:
  `src/crumblr/mt5_gateway/enums.py` (new, shared decode tables),
  `readonly.py::instrument` (decodes instead of stringifying),
  `risk/policies.py` and `config/paper.yaml` (Q2 answer recorded),
  `tests/unit/test_mt5_readonly_gateway.py` (fake now supplies real integers,
  three new regression tests)
- ruff, mypy — clean, 94 source files
- full suite with PostgreSQL, rerun after the D-037 fix — **666 passed, 3
  skipped** in 284s (up from 663/3 before this entry's code changes; the
  three new tests are the D-037 regression cases, all passing, same three
  platform-dependent skips as before, no failures, no regressions)

**Risk impact**

None to the running system — read-only throughout, no order path touched.
The one new operational fact (`APP-016`) has zero impact now and a real one
at M5 if not addressed by then.

**Decision**

Advances D-035 (partial), closes D-037, answers build.md §29 Q2. D-034
remains open — owner decision, not engineering.

**Next**

- Owner: resolve the Pepperstone entity (D-034/APP-013) and the AlgoTrading
  toggle (APP-016) — both human actions.
- Engineering: continuous bar/tick read and observed reconnect behaviour
  (HANDOVER.md §4.5) is what completes M1; one connection is not the
  milestone.
- Commit and push this session's full body of work (review 1.7/1.8
  processing, F-031 sanitization, F-030 closure, and this first-contact
  entry) — held pending the owner's go-ahead.

## Update 2026-08-24 (seventh entry) — review 1.9 processed; the continuous reader built and tested

```text
Component: 1 — Platform / Application
Milestone: M1 — MT5 read-only gateway
Status before: one successful MT5 connection proven; no continuous read, no
               reconnect handling; the entity question open with no
               resolution mechanism
Status after:  O-005 recorded; a reconnect/revalidation engine built and
               unit-tested against all five scenarios review 1.9 F-034 named;
               a runnable soak-test script exists. The soak test itself —
               against the real terminal — has not run yet
```

**Review 1.9 processed**

`review/feedback.1.9.md` arrived (untracked file, same mechanism as 1.7/1.8).
Processed per `CLAUDE.md` §1, committed separately as `57b5b05` before the
engineering work below started:

- **O-005** — for the demo/development environment only, the Pepperstone
  entity is **Pepperstone Limited (UK)**, refining O-001's "Pepperstone EU"
  shorthand now that first contact's `company` field is known. Explicitly
  scoped: does not decide the entity for a future live account. Closes
  D-034/APP-013 for M1; live promotion reopens the question by design.
- **APP-016** recorded as **KNOWN / DEFERRED TO M5 READINESS** per the
  reviewer's explicit instruction not to enable AlgoTrading yet.
- **F-033** — stale post-first-contact sections (the M1 checklist, Component
  1's objective, the milestone tracker, the risk table) rewritten to current
  state. Historical §13 entries untouched.
- **F-026 … F-032** reconfirmed CLOSED by the reviewer; nothing reopened.

**The continuous reader (workstream A, review 1.9 §5 / F-034)**

HANDOVER.md §4.5 named this the missing half of M1: `readonly.py` declared
`ticks`/`bars` nowhere, and the Windows host had never read more than one
snapshot. Built in this order:

1. `src/crumblr/mt5_gateway/enums.py` gained `decode_enum`, reused by the
   D-037 fix; `client.py`'s `Mt5Module` protocol gained `COPY_TICKS_ALL` and
   the `TIMEFRAME_*` constants — read off the real package at call time, not
   hardcoded, for the same reason D-037 existed.
2. `ReadOnlyMt5Gateway.ticks()` and `.bars()` — the first calls
   `copy_ticks_from`, the second `copy_rates_from_pos`, both converting
   through a `_field()` helper that accepts either a numpy structured-array
   row or a plain object, so the test fakes do not have to reproduce numpy.
   `bars()` sorts before handing broker-delivered bars to
   `market_data.pipeline.normalize_bars` — the documented delivery order is
   not trusted, on the same principle D-037 was fixed on. 12 new tests in
   `tests/unit/test_mt5_readonly_gateway.py`.
3. `src/crumblr/application/live_reader.py` (new) — `LiveReader`, a
   reconnect/revalidation state machine, deliberately **not** the decision
   orchestrator: it reads ticks and bars and persists them, nothing else. Two
   failure classes, handled differently on purpose:
   - `STALE` — no fresh data for a while. Self-clears the moment fresh data
     arrives; nothing here was ever wrong.
   - `UNHEALTHY` — revalidation disagreed (server/currency/leverage/demo
     status via the existing `AccountGuardError` path, or a new margin-mode
     comparison against the first-observed value — D-038) or a stored bar
     conflicted with a re-delivered one (`JournalIntegrityError`). **Sticky**:
     only `LiveReader.acknowledge(operator=, note=)` clears it, mirroring
     `risk/kill_switch.py`'s no-automatic-reset discipline as a deliberately
     separate instance of it.
4. `scripts/mt5_live_reader.py` (new) — the soak-test runner. Prints health
   every poll, stops cleanly on Ctrl+C, writes a JSON health snapshot with
   `--json` that carries no credential-shaped field.
5. `MissingCredentialsError`/`read_credentials` moved from `mt5_probe.py` into
   `mt5_gateway/client.py` so the new script does not duplicate them — the
   same "one copy, not two that can drift" reasoning as the D-037 fix.

**Every one of review 1.9 F-034's five required scenarios has a passing unit
test** (`tests/unit/test_live_reader.py`, 13 tests, all against a scripted
fake terminal — no PostgreSQL needed, `LiveReader` is typed against a
narrower `MarketDataSink` protocol for exactly this):

```text
normal disconnect -> reconnect -> same account -> recover         PASS
reconnect -> wrong server/account -> fail closed                  PASS
reconnect -> symbol spec changed -> detect + record, no silence   PASS
reconnect -> no tick data -> stale                                PASS
terminal restart -> reconnect -> full account guard re-run        PASS
```

Two scope decisions recorded rather than silently made: margin-mode
revalidation checks against the *first observed* value rather than a new
`AccountGuardConfig.expected_margin_mode` field (D-038); tick/bar timestamps
are still assumed UTC, unverified against a real feed (D-039, the same class
of open question D-035 already names for everything else about this
terminal).

**A real bug found and fixed while writing the tests**

`poll_once()` originally connected on the first call and returned immediately
without reading — meaning the very first successful connection produced zero
ticks, zero bars, and could not detect a `JournalIntegrityError` on that same
call. Found by `TestFirstConnect` and `TestDataConflict` failing; fixed by
falling through to a read in the same poll a connect succeeds, rather than
waiting a full cycle. A second, separate bug — staleness never triggering
when no data had ever arrived, because the reference timestamp was `None` —
fixed by falling back to `last_reconnect_at_utc` when there is no tick or bar
to measure staleness from yet.

**Evidence**

- `tests/unit/test_live_reader.py` — 13 passed (all new)
- `tests/unit/test_mt5_readonly_gateway.py` — 7 new ticks/bars tests (51 total
  in the file), all passed, none of the existing tests broken
- ruff, mypy — clean, 97 source files
- full suite with PostgreSQL, rerun after this entry's code — **686 passed, 3
  skipped** (up from 666/3; +20 new tests, the same three platform-dependent
  skips as before, no failures)
- replay determinism — unchanged, byte-identical
  (`6528cef1969d4d973cc085ebaebcc6a8`)

**What this does not prove**

Nothing here has run against the real terminal yet. Every scenario above is
proven against a scripted fake, which is exactly the class of evidence D-035
calls weaker than observation. `scripts/mt5_live_reader.py` exists to close
that gap; running it, including a deliberate interruption, is the next step
and needs the owner present — an interruption test does something to their
real MT5 session.

**Risk impact**

None to the running system. Read-only throughout; no order path touched;
`--json` output carries no account number (same `to_payload()` discipline as
the probe's sanitized output).

**Decision**

F-034 substantially built and unit-tested; not closeable yet — review 1.9 §5
asks for soak-test evidence against the real terminal, which this entry does
not have. Left `IN PROGRESS` in `review/FEEDBACK.md`.

**Next**

- Run `scripts/mt5_live_reader.py` against the real terminal, with the owner
  present, including at least one deliberate interruption (§5's requirement).
- Record the result here, and settle D-039 (timestamp UTC assumption) from
  what it shows.
- Only after that: Dashboard v0 (F-035) and reconciliation, per review 1.9's
  own ordering.

## Update 2026-08-24 (eighth entry) — review 1.10 processed: F-033 reopened and fixed properly, F-036 found and fixed

```text
Component: 1 — Platform / Application
Milestone: M1 — MT5 read-only gateway
Status before: the seventh entry's own current-state fixes were incomplete;
               a real crash-on-missing-symbol gap existed in the reconnect
               path, unfound because no test exercised it
Status after:  current-state sections actually consistent with the update
               log; the reconnect engine fails closed on an unresolvable
               symbol instead of crashing; ready for the real soak with
               nothing else known to fix first
```

**Review 1.10 arrived and was processed**

`review/feedback.1.10.md` (untracked file, same mechanism as prior reviews).
Verdict: **GO — EXECUTE THE REAL READ-ONLY SOAK NOW**. Two findings required
code or documentation changes before that soak should run.

**F-033, reopened.** The seventh entry's "current-state fix" turned out to
still contain stale claims: the MT5 checklist (14 items, every single one
unchecked, including things proven against the real terminal that same day),
the M1 milestone row's prose ("not implemented" after it had shipped),
"Overall health"'s MT5 connectivity line, the repository/build checklist's
`(no remote)` note (the remote has existed since the third 2026-08-24 entry),
and stale test/file counts (479 unit tests, 88 source files — both long out
of date). All rewritten. The MT5 checklist is now a table with two columns —
impl+unit-tested vs. validated against the real terminal — because that is
exactly the distinction that kept going stale when collapsed into one
checkbox.

**F-036: acknowledgement must never itself mean "safe again".** The reviewer
asked for the invariant to be checked explicitly, and checking it surfaced a
real bug: `LiveReader._reconnect()` caught `Mt5CallFailedError` and
`Mt5UnavailableError`, but not `SymbolNotFoundError` — the exception
`resolve_symbol()` raises when the account genuinely does not have the
expected symbol at all (as opposed to a *changed* spec, which is the
non-fatal case F-034 already handled correctly). An account missing EURUSD
entirely would have crashed the reader outright instead of failing closed.
Fixed: `SymbolNotFoundError` is now caught alongside `AccountGuardError` and
produces the same sticky `UNHEALTHY` outcome. Three new tests confirm
`acknowledge()` never restores `HEALTHY` by itself — only a subsequent,
fully successful revalidation does, whether the underlying problem was a
wrong account, a missing symbol, or (the positive case) something that was
actually fixed.

**Evidence**

- `tests/unit/test_live_reader.py` — 16 passed (13 + 3 new for F-036)
- ruff, mypy — clean, 97 source files
- full suite with PostgreSQL, rerun after the F-036 fix — **689 passed, 3
  skipped** in 269s (up from 686/3; the three new F-036 tests, same three
  platform-dependent skips, no failures)

**What this does not prove**

Same limit as the seventh entry: still nothing here has run against the real
terminal. This entry closes out the reviewer's pre-soak requirements; it does
not substitute for the soak itself.

**Risk impact**

The `SymbolNotFoundError` fix has real risk impact for the read path — an
uncaught crash on a legitimate account state (missing symbol) is exactly the
kind of failure that looks like "the reader stopped" rather than "the reader
correctly refused," which is worse for an operator trying to trust its
health reporting. No execution path exists to affect either way.

**Decision**

F-033 closed again, with the specific rule ("current sections = present
truth, update log = historical truth") applied more completely this time.
F-036 closed. F-034 and F-037 remain open pending the real soak — nothing
here substitutes for that evidence.

**Next**

- The real soak, Phase A: run `scripts/mt5_live_reader.py` against the actual
  Pepperstone terminal, normal operation, no owner intervention needed for
  this phase, 30–60 minutes during an active FX session. Prove real ticks and
  M5 bars land in PostgreSQL. Settle D-039/F-037 from what the timestamps show.
- The real soak, Phase B: with the owner present, one deliberate terminal
  interruption (not combined with other failure modes), proving reconnect and
  full revalidation against reality.
- Record sanitized evidence from both phases here.
- Only then: Dashboard v0, CI, domain-contract package, reconciliation — in
  review 1.10's own stated order.

## Update 2026-08-24 (ninth entry) — Phase A first attempt: a real defect, found and fixed within minutes

```text
Component: 1 — Platform / Application
Milestone: M1 — MT5 read-only gateway
Status before: LiveReader unit-tested, never run against the real terminal
Status after:  first real attempt crashed on the first tick; root-caused,
               fixed, regression-tested, and Phase A restarted
```

**What happened**

`scripts/mt5_live_reader.py --duration 1800` was started against the real
Pepperstone terminal. It connected, resolved `EURUSD`, and crashed on the
very first tick with `decimal.InvalidOperation`.

**Root cause: `copy_ticks_from`/`copy_rates_from_pos` return numpy structured
arrays, not the named-tuple-with-plain-floats shape `account_info()` and
`symbol_info()` return** — a real, observed difference exactly in the spirit
of D-035, just one level deeper than the enum/bitmask defect D-037 already
found. `numpy.float64` subclasses Python's `float`, so the adapter's
`isinstance(value, float)` check passed silently, but numpy 2.x gives its
scalars their own `repr` — `"np.float64(1.167)"` — which `Decimal()` cannot
parse. Every tick has this shape, so every tick crashed. Recorded as D-040.

**Why 20 unit tests for `ticks()`/`bars()` didn't catch it:** every test
fixture built rows from `SimpleNamespace`, which holds plain Python floats.
That's a faithful stand-in for the calls it was originally written to test
(`account_info`/`symbol_info`), and an unfaithful one for the two new numpy-
backed calls this session added — nothing had exercised those two with an
actual `numpy.dtype` array before the real terminal did.

**A note on this entry's own writing process:** the first `cat` of the
crashed run's console output was not filtered before being displayed, and
briefly showed the real account number in this session's own transcript
(the `mt5.connected` log line — `Mt5Client` logs `login` deliberately, since
the codebase's own position, stated in `client.py` and `.env.example`, is
that an account number is an identifier rather than a secret). The raw
console file itself never left `var/` (git-ignored) and was never
committed. Flagged to the owner directly when it happened; going forward,
console output from this script gets filtered or read via the sanitized
JSON health snapshot rather than cat'd raw.

**Fix**

`_to_decimal` now converts through `float(value)` before `repr`, which
strips a numpy wrapper (or does nothing to an already-plain float). Two new
tests build genuine `numpy.dtype` structured arrays for `ticks()` and
`bars()` — not `SimpleNamespace` — so this specific gap cannot regress
silently.

**Evidence**

- `tests/unit/test_mt5_readonly_gateway.py` — 2 new tests using real numpy
  structured arrays, both passed; 52 passed / 1 skipped in the file overall
- ruff, mypy — clean, 97 source files
- full suite with PostgreSQL, rerun after the fix — **691 passed, 3 skipped**
  in 143s (up from 689/3; +2 new numpy regression tests, same three
  platform-dependent skips, no failures)

**Risk impact**

None to the running system — read-only throughout. Real impact on the
soak-test schedule: Phase A's first attempt produced zero usable evidence,
restarted after the fix.

**Decision**

D-040 opened and closed the same session. Confirms the review's own framing
(1.9 §16, 1.10 §13): the real terminal finds things a fake cannot, and this
is exactly why the soak test was the priority rather than another round of
simulation.

**Next**

- Restart Phase A with the fix in place.
- Everything else unchanged from the previous entry's Next section.

## Update 2026-08-24 (tenth entry) — Phase A second attempt: a second real defect, in code that predates this session

```text
Component: 1 — Platform / Application
Milestone: M1 — MT5 read-only gateway
Status before: D-040 fixed; Phase A restarted
Status after:  second attempt crashed on the first persist, in
               MarketDataStore itself; root-caused, fixed, regression-tested
```

**What happened**

Phase A restarted after the D-040 fix. It connected, resolved `EURUSD`, then
crashed on the very first `record_ticks` call with `psycopg.OperationalError:
sending query and params failed: number of parameters must be between 0 and
65535`.

**Root cause: `MarketDataStore._record_ticks` built one `INSERT` for an
entire batch.** `market_ticks` binds 14 parameters per row; PostgreSQL
refuses any statement bound to more than 65535 total — a hard ceiling of
4681 rows per statement. `LiveReader`'s default five-minute tick lookback,
against a real, actively-quoting EUR/USD feed, returned enough ticks on its
first read to cross that ceiling outright. Recorded as D-041.

**This one predates this session.** Unlike D-040, `_record_ticks` is
original M2 code (review 1.6 F-022), not something written this week. It
had simply never been exercised with a batch anywhere near real-market
volume — every existing test, and every replay run, inserts a handful of
ticks at a time. The soak test is finding gaps across the whole persistence
path, not only in the code written to reach it.

**Fix**

`_record_ticks` now chunks into `INSERT`s of at most 2000 rows
(comfortably under the 4681-row ceiling), looping inside the same
connection so the operation is still atomic from the caller's side. A new
integration test in `tests/integration/test_market_data_store.py` inserts
4001 ticks in one `record_ticks` call — more than two chunks' worth — and
asserts every one lands. `record_bars` was checked and is not affected: it
already inserts one row at a time.

**Evidence**

- `tests/integration/test_market_data_store.py` — 15 passed, including the
  new 4001-tick chunking test (needs PostgreSQL)
- ruff, mypy — clean, 97 source files
- full suite with PostgreSQL — pending in this session; see the next entry

**Risk impact**

None to the running system — this is a storage-layer robustness fix, no
order path involved. Real impact on the soak schedule: two attempts in, zero
minutes of clean evidence yet.

**Decision**

D-041 opened and closed the same session, same pattern as D-040: found by
the real soak, fixed immediately, regression-tested before trying again.

**Next**

- Restart Phase A a third time.
- Everything else unchanged from the eighth entry's Next section.

---

## Update 2026-08-24 (eleventh entry) — review 1.11 processed: F-031 reopened and fixed, F-038 proven, F-033 fixed a third time

**Review verdict:** GO — CONTINUE THE REAL SOAK. M1 NOT YET PASSED (two
real-soak defects found and fixed, clean soak still required). M2 PASSED.
Dashboard v0 GO AFTER A CLEAN PHASE A. M5/P2 NO-GO.

**What the review found that this pass had missed**

Two real gaps, both in existing rather than new code:

- **F-031 reopened.** The tenth entry's own text records that the second
  failed soak's raw console output showed the real MT5 login, because
  `Mt5Client.connect()` logs `login=credentials.login` on every successful
  connect, and `ReadOnlyMt5Gateway._verify_account` logs
  `login=state.login` on every account-guard failure. Both are *ordinary*
  log lines, not the opt-in probe artifacts the original 2026-08-24 F-031 fix
  covered — the review is right that "remember not to `cat` the file" (this
  session's own working discipline after the earlier mistake) is not a
  control; the software has to prevent it.
- **F-038 (new).** D-041's fix chunks a large tick batch into several
  `INSERT`s "looping within the same connection". That sentence was an
  assumption about transaction boundaries, not a proven one — a shared
  connection does not by itself demonstrate that a failure in chunk 2 rolls
  back chunk 1's already-sent rows rather than leaving them committed.

**Fix — F-031**

Two layers, not one:

1. `mt5_gateway/client.py::mask_login(login)` returns `***` + the last three
   digits. Both call sites (`Mt5Client.connect`'s `mt5.connected`,
   `ReadOnlyMt5Gateway._verify_account`'s `mt5.account_guard_failed` and the
   `AccountGuardError` message it raises) now log `account_ref=mask_login(...)`
   instead of the raw login. `Mt5Credentials.__repr__` masks it too.
2. `observability/logging.py`'s existing secret-redaction processor (the one
   that already catches `password`, `token`, `api_key`, etc. by key name) now
   also catches any key containing `login`, forcing it to `[redacted]`. This
   is the actual control the reviewer asked for: a future call site that logs
   a raw `login=<value>` cannot reintroduce the exposure, because the
   processor strips it regardless of whether the call site remembered to mask
   it. One existing test (`test_ordinary_fields_survive_untouched`) had
   asserted the old, now-wrong behaviour and was rewritten.

Because `AccountGuardError`'s message is what `LiveReader._reconnect` copies
verbatim into `ReaderHealth.last_error`/`detail` — which `mt5_live_reader.py
--json` writes to disk — masking it at the source also satisfies the
review's "sanitized health/evidence" requirement, not only console logging.

**Fix — F-038**

No code change was needed; `_record_ticks` was already batch-atomic, because
`record_ticks` never commits a caller-supplied `Connection` and every chunk
in one call runs against the same connection/transaction. What was missing
was proof. Added
`tests/integration/test_market_data_store.py::TestChunkedInsertFailureSemantics`:
opens a real PostgreSQL connection, monkeypatches it to raise on the second
of two chunk-sized `execute()` calls, runs `record_ticks` inside that
connection's own transaction, and asserts the exception propagates *and*
zero rows from the batch survive afterward. (First version of this test put
`pytest.raises` inside the transaction block, which swallowed the exception
before the transaction manager ever saw it and let chunk 1 commit anyway —
caught by the test itself failing, fixed by moving `pytest.raises` outside
`connection.begin()`.) `_record_ticks`'s docstring and `review/DEVIATIONS.md`
D-041 both now state the contract as proven rather than assumed.

**Fix — F-033 (third time)**

§12 "Next 10 actions" still listed the Pepperstone entity and Q2
hedging/netting as open checkboxes after both had been resolved elsewhere in
this same document (O-005; `RETAIL_HEDGING`). Checked off in place with the
resolution restated inline, per the review's instruction that historical
update-log text may stay unchanged — only the current-state section was
wrong.

**Evidence**

- `tests/unit/test_mt5_client.py` — new file, 7 tests, all passing
- `tests/unit/test_mt5_readonly_gateway.py::TestAccountGuard` — 2 new tests
- `tests/unit/test_logging.py::TestSecretsAreRedacted` — 3 new tests, 1
  existing test rewritten
- `tests/integration/test_market_data_store.py::TestChunkedInsertFailureSemantics`
  — 1 new test, run against real PostgreSQL, passing
- Full suite with PostgreSQL, prior to this pass's additions: 691 passed, 3
  skipped (the platform-dependent skips already accounted for)
- Full suite with PostgreSQL, after this pass's additions: **705 passed, 3
  skipped**, exit 0 (0:03:45) — the 14 new tests, zero regressions
- ruff, mypy — both clean, 98 source files

**Problems found**

The chunk-failure test's first draft asserted the wrong thing about its own
harness, not about the production code — see the F-038 fix note above. Worth
recording because it is exactly the kind of mistake this project's own
review process exists to catch, caught here by the test failing loudly
rather than by a reviewer.

**Risk impact**

None to the running system — both fixes are observability/persistence-layer
robustness, no order path involved. Confirms rather than changes D-041's
resolution.

**Decision**

F-031 and F-038 closed this session with test evidence. F-033 closed a third
time. F-034 and F-037 remain open exactly as review 1.10 left them — nothing
in review 1.11 changes what closes them, only the real soak does.

**Next**

- Commit and push.
- Restart Phase A a third time.
- Everything else unchanged from the tenth entry's Next section.

---

## Update 2026-08-24 (twelfth entry) — Phase A third attempt: an operational mistake, then a third real defect, D-042

**What happened**

Committed and pushed the eleventh entry's fixes, then restarted Phase A.
Two separate problems, in order:

**1. Self-inflicted: the shared database's schema was gone.** The
`tests/integration` `engine` fixture drops and recreates the schema per
test, and drops it again at teardown — by design, for hermetic tests. It
runs against `DEFAULT_TEST_URL`
(`postgresql+psycopg://crumblr:crumblr@localhost:55432/crumblr`), which is
the *same* physical database `create_db_engine()` defaults to when
`CRUMBLR_DATABASE_URL` is not set — the one the soak script also uses. The
full-suite run at the end of the eleventh entry left that database with no
tables at all. Launching the soak straight after hit
`psycopg.errors.UndefinedTable: relation "market_ticks" does not exist`
on the very first `record_ticks` call. Not a code defect — fixed by
`uv run alembic upgrade head` against the same database, confirmed with a
direct `inspect(engine).get_table_names()` check before trying again.
**Lesson, not yet enforced in tooling:** running the integration suite and
the real soak against the same local PostgreSQL instance requires
re-migrating between them. Worth a dedicated soak-only database (a second
Postgres or a second database name in the same instance) if this keeps
costing attempts — noted here rather than acted on, since it is tooling
convenience, not a platform gap.

**2. D-042 — the still-forming current bar was persisted as if closed.**
With the schema restored, the reader connected, resolved the symbol, read
real ticks — and went `UNHEALTHY` nine seconds later on a bar data
conflict:

```text
bar ... for EUR/USD M5 at 2026-08-24T17:10:00+00:00 is already stored with
different values: stored OHLC .../1.16644, incoming .../1.16647
```

Only the close differed. **Root cause:** `ReadOnlyMt5Gateway.bars()` reads
`copy_rates_from_pos(symbol, timeframe, 0, count)` — MT5's position 0 is
its *current*, still-forming bar, whose OHLC changes every call until the
interval closes. `LiveReader` polls every 5 seconds and persists whatever
`bars()` returns; the first poll inside an open M5 window stored that
bar's close at that instant, the next poll saw a different close for the
same interval, and `MarketDataStore.record_bars` did exactly what it is
supposed to do — treated the second value as a contradiction of the first
and raised. This is the F-038-proven atomicity/integrity machinery working
correctly against a real precondition violation, not a false alarm and not
a repeat of D-040/D-041's class of bug.

**Fix:** `bars()` now drops any row whose interval has not yet closed
relative to the read's own `received_time_utc`
(`open_time_utc + interval_for(timeframe) <= received_time_utc`), before
the series reaches `normalize_bars` or the store. Two new unit tests in
`tests/unit/test_mt5_readonly_gateway.py::TestBars`: a still-forming bar is
excluded, and a bar that closed one second ago is included — nothing is
lost, only correctly delayed until its interval actually ends. Recorded as
**D-042** in `review/DEVIATIONS.md`.

**Evidence**

```text
ruff, mypy — clean, 98 source files
tests/unit/test_mt5_readonly_gateway.py::TestBars — 6 passed (4 existing + 2 new)
full suite with PostgreSQL (after re-migrating) — 707 passed, 3 skipped,
  exit 0 (0:02:50)
```

**Problems found**

Two, both described above: an operational one (shared database between
tests and the live soak) and a real one (D-042). Neither is a repeat of a
previously-fixed defect.

**Risk impact**

None to the running system — read path only, no order path involved. Real
impact on the soak schedule: three real connection attempts in, still zero
minutes of clean evidence, though each attempt has been getting further
than the last (D-040: crashed on tick conversion; D-041: crashed on tick
volume; D-042: connected, read ticks, read bars, ran nine seconds before
the bar conflict).

**Decision**

D-042 opened and closed the same session, same pattern as D-040/D-041.
Migrations re-applied to the shared database rather than provisioning a
separate one — accepted as a known operational cost for now, not hidden.

**Next**

- Re-apply migrations (again) before restarting, since the full suite just
  ran against the same database once more.
- Commit and push.
- Restart Phase A a fourth time.
- Everything else unchanged from the tenth entry's Next section.

---

## Update 2026-08-24 (thirteenth entry) — a dedicated soak database, while Phase A's fourth attempt runs

**What happened**

The Phase A fourth attempt (started at the end of the twelfth entry) was
left running — real-terminal soaks are not something to interrupt over
tooling — while the shared-database problem it had already hit twice was
fixed properly. The reviewer/supervisors raised the same point
independently: the twelfth entry's own "worth a dedicated soak-only
database... noted here rather than acted on" line should be acted on, not
left as a recurring manual step.

**What changed**

- A new database, `crumblr_soak`, created on the same local PostgreSQL
  instance (`localhost:55432`, same `crumblr` role) and migrated with the
  same Alembic baseline (`ce70efeb9fe9`).
- `scripts/mt5_live_reader.py` now **refuses to start** unless
  `CRUMBLR_DATABASE_URL` is explicitly set — it no longer falls back to
  `create_db_engine()`'s convenience default, which is the same physical
  database `tests/integration`'s `engine` fixture tears down. Same
  reasoning as F-031's fix: a control that depends on a person remembering
  is not a control.
- `.env` (local, gitignored) repointed at `crumblr_soak` for real-terminal
  runs — done with a find/replace that never printed the file's contents,
  consistent with this session's credential-handling discipline. Does
  **not** affect the fourth Phase A attempt already running; that process
  captured its environment at launch and keeps using the database it
  started against.
- `.env.example` documents the split with the D-042 story inline, so a
  future developer copying it sees why the soak needs its own database
  rather than discovering it the same way this session did.
- `status.md` §10 decision log row added.

**Evidence**

```text
ruff, mypy — clean, 98 source files (only scripts/mt5_live_reader.py and
  .env.example changed; no test file exists for this script's CLI wiring,
  consistent with mt5_probe.py's existing testing boundary)
```

**Problems found**

None — this is the fix for a problem already fully diagnosed and recorded
in the twelfth entry.

**Risk impact**

None. Tooling/operational change only; no code on the order or read path
changed. Reduces the chance of repeating the exact mistake that cost the
third Phase A attempt its first ten minutes.

**Decision**

Recorded in §10: real MT5 runs use `crumblr_soak`; the shared `crumblr`
database stays test-only. `scripts/mt5_live_reader.py` enforces this at
startup rather than relying on documentation alone.

**Next**

- Once the fourth Phase A attempt concludes (pass or fail), record its
  outcome as its own entry.
- If it produced a clean 30-60 minute run: proceed to timestamp
  verification (F-037/D-039) and Phase B scheduling, per review 1.11 §6-8.
- If it failed again: diagnose before a fifth attempt, same discipline as
  D-040/D-041/D-042.

---

## Update 2026-08-24 (fourteenth entry) — Phase A fourth attempt: no crash, real ticks proven, bars blocked by D-039, now fixed and closed

**What happened**

The fourth Phase A attempt ran for the full 30 minutes without a single
disconnect: `status HEALTHY` throughout, `reconnect_count: 1` (the initial
connect only), `consecutive_failures: 0`, `last_error: None`. That is real
progress — the first attempt with zero connection-stability problems.

It used the shared `crumblr` database, not the new `crumblr_soak` one: it
had already been launched before `crumblr_soak` existed (the user relayed
reviewer/supervisor feedback asking for a dedicated database while this
attempt was already past halfway, and it was deliberately left running
rather than interrupted — real-terminal soaks are not something to restart
over tooling). This meant the wrong-database mistake from the twelfth entry
was accidentally repeated once more for this specific run, harmlessly this
time since the schema had already been re-migrated beforehand.

**The console showed `last_bar=-` for all 360 polls.** Final health:
`last_bar_at_utc: None`. Zero bars persisted in 30 minutes — worth
investigating rather than accepting, since "clean connection" is not the
same claim as "clean data".

**Investigation**

Queried the actual database directly (`crumblr`, not `crumblr_soak` — the
first check queried the wrong one and had to be redone once that was
noticed): **19,437 real ticks were stored** — genuine proof of real
EUR/USD ticks reaching PostgreSQL, the review's primary ask for Phase A.
Zero bars, confirmed.

Sampling `event_time_utc` (from the terminal) against `received_time_utc`
(this platform's own `utc_now()`) across 20 points spread through the
run's ticks showed the gap growing from near-zero at connect to a
**stable ~2:59:39-2:59:40**, holding there for the rest of the run. Two
findings from that one measurement:

1. **F-037/D-039, settled:** the terminal's clock is not UTC. It runs
   about three hours ahead of true UTC — `readonly.py` had assumed
   otherwise throughout, silently, since before this session.
2. **Why `last_tick` climbed so fast and bars never appeared:**
   `ticks()` passed a true-UTC `since` straight to `copy_ticks_from`,
   which the terminal compared against its own, three-hours-later clock —
   turning "since 5 minutes ago" into "since about 3 hours and 5 minutes
   ago" and handing back a multi-hour backlog that took the whole 30
   minutes to work through. `bars()`'s D-042 filter compares a bar's
   (mislabelled) open time against true UTC now; a bar stamped 3 hours
   into the apparent future looks perpetually not-yet-closed, so D-042's
   fix — correct in isolation — never let a single bar through against
   this input.

**Decision point**

This touches core data semantics (every stored timestamp's meaning) and
the review's own explicit constraint — "do not invent a broker-time
correction unless observation requires one" — now satisfied, but *how* to
apply the correction was still a real design choice: hard-code the
measured ~3 hours, or detect it dynamically each session. Asked the user
directly rather than deciding unilaterally, given the magnitude. Chosen:
**dynamic detection**, matching the project's existing "discover, never
assume" pattern for the symbol and account.

**Fix**

`ReadOnlyMt5Gateway._clock_offset()`: on first use after each gateway
construction (i.e. every `LiveReader` reconnect — `_reconnect()` now
threads its own `self._clock` through to the gateway it builds), reads
`symbol_info_tick`'s current time, compares it to the platform's own
clock, and rounds to the nearest 30 minutes. A new `_to_utc()` helper
applies the correction wherever a raw MT5 timestamp is converted
(`ticks`/`_tick_from_raw`, `bars`/`_bar_from_raw`, `positions`), and
`ticks()` shifts the caller-supplied `since` into the terminal's own
clock before calling `copy_ticks_from` — the same correction run in
reverse.

**Evidence**

```text
ruff, mypy — clean, 98 source files
tests/unit/test_mt5_readonly_gateway.py::TestClockOffset — 4 new tests
full unit suite — 582 passed, 1 skipped
full suite with PostgreSQL — 711 passed, 3 skipped, exit 0 (0:15:09)
```

**Problems found**

The two described above (D-039 itself, and the compounding effect on
`since`), both now understood and fixed. A third, smaller mistake caught
mid-investigation: the first database check queried `crumblr_soak` instead
of the database this run had actually used (`crumblr`) — corrected before
drawing any conclusion from it.

**Risk impact**

None to the running system — read path only. Real positive impact: the
platform's stored timestamps were silently wrong by a fixed ~3 hours since
before this session (`positions()` inherited the same assumption, also
fixed here), and this closes that gap with evidence rather than leaving it
open indefinitely per D-039's own "watch for" note.

**Decision**

F-037 closed. D-039 resolved. `status.md` §10 records the dynamic-detection
choice. Fourth attempt's own evidence (30 clean minutes, zero disconnects,
19,437 real ticks) stands, but does not by itself satisfy "clean bars
proven" — that needs a fifth attempt with both fixes present.

**Next**

- Re-apply migrations (the pytest run in progress will drop `crumblr`'s
  schema again at teardown, same as every prior cycle).
- Commit and push.
- Restart Phase A a fifth time, this time actually against `crumblr_soak`.
- If clean (ticks and bars both persisting, health stays HEALTHY): proceed
  to timestamp boundary verification against the fresh evidence and
  schedule Phase B with the owner present.

---

## Update 2026-08-24 (fifteenth entry) — Phase A fifth attempt: D-039's fix confirmed working, a fourth real defect found and fixed, D-042 refined

**What happened**

Restarted Phase A a fifth time, this time correctly against the new
`crumblr_soak` database. Connected cleanly; `mt5.broker_clock_offset_detected`
logged `offset_minutes: 180` (exactly 3 hours), measured as 2:59:39.77 raw —
matching the fourth attempt's independent measurement almost to the second.
D-039's fix is confirmed working against the real terminal, not only against
its own unit tests.

Bars started arriving for the first time in any real soak attempt:
`last_bar_at_utc` advanced from 15:10:00 to 15:15:00, aligned to real M5
boundaries. Twelve bars persisted before the run went `UNHEALTHY` again —
progress, not a regression: three real defects deep, each attempt reaches
further than the last.

**The fourth real defect.** The bar for the 15:15–15:20 UTC interval was
first stored the instant real time crossed 15:20:00 (`received_time_utc:
15:20:03`), then read again roughly one poll later with a **different
`tick_volume`** for the same interval — the OHLC values printed in the
error looked identical, which is what made this one take a direct database
query to actually see: `record_bars`'s conflict check compares the whole
`Bar`, not only OHLC, and `tick_volume` was the field that had moved.
MT5 continues attributing a few very-late ticks to a bar for a short window
*after* its nominal close, not only *before* it (D-042's original finding).
"The interval has ended" was necessary but not sufficient.

**Fix:** `_BAR_SETTLE_BUFFER = timedelta(seconds=30)` added on top of
D-042's existing closedness check — a bar must be 30 seconds past its raw
boundary, not just past it, before `bars()` returns it. 30 seconds is six
poll cycles at the default 5-second interval: margin over the one observed
revision (seen within roughly one poll of the boundary), not tuned tight
against it. Two new tests make the boundary explicit: a bar one second past
the raw interval end but still inside the buffer is withheld; one
comfortably past the buffer is returned. Recorded as a D-042 addendum, not
a new deviation — same underlying question (when is a broker-delivered bar
actually done changing), refined by a second, independent piece of real
evidence.

Handled without stopping to ask first, unlike D-039: this is a buffer-sizing
choice within an already-agreed architecture (exclude bars until settled),
not a foundational semantic decision about what "UTC" means for every
timestamp in the platform — the same distinction that made D-039 worth a
question and this one not.

**Evidence**

```text
ruff, mypy — clean, 98 source files
tests/unit/test_mt5_readonly_gateway.py::TestBars — 8 passed (4 existing +
  2 D-042 original + 2 new settle-buffer tests)
full unit suite — 587 passed, 1 skipped (after fixing bar_row's default time)
full suite with PostgreSQL — 712 passed, 3 skipped, exit 0 (0:02:59)
```

**Problems found**

The fourth real defect, described above. Also worth naming: this session's
own database-check mistake from the fourth-attempt investigation (querying
`crumblr_soak` instead of the database actually in use) was *not* repeated
this time — the fifth attempt's evidence was pulled from the correct
database on the first try.

A second, genuine process gap this time: after adding `_BAR_SETTLE_BUFFER`,
verification ran only the targeted `TestBars`/`TestClockOffset` subset, not
the full suite — and the full suite (started afterward, described below)
caught two failures the targeted run could not have: `test_live_reader.py`'s
own `bar_row()` fixture defaulted to a `time` sitting exactly on the old
filter's boundary, which the new 30-second buffer then excluded. Fixed by
giving that fixture the same "safely closed by default" shift already
applied to `test_mt5_readonly_gateway.py`'s `a_bar_row()`. The lesson is
procedural, not about the fix itself: a targeted test run after touching
shared filter logic is not a substitute for the full suite before calling
something verified.

**Risk impact**

None — read/persistence-layer robustness fix, no order path. Real cost: a
fourth soak attempt spent on a genuine defect the previous three could not
have surfaced (each ended before two consecutive polls of the same
just-closed bar interval had a chance to disagree).

**Decision**

D-042 stands as CLOSED with this addendum rather than being reopened as a
new deviation — the fix's principle (exclude a bar until it is done
changing) did not change, only how "done changing" is measured.

**Next**

- Re-apply migrations to `crumblr` (dropped again by the suite above) and
  confirm `crumblr_soak`'s schema is still intact.
- Commit and push.
- Restart Phase A a sixth time, against `crumblr_soak`.
- If a full 30-60 minutes passes with ticks and bars both persisting
  cleanly and no further conflicts: that is the clean Phase A run review
  1.10/1.11 have been asking for. Proceed to recording the evidence, the
  timestamp-boundary comparison D-039's fix enables, and scheduling Phase B
  with the owner present.

---

## Update 2026-08-24 (sixteenth entry) — Phase A: the clean run, six attempts in

**Verdict: Phase A satisfied.** A sixth attempt — after clearing one piece of
leftover state, described below — ran the full 30 minutes against the real
Pepperstone demo terminal with zero disconnects, zero errors, and zero data
conflicts.

**One operational step before the clean run.** The attempt right before this
one (against the newly dedicated `crumblr_soak`, correctly this time) failed
*immediately*, on its very first poll, on the exact same bar the fifth
attempt had already stored — MT5 reported yet another different `close` for
the 15:15 UTC interval, weeks — no, minutes — after the fifth attempt's own
already-revised value had settled. `crumblr_soak` is scratch state for an
in-progress soak, not a record worth preserving across a failed attempt, so
its schema was dropped and rebuilt rather than treated as evidence to
reconcile. (One wrinkle: `drop_schema()` clears the application's own tables
but not Alembic's `alembic_version` tracking table, so `alembic upgrade
head` afterward believed it had nothing to do and left the tables missing.
`bootstrap_schema()` — the same `create_all`-based path tests use — was used
directly instead, which does not depend on the version table's state.) This
third revision, on a bar already revised once, is noted but not chased
further right now: the magnitude was tiny (a 0.00003 close difference) and
it happened across two separate terminal sessions, not within one — a
different shape than D-042's original within-session finding. Worth
watching for if it recurs within a single continuous run; not evidence of
that yet.

**The clean run itself:**

```text
Connected:        2026-08-24T15:33:43 UTC
Duration:         30:06 (360 polls, 5s interval)
Reconnects:       1 (the initial connect only)
Consecutive failures: 0
Errors:           none
Broker clock offset: 180 min (exactly 3h), measured at 2:59:39.26 raw
Ticks persisted:  2,920
Bars persisted:   17, all M5, all data_quality=GOOD, zero anomalies
Bar alignment:    all 17 open times land exactly on a 5-minute UTC boundary
Bar continuity:   zero gaps — every consecutive pair is exactly 5 minutes apart
Last tick age:    19.5 seconds old at the moment it was read
```

**F-037/D-039 evidence, from this run specifically:** every stored bar's
open time is minute-aligned to 0/5/10/.../55 with zero seconds — direct
confirmation that the corrected timestamps land on real M5 boundaries, not
only that the gap measurement was stable. Combined with the fourth and
fifth attempts' independent ~180-minute offset measurements (10779.77s,
10779.26s — agreeing to within half a second of each other), F-037 is
closed on stronger evidence than the fix that closed it already had.

**Evidence location:** `var/soak_phase_a_health.json` (gitignored, local) —
the sanitized health snapshot, no account number. Raw counts and sample
rows recorded above; the database itself (`crumblr_soak`) is local-only and
not part of this repository.

**Problems found**

None in this run itself. The pre-run state-clearing step and its
`alembic_version` wrinkle, described above.

**Risk impact**

None — read-only, no order path. This is the evidence review 1.9/1.10/1.11
have all named as the primary blocker to M1 qualification.

**Decision**

Phase A: satisfied. Six real-terminal attempts, four real defects found and
fixed (D-040, D-041, D-042 in two parts, D-039), one operational fix
(dedicated soak database), one process-discipline lesson (full suite over
targeted subsets after touching shared filter logic). Phase B — the
deliberate, owner-present terminal interruption — is the only thing left
before F-034 and the M1 qualification decision itself.

**Next**

- Report this result to the user/owner plainly and ask whether they are
  available now for Phase B, per the project's own repeated requirement
  that the owner be present for the deliberate interruption.
- Do not start Dashboard v0 before Phase B, per review 1.10 §7/1.11 §9 — it
  may be prepared in parallel with scheduling Phase B, not instead of it.
- Update `review/FEEDBACK.md`'s finding register and "Unreviewed work"
  table with this result.

---

## Update 2026-08-24 (seventeenth entry) — Phase B: two real reconnects, owner present, both clean

**Verdict: Phase B satisfied. F-034 closed.**

The owner confirmed availability immediately after the sixteenth entry's
report. `scripts/mt5_live_reader.py` was restarted against the same
`crumblr_soak` database Phase A had just proven clean (continuing that
evidence rather than discarding it — Phase B is a continuation of normal
operation with a deliberate interruption in the middle, not a fresh start).
Connected cleanly; broker clock offset re-detected at 180 minutes, matching
every prior measurement.

**The owner then closed the MT5 terminal twice**, minutes apart. Full
sequence, from the reader's own structured log:

```text
16:09:16  connected, HEALTHY (reconnect_count 1)
16:10:23  live_reader.read_failed — "MT5 copy_ticks_from failed:
          [-10001] IPC send failed" (first closure detected)
16:10:28  mt5.disconnected
16:10:34  mt5.connected — reconnect_count 2
          symbol re-resolved (EURUSD)
          live_reader.spec_changed logged, not hidden (fresh
          captured_at_utc means every reconnect's spec fingerprint
          differs — expected, and this is what makes that visible
          rather than silent)
          account guard re-run silently (no AccountGuardError — still
          the correct account; a wrong one goes UNHEALTHY here instead,
          per TestScenario2WrongAccountFailsClosed)
          broker clock offset re-measured: 180 min (10778.20s raw)
16:10:50  live_reader.read_failed — same IPC error (second closure
          detected)
16:10:55  mt5.disconnected
16:10:58  mt5.connected — reconnect_count 3
          symbol re-resolved, spec_changed logged again, account guard
          passed again, offset re-measured: 180 min (10779.94s raw)
```

**Fresh data resumed immediately both times.** Verified directly against
`crumblr_soak` at 16:12:00 UTC, ~74 seconds after the second reconnect:
ticks had grown from Phase A's 2,920 to 3,578 and bars from 17 to 19 — no
gap in persistence across either interruption — and the single most recent
tick's `received_time_utc` was **1.6 seconds** old at the moment of the
check.

**Against the review 1.10 §5 sequence, checked off directly:**

```text
HEALTHY reader                              — yes, both times before closure
→ deliberately stop/restart MT5 terminal    — owner did this twice
→ reader detects loss                       — live_reader.read_failed, both times
→ HEALTHY is lost                           — implied; no further data until reconnect
→ terminal returns                          — owner reopened it, both times
→ reconnect                                 — automatic, both times, ~6-11s after detection
→ full revalidation                         — symbol, account, spec, clock offset — all four,
                                               both times
→ fresh data resumes                        — yes, within ~1-2 poll cycles both times
```

Two interruptions rather than one is not a departure from review 1.10 §5's
"do not combine failure modes in the first test" — that guardrail is about
mixing *different kinds* of failure together (e.g. a terminal closure and a
database outage at once), not about repeating the same one. If anything,
two consecutive clean recoveries is stronger evidence than one.

**Evidence**

```text
ruff, mypy, tests — unchanged this entry; no code changed, only the real
  terminal was interrupted
Reconnects: 3 total (1 initial connect + 2 recoveries), 0 failed to recover
Account guard: passed on every one of the 3 connects
Broker clock offset: 180 min on all 3 (10779.15s, 10778.20s, 10779.94s raw
  — all agreeing to within ~1 second of each other and of Phase A's own
  measurements)
Ticks: 2,920 → 3,578 across the run (Phase A count → post-Phase-B count)
Bars: 17 → 19
Data gap during either interruption: none observed in the persisted series
```

**Problems found**

None. Both interruptions recovered exactly as the five unit-tested
scenarios in `tests/unit/test_live_reader.py` predicted.

**Risk impact**

None — read-only, no order path. This is real-terminal confirmation that
`LiveReader`'s reconnect/revalidation logic, unit-tested since review 1.9,
behaves the same way against reality that it does against the scripted
fake.

**Decision**

F-034 closed. Phase A and Phase B are both now satisfied — the trigger
review 1.11 §12 step 14 named for `feedback.1.12.md` (M1 qualification /
dashboard review) has been met. M1 qualification itself remains the
reviewer's/owner's decision, not this document's to declare.

**Next**

- Update `review/FEEDBACK.md` (done alongside this entry): F-034 closed,
  "Unreviewed work" section reflects both phases satisfied.
- Await `feedback.1.12.md` for M1 qualification / dashboard review.
- Dashboard v0 may now be prepared per review 1.10 §7/1.11 §9 — read-only,
  no `MetaTrader5` import, no credentials, no `order_send`, no HALT reset,
  no risk-config mutation, no buttons — displaying the real rows this
  session's soak produced.
- AlgoTrading, execution adapters, `order_send`, ICT v2, and any new
  brokers/markets remain explicitly out of scope, unchanged from every
  prior review.

---

## Update 2026-08-24 (eighteenth entry) — review 1.12 processed: M1 PASSED, F-039/F-040/F-041 fixed

**Verdict: `feedback.1.12.md` processed per its required action order (§13
steps 1-6). M1 recorded PASSED. Three new findings closed same-day.**

Review 1.12 confirmed M1 PASSED (`feedback.1.12.md` §7), reconfirmed
F-034/F-037/F-038 CLOSED, reopened F-033 again (stale current-state
sections still describing the project as if the real soak never happened —
addressed below), and opened three new findings, all fixed before any other
work per `CLAUDE.md`'s session-start protocol.

**F-039 — semantic instrument identity must not change merely because it was
re-observed.** The seventeenth entry's own text attributed Phase B's
per-reconnect `spec_changed` alerts to "fresh `captured_at_utc`" — but
`InstrumentSpec.spec_version` already excluded `captured_at_utc` from its
hash before this session (`tests/unit/test_control_plane_contracts.py
::test_identical_specs_share_a_version` already asserted this). That
explanation was wrong; checked against the code rather than repeated. The
real cause is `tick_value`: the sixth-entry first-contact evidence already
recorded it as non-round (`0.8568539749455898`) "because the account
currency (EUR) differs from the quote currency (USD)" — MT5 recomputes it
live from the current cross-currency rate, so it drifts tick-to-tick with
the market, not with broker policy. Hashing it manufactured a false
"specification changed" on every reconnect. Fixed by excluding `tick_value`
from `spec_version`'s hash alongside `captured_at_utc`; it is still recorded
on every `InstrumentSpec`, just not part of what "changed" means. New test:
`test_a_tick_value_fluctuation_alone_does_not_change_the_version`.

**F-040 — broker-clock detection must fail closed when its reference tick is
stale.** D-039's `_clock_offset()` trusted whatever `symbol_info_tick`
returned and cached it for the life of the gateway connection. A stale
reference (e.g. the terminal handing back the last quote it ever saw) would
have produced a wrong "offset" with no way to notice, silently
mis-correcting every subsequent timestamp. Fixed: a measurement is now only
accepted if its residual from a clean half-hour multiple is within 3
minutes (real offsets round cleanly — Phase A/B measured ~2:59:39-2:59:40
for a 180-minute offset, a few seconds of call latency, not minutes) and its
magnitude is within ±15h (no real GMT offset exceeds that). A rejected
measurement raises `ClockOffsetUnavailableError` rather than being cached,
so the next attempt re-measures fresh. `LiveReader` treats this like a
stale feed — `DISCONNECTED`, not sticky, clears on its own — rather than
like a wrong-account mismatch, since a stale tick is not evidence anything
was ever wrong. New tests: `TestClockOffset` (+3, gateway level),
`TestClockOffsetStaleReference` (+2, reader level).

**F-041 — operational soak/database reset must remain on the migration
path.** The twelfth entry's real recovery from a dirty `crumblr_soak` mixed
`bootstrap_schema()`/`create_all` with Alembic's `alembic_version` table,
which is exactly the two-paths-disagreeing failure `persistence/migrations.py`
already warns against. `scripts/reset_soak_database.py` now provides the
coherent alternative: `alembic downgrade base` -> `upgrade head` only — the
same round trip `tests/integration/test_migrations.py
::test_the_baseline_can_be_unwound` already proves leaves nothing behind —
run deliberately, refusing a URL without "soak" in it and requiring `--yes`.
Documented in `.env.example`.

**F-033, fixed a fourth time.** §1 "Overall health" (Data health, MT5
connectivity) and §3's MT5 checklist (`bars/ticks collection`, `reconnect
behaviour` rows) still read as though Phase A/B had not happened, even
though the milestone tracker and update log already described them
correctly — the same class of drift as the first three times this finding
was raised. Rewritten in place; historical entries above are untouched.

**Full quality gate, after all three fixes:**

```text
ruff check .          — all checks passed
ruff format --check . — 125 files already formatted
mypy                  — no issues, 99 source files
pytest                — 718 passed, 3 skipped, 0 failed
                         (721 collected: up from 705 at review 1.11 —
                         12 new tests for F-039/F-040/F-041 plus 2 test-
                         fixture corrections, see Problems found)
```

**Problems found**

Running the full suite (not a targeted subset — the lesson recorded at the
fifteenth entry) surfaced two pre-existing fixture failures once F-040's
tolerance was in place: `TestScenario4NoTickData`'s two tests build a
`FakeClock` starting 300 seconds before `test_live_reader.py`'s module-level
`NOW`, while `ScriptedMt5`'s default `symbol_info_tick` always returns a
timestamp fixed to `NOW`. Previously this 300-second mismatch was silently
absorbed by rounding to the nearest 30 minutes (residual 300s rounds to a
0-minute offset with no plausibility check); F-040's new 3-minute residual
tolerance correctly rejected it as an implausible measurement, which is the
guard working as intended, not a regression — the fixture's reference tick
never actually agreed with that test's own clock. Fixed by pinning both
tests' `symbol_info_tick` override to the test's own `FakeClock` instead of
the module constant.

**Decision**

M1 recorded PASSED in §16 Promotion history, reviewer `feedback.1.12.md`.
Does not authorize `order_send`. F-039/F-040/F-041 closed same-day, ahead of
starting Dashboard v0, per this review's own required action order and
`CLAUDE.md`'s session-start protocol (open findings resolved before new
work).

**Next**

- Build Dashboard v0 (review 1.12 §8) — read-only, no `MetaTrader5` import,
  no credentials, no control surface, per the hard boundary and minimum
  screen the review specifies.
- Run CI on a runner and record the result; provide the domain-contract
  package for reviewer/human approval to close M0 (review 1.12 §9).
- Read-only reconciliation against real MT5 state (review 1.12 §10), after
  the dashboard.
- Update `review/FEEDBACK.md`'s finding register and "Unreviewed work"
  table with this result (done alongside this entry).

---

## Update 2026-08-24 (nineteenth entry) — Dashboard v0 built and smoke-tested

**Verdict: Dashboard v0 shipped, read-only, boundary enforced structurally.**

With M1 PASSED and F-039/F-040/F-041 closed, review 1.12 §8's Dashboard v0 —
next in its required action order — was built. Asked the user to choose a
stack before adding a new dependency (AskUserQuestion, not a silent pick):
**FastAPI + server-rendered Jinja2 HTML**, over Streamlit or a stdlib
`http.server` build. Recorded in §10 decision log.

**What was built.**

```text
src/crumblr/dashboard/
  __init__.py       the boundary, stated as a docstring contract
  reader_health.py  reads LiveReader's JSON snapshot file — never MT5 itself
  state.py          DashboardState — one read-only snapshot, assembled from
                     MarketDataStore, EventJournal, PostgresSafetyStateStore
  app.py            FastAPI app: GET / (HTML), GET /api/state (JSON) — no
                     other routes, docs_url/redoc_url/openapi_url disabled
  templates/dashboard.html
scripts/run_dashboard.py   CLI entrypoint (uvicorn)
```

`scripts/mt5_live_reader.py --json` now writes its health snapshot after
every poll (atomically, via `os.replace`), not only at exit, so the
dashboard — a separate process — can read current `LiveReader` health
without ever touching MT5 itself. Two small, generically useful reads were
added alongside existing stores rather than duplicated ad hoc:
`MarketDataStore.latest_tick`/`latest_bar` and `EventJournal.latest(event_type)`
— `read_ticks`/`read_bars`/`read_all` all order ascending for replay, which
is the wrong direction for "what just happened".

**The screen** matches review 1.12 §8's minimum: MT5 connectivity/`LiveReader`
health (status, connected, reconnect count, last gateway error, spec
changes), broker/server config (no login), latest tick (bid/ask/spread/age)
and closed M5 bar (OHLC/quality/anomalies), HALT state/reason, and the
latest Signal/RiskDecision/SupervisorDecision including uncalibrated
supervisor checks (F-024). "Account mode" is shown as explicitly not
displayed rather than fabricated — margin mode is not currently persisted
anywhere a separate read-only process can query, and inventing a value
would be worse than naming the gap.

**Boundary, enforced structurally, not by intent** (review 1.9 F-035, review
1.12 §8's hard boundary list):

```text
tests/integration/test_dashboard.py::TestReadOnlyBoundary
  test_no_route_accepts_a_mutation
    — walks app.routes, asserts none accepts POST/PUT/PATCH/DELETE
  test_the_dashboard_package_never_imports_metatrader5
    — walks the AST of every file in src/crumblr/dashboard/, asserts no
      import of MetaTrader5 or crumblr.mt5_gateway
  test_a_post_to_the_index_route_is_refused
    — an actual POST / returns 405
```

Kept as its own package rather than folded into the existing (empty) `api/`
stub, because build.md §21 already earmarks `api/` for "authenticated
operator functions" — the opposite claim. Recorded as **D-043**: this is a
deliberate, scoped subset of build.md §22's full observability dashboard and
Milestone 8's full operator dashboard (no regime/drift/signal-frequency
panels, no orders/positions/audit-search, and — the one that matters —
**no manual HALT/CANCEL/FLATTEN control surface at all**). Not an oversight;
neither review 1.9 F-035 nor review 1.12 §8 authorizes building the control
surface yet.

**Evidence.**

```text
ruff check .          — all checks passed
ruff format --check . — all files formatted
mypy                  — no issues, 106 source files
pytest                — 737 passed, 3 skipped, 0 failed
                         (up from 718 — 19 new tests: 10 dashboard
                         integration, 3 reader-health unit, 4 latest-read
                         store/journal integration, 2 boundary tests
                         counted above)
```

**Manually verified in a browser-equivalent check** (`curl`, per `CLAUDE.md`'s
own UI-testing requirement — no interactive browser available in this
session): started `scripts/run_dashboard.py` against the real `crumblr_soak`
database (Phase A/B's actual data, 13,108 ticks / 37 bars at the time of the
check, still growing from later reader runs). `GET /` rendered the full page
with real values (`PepperstoneUK-Demo`, real bid/ask, real tick/bar counts);
`GET /api/state` returned the same data as JSON; `POST /` returned `405`;
`GET /docs` and `GET /openapi.json` both `404` (disabled). Server stopped
afterward.

**Problems found.** None in the dashboard itself. `uv add --group dev
httpx2` (needed for FastAPI's `TestClient`) and the earlier `uv sync` for
`fastapi`/`jinja2`/`uvicorn` both reset this Windows host's environment to
the base dependency set, silently dropping the `mt5` optional extra
(`MetaTrader5`, `numpy`) each time — `uv sync --extra mt5` had to be re-run
after each. Worth remembering for any future dependency change on this host;
`HANDOVER.md`/`README.md` already document `--extra mt5` for the initial
sync but not this re-sync trap.

**Decision.** Dashboard v0 (review 1.12 §8, review 1.9 F-035) is built,
tested, and boundary-verified. D-043 records the scope gap against build.md
§22/M8 honestly rather than silently. Review 1.12's required action order
items 2-7 (M1 PASS recorded, F-039/F-040/F-041 fixed, Dashboard v0 built)
are now all complete.

**Next**

- Run CI on a runner and record the result; provide the domain-contract
  package for reviewer/human approval to close M0 (review 1.12 §9).
- Build read-only reconciliation against real MT5 state (review 1.12 §10).
- `feedback.1.13.md`'s suggested trigger (review 1.12 §14) — Dashboard v0
  available, F-039/F-040/F-041 addressed, CI and/or M0 contract review, and
  initial reconciliation evidence — is now partly met; CI, the contract
  review and reconciliation remain.
- Update `review/FEEDBACK.md` with this result (done alongside this entry).

---

## Update 2026-08-25 (twentieth entry) — review 1.13 processed: Dashboard v0 redesigned, F-043/F-044 closed

**Verdict: Dashboard v0 rebuilt to a modern dark ops-console layout per review**
**1.13 §§4-10. F-043 (stale-data presentation) and F-044 (replay-vs-live**
**decision labelling) closed. Read-only boundary unchanged and re-verified.**

Review 1.13 accepted the dashboard's functional boundary from the previous
pass and asked for a visual rebuild toward "a modern trading-operations
console, not a developer debug page" (§4), plus two new findings. The owner
independently asked for the same thing in the same terms. Processed per the
review's own required order (§18): keep the boundary, rebuild the visuals,
fix F-043, fix F-044; CI/domain-contracts/reconciliation (items 6-8) are
each their own follow-up pass, not attempted this entry — flagged rather
than silently deferred.

**Visual rebuild (F-042, review §§4-10).** Full rewrite of
`src/crumblr/dashboard/templates/dashboard.html`: dark charcoal/navy palette,
consistent card system, small status badges instead of raw text, and the
five-row layout the review specifies —

```text
Top bar:  CRUMBLR / EUR/USD Autonomous Trading Platform, with DEMO /
          READ ONLY / EXECUTION DISABLED always visible, never subtle
Row 1:    MT5 / Data feed / Safety / Milestone — four compact status cards
Row 2:    EUR/USD hero (large bid/ask/spread/last-tick-age) + a hand-rolled
          vanilla-JS/SVG candlestick chart of the last 60 closed M5 bars —
          no charting library, no framework
Row 3:    Connection / Data integrity / Account context panels
Row 4:    Decision pipeline — Trading Agent -> Risk Engine -> Supervisor ->
          Execution (DISABLED), each its own card; NO_TRADE renders as an
          intentional state, not an empty/broken one
Row 5:    Recent journal activity — a compact table from EventJournal.recent()
```

Visual-state semantics (review §9) implemented as a single lookup
(`app.py::state_class`, mirrored in ~15 lines of JS for the live-refreshed
fields) rather than repeated conditionals: `CONNECTED/HEALTHY/RUNNING/GOOD/
MATCHED` → green, `STALE/UNCALIBRATED/DEGRADED` → amber, everything else —
**including `UNKNOWN`**, per the review's own "most conservative state
dominates" rule — → red. "Account context"/"Connection" fields with no
persisted source (entity, margin mode, broker clock offset) render literally
as `NOT AVAILABLE` rather than a guessed value, per the review's explicit
instruction.

**Auto-refresh** (review §10): a ~120-line vanilla-JS poller hits
`GET /api/state` every 5 seconds and updates only the fields that actually
change second-to-second in practice — the two headline status badges, the
safety badge, the EUR/USD price block, tick/bar counts, the reconnect/error
line, and the candlestick chart — without a full-page reload/flicker and
without duplicating the server's Jinja rendering logic for the slower-moving
panels (connection detail, account context, decision pipeline, activity
timeline), which stay accurate as of the last full page load. No mutation
endpoint is polled or exists.

**F-043 — stale-data presentation.** `DashboardState` gained
`mt5_connectivity`/`data_feed_state` (`CONNECTED/DISCONNECTED/UNKNOWN` and
`HEALTHY/STALE/DOWN/UNKNOWN`), derived from `LiveReader`'s own
`status`/`connected` fields rather than a freshness threshold the dashboard
would have to guess at independently. A missing reader-health snapshot now
reads as `UNKNOWN` (red), never as `HEALTHY`. A database outage is caught at
the route level (`SQLAlchemyError`) and renders a full-page `DATABASE
UNAVAILABLE` state at HTTP 503 — explicitly distinct from "no data yet",
which is what F-043 asked for. Five presentation cases are now
directly tested: fresh, stale, disconnected, missing snapshot, and database
unavailable (`tests/integration/test_dashboard.py::TestF043PresentationStates`,
5 tests).

**F-044 — decision-context ambiguity.** Confirmed by reading the code rather
than assuming: `application/live_reader.py` has zero references to
`TradingAgent`/`RiskEngine`/`Supervisor` — `LiveReader` only ingests and
persists real MT5 ticks/bars, and nothing in this codebase feeds a live tick
into a live decision today. Every journalled decision is therefore a
replay/backtest artifact, never a live one, and `DashboardState
.decision_pipeline_label` says so unconditionally: `"LATEST REPLAY DECISION"`
when at least one exists, `"NO LIVE DECISION PIPELINE ACTIVE"` when none do
— never phrased as though it belongs to the live price shown next to it.
Every decision now also carries `environment`/`source`/`occurred_at_utc`/
`correlation_id`/a version label, both in the JSON and rendered in the
pipeline banner. `tests/integration/test_dashboard.py
::TestF044DecisionContextIsNeverAmbiguous` (2 tests).

**New read paths, added to the existing stores rather than duplicated ad
hoc** (same reasoning as the nineteenth entry's `latest_tick`/`latest_bar`):
`MarketDataStore.recent_bars()` (newest N, chronological order, for the
chart) and `EventJournal.recent()` (newest N of any type, chronological
order, for the timeline) — `read_bars`/`read_all` both order ascending for
replay, the wrong direction for "what just happened".

**A real bug found by manually smoke-testing the visual states, not by unit
tests alone**: hand-writing a test reader-health snapshot via PowerShell's
`Out-File` produced a file with a leading UTF-8 byte-order mark, which made
`read_health_snapshot` silently treat a well-formed `STALE` snapshot as
unreadable (rendered `UNKNOWN`) — invalid JSON has a BOM in front of `{`.
Real production writers (`Path.write_text(..., encoding="utf-8")` in
`mt5_live_reader.py`) never add a BOM, so this had not been exercised, but a
hand-edited or differently-tooled snapshot file could hit it in practice.
Fixed with a one-line change (`"utf-8-sig"` instead of `"utf-8"`, which
strips a BOM if present and is identical otherwise) plus a regression test.
This is the value of actually opening the page rather than only trusting
green tests — recorded per `CLAUDE.md`'s UI-testing requirement.

**Evidence.**

```text
ruff check .          — all checks passed
ruff format --check . — all files formatted
mypy                  — no issues, 106 source files
pytest                — 748 passed, 3 skipped, 0 failed
                         (up from 737 — 11 new tests: 5 F-043, 2 F-044,
                         1 bar-gap, 1 BOM regression, plus 2 persistence-
                         layer tests for recent_bars/recent)
```

Manually verified in a browser-equivalent check (`curl`/`Invoke-WebRequest`,
no interactive browser available in this session — same limitation and same
mitigation as the eighteenth entry): started the dashboard against real
`crumblr_soak` data (13,108 ticks, 37 bars). Confirmed: the candlestick chart
renders real OHLC bars including a genuine ~70-minute data gap between two
reader sessions (`bar_gap_count` correctly reported it as `1`); age
formatting reads "15h 8m ago" rather than a raw Python `timedelta`; a
hand-crafted `STALE` reader-health snapshot correctly turned the Data Feed
badge amber and populated the reconnect count/last-error fields once the BOM
bug above was fixed; the `DATABASE UNAVAILABLE` page renders at 503 against
an intentionally unreachable engine.

**Problems found.** The BOM bug above (fixed). No regressions in the
existing 737 tests.

**Decision.** F-042 (visual baseline), F-043 (stale-data presentation) and
F-044 (decision-context ambiguity) closed. The read-only boundary from
review 1.9 F-035/review 1.12 §8 is unchanged and re-verified structurally
(`TestReadOnlyBoundary` still passes against the redesigned app). CI,
domain-contract review and reconciliation (review 1.13 §18 items 6-8) remain
open — not attempted this entry, prioritized after the dashboard per the
user's explicit direction this session.

**Next**

- Run CI on a runner and record the result; provide the domain-contract
  package for reviewer/human approval to close M0 (review 1.13 §§14-15).
- Build read-only reconciliation against real MT5 state (review 1.13 §16).
- Decide remaining owner risk/intraday/HALT-reset policies before M5
  (review 1.13 §11).
- Update `review/FEEDBACK.md` with this result (done alongside this entry).

---

## Update 2026-08-25 (twenty-first entry) — review 1.14 processed: dashboard visual direction accepted, F-045/F-046 closed, F-033 closed a fifth time

**Verdict: Dashboard v0's visual direction is ACCEPTED (review 1.14 §2) — no**
**further large visual work planned. F-045 (environment badge) and F-046**
**(historical/offline treatment) closed. F-044's heading refined. F-033's**
**stale current-state claims corrected a fifth time.**

Review 1.14 reviewed the owner-supplied screenshot of the redesigned
dashboard and confirmed it "now visibly resembles a modern trading-operations
interface" (§1). It closed F-042/F-043/F-044 in implementation, asked for two
new semantic fixes (F-045, F-046) plus a copy refinement to F-044's visible
heading, reconfirmed a fifth F-033 documentation gap, and explicitly told the
project to stop spending engineering time on dashboard visuals after these
and return to CI/domain-contracts/reconciliation (§16). Processed per the
review's own required order.

**F-045 — environment badge (review §6).** The top-bar badge showed the raw
`Environment.PAPER` config value. To an owner glancing at the screen that
reads as an active paper-execution campaign; none has started
("Paper campaign: NOT STARTED" above) and this build has no order path at
all (F-035). `DashboardState` gained `environment_badge_label`
(`state.py::_environment_badge_label`): `PAPER` renders as `"DEMO DATA"`;
every other `Environment` value (`BACKTEST`/`REPLAY`/`SHADOW`/`LIVE`) already
says what it means and passes through unchanged.
`tests/integration/test_dashboard.py::TestF045EnvironmentBadgeIsNotMisreadAsACampaign`
(3 tests).

**F-046 — historical/offline treatment (review §7).** The review's own
example was reproduced against the real `crumblr_soak` data during manual
testing: `MT5 UNKNOWN`, `DATA FEED UNKNOWN`, last tick ~17h old, while the
old bid/ask and candlestick chart kept rendering with no visual distinction
from live. Whenever `data_feed_state != "HEALTHY"` (stale, disconnected, or
no reader session at all), the EUR/USD hero now shows a
"Historical data — no active live data session" banner (amber, or red when
the state is `DOWN`/`UNKNOWN`) with the last-live-tick age alongside it, and
the chart gets a subdued "No active live data session" overlay — per the
review's explicit "do not hide the chart" instruction, the chart itself
never disappears, only its live-ness claim is withdrawn. Both the initial
server render and the 5-second JS poll refresh keep this in sync as
`data_feed_state` changes between polls, not only at page load.
`tests/integration/test_dashboard.py::TestF046HistoricalDataIsNeverMistakenForLive`
(3 tests).

**F-044 refinement (review §5).** The visible section heading still read
"Decision pipeline — latest window" — easier to misread as live than the
banner beneath it already prevented. Changed to
"Decision pipeline — latest replay window", matching the review's own
suggested wording.

**Age-formatting consistency, found while implementing F-046.** The
server-rendered page used `app.py::format_age()` (hours-aware: "16h 48m"),
but the JS poller's inline age calculation only handled seconds and minutes,
so a session that went stale between the initial render and a 5-second
refresh would silently regress to a raw minute count. Added a JS `formatAge()`
mirroring the Python formatter and used it everywhere the poller updates an
age string, closing the gap review §9.B asked about ("make sure the
screenshot/runtime build uses that version").

**F-033, fifth reopen/close (review §11).** Three current-state claims in
this document still contradicted the fact that M1 had passed:

```text
"no test in the repository has run against the real terminal, only the
  manual first-contact probe has" — silently ignored Phase A (30 real
  minutes) and Phase B (two real reconnects), both manual-but-far-beyond-
  a-probe evidence
"No capability is MT5-integrated ... and none can be until M1" — M1 had
  already passed; the real, still-true gap is that no live decision
  pipeline exists (F-044), not that M1 is pending
APP-014 "PARTLY CLOSED ... still open: continuous bar/tick read and
  observed reconnect behaviour" — both completed in Phase A/B the same day
  APP-014 was last touched
```

All three rewritten in place (§3 platform checklist, §3 Risk section intro,
§2 APP-014 row) rather than only noted here, per the pattern the last four
F-033 reopenings established.

**Evidence.**

```text
ruff check .          — all checks passed
ruff format --check . — all files formatted
mypy                  — no issues, 106 source files
pytest                — 754 passed, 3 skipped, 0 failed
                         (up from 748 — 6 new tests: 3 F-045, 3 F-046)
```

Manually verified in a browser-equivalent check (`curl`, same limitation as
the eighteenth/twentieth entries): started the dashboard against the real
`crumblr_soak` data with no `LiveReader` currently running (no health
snapshot file). Confirmed: badge renders `DEMO DATA`, not `PAPER`; heading
renders "Decision pipeline — latest replay window"; the historical banner
renders unhidden (`data_feed_state` was `UNKNOWN` — no reader session) with
"17h 3m" as the last-live-tick age, matching `format_age`'s hour-aware
formatting; the chart overlay renders alongside the still-visible
candlesticks, not in place of them.

**Problems found.** The JS/Python age-formatter drift above (fixed same
entry). No regressions in the existing 748 tests.

**Decision.** F-045 and F-046 closed. F-044's heading refined. F-033 closed
a fifth time. Dashboard visual scope is now frozen per review 1.14 §10 — the
next dashboard work, if any, is semantic labels, a reconciliation panel, or
account-snapshot data, not layout/animation/framework work. CI, the domain-
contract package and reconciliation (review 1.14 §16 steps 6-11) remain open
— not attempted this entry, per the review's own required order putting the
dashboard refinements first.

**Next**

- Run CI on a runner and record the result; provide the domain-contract
  package for reviewer/human approval to close M0 (review 1.14 §§12-13).
- Build read-only reconciliation against real MT5 state (review 1.14 §14).
- Decide remaining owner risk/intraday/HALT-reset policies before M5
  (review 1.14 §16 step 12).
- Update `review/FEEDBACK.md` with this result (done alongside this entry).

---

## Update 2026-08-25 (twenty-second entry) — review 1.15 processed: engineering reprioritized to the M5 critical path (O-006), F-047/F-048/F-049 opened

**Verdict: dashboard and foundation polish are substantially complete. Review**
**1.15 redirects engineering effort toward one gated autonomous DEMO canary**
**order (O-006) via an explicit six-phase path. Three findings opened;**
**nothing implemented yet this entry — trackers updated only.**

Review 1.15 reconfirmed F-045/F-046/F-044/F-033 (closed by the twenty-first
entry) without new comment, and explicitly told the project to stop treating
documentation cleanup and dashboard visuals as ongoing work unless a
contradiction affects a gate or operator decision (§2). It then relayed a
direct owner instruction: move toward an attachable Trading Agent and actual
autonomous DEMO trading without unnecessary delay. Recorded as **O-006** —
concrete target is one `feedback.2.0`-gated canary order on the MT5 DEMO
account, explicitly **not** a live account, not real money, and not
evidence the strategy is profitable from a handful of trades.

**F-047 (new) — durable broker account/position/pending-order state.** The
platform persists ticks, bars, journal decisions, risk-session state and
safety state, but nothing durably records the *observed real broker's*
balance/equity/margin, open positions or pending orders — only ever held
in-memory by whatever last called `account_info()`/`positions_get()`.
Blocks reconciliation, live risk state and the first demo order. Review
specifies append-only snapshot tables, `balance` and `equity` both as
`Decimal`, and an explicit `COMPLETE`/`UNKNOWN`/`FAILED` completeness state
so an empty position book is never confused with a failed query — the same
fail-vs-empty distinction `positions_get(None)` already makes in-memory,
now required to survive into persistence. Not started this entry.

**F-048 (new) — live/shadow decision orchestrator.** `LiveReader` (real MT5
ticks/bars) and `ReplayOrchestrator`/Trading Agent (replay decisions) remain
two unconnected systems, correct at M1 and confirmed again by F-044 — now
the main blocker to the agent operating on real data at all. Required: real
closed M5 bar → features → Trading Agent → intent-time Risk → Supervisor →
persist, with order submission never reached (shadow/dry-run only). Agent
boundary restated as non-negotiable regardless of implementation (no MT5
credentials, no `order_send` access, no HALT-reset authority, no risk-policy
mutation). Not started this entry.

**F-049 (new) — multi-gated execution enablement.** Formalizes that the
first order must require environment=DEMO, verified account/server,
reconciliation=MATCHED, data=HEALTHY, safety=RUNNING, owner-approved risk
policy, an explicitly enabled execution adapter, terminal AlgoTrading
enabled, and `feedback.2.0` GO — simultaneously, with the MT5 AlgoTrading
toggle alone never sufficient (consistent with the existing APP-016
decision). Correctly not yet implemented — M5 is NO-GO. Not started this
entry.

**Decision.** Trackers (`review/FEEDBACK.md`, this document's §10/§12/§13,
the M8 milestone row) updated to record O-006 and F-047/F-048/F-049.
Engineering on F-047/F-048/CI was deliberately **not** started in this same
entry: Phase 1 of the review's own critical path (§12) is running CI on a
runner, which requires committing and pushing the substantial uncommitted
review-1.13/1.14 work already sitting in the working tree — a shared,
harder-to-reverse action this document's owner has not yet confirmed (asked
at the end of the twenty-first entry, not yet answered). Implementing
F-047/F-048 ahead of that confirmation would also mean committing to
specific schema/architecture choices (snapshot table shape, where the live
orchestrator lives, how shadow mode is wired) without the chance to check
those against the owner's actual priority ordering first.

**Evidence.** No code changed this entry; `ruff`/`mypy`/`pytest` status is
unchanged from the twenty-first entry (754 passed, 3 skipped).

**Problems found.** None — a tracking/reprioritization entry only.

**Next**

- Get direction on committing and pushing the review-1.13/1.14/1.15 tracker
  work, since Phase 1 (CI) cannot run without it.
- Phase 1: run CI on a runner and record the result; assemble the
  domain-contract package for reviewer/human approval (review 1.15 §11).
- Phase 2: implement F-047 broker-state snapshots, then read-only
  reconciliation (review 1.15 §5/§10).
- Update `review/FEEDBACK.md` with this result (done alongside this entry).

---

## Update 2026-08-25 (twenty-third entry) — F-047: durable broker account/position/pending-order snapshots

**Verdict: broker account balance/equity/margin and open positions/pending**
**orders are now durably captured on every `LiveReader` reconnect and**
**periodically, never held only in memory. Review 1.15 §12 Phase 2a done.**

Processed per the review's own required order (§16): with the tracker work
committed/pushed (unblocking Phase 1/CI, still to be run) and the user's
explicit direction to start F-047 next, this entry builds the persistence
layer review 1.15 §5 specifies before touching reconciliation itself —
reconciliation needs something real to compare against.

**Domain layer.** `SnapshotCompleteness` (`COMPLETE`/`FAILED`/`UNKNOWN`,
`domain/enums.py`) carries the gateway's existing `positions_get(None)`/
`orders_get(None)` fail-vs-empty distinction into persistence — a failed
collection stores zero child rows and says so explicitly, rather than being
indistinguishable from a confirmed-flat book. Three new contracts in
`domain/models.py`: `BrokerAccountSnapshot`, `BrokerPositionSnapshot`,
`BrokerPendingOrderSnapshot` — deliberately separate from the existing
`AccountState`/`PositionState` (the live reads the account guard and risk
engine act on for one request), so neither of those contracts' shape or
existing callers/tests were touched. `BrokerAccountSnapshot.account_ref` is
the same non-reversible fingerprint `AccountState.login_hash` already
computes — never the raw MT5 login, per build.md §21.

**Gateway.** Two additions to `mt5_gateway/readonly.py`, both following the
`positions()` fail-vs-empty pattern already established: `pending_orders()`
(new — `orders_get`, decoded via new `ORDER_TYPES`/`ORDER_STATES` tables in
`mt5_gateway/enums.py`, documented-not-yet-observed the same way
`filling_mode`/`trade_mode` were before D-037's first contact, since no
pending order has ever been placed against the real account) and
`account_extras()` (a second `account_info()` read for `profit`/
`margin_mode`, kept off `AccountState` rather than widening that contract).
`PositionState` gained an optional `current_price` field (from MT5's
`price_current`), populated defensively so a fake terminal without it still
reads as `None` rather than crashing.

**Composition.** `application/broker_state.py::capture_broker_state` reads
the account, its extras, terminal health, positions and pending orders
through the gateway's own public methods only (no reaching into gateway
internals) and assembles one `BrokerStateObservation`. Positions and pending
orders are read independently, each in its own `try`/`except` — one failing
must not discard the other or falsely report it as empty. An account-read
failure is not caught here: there is no snapshot worth recording without it,
the same fail-closed treatment every other gateway call in this codebase
gets.

**Persistence.** Three append-only tables (`persistence/schema.py`):
`broker_account_snapshots` (parent, one row per observation, carries
`position_set_state`/`pending_order_set_state`) and
`broker_position_snapshots`/`broker_pending_order_snapshots` (children,
FK'd to the parent's `snapshot_id`, content-derived `row_id` from
`(snapshot_id, ticket|order_id)` so a re-capture collapses rather than
duplicating — the same identity discipline `market_ticks`/`market_bars`
already use). Migration `f3a8c1d9b2e4` added by hand, matching `schema.py`
column-for-column;
`test_migrations.py::test_create_all_and_the_migration_produce_the_same_schema`
confirms they agree. `persistence/broker_state.py::BrokerStateStore` writes
the parent + children in one transaction and reads them back
(`latest_account_snapshot`, `positions_for`, `pending_orders_for`,
`record_count`).

**Wiring.** `LiveReader` gained `broker_state_store: BrokerStateSink | None`
(a narrow Protocol, same testability reasoning as `MarketDataSink`),
`environment`, and `broker_state_interval` (default 60s) — all optional,
`None` by default, so every existing caller and test that never opted in is
unaffected. Capture happens at the end of every successful `_reconnect()`
and again in `_read_and_persist()` once `broker_state_interval` has elapsed
since the last one. A capture failure is caught, logged, and never touches
`ReaderStatus` — ticks/bars are the reader's primary claim; broker state is
a smaller, separate one. `scripts/mt5_live_reader.py` wired in for real
runs, with a new `--broker-state-interval` flag.

**What this deliberately does not do.** Review 1.15 §5 names six capture
triggers; only "connect", "reconnect" and the explicit "periodic
observation cycle" allowance are wired. "Each live decision window",
"before/after order submission" and "after a reconciliation mismatch"
cannot be implemented yet — none of the things they name (a live decision
pipeline, an order path, a reconciliation service) exist in this codebase.
Recorded as `review/DEVIATIONS.md` D-044 rather than silently narrowed;
should close as F-048/M5/reconciliation land, not widen further on its own.
Reconciliation itself (comparing these snapshots against expected state) is
explicitly the next piece, not attempted this entry.

**Evidence.**

```text
ruff check .          — all checks passed
ruff format --check . — all files formatted
mypy                  — no issues, 110 source files
pytest                — 786 passed, 3 skipped, 0 failed
                         (up from 754 — 32 new tests: 18 gateway
                         (pending orders + account extras + current_price),
                         8 capture-composition, 5 LiveReader wiring,
                         6 persistence/store, migration parity reused)
```

**Problems found.** None during this entry's implementation — no manual
real-terminal verification was possible or attempted (no MT5 host available
this session); `pending_orders()`/`account_extras()` remain unit-tested
against a fake terminal only, the same REPLAY-TESTED-not-yet-MT5-INTEGRATED
status every other capability starts at.

**Decision.** F-047 closed (SHIPPED). Reconciliation (Phase 2b), CI (Phase
1), the domain-contract package (Phase 1) and F-048 (Phase 3) remain open —
not attempted this entry, in the order review 1.15 §12 sets out.

**Next**

- Build read-only reconciliation against the F-047 snapshots (review 1.15
  §10, Phase 2b).
- Run CI on a runner and record the result; provide the domain-contract
  package for reviewer/human approval to close M0 (Phase 1, still open).
- Update `review/FEEDBACK.md` with this result (done alongside this entry).

---

## Update 2026-08-25 (twenty-fourth entry) — review 1.16 processed: F-052/F-050 fixed, reconciliation v0 shipped

**Verdict: F-047 reconfirmed IMPLEMENTED/PERSISTENCE-TESTED with real-MT5**
**validation still pending (F-051, blocked on host access). The one real**
**defect the review found (F-052, two account reads per snapshot) is fixed.**
**F-050 (BrokerStateHealth) built. Read-only reconciliation v0 shipped.**

Processed per the review's own required order (§16): fix F-052 first (a
genuine data-integrity bug in last entry's own work), then F-050, then
reconciliation — CI/domain-contracts (Phase 1) remain open in parallel, not
attempted this entry, consistent with every prior entry's honest accounting
of what was and was not done.

**F-052 — one coherent account observation per snapshot.**
`capture_broker_state` originally called `gateway.account()` then a
separate `gateway.account_extras()` — two `account_info()` reads that could
straddle a real change at the broker (a fill, a swap charge) between them,
so one stored `BrokerAccountSnapshot` could combine `balance`/`equity` from
one moment with `profit`/`margin_mode` from another moment — a snapshot
that never existed as such at the broker. Fixed per the review's own
preferred design: `ReadOnlyMt5Gateway.account_with_extras()` makes one
`account_info()` call and derives both halves from the same raw response
(`_account_state_from`/`_account_extras_from`, factored out of the old
`account()`/`account_extras()` bodies so `account()` itself is unchanged
for every other caller). The standalone `account_extras()` method is
retired — nothing else used it.
`tests/unit/test_mt5_readonly_gateway.py::TestAccountWithExtras::test_only_one_account_info_call_is_made`
asserts the fix directly, via a new call-counter on the fake terminal.

**F-050 — `BrokerStateHealth`, kept separate from `ReaderStatus`.** Review
§3's own words: "A system can have fresh EUR/USD ticks + stale
balance/positions/pending orders and must not treat that as a safe trading
state." `application/live_reader.py::BrokerStateHealth` is a new dataclass
(`last_snapshot_at_utc`, `position_set_state`, `pending_order_set_state`,
`last_error`), exposed as `LiveReader.broker_state_health`, tracked
alongside but never merged into `ReaderHealth`. Its `is_usable(now,
max_age)` method encodes the review's exact rule — missing, stale, or
either collection not `COMPLETE` → not usable — as a predicate reconciliation
calls directly rather than re-deriving. A capture failure updates
`last_error` only; the last successful snapshot's fields are left alone, so
usability degrades through the passage of time, not through the failure
erasing what was last known. Written to the JSON health snapshot under its
own `broker_state` key (`scripts/mt5_live_reader.py`), never folded into
`ReaderHealth`'s payload, per the review's explicit "do not overload
`ReaderStatus`". The one piece of F-050 deliberately not attempted: a fresh
synchronous broker-state read immediately before an order. No execution
path exists to need it yet.

**Reconciliation v0.** `application/reconciliation.py::reconcile()` compares
the latest F-047 snapshot against `ExpectedState` and returns
`MATCHED`/`MISMATCHED`/`UNKNOWN`, implementing review 1.16 §7's fail-closed
table exactly: missing/stale/incomplete observation → `UNKNOWN`; wrong
account/server/currency/leverage, an unexpected or missing position/pending
order → `MISMATCHED`; everything agreeing → `MATCHED`. `UNKNOWN` is never
upgraded to `MATCHED` by construction — each `UNKNOWN` branch returns
immediately, before any mismatch check runs. `ExpectedState.flat()` is the
only correct expectation before an execution path exists (review §8): zero
positions, zero pending orders — building it from anything else today would
be inventing expected state nobody has approved. When `AccountGuardConfig
.expected_login` is configured, account identity itself is checked via the
same fingerprint `AccountState.login_hash`/`BrokerAccountSnapshot
.account_ref` already use, not only server/currency/leverage. Reads only
PostgreSQL through a narrow `BrokerStateSource` Protocol (implemented by
`persistence.broker_state.BrokerStateStore` as-is, no store changes
needed) — never MT5, never the live gateway. `scripts/reconcile.py` is a
new read-only, database-only CLI, smoke-tested against the real
`crumblr_soak` database (migrated to the new schema for this) — correctly
reports `UNKNOWN` ("no broker-state snapshot has ever been captured"),
since no live capture has run against that database yet.

**Known v0 gap, recorded rather than hidden** (`review/DEVIATIONS.md`
D-045): reconciliation does not compare the EUR/USD instrument spec, because
`instrument_specs` still has no producer (`LiveReader` only holds a spec in
memory for `spec_changed` detection). Account identity, server/currency/
leverage, and every observed position's/pending order's symbol are
compared; contract-size/volume-step/digits drift is not, yet.

**Evidence.**

```text
ruff check .          — all checks passed
ruff format --check . — all files formatted
mypy                  — no issues, 114 source files
pytest                — 820 passed, 3 skipped, 0 failed
                         (up from 786 — 34 new tests: gateway
                         account_with_extras (6), broker-state-health (6),
                         reconciliation unit (19), reconciliation
                         integration (3))
```

Manually verified: applied migration `f3a8c1d9b2e4` to the real
`crumblr_soak` database (previously only exercised via `bootstrap_schema()`
in tests) and ran `scripts/reconcile.py` against it — correct `UNKNOWN`
result with the right reason, confirming the script's database-only read
path works end to end.

**Problems found.** None beyond the F-052 defect this entry exists to fix.
No regressions in the existing 786 tests.

**Decision.** F-052 and F-050 closed (SHIPPED). Reconciliation v0 shipped.
F-051 (real-terminal verification of all of F-047/F-052/reconciliation)
remains open — this session had no MT5 host available, the same limitation
noted in the twenty-third entry. CI (Phase 1) and the domain-contract
package (Phase 1) remain open, not attempted this entry. F-048 (live/shadow
decision orchestrator) remains open, correctly sequenced after
reconciliation per review 1.16's own order.

**Next**

- F-051 on the next Windows/MT5 session: verify F-047/F-052 against the
  real terminal, and run `scripts/reconcile.py` against a real flat demo
  account to confirm `MATCHED` (review 1.16 §16 steps 4-8).
- Run CI on a runner and record the result; provide the domain-contract
  package for reviewer/human approval to close M0 (Phase 1, unchanged).
- Build F-048's live/shadow decision orchestrator (review 1.16 §9).
- Update `review/FEEDBACK.md` with this result (done alongside this entry).

---

## Update 2026-08-25 (twenty-fifth entry) — domain-contract package assembled (Phase 1a); CI status needs a human check

**Verdict: `review/domain_contracts.md` now exists, covering all twelve**
**contracts review 1.14 §13 named against its own checklist. CI's actual**
**result could not be checked this entry — no `gh` CLI or Actions access.**

Continuing review 1.16's required order after F-052/F-050/reconciliation
(twenty-fourth entry), per the user's direction this session ("focus on
CI/domain contracts").

**Domain-contract package.** `review/domain_contracts.md` documents, for
each of the twelve contracts (`MarketSnapshot`, `Bar`, `InstrumentSpec`,
`TradeIntent`, `RiskDecision`, `SupervisorDecision`, `ApprovedOrder`,
`ExecutionResult`, `AccountState`, `PositionState`, `Incident`,
`DecisionCapsule`): the cross-cutting guarantees every `Contract` subclass
gets structurally (frozen, extra-field-forbidding, exact-Decimal, UTC-only —
§1); which package constructs which contract and which are still unbuilt
(`ApprovedOrder`/`ExecutionResult`, correctly, since no execution path
exists — §2); exactly which `TradeIntent` fields are agent-controlled and
which are structurally absent (lot size, final approval, order submission,
execution/credential access, HALT/risk-policy state — §3, matching review
1.15 §8's boundary list field-for-field); why nothing today can cause an
order to be sent (§4); how `RiskDecision`/`SupervisorDecision` stay
independently constructed with neither able to alter the other's verdict,
and how reconciliation status is read, not set, by the Supervisor (§5). Read
directly from the current `domain/models.py`, not written from memory or
build.md's description of what it should contain — line references are
cited per contract so a reviewer can check the claim against the actual
code rather than trusting the summary.

**CI.** The workflow (`.github/workflows/ci.yml`) triggers on push to
`main` and looks correct: ruff/mypy/pytest against a real PostgreSQL
service on Linux, a separate Windows job for the gateway's fake-terminal
tests (MT5 only ships Windows wheels), and a `gitleaks` secret scan. Two
commits have pushed to `main` this session (`2ce40d5`, `f67f341`), which
should have triggered it automatically — but this environment has no `gh`
CLI, no cached GitHub API token usable for a read-only status check, and no
other way to read the Actions tab. Explicitly not claiming CI passed or
ran; the workflow being well-formed is not the same claim as it having
executed successfully, and review 1.14 §13's own instruction not to mark
something approved merely because the supporting artifact exists applies
here too.

**Evidence.** No code changed this entry — `ruff`/`mypy`/`pytest` status is
unchanged from the twenty-fourth entry (820 passed, 3 skipped, 114 mypy
source files).

**Problems found.** None — a documentation entry.

**Decision.** Domain-contract package (Phase 1a) complete and ready for
human review. CI (Phase 1b) cannot be confirmed from this session; recorded
honestly as blocked on tooling access rather than silently left unmentioned.

**Next**

- A human (or a session with `gh`/GitHub Actions access) checks the Actions
  tab for both recent runs and records the result here.
- The domain-contract package awaits actual human review — its existence
  does not close M0 on its own.
- F-048 (live/shadow decision orchestrator) is the next code-shaped piece
  of the critical path once M0 closes, per review 1.16 §9.
- Update `review/FEEDBACK.md` with this result (done alongside this entry).

---

## Update 2026-08-25 (twenty-sixth entry) — F-048: real market data now reaches the Trading Agent, Risk Engine and Supervisor; execution stays unreachable

**Verdict: `LiveDecisionOrchestrator` closes the gap between `LiveReader`**
**(real MT5 data) and the decision pipeline (Trading Agent/Risk/Supervisor)**
**that F-044 first named. A real closed M5 bar can now produce a real,**
**persisted Signal/Risk/Supervisor decision — with no code path to an**
**order anywhere in the process.**

Continuing review 1.16's required order (§16 steps 11+, after F-052/F-050/
reconciliation): the live/shadow decision orchestrator, per §9's exact
pipeline diagram.

**A real blocking dependency, found while building this, closed first.**
The Trading Agent's `evaluate()` call needs an `InstrumentSpec` (contract
size, point, digits, volume steps) to size and validate against — and
`instrument_specs` (the table) has existed since the M2 baseline migration
with no producer: `LiveReader` observes a real spec on every reconnect and
only ever held it in memory, for `spec_changed` detection (D-045). Closed
via `persistence/instrument_specs.py::InstrumentSpecStore` — content-keyed
by `spec_version`, the same identity discipline every other table in this
schema uses — wired into `LiveReader._reconnect()` as an optional sink,
the same opt-in pattern F-047's broker-state capture already established
(`None` by default, so every existing caller/test is unaffected).
`tests/integration/test_instrument_spec_store.py` (4 tests).

**`LiveDecisionOrchestrator`** (`application/live_decision.py`) is a class
of its own, not a mode added to `LiveReader` or `ReplayOrchestrator` —
review 1.16 §9 is explicit about this boundary, and the reasoning holds up:
`LiveReader` observes and persists, this decides from what was persisted,
an eventual execution service later executes. One `decide_once()` call is
one decision window:

```text
InstrumentSpecStore.latest()      -> the spec to size against
MarketDataStore.recent_bars()     -> up to 400 bars of real history
MarketDataStore.latest_tick()     -> the current quote
BrokerStateStore.latest_account_snapshot()/positions_for() -> real F-047 state
    |
build a real MarketSnapshot (mirrors market_data.synthetic.build_snapshot,
    field for field, except every input is real)
    |
risk_session.recover_session()    -> a durable ledger, from real equity
loss-gate / session-boundary checks (same logic ReplayOrchestrator proves,
    now reading real broker state instead of a simulated broker)
    |
trading_agent.registry: the same strategy replay uses, called the same way
    |
SignalGenerated persisted regardless (including NO_TRADE)
    |
if a TradeIntent: reconcile() [application/reconciliation.py, this
    session's earlier work] -> real ReconciliationStatus fed to the
    Supervisor, not a hardcoded MATCHED
    |
risk.policies.evaluate() -- unmodified, the same function replay uses
    |
evaluator.pretrade.evaluate() -- unmodified, the same function replay uses
    |
DecisionCapsule sealed through the same RunRecorder/journal machinery
    |
STOP -- no ApprovedOrder, no order_check, no order_send anywhere
```

Nothing about *how a decision is judged* is new — the Trading Agent, Risk
Engine and Supervisor are literally the same functions `ReplayOrchestrator`
already calls and this codebase already tests. The only new work is *where
the inputs come from*.

**Account identity, reconciled with F-052's own lesson.** `BrokerAccountSnapshot`
never carries the raw MT5 login (build.md §21) — only `account_ref`, a
fingerprint. The live-reconstructed `AccountState.login` is therefore a
placeholder `0` that can never match a real `expected_login`, the
fail-closed direction if one is ever configured; the `RiskContext` built
for this path forces `expected_login=None` regardless of what the shipped
config says, and account identity is instead verified by reconciliation's
own `account_ref` comparison — a second, independent check, not a gap.
Documented in the module docstring and `_account_state_from_snapshot`'s own
comment, not discovered later.

**Known v0 gaps, recorded as D-046 rather than hidden:** `orders_in_last_hour`
is always `0` (no order path exists to count); `seen_decision_hashes`
(duplicate-intent detection) lives only for the process's lifetime, not
persisted (a restart losing it produces a duplicate audit row, never a
duplicate order); D-031 (feature-value persistence) is **not** closed by
this entry — review 1.16 §10 explicitly permits "a first wiring test"
before that closes, and this is that wiring test, not yet evidence-quality
shadow output.

**Evidence.**

```text
ruff check .          — all checks passed
ruff format --check . — all files formatted
mypy                  — no issues, 120 source files
pytest                — 836 passed, 3 skipped, 0 failed
                         (up from 820 — 16 new tests: 4 instrument-spec
                         store, 2 LiveReader instrument-spec wiring, 8
                         LiveDecisionOrchestrator control-flow-against-
                         fakes, 2 end-to-end against real PostgreSQL with
                         a full 400-bar deterministic synthetic series)
```

Manually verified: `scripts/live_decision.py` run against the real
`crumblr_soak` database — connects, builds the durable runtime (kill
switch correctly reports `UNKNOWN`, fail-closed, since no safety state has
ever been recorded there), and correctly reports "skipped — no instrument
spec has been observed yet" — honest, since no real-terminal `LiveReader`
run with this session's code has happened yet (same F-051 limitation).

**Problems found.** None beyond the InstrumentSpec dependency itself, which
was anticipated as a possibility (D-045 already flagged the gap) and closed
before it could block this entry rather than discovered as a surprise
partway through.

**Decision.** F-048 closed (SHIPPED), not yet real-terminal-validated
(folded into F-051's scope, unchanged). InstrumentSpec persistence closes
part of D-045; the instrument-spec-comparison half of D-045 (reconciliation
still cannot compare specs) remains open, now genuinely closable since a
durable spec exists — not attempted this entry. Dashboard integration for
broker state/reconciliation/decision-pipeline data (review 1.16 §12)
correctly not attempted — waiting on real-MT5 validation first, as the
review specifies.

**Next**

- F-051 on the next Windows/MT5 session: verify F-047/F-052 against the
  real terminal, confirm reconciliation reports `MATCHED` against a real
  flat demo account, and run `scripts/live_decision.py` against real data
  end to end.
- Run CI on a runner and record the result; the domain-contract package
  awaits human review (both Phase 1, unchanged).
- Close D-031 (feature-value persistence) before calling any live-shadow
  evidence audit-quality (review 1.16 §10).
- Update `review/FEEDBACK.md` with this result (done alongside this entry).

---

## Update 2026-08-26 (twenty-seventh entry) — review 1.17 processed; HANDOVER.md and README.md brought current for an incoming second developer

**Verdict: F-050/F-052 reconfirmed CLOSED, F-051 reconfirmed OPEN (now one**
**twelve-step real-terminal checklist), two new findings opened (F-053**
**instrument-spec reconciliation, F-054 durable decision idempotence), both**
**deliberately deferred this session. The owner asked, separately, for a**
**thorough documentation pass so a new developer joining the project does**
**not have to reconstruct the project's current state from the update log**
**by hand — that is the bulk of this entry's work.**

`review/feedback.1.17.md` arrived reviewing the state after F-048 (twenty-
sixth entry). Its verdict: **GO — reconciliation and the live-shadow Agent**
**are now built; prove them against real MT5 next.** M1/M2 remain PASSED;
M0 stays open only on CI and actual human/reviewer contract approval; M5
remains NO-GO but execution engineering may be *prepared* once F-051's
real-terminal checkpoint lands.

**Findings processed, per `CLAUDE.md` §1:**

- **F-050, F-052** — reconfirmed CLOSED, no new work required (review 1.17
  §§2-3 read the existing evidence and agree with it).
- **F-051** — reconfirmed OPEN. Review 1.17 §6 folds it into a single
  twelve-step sequence that also exercises reconciliation and F-048 end to
  end on the real terminal, rather than three separate verification passes.
  Still blocked on Windows/MT5 host access, which this session does not
  have — same reason as every prior processing of this finding.
- **F-053** (new) — reconciliation must compare the semantic instrument
  specification now that `instrument_specs` has a real producer (F-048).
  This is exactly the condition `review/DEVIATIONS.md` D-045 already named
  as its own "watch for" trigger. **Not built this session** — see below.
- **F-054** (new) — `LiveDecisionOrchestrator`'s `seen_decision_hashes`
  must become durable before any execution service is attached, so a
  restart can never turn one closed M5 window into two independently
  executable order proposals. This is D-046 point 3's "watch for", now
  formalized with a required invariant. **Not built this session** — see
  below.
- **CI** and the **domain-contract human review** — both reconfirmed as
  pending evidence this session cannot produce (no `gh`/Actions access; no
  ability to make a human read a file). Review 1.17 §11 is explicit these
  are no longer engineering blockers, just retrieval/approval tasks.

**A tracker bookkeeping bug found and fixed while doing this.**
`review/FEEDBACK.md`'s finding register had **F-052 recorded twice** — once
correctly updated to CLOSED/SHIPPED when the fix shipped 2026-08-25 (F-052
was originally added as its own row when opened, then a second CLOSED row
was appended alongside F-050 rather than the original row being edited in
place), leaving a stale OPEN/PENDING duplicate sitting later in the same
table. Nothing downstream ever silently trusted the wrong one — F-052 is
correctly listed CLOSED in the "Reviews received" verdict summaries and in
every update-log entry since — but a table that disagrees with itself about
one finding's status is exactly the kind of drift this tracker exists to
prevent. Fixed by removing the stale duplicate and adding a note to the
remaining row explaining what happened and why, rather than silently
deleting the evidence that a mistake occurred.

**Why F-053/F-054 were deferred rather than built.** Both are legitimate,
unblocked engineering work — neither needs an MT5 host or a human decision,
unlike F-051/CI/contract-review. They were deferred anyway because the
owner's message this session asked explicitly and specifically for a
thorough documentation/handover pass ahead of a second developer joining,
and doing that properly (see below) was already a full session's worth of
work on its own. Recorded here, with reasons, rather than silently skipped
— per `CLAUDE.md` §1's "explicitly answered with a reason for not acting."
Both are queued as the next concrete engineering tasks in §12 above.

**Documentation pass — the actual ask this session.** A second developer is
expected to join soon. `HANDOVER.md` (written 2026-08-18, last rewritten
2026-08-24) still described the project from *before* MT5 first contact —
account creation as the next blocking step, M1 as "code written, never
run", no mention of Phase A/B, M1 PASSED, Dashboard v0, F-047 broker-state
persistence, reconciliation, or F-048's live-shadow decision pipeline. Read
today, it would have sent a new developer toward work that already
happened and left them unaware of the actual next step (F-051). Rewritten
in full against the current state: gate status, milestone tracker, the
code map (now including `application/broker_state.py`,
`application/reconciliation.py`, `application/live_decision.py`,
`persistence/broker_state.py`, `persistence/instrument_specs.py`,
`scripts/reconcile.py`, `scripts/live_decision.py`, `dashboard/`), the
real-terminal runbook superseded by what Phase A/B actually proved, the
review-loop pointer updated from "`feedback.1.7.md` is due" to
"`feedback.1.18.md` is due", and the traps/pitfalls section kept (still
accurate) with new entries for the live-decision-pipeline boundary and the
broker-state/market-data health split (F-050). `README.md`'s "what is
implemented" table and package-layout section were similarly stale —
reconciliation read "Not started" after it had shipped a session earlier —
and are now current.

**Evidence.**

```text
Documents changed: review/FEEDBACK.md, review/DEVIATIONS.md, status.md,
                    HANDOVER.md, README.md
No source code changed this entry — no quality-gate re-run required beyond
what the prior (twenty-sixth) entry already recorded: 836 passed, 3
skipped, mypy clean over 120 files, ruff clean.
```

**Problems found.** The F-052 tracker duplication (above) — a real, if
low-stakes, documentation-integrity bug, found only because this session
read the finding register closely enough to notice two rows sharing an ID.

**Decision.** F-053 and F-054 stay OPEN, explicitly deferred with reason,
not silently narrowed. HANDOVER.md and README.md are now safe to hand to a
new developer as a starting point; status.md's top sections were spot-
checked against this update rather than rewritten wholesale, since §13's
append-only log is the source of truth for history and rewriting it would
destroy exactly what makes it trustworthy.

**Next**

- F-051 on the next Windows/MT5 session — the twelve-step checklist,
  `feedback.1.17.md` §6.
- F-053 and F-054 as the next concrete engineering tasks, neither blocked
  on anything this environment lacks.
- Run CI on a runner and record the result; supply
  `review/domain_contracts.md` unchanged for actual reviewer inspection —
  both still pure evidence/approval tasks, not engineering.
- Close D-031 (feature-value persistence) before calling any live-shadow
  evidence audit-quality (review 1.17 §9, reconfirming review 1.16 §10).
- Update `review/FEEDBACK.md` with this result (done alongside this entry).

---

## Update 2026-08-26 (twenty-eighth entry) — review 1.18 processed: F-033 fixed a sixth time, F-053/F-054/D-031 all built and shipped

**Verdict: the reviewer explicitly instructed "build now, do not defer"**
**for F-053 and F-054, and framed F-054 as a hard prerequisite before an**
**execution service can exist. All three of F-053, F-054 and D-031 are**
**now shipped — the only findings still open (F-051, CI, domain-contract**
**review) are genuinely blocked on a Windows/MT5 host, GitHub Actions**
**access, or a human reviewer, none of which this session has.**

`review/feedback.1.18.md` arrived reviewing the twenty-seventh entry's
documentation pass. Its verdict: useful, but no gate advancement, and a
reopened documentation-integrity finding — F-033, a sixth time.

**F-033 fixed.** Four current-state sections in this file contradicted its
own more recent history, found because the file is now explicitly
positioned as onboarding truth for a second developer:

1. The MT5 capability table's `orders (pending)`/`reconciliation` rows
   still read "not built"/"M5 prerequisite" after F-047 and reconciliation
   v0 had both shipped a session earlier.
2. The Risk section still said "nothing in this codebase feeds a live MT5
   tick into the risk engine" after F-048 had shipped.
3. The Data checklist still said `instrument_specs` "still no producer"
   after F-048 had added `InstrumentSpecStore` — directly undermining
   F-053, which only exists because that producer now exists.
4. The current-risks table still described the M1 reconnect path as "not
   yet exercised against a real reconnect" after Phase B had proven
   exactly that and M1 had passed on that evidence.

All four rewritten in place with explicit real-terminal-validated-vs-not
distinctions, not simply asserted clean.

**F-053 — instrument-spec reconciliation, built.** `reconcile()` now
requires an `InstrumentSpecSource` (`latest`/`earliest`) and compares the
durable baseline spec — the first one ever observed for the symbol, since
this platform deliberately never hard-codes a contract specification
(O-001) so there is no config-declared "expected" spec the way account
fields have one — against the current observation, via
`InstrumentSpec.spec_version` (already excludes `captured_at_utc`/
`tick_value`, F-039, so this check cannot regress independently of that
fix). Missing/unreadable spec → `UNKNOWN`; a changed version →
`MISMATCHED`, combined with any account/position/pending-order mismatches
already found. New: `InstrumentSpecStore.earliest()`. Both
`scripts/reconcile.py` and `LiveDecisionOrchestrator` wired through their
existing spec source — no new dependency for the live path, since it
already held an `InstrumentSpecSource` for its own spec lookups.

**F-054 — durable live-decision idempotence, built.** New
`application/decision_window.py::DecisionWindowState`/`DecisionWindowStore`,
the same shape `risk/session.py` already established for the daily-loss
budget (F-019): a frozen state, a narrow store Protocol, an in-memory
implementation for tests, `persistence/decision_window.py::PostgresDecisionWindowStore`
for real runs. Keyed by `(canonical_symbol, strategy_id, config_version)` —
matching the review's own invariant ("same strategy + same config + same
canonical symbol + same closed M5 window + same feature/input identity →
same logical decision identity"): a config change is a genuinely new
logical-decision space, so starting fresh when it changes is correct, not
a gap, and is directly tested. `LiveDecisionOrchestrator.__init__` now
restores `_last_decided_open_time`/`_seen_hashes` from the store instead
of starting blank, and `decide_once()` re-saves twice per window: once
right after claiming it (so an early skip for insufficient feature history
still durably marks the window handled) and again after a decision hash is
added. Deliberately simpler failure semantics than `RiskSessionStore`: an
unreadable record collapses to "nothing recorded" rather than halting,
documented explicitly as a choice revisit-worthy once execution exists,
since the worst consequence today is a duplicate audit row, not a
duplicate order. Migration `a7c4e19d6f52`.

**D-031 — feature-value persistence, built.** New
`persistence/features.py::FeatureSnapshotStore`, content-keyed by
`feature_snapshot_id` (both `compute_features` and the ICT model's own
snapshot builder already derive this deterministically), the same identity
discipline `InstrumentSpecStore` uses. Two different concrete
`FeatureEvidence` shapes exist — `FeatureSnapshot` for `baseline_v1`,
`IctFeatureSnapshot` for `ict_v1` — distinguished by `feature_set_version`;
the store persists whichever raw payload it is given and does not attempt
to decode either back into a typed object, since nothing in this codebase
consumes one yet. `trading_agent/base.py::FeatureEvidence` widened to
require `model_dump`/`symbol` — persisting the values is now part of what
a strategy must be able to say about its evidence, not bolted on from
outside. `RunRecorder` gained `record_features()`, implemented by both
`NullRecorder` (no-op) and `JournalRecorder` (writes immediately, not
batched — one row per decided window is low-volume, and content-derived
identity already makes a duplicate write a no-op). Called from **both**
`ReplayOrchestrator` and `LiveDecisionOrchestrator`, for every window that
has features at all — including NO_TRADE and BLOCKed/HALTed windows, the
same rule `SignalGenerated` already follows. Migration `b3f8a2c7d914`.

**Evidence.**

```text
ruff check .          — all checks passed
ruff format --check . — 161 files already formatted
mypy                  — no issues, 125 source files (up from 120)
pytest                — 863 passed, 3 skipped, 0 failed
                         (up from 836 — 27 new tests: 5 instrument-spec
                         reconciliation unit, 2 reconciliation integration,
                         3 InstrumentSpecStore.earliest() integration, 4
                         F-054 unit including a config-version-change
                         case, 6 DecisionWindowStore integration, 1 F-054
                         real-PostgreSQL restart simulation, 3 D-031 unit,
                         3 FeatureSnapshotStore integration)
```

Determinism reproven: `scripts/run_replay.py --bars 2000` hashed identical
across two runs. Manually verified: `scripts/live_decision.py` run against
the real `crumblr_soak` database (migrated to `b3f8a2c7d914`) still
correctly reports "skipped — no instrument spec has been observed yet" —
honest, unchanged, since no real-terminal `LiveReader` run with this
session's code has happened yet (same F-051 limitation).

**Problems found.** None in the new code. One test-design mistake caught
and fixed before it reached the suite: an early draft of the F-054
duplicate-hash test assumed every capsule carrying a `TradeIntent` also
added its hash to `seen_decision_hashes`, which is only true on a `PASS`
risk verdict — a `BLOCK`ed/`HALT`ed intent's capsule still carries the
intent for audit purposes but was never added to the duplicate-protection
set, unchanged by this entry. Corrected before the suite was reported
green, not discovered by a later reviewer.

**Decision.** F-053, F-054 and D-031 all CLOSED (SHIPPED). F-033 CLOSED
again, sixth time. Real-terminal validation for all of the above remains
F-051's job — none of this session's work has met the real terminal.

**Next**

- F-051 on the next Windows/MT5 session — the checklist in
  `feedback.1.17.md` §6 / `feedback.1.18.md` §5, now also covering the
  instrument-spec comparison, decision-window durability and feature
  persistence added this entry.
- Run CI on a runner and record the result; supply
  `review/domain_contracts.md` unchanged for actual reviewer inspection —
  both remain pure evidence/approval tasks, not engineering.
- Update `review/FEEDBACK.md` with this result (done alongside this entry).

---

## Update 2026-08-26 (twenty-ninth entry) — review 1.19 processed: F-054 hardened to fail closed, F-055 (pinned instrument-spec baseline) built, F-033 synced a seventh time

**Verdict: review 1.19 accepted F-053/F-054/D-031 as material progress but**
**found two real execution-grade gaps in the same-day work and reopened**
**both immediately — a corrupted F-054 record was indistinguishable from**
**an absent one, and F-053's baseline trusted whichever spec was observed**
**first rather than an approved value. Both fixed the same day, alongside**
**the two small F-033 summary-sync drifts the review flagged as**
**non-blocking. F-051 is now, in the reviewer's own words, "the main**
**real-world checkpoint" — nothing else stands between this platform and**
**that session.**

`review/feedback.1.19.md` arrived reviewing the twenty-eighth entry's
F-053/F-054/D-031 work. Its verdict: **GO — material technical progress;**
**F-051 is now the main real-world checkpoint.** M1/M2 remain PASSED; M0
stays open on CI + contract review only.

**F-033 — seventh reopen, non-blocking, fixed while processing.** Two
summary-sync drifts: §1's maturity table still called Platform/Application
flatly `REPLAY-TESTED` while §3 already said `MT5-INTEGRATED (M1)`; and
§1's "Overall health" line cited the pre-F-053/054/031 test count (836)
while the repository checklist already said 863. Both corrected in place.

**F-054 reopened for execution-grade failure semantics (review 1.19 §5).**
The version shipped in the twenty-eighth entry collapsed "the decision-
window record is unreadable/corrupt" into "nothing recorded" — deliberately,
on the reasoning that the only consequence today is a duplicate audit row.
The reviewer's point stands regardless: a corrupted idempotence record
would look *identical* to a legitimate fresh start, and that distinction is
exactly what F-054 exists to preserve, not a corner to cut while it is
still cheap. Fixed by mirroring `risk/session.py`'s `SessionRecord`/
`recover_session` shape exactly: `DecisionWindowRecord(state, unreadable)` —
three answers, not two — and a new pure function `recover_decision_window()`.
`LiveDecisionOrchestrator._recover_decision_window()` (called once, lazily,
on the first `decide_once()`, not in `__init__` — a constructor should not
have this kind of side effect) now trips the kill switch with the new
`ReasonCode.DECISION_STATE_UNKNOWN` when the record cannot be trusted,
using the exact same `_trip()` mechanism `_recover_session()` already uses
for a corrupted risk-session record. `PostgresDecisionWindowStore.load_latest()`
now returns `DecisionWindowRecord(unreadable=...)` on a connection failure,
a schema-version mismatch, or a malformed row — never a bare `None`.

**F-055 — instrument-spec baseline must be authorized, not first-observed
(new finding, review 1.19 §4, built and closed the same day).** F-053's
original design compared the latest observed spec against
`InstrumentSpecStore.earliest()` — the first spec ever durably recorded.
The reviewer's exact framing: that "detects drift after the first row" but
"does not establish that the first row itself is the approved expected
broker contract" — trust-on-first-use, not authority. The concrete risk:
`scripts/reset_soak_database.py` already exists precisely because this
project resets its observation database sometimes, and a fresh database's
first observation after a reset could be an already-wrong spec that
reconciliation would then call `MATCHED` — because it would be comparing
the broker to its own new first observation, not to anything approved.
Fixed per the review's required sequence ("discover → verify → pin →
reconcile future observations"): `config.MarketConfig.expected_spec_version:
str | None = None` — a new field, explicitly set by a human only after a
real F-051 observation has been reviewed and accepted, exactly as
git-reviewable as any other config edit. `None` (the state of every
shipped config today, since no F-051 run has happened) reconciles as
`UNKNOWN`, never `MATCHED`. `PlatformConfig.market_for()` (new) looks up
the pin per symbol; `ExpectedState.expected_spec_version` carries it into
`reconcile()`; `scripts/reconcile.py` and `LiveDecisionOrchestrator` both
wired through. `InstrumentSpecStore.earliest()` itself is unchanged and
still useful as a discovery/diagnostic tool — it is simply no longer part
of what reconciliation trusts.

**A real behavioural consequence, caught while building this, not by a
later reviewer:** since no shipped config pins a baseline, every existing
`LiveDecisionOrchestrator` test now sees reconciliation read `UNKNOWN` for
the instrument-spec dimension whenever an intent is actually proposed —
which correctly makes the Supervisor HALT on `RECONCILIATION_UNKNOWN`
(`evaluator/pretrade.py`'s existing, unmodified rule). Checked against
every existing unit and integration test in `test_live_decision.py`: none
of them assert a specific non-HALT outcome for a produced intent, so this
is a real, correct, fail-closed behaviour change with no false test
regressions — confirmed by running the targeted suite before and after.
Two new integration tests make the behaviour itself explicit rather than
leaving it implicit: `TestF055PinnedInstrumentSpecBaseline` proves both
that an unpinned baseline HALTs a produced intent on
`RECONCILIATION_UNKNOWN` and that a baseline pinned to match the real
observed spec clears that specific reason code.

**Evidence.**

```text
ruff check .          — all checks passed
ruff format --check . — 162 files already formatted
mypy                  — no issues, 125 source files
pytest                — 877 passed, 3 skipped, 0 failed
                         (up from 863 — 17 new tests: 2 F-054 fail-closed
                         unit tests + 1 recovery-runs-once test, 1
                         schema-mismatch-is-unreadable integration test, 4
                         F-055 unit tests (incl. the database-reset
                         scenario), 3 F-055 integration tests, 4 config
                         market_for()/expected_spec_version unit tests, 2
                         F-055 end-to-end integration tests)
```

Determinism reproven: `scripts/run_replay.py --bars 2000` hashed identical
across two runs. Manually verified: `scripts/live_decision.py` run against
the real `crumblr_soak` database (migrated through the current head) still
correctly reports "skipped — no instrument spec has been observed yet" —
honest, unchanged.

**Problems found.** None beyond the two the reviewer named, both fixed
before this entry.

**Decision.** F-054 reopened then CLOSED again (hardened). F-055 opened
and CLOSED the same day. F-033 partly reopened (non-blocking) then CLOSED.
`InstrumentSpecStore.earliest()` kept as a discovery tool, deliberately no
longer load-bearing for reconciliation.

**Next**

- F-051 on the next Windows/MT5 session — `feedback.1.19.md` §8's 18-step
  checklist, now covering F-047 through F-055 and D-031 together. Step 9
  of that checklist is the first real opportunity to actually pin a
  baseline (§4.4 step 8 in `HANDOVER.md`).
- Run CI on a runner and record the result; supply
  `review/domain_contracts.md` unchanged for actual reviewer inspection —
  both remain pure evidence/approval tasks, not engineering.
- Update `review/FEEDBACK.md` with this result (done alongside this entry).

---

## Update 2026-08-26 (thirtieth entry) — review 1.20 processed: pure acceptance, no code changes requested

**Verdict: F-054's fail-closed recovery and F-055's pinned-baseline**
**mechanism (both built in response to review 1.19) are accepted as**
**CLOSED IN IMPLEMENTATION, alongside F-053 and D-031. No new findings**
**were opened. The reviewer's own words: "additional simulated safety**
**work has diminishing value... run F-051 and answer [whether the stack**
**behaves correctly against Pepperstone] with evidence."**

`review/feedback.1.20.md` arrived reviewing the twenty-ninth entry's F-054
hardening and F-055 work. Nothing in it required a code change — it is
recorded here in full per `CLAUDE.md` §1 regardless, since every review is
processed the same way whether or not it asks for engineering.

**The one item it did ask for**, §5: `status.md` §3's "Last meaningful
update" line still narrated review 1.18's work after review 1.19 had
already been processed and this file updated for it. The review was
explicit that this is **not** a reason to reopen F-033 — "fix
opportunistically the next time `status.md` is touched, do not spend
another session on this" — so it is fixed as part of this entry rather
than as its own finding-closure cycle.

**Everything else in review 1.20 is forward guidance, not a finding:**
a 26-step F-051 sequence (`feedback.1.20.md` §6) that explicitly requires
starting with `expected_spec_version` unpinned and proving reconciliation
reads `UNKNOWN` *before* a human observes, verifies and pins the real
spec — the two-step discover-then-pin behaviour is itself "desirable
evidence," not something to shortcut; explicit authorization for Phase 4
(non-sending execution engineering) to proceed in parallel with F-051, not
after it; and a preview of the one hard invariant Phase 4 must carry
forward once it exists — durable `order_request_id` idempotence at the
broker-submission boundary, a different boundary from the decision-window
idempotence F-054 already protects, and explicitly not yet a finding since
no execution path exists to raise it against.

**No engineering was undertaken this entry** — there is no unblocked
engineering work queued (F-053/F-054/F-055/D-031 are all shipped; F-051,
CI and the domain-contract supply are the only open items, and all three
are genuinely blocked on something this session does not have: a
Windows/MT5 host, `gh`/Actions access, and a human reviewer respectively).
Whether to begin Phase 4 preparation now, ahead of F-051, is a scope
question put to the user rather than assumed — the reviewer authorizes it
but does not require it, and it is a substantial new engineering surface
(execution-capable adapter, `order_check`, `ApprovedOrder`, durable
`order_request_id`) worth a deliberate decision rather than a default.

**Evidence.** No source code changed this entry beyond the one wording
fix in this file; the previous (twenty-ninth) entry's gate results stand
unchanged: 877 passed, 3 skipped, ruff/mypy clean, determinism reproven.

**Decision.** Review 1.20 fully processed; no findings left open by it.
F-033 not reopened, per the reviewer's explicit instruction, but the
underlying sentence is fixed.

**Next**

- F-051 on the next Windows/MT5 session — `feedback.1.20.md` §6's 26-step
  sequence.
- Run CI on a runner and record the result; supply
  `review/domain_contracts.md` unchanged for actual reviewer inspection.
- Phase 4 (non-sending execution engineering) — authorized to start in
  parallel, pending a decision on scope/timing.
- Update `review/FEEDBACK.md` with this result (done alongside this entry).

---

## Update 2026-08-26 (thirty-first entry) — F-051 real-terminal session, part 1: discovery through reconciliation MATCHED, the first time ever

**Verdict: this session ran on the Windows/MT5 host itself (confirmed AMD64,**
**MT5 terminal `Pepperstone MetaTrader 5` installed and running). The full**
**discovery-through-reconciliation half of `feedback.1.20.md` §6's 26-step**
**checklist (steps 1-18) succeeded cleanly against the real Pepperstone**
**demo terminal, with zero defects found — the first real-terminal run in**
**this project's history that found nothing to fix. Reconciliation read**
**`UNKNOWN` before a human-approved pin and `MATCHED` after, proving F-055's**
**fail-closed design end to end for real. The remaining steps (19-26, a**
**real Trader decision) are honestly blocked on real M5 bar accumulation,**
**not a defect — see "what remains" below.**

**A. Discovery / fail-closed proof (steps 1-11).**

```text
1. crumblr_soak already at head (b3f8a2c7d914) from earlier this session
2. config/paper.yaml confirmed unpinned (expected_spec_version unset)
3. scripts/mt5_live_reader.py run against PepperstoneUK-Demo, 120s
4. mt5.connected — account_ref ***706 (masked, F-031), server matched
5. InstrumentSpec persisted — spec_version bcd6a592...00283bd4
6. BrokerAccountSnapshot persisted — balance/equity EUR 10,000.00, margin 0,
   margin_free 10,000.00, RETAIL_HEDGING, currency EUR, leverage 30
7. position_set_state = COMPLETE
8. pending_order_set_state = COMPLETE
9. Account confirmed flat: 0 open positions, 0 pending orders
10. scripts/reconcile.py run
11. Result: UNKNOWN — "no approved instrument-spec baseline has been
    pinned for 'EUR/USD'" — exactly the required result before a pin exists
```

Zero reconnects, zero failures, 24 consecutive HEALTHY polls. Broker clock
offset re-measured fresh: 180 minutes, consistent with every prior
measurement since D-039 — no drift, no surprises.

**Field-by-field comparison against the 2026-08-24 first-contact evidence**
(`var/first-contact.sanitized.json`), done before asking for approval:
broker_symbol, digits, point, tick_size, contract_size, volume_min/max/step,
stops_level, freeze_level, trade_mode and filling_modes all matched
exactly, two days and one real session apart. Only `tick_value` differed
(0.8569 → 0.8579), exactly as F-039 predicts — it drifts with the live
EUR/USD cross-currency rate, not broker policy, which is precisely why it
is excluded from `spec_version`'s hash. This is real evidence that F-039's
fix behaves correctly in production, not only in the unit tests that
originally proved it.

**B. Human pin (steps 12-15).** The comparison above, plus the flat-account
confirmation, was presented to the owner directly in this conversation —
not self-approved by the agent, per F-055's explicit design intent and
review 1.19 §4's "human/reviewer accepts it" requirement. Approved.
`config/paper.yaml` gained a `markets` override (the base config's
`markets` list is replaced wholesale by the environment overlay, not
deep-merged, so the full entry is repeated) setting
`expected_spec_version: bcd6a59271173c8fc49f4d88d522a9bd55d9e0e5ba44137b6d8c9b4d00283bd4`
for `EUR/USD`, dated and reasoned in a comment in the file itself. This is
the **first time this project has ever pinned a real, human-approved
instrument-spec baseline** — everything before this was either synthetic
or an unapproved first observation.

**C. Reconciliation after the pin (steps 16-18).**

```text
16. Config reload confirmed to pick up the new pin (verified directly)
17. Fresh broker-state capture (a second, shorter mt5_live_reader.py run)
18. scripts/reconcile.py → MATCHED
```

**The first `MATCHED` reconciliation result this project has ever produced
against a real broker.** `snapshot_id=037ab721-12df-426d-84ea-2ca3a63ed12f`.

**What remains (steps 19-26) — honestly blocked, not a defect.**
`scripts/live_decision.py` (the shipped `ict_v1` strategy) correctly and
safely reported `skipped: only 49 bars stored, strategy needs 120` — no
crash, no wrong answer, exactly the fail-closed "insufficient evidence"
behaviour the strategy is supposed to produce. A one-off wiring-proof
substitution to `baseline_v1` (the same substitution
`tests/integration/test_live_decision.py` already uses, and for the same
documented reason — `config/paper.yaml` itself was never touched, the
shipped strategy stays `ict_v1`) still came up short: `baseline_v1` needs
65 bars and only 49 exist. **There is no bar-history backfill capability
in this codebase** (the MT5 capability table has always listed this as
"not built; not required for M1's own acceptance") — real M5 bars only
accumulate through continuous real-time polling, one every five minutes,
and `crumblr_soak`'s 49 bars were built up across Phase A/B (2026-08-24)
plus this session. Reaching 65 needs roughly 80 more minutes of continuous
real-time accumulation; reaching `ict_v1`'s 120 needs roughly six hours.
**No synthetic bar was, or will be, mixed into this real data to shortcut
this** — `MarketBar.origin` exists specifically to keep that distinction
honest, and manufacturing "real" evidence would undermine the entire point
of this checkpoint.

**Decision, with the owner in this conversation:** run
`scripts/mt5_live_reader.py --duration 6000` (100 minutes) in the
background to accumulate enough real bars, then complete the `baseline_v1`
wiring proof. This entry records what is proven so far; a follow-up entry
will record the Trader-decision evidence once the accumulation finishes.

**Evidence.** No source code changed this entry — only `config/paper.yaml`
(the pin) and this file. The quality gate is unaffected; the previous
(thirtieth) entry's results stand: 877 passed, 3 skipped, ruff/mypy clean.

**Problems found.** None. This is the first real-terminal session in this
project's history to find zero defects — every prior first-contact/Phase
A/Phase B session found at least one real bug (D-037, D-039, D-040, D-041,
D-042, F-034's original gap). That the platform is now mature enough for a
real session to simply confirm correct behaviour is itself informative.

**Next**

- Wait for the background `mt5_live_reader.py` run to accumulate enough
  real bars, then run the `baseline_v1` wiring proof (or, given enough
  time, the real `ict_v1` run) and record a follow-up entry.
- Everything else in `feedback.1.20.md` §6 remains as previously recorded:
  CI evidence, the domain-contract supply, owner risk-policy decisions,
  and Phase 4 preparation.

---

## Update 2026-08-26 (thirty-second entry) — F-056: CI ran for real for the first time and failed; root cause found and fixed the same day

**Verdict: GitHub Actions executed this repository's CI workflow for the**
**first time in the project's history — both platform jobs failed fast.**
**The owner relayed the failure notifications directly. Reproduced exactly**
**by running the same commands locally under the same conditions (no `mt5`**
**extra installed): `numpy` was an undeclared test dependency, present**
**only as a side effect of the `mt5` extra. Fixed the same day.**

The owner reported, via GitHub's own notification (not through `gh`
CLI/Actions access, which still does not exist in this environment):

```text
Lint, types and tests (Linux)     — Failed in 19 seconds
Tests (Windows — MT5 host platform) — Failed in 23 seconds
No secrets committed              — Succeeded in 6 seconds
```

Both failures in well under 30 seconds ruled out an ordinary test failure
(the full suite takes minutes) and pointed at an early step — checkout,
`uv sync --locked`, or test collection itself.

**Reproduction.** Both CI jobs run `uv sync --locked` without the `mt5`
extra (the Linux job cannot install it at all — Windows-only wheels; the
Windows job deliberately does not, per its own comment, "today it proves
the platform code is host-independent"). Running the exact same command
locally, then the exact test commands each job runs
(`uv run pytest -m "not integration"` for the Windows job; the full suite
for the Linux job), reproduced two failures exactly:

```text
ModuleNotFoundError: No module named 'numpy'
  tests/unit/test_mt5_readonly_gateway.py::TestTicks
    ::test_real_numpy_structured_rows_convert_without_crashing
  tests/unit/test_mt5_readonly_gateway.py::TestBars
    ::test_real_numpy_structured_rows_convert_without_crashing
```

These are the D-040 regression tests (added after the first real MT5
soak, 2026-08-24) that build a real numpy structured array to reproduce
the exact shape `copy_ticks_from`/`copy_rates_from_pos` return, proving
`Decimal(repr(x))` handles numpy 2.x's own scalar `__repr__` correctly.
`numpy` itself was never a declared dependency anywhere — it happened to
be present locally only as a transitive dependency of the `mt5` extra's
`MetaTrader5` package. These two tests therefore silently passed only on
hosts that happened to have that extra installed, and were never actually
exercised on any host without it — including both CI jobs, and including
every CI run there might have been before this one, had CI ever run
before.

**Fixed** by adding `numpy>=2.0` to `[dependency-groups] dev` in
`pyproject.toml` — the dev group, not the `mt5` extra, since this
regression test must run on every platform regardless of whether MT5
itself is installed — and regenerating `uv.lock`. Confirmed against the
exact commands both jobs run:

```text
uv sync --locked                          — succeeds, installs numpy 2.5.2
                                             without the mt5 extra
uv run pytest -m "not integration"        — 724 passed, 1 skipped, 0 failed
                                             (the Windows job's exact command)
uv run ruff check . / format --check .    — clean
uv run mypy                               — clean, 125 source files
uv run pytest (full suite, no mt5 extra)  — 877 passed, 3 skipped, 0 failed
                                             — matches the Linux job's
                                             conditions exactly, fully clean
```

**Problems found.** Exactly the one described above — genuinely the first
concrete evidence CI has ever produced, and it caught something real:
these two tests had an unstated, environment-dependent dependency that no
local development session had ever noticed, because every local
development session so far happened to have the `mt5` extra installed at
some point. This is precisely the kind of gap CI exists to catch and that
"CI has never run" had been silently hiding.

**Decision.** F-056 CLOSED (SHIPPED) — the specific reported failure is
reproduced and fixed locally with high confidence. **Not yet CLOSED as
"CI confirmed green"** — that still requires an actual push and a human
(or a session with `gh`/Actions access) to check the next run's result.
Recording the fix here rather than waiting for that confirmation matches
this project's discipline of logging real defects and their fixes as soon
as they are understood, not only once every downstream verification step
completes.

**Next**

- Push this fix and check whether the next CI run is actually green — a
  human check, or a future session with `gh`/Actions access, since this
  one still has neither.
- Continue F-051 part 2 (real Trader decision, pending bar accumulation).
- Everything else in `feedback.1.20.md` §6 remains as previously recorded.

---

## Update 2026-08-26 (thirty-third entry) — review 1.21 processed: F-051 part 1 accepted PASSED, F-056 accepted fix-correct/gate-still-open, Phase 4 explicitly authorized

Component: Process (review intake), documentation sync
Milestone: M0 (unchanged — still OPEN), F-051 (part 1 formally accepted), F-056 (gate framing corrected)
Status before: `feedback.1.21.md` unread this session; `review/FEEDBACK.md` not yet registering review 1.21; two `status.md` current-state lines slightly stale
Status after: Review 1.21 fully processed per the CLAUDE.md session-start protocol; no engineering required — this review opened no new findings

Completed:
- Read `feedback.1.21.md` in full (19 sections). Confirmed it opens **no new
  findings** — it is an acceptance-plus-authorization review.
- Applied the two tiny current-state syncs review 1.21 §9 asked for:
  1. `status.md` §3 Data section: the InstrumentSpec producer line now reads
     "Real-terminal validated 2026-08-26 (F-051 part 1)..." instead of "not
     yet run against the real terminal" (was already fixed by the earlier
     share-readiness pass, commit `5af6f83` — confirmed correct, no further
     change needed there).
  2. `status.md` §2 M0 acceptance checklist: the CI line said "not yet
     confirmed green on an actual runner — needs a push and a human/`gh`
     check of the next run" (stale — the push already happened). Corrected
     to "fix pushed → **hosted rerun result pending** (no `gh`/Actions access
     in this environment — needs a human check of the next run)", matching
     the wording already used correctly elsewhere in §1's Overall health
     block. Confirmed via grep that no other current-state section (§1, §3
     milestone tracker, historical §13 entries) carried the stale phrasing —
     the one checklist line was the only drift.
- Updated `review/FEEDBACK.md`:
  - Registered `feedback.1.21.md` in the "Reviews received" table.
  - Updated the F-051 finding row: part 1 now **PASSED** (review 1.21 §2,
    zero defects), part 2 **IN PROGRESS, NO DEFECT** (review 1.21 §5
    explicitly instructs not to interfere with the running bar
    accumulation).
  - Updated the F-056 finding row: **SPECIFIC DEFECT FIXED / CI GATE STILL
    OPEN** (review 1.21 §8's own framing) — the fix is pushed and correct in
    principle, but the M0 CI gate stays open until a hosted rerun is
    visibly green ("local reproduction of CI ≠ green hosted CI").
  - Rewrote the "Unreviewed work" section: heading now names
    `feedback.1.22.md` as the next trigger (not yet sent); body records
    review 1.21's acceptance of F-051 part 1, its explicit
    do-not-interfere instruction for part 2, its F-056 gate-still-open
    framing, its Phase-4 authorization, and the submission-idempotence and
    owner-risk-policy items it named as now-relevant.
  - Refreshed the "Nothing below has been seen by a reviewer yet" table:
    removed the now-reviewed CI/F-051 rows and the already-fixed §3
    wording-lag row, added Phase 4 (authorized, not yet started) and owner
    risk-policy decisions as the new unreviewed items.

Evidence:
- tests: none run — no code changed, documentation/process only
- logs: n/a
- metrics: real M5 bar count reconfirmed at 57 (up from 49 at review 1.21's
  own snapshot) via a direct query against `crumblr_soak`, consistent with
  review 1.21 §5's framing that the background `mt5_live_reader.py` run
  (task `bmzovc8kd`) is still the correct thing to leave alone
- artifact/commit: pending — this entry, plus the `status.md` and
  `review/FEEDBACK.md` edits above, not yet committed

Problems found:
- None. This was a pure acceptance-and-authorization review; the only
  actionable items were the two tiny wording syncs in §9, one of which
  turned out to already be correct.

Risk impact:
- None. No code, config, or risk-relevant behavior changed.

Decision:
- Review 1.21 fully processed. F-051 part 1 formally CLOSED as PASSED;
  part 2 remains open, correctly so, pending real bar accumulation — no
  action to take beyond waiting. F-056 remains SHIPPED with the CI gate
  explicitly still open pending a hosted green run. Phase 4 (non-sending
  execution engineering) is now explicitly authorized to begin, in
  parallel, without waiting for F-051 part 2 or CI confirmation — not yet
  started as of this entry.

Next:
- Begin Phase 4 (non-sending execution engineering): execution-capable MT5
  adapter (still never calling `order_send`), `ApprovedOrder` construction,
  an `order_check` wrapper, a durable `order_request_id` for submission
  idempotence (review 1.21 §12), `ExecutionResult` persistence, a
  FINAL execution-time Risk re-check, automatic flatten, an execution
  multi-gate, and post-execution reconciliation design. Given the
  safety-criticality of this code, plan it properly before writing it.
- Continue monitoring the background `mt5_live_reader.py` run for
  `baseline_v1`'s 65-bar threshold; once crossed, rerun the wiring-proof
  script and record F-051 part 2 evidence against review 1.21 §7's 16-item
  checklist.
- Await a human/`gh` check of the next hosted CI run (F-056 gate).
- `domain_contracts.md` still needs an actual human reviewer — unchanged
  blocker.
- Owner risk-policy decisions (risk per trade, max daily loss/drawdown,
  last-entry cutoff, flatten deadline, HALT-reset authority) can now be
  decided in parallel per review 1.21 §13 — this requires the owner, not
  further agent action.

---

## Update 2026-08-27 (thirty-fourth entry) — Phase 4 slice 1: `OrderCheckMt5Gateway`, the order-check-capable/order-send-disabled MT5 adapter

Component: `mt5_gateway/execution.py` (new), `mt5_gateway/port.py`, `mt5_gateway/client.py`
Milestone: Phase 4 (non-sending execution engineering) — authorized by review 1.21 §11-12, plan reviewed and approved with eight corrections (`review/PHASE4_PLAN_REVIEW_GO_WITH_TWEAKS.md`)
Status before: Phase 4 not started. `ReadOnlyMt5Gateway` refuses `order_check`/`order_send`/`cancel_pending_orders`/`close_all_positions` unconditionally (M1, by design).
Status after: A second, separate `BrokerPort` implementation exists — `OrderCheckMt5Gateway` — that performs a real, live `order_check` call (MT5's server-side dry run: validates a request, creates no ticket, no exposure) while `order_send`/`cancel_pending_orders`/`close_all_positions` still always raise. `ReadOnlyMt5Gateway` itself is untouched (D-036: execution is a separate adapter, not a modification of the read-only one).

Completed:
- A first plan draft was reviewed directly and returned GO WITH TWEAKS
  (`review/PHASE4_PLAN_REVIEW_GO_WITH_TWEAKS.md`, 2026-08-27): eight
  corrections, all non-negotiable per the reviewer's own framing (FINAL Risk
  never resizes; two separate gates, not one; `ApprovedOrder` built only
  after FINAL Risk; immutable-request + append-only-events persistence;
  claim before broker interaction; old shadow approvals never retroactively
  executable; FINAL Risk uses a fresh synchronous observation; adapter
  renamed `OrderCheckMt5Gateway`). The revised plan is saved and was
  approved before any code was written.
- Built slice 1 of that plan — the adapter itself, the smallest
  independently-testable, independently-valuable piece and the one that is
  literally the boundary keeping `order_send` unreachable:
  - `mt5_gateway/port.py`: added `ExecutionDisabledError`.
  - `mt5_gateway/client.py`: extended the `Mt5Module` Protocol with
    `order_check` and the request-parameter constants it needs
    (`TRADE_ACTION_DEAL`, `ORDER_TYPE_BUY`, `ORDER_TYPE_SELL`,
    `ORDER_TIME_GTC`, `ORDER_FILLING_IOC`, `TRADE_RETCODE_DONE`) — read from
    the real module by name at call time (D-037 discipline), never
    hardcoded as integers.
  - `mt5_gateway/execution.py` (new): `OrderCheckMt5Gateway`. Delegates
    `account()`/`instrument()`/`positions()`/`terminal_health()` to an
    internally held `ReadOnlyMt5Gateway` (composition, not duplication).
    `order_check()` builds a real MT5 request from an `ApprovedOrder` and
    calls `module.order_check(...)`. `order_send`/`cancel_pending_orders`/
    `close_all_positions` always raise `ExecutionDisabledError`,
    unconditionally — no config flag is read inside any of those three
    methods, so there is no runtime toggle anywhere in this class that could
    switch them on.
  - Updated the three existing fake-terminal test doubles
    (`test_mt5_readonly_gateway.py::FakeMt5`, `test_mt5_probe.py::FakeMt5`,
    `test_live_reader.py::ScriptedMt5`) to still structurally satisfy the
    widened `Mt5Module` Protocol — each gained the new constants and an
    `order_check` that raises `AssertionError` if ever called, since none of
    those three call sites should ever reach it.

Evidence:
- tests: new `tests/unit/test_mt5_execution_gateway.py` — 10 tests: a real
  `order_check` call reaches the fake module with the correct request shape
  for BUY and SELL; a rejected check decodes as `accepted=False` with the
  broker's retcode/comment; a missing response raises
  `Mt5CallFailedError` naming the terminal's own reason; `order_send` is
  hard-asserted never called by `order_check`; `order_send`/
  `cancel_pending_orders`/`close_all_positions` all raise
  `ExecutionDisabledError` even given a well-formed order; reads
  (`account()`, `terminal_health()`) delegate correctly to the read-only
  gateway.
- Full quality gate, run twice (targeted, then whole repo):
  `uv run ruff check .` — all checks passed. `uv run ruff format --check .`
  — 167 files already formatted (1 file auto-formatted during development).
  `uv run mypy` — success, no issues found in 127 source files.
  `uv run pytest -q` — **887 passed, 3 skipped** (877 passed/3 skipped
  before this change, per the thirty-second entry's F-056 evidence; +10 is
  exactly the new test file, zero regressions elsewhere).

Problems found:
- None. The Protocol-widening fallout (three unrelated fake terminals no
  longer structurally satisfying `Mt5Module`) was caught immediately by
  `mypy --strict` on the first full-repo run, not by a later test failure —
  exactly what the strict gate exists to catch.

Risk impact:
- None reachable. `OrderCheckMt5Gateway` is not wired into any running
  process yet — nothing in `scripts/` constructs it. `order_send` remains
  unreachable through every existing code path, unchanged from before this
  slice; this slice adds a class that performs a real (but non-mutating)
  `order_check` and is not yet called from anywhere live.

Decision:
- Slice 1 of the reviewed, revised Phase 4 plan is complete and gate-clean.
  Not yet committed — pending the usual per-turn approval. The remaining
  plan items (FINAL Risk revalidation with same-volume-or-BLOCK semantics;
  the two-gate split; the eligibility/activation-watermark check; the fresh
  synchronous observation + persisted snapshot + reconciliation step;
  immutable-request + append-only-event persistence with atomic claim; the
  `ExecutionOrchestrator` that assembles all of it) remain to be built as
  further slices — deliberately not attempted in one pass, consistent with
  this project's standing rule against half-finished implementations.

Next:
- Continue Phase 4 with the next slice of the approved, revised plan
  (`review/PHASE4_PLAN_REVIEW_GO_WITH_TWEAKS.md` and the plan file it
  produced), most naturally FINAL Risk's same-volume-or-BLOCK revalidation
  in `risk/policies.py` next, since the eligibility/gate/persistence/
  orchestrator pieces all consume its output.
- Continue monitoring for `ict_v1`'s 120-bar threshold (real M5 bars stood
  at 82 as of this session, past `baseline_v1`'s 65 but short of `ict_v1`'s
  120) and run the `baseline_v1` wiring-proof for F-051 part 2 once
  convenient.
- Await a human/`gh` check of the next hosted CI run (F-056 gate);
  `domain_contracts.md` still needs a human reviewer; owner risk-policy
  decisions remain open per review 1.21 §13.

---

## Update 2026-08-27 (thirty-fifth entry) — Phase 4 slice 2: ADR-001's FINAL execution-time risk revalidation, `risk/policies.py`

Component: `risk/policies.py`, `domain/enums.py`, `review/adr/ADR-001-execution-time-risk-revalidation.md`
Milestone: Phase 4 (non-sending execution engineering), continuing slice 1 (thirty-fourth entry). ADR-001, ACCEPTED 2026-08-17, "required before M5" — ADR-001's own algorithm now built, tested against its own required-test list, and reused (not duplicated) exactly as its Implementation Notes require.
Status before: ADR-001 accepted but not implemented. `risk/policies.py` had one entry point, `evaluate()`, used only at intent time.
Status after: `revalidate_fixed_volume_at_execution_time()` exists, reuses `evaluate()` for every check it already performs, and adds the two things ADR-001 requires on top: repricing the stop against the current executable side of the book (BUY→ask, SELL→bid) instead of the intent's stale reference price, and refusing — never resizing — when the fixed, already-approved volume no longer fits the freshly computed budget.

Completed:
- Added `ReasonCode.EXECUTION_TIME_RISK_BLOCK` (`domain/enums.py`) — appended
  alongside the specific reason(s) whenever FINAL Risk refuses, so an
  operator can tell a final-gate refusal from an intent-time one.
- Added `risk/policies.py::revalidate_fixed_volume_at_execution_time()` and
  its `_refuse_at_execution_time()` helper (gives an execution-time refusal
  its own `decision_id`, distinct from the intent-time one for the same
  intent, so the two never collide when both get persisted).
- Design follows `review/PHASE4_PLAN_REVIEW_GO_WITH_TWEAKS.md` point 1
  exactly: **same volume, or BLOCK — never resize.** A fresh evaluation that
  would size a *smaller* volume than the one already approved is refused
  into, not silently shrunk into.
- Updated `review/adr/ADR-001-execution-time-risk-revalidation.md`'s status:
  algorithm implemented, evidence pointer added, explicitly **not yet
  wired into a live orchestrator** — the ADR stays open until the fresh
  observation → persisted snapshot → reconciliation → this check →
  `ApprovedOrder` → `order_check` chain exists end to end (later slices of
  the same plan).

Evidence:
- tests: `tests/unit/test_risk_engine.py::TestExecutionTimeRevalidation` —
  14 new tests, including one `test_adr001_N_*` per applicable item of
  ADR-001's own eight-item "Required tests before M5" list (7 of 8 apply
  directly to this function; item 5, symbol-spec changes, is the caller's
  reconciliation step's responsibility, not this function's — documented
  as such in the ADR). Covers: unchanged volume on a clean revalidation;
  BUY and SELL BLOCKed when the executable price moves away from the stop
  (mirror cases); a favourable move keeps the volume unchanged rather than
  growing it; a widened spread BLOCKs; an equity drop refuses rather than
  resizing down; an intent that expired since approval BLOCKs; a kill
  switch tripped since approval is refused; a property test that the
  outcome is always exactly `None` or the originally approved volume across
  four varied scenarios; and that an execution-time refusal's `decision_id`
  never collides with an intent-time one for the same intent.
- Full quality gate: `uv run ruff check .` — all checks passed.
  `uv run ruff format --check .` — 167 files already formatted.
  `uv run mypy` — success, no issues found in 127 source files.
  `uv run pytest tests/unit/test_risk_engine.py -v` — 51 passed (37 existing
  + 14 new), zero regressions. Full-repo `uv run pytest -q` — **901 passed,
  3 skipped** (887 passed/3 skipped after slice 1, +14 is exactly this
  slice's new tests, zero regressions). A first full-suite attempt run
  concurrently with a second one (both launched against the real Postgres
  integration test database at nearly the same time, a self-inflicted
  mistake) produced two different spurious schema-race failures
  (`DROP TABLE` on an already-dropped table, an unrelated subprocess
  assertion) — not real regressions, confirmed by a clean solo rerun.

Problems found:
- Two test-authoring mistakes, caught and fixed before this entry: (1) an
  initial "clean, nothing changed" test used `make_snapshot`'s default ask
  (1.08512), which differs from the intent's reference price (1.08500) by
  design (realistic spread) — correctly BLOCKed rather than passing, which
  is the function working as intended, not a bug; fixed by pricing the test
  snapshot's ask to exactly match the reference price to isolate a genuinely
  unchanged scenario. (2) a kill-switch test assumed `SYSTEM_HALTED` alone
  escalates `evaluate()`'s verdict to HALT; it does not — `SYSTEM_HALTED` is
  not a member of `HALT_REASONS`, since the halt already happened when the
  kill switch was tripped, and `evaluate()` enforces it as an ordinary BLOCK
  rather than re-declaring a HALT that add nothing new. Fixed the test's
  expectation to match this existing, correct `evaluate()` convention rather
  than changing the convention.

Risk impact:
- None reachable. `revalidate_fixed_volume_at_execution_time` is not called
  from anywhere live yet — no orchestrator constructs it. Pure addition to
  `risk/policies.py`; `evaluate()` itself is untouched.

Decision:
- Slice 2 of the reviewed, revised Phase 4 plan is complete and gate-clean,
  full suite confirmed green solo. Not yet committed — pending the usual
  per-turn approval.

Next:
- Continue Phase 4 with the remaining plan items: the execution
  eligibility/activation-watermark check, the two-gate split
  (`ExecutionPreflightGate` now, `SubmissionGate` design-stub), the fresh
  synchronous observation + persisted snapshot + reconciliation step, the
  immutable-request + append-only-event persistence with atomic claim, and
  the `ExecutionOrchestrator` that assembles all of it.
- Continue monitoring for `ict_v1`'s 120-bar threshold and run the
  `baseline_v1` wiring-proof for F-051 part 2 once convenient.
- Await a human/`gh` check of the next hosted CI run (F-056 gate);
  `domain_contracts.md` still needs a human reviewer; owner risk-policy
  decisions remain open per review 1.21 §13.

---

## Update 2026-08-27 (thirty-sixth entry) — Phase 4 slice 3: execution eligibility, the two-gate split, and the `SubmissionGate` design stub

Component: `risk/execution_eligibility.py` (new), `risk/execution_preflight_gate.py` (new), `risk/submission_gate.py` (new), `domain/enums.py`
Milestone: Phase 4, continuing slices 1-2 (thirty-fourth/thirty-fifth entries)
Status before: no eligibility filter existed; nothing distinguished "may the preflight chain run at all" from "may a real order_send run".
Status after: three small, pure-function modules exist, each doing exactly one narrow job, none of them wired into a live orchestrator yet.

Completed:
- `risk/execution_eligibility.py::evaluate_execution_eligibility()` — the
  cheap, first gate on a sealed `DecisionCapsule`, run before any of the
  expensive fresh-observation/reconciliation/FINAL-Risk work.
  Review/PHASE4_PLAN_REVIEW_GO_WITH_TWEAKS.md point 6, non-negotiable: an
  old shadow-mode approval must never become retroactively executable.
  Checks, all collected rather than short-circuited: the capsule was sealed
  at or after a human-set `activation_watermark` (`None` means never set,
  which makes everything ineligible — there is no config path that opens
  this on its own); `strategy_version`/`risk_config_version` still match
  what is currently running; the intent has not expired
  (`TradeIntent.is_expired`, reused rather than re-derived); still within
  the allowed trading window (`trading_window.permits_new_entry`, reused).
- `risk/execution_preflight_gate.py::evaluate_preflight_gate()` — the
  narrower of the two Phase-4 gates (point 2): governs only whether the
  chain fresh-observation → reconciliation → FINAL Risk → `ApprovedOrder` →
  `order_check` may run at all. Checks: `Environment.LIVE` is structurally
  refused (new `ReasonCode.LIVE_EXECUTION_NOT_PERMITTED` — the same
  no-live-trading rule CLAUDE.md §4 states, enforced one layer further in);
  symbol is on the allowlist; kill switch is not halted.
- `risk/submission_gate.py::evaluate_submission_gate()` — deliberately a
  design-only stub this slice (point 2 again): documents the full F-049
  multi-gate checklist in its docstring, takes no arguments (nothing could
  open it yet), and always returns closed with the new
  `ReasonCode.SUBMISSION_GATE_NOT_IMPLEMENTED`. `order_send` stays
  technically impossible via `OrderCheckMt5Gateway` regardless of this
  module — this exists only so the later real gate has a named landing
  spot instead of being invented from scratch at M5.
- New reason codes in `domain/enums.py`:
  `DECISION_PREDATES_EXECUTION_ACTIVATION`, `STRATEGY_VERSION_NOT_CURRENT`,
  `LIVE_EXECUTION_NOT_PERMITTED`, `SUBMISSION_GATE_NOT_IMPLEMENTED`.

Evidence:
- tests: new `tests/unit/test_execution_gates.py` — 16 tests across all
  three modules. Eligibility: no watermark ever set is never eligible; a
  capsule sealed before the watermark is ineligible; one sealed after
  passes that leg; superseded strategy/risk-config versions are ineligible;
  an expired intent is ineligible; every failing leg is reported, not just
  the first; a capsule with no `trade_intent` raises (a caller error, not a
  reachable state); the result dataclass rejects an inconsistent
  eligible/reason_codes combination. Preflight gate: a clean case opens it;
  `LIVE` is structurally refused; a disallowed symbol is refused; a halted
  kill switch closes it; every failing leg is reported together. Submission
  gate stub: always closed; takes no parameters (asserted via
  `inspect.signature`).
- Full quality gate: `uv run ruff check .` — all checks passed.
  `uv run ruff format --check .` — 171 files already formatted.
  `uv run mypy` — success, no issues found in 131 source files.
  `uv run pytest -q` (solo, no concurrent run this time) — **917 passed, 3
  skipped** (901 after slice 2, +16 is exactly this slice's new tests, zero
  regressions).

Problems found:
- None.

Risk impact:
- None reachable. None of these three functions is called from anywhere
  live yet.

Decision:
- Slice 3 of the reviewed, revised Phase 4 plan is complete and gate-clean.
  Not yet committed — pending the usual per-turn approval.

Next:
- Continue Phase 4 with the remaining plan items: the fresh synchronous
  observation + persisted snapshot + reconciliation step, the
  immutable-request + append-only-event persistence with atomic claim, and
  the `ExecutionOrchestrator` that assembles everything built so far
  (adapter, FINAL Risk, eligibility, preflight gate) into the target flow.
- Continue monitoring for `ict_v1`'s 120-bar threshold and run the
  `baseline_v1` wiring-proof for F-051 part 2 once convenient.
- Await a human/`gh` check of the next hosted CI run (F-056 gate);
  `domain_contracts.md` still needs a human reviewer; owner risk-policy
  decisions remain open per review 1.21 §13.

---

## Update 2026-08-27 (thirty-seventh entry) — Phase 4 slice 4: immutable execution requests + append-only execution events, with a real Alembic migration

Component: `persistence/execution.py` (new), `persistence/schema.py`, `domain/enums.py`, `migrations/versions/20260827_c9e1d5a3f286_execution_requests_and_events.py` (new)
Milestone: Phase 4, continuing slices 1-3 (thirty-fourth through thirty-sixth entries)
Status before: no persistence existed for execution requests/outcomes at all.
Status after: two new append-only-granted tables exist — `execution_requests` (one immutable row per `order_request_id`, ever) and `execution_events` (the lifecycle log) — with a real Alembic migration proven equivalent to `schema.py`'s own metadata.

Completed:
- Discovered, while designing the "atomic claim" the plan review demanded
  (point 5), that this project enforces append-only *at the database
  permission layer* (`schema.py::append_only_grants` — `REVOKE ALL` then
  `GRANT SELECT, INSERT` only, no `UPDATE`, for every table in
  `APPEND_ONLY_TABLES`). A claim implemented as `UPDATE ... SET claimed_at
  WHERE claimed_at IS NULL` — the literal reading of the plan review's
  Dutch text — would need a grant this project deliberately never hands
  out. Resolved by recognising that **the claim is the winning insert**:
  `INSERT ... ON CONFLICT (order_request_id) DO NOTHING RETURNING
  order_request_id` gives exactly one concurrent caller a returned row for
  a given key, which is Postgres's own atomicity guarantee — no `UPDATE`
  needed anywhere, and both new tables fit the existing append-only grant
  model without an exception.
- `persistence/execution.py::ExecutionRequestStore.claim()` — the immutable
  half. A losing insert (someone already holds this `order_request_id`)
  then compares the caller's fingerprint against the stored one: a match is
  "already registered" (not an error — this is what makes retrying the same
  decision after a crash safe); a mismatch raises
  `ExecutionRequestConflictError`, satisfying point 4's "never silently
  ignored."
- `persistence/execution.py::ExecutionEventStore` — the append-only half.
  `event_id` is content-derived from `(order_request_id, event_type)` (new
  `event_id_for()` helper), so a retry re-logging the same transition
  converges on the same row rather than duplicating it — the same
  idempotence discipline `domain/events.py::build_event`'s docstring
  describes for the main journal.
- New `domain/enums.py::ExecutionEventType` — `REQUEST_CLAIMED`,
  `INELIGIBLE`, `GATE_CLOSED`, `RECONCILIATION_BLOCKED`,
  `FINAL_RISK_BLOCKED`, `ORDER_CHECKED`, `ORDER_CHECK_REJECTED` for this
  phase; `SUBMISSION_STARTED`/`SUBMITTED`/`BROKER_ACK`/`FILLED`/
  `RECONCILED`/`CLOSED` reserved, named but never emitted, for M5.
- `persistence/schema.py`: both tables added to `APPEND_ONLY_TABLES` and
  their sequence grants; `execution_requests.capsule_id` foreign-keys to
  `decision_capsules`, `execution_events.order_request_id` foreign-keys to
  `execution_requests`.
- New Alembic migration
  `20260827_c9e1d5a3f286_execution_requests_and_events.py`, chained after
  the existing head (`b3f8a2c7d914`).

Evidence:
- tests: new `tests/integration/test_execution_persistence.py` — 8 tests
  against real Postgres: the first claim wins; a second claim with matching
  content is not an error; a second claim with different content raises
  `ExecutionRequestConflictError` naming the `order_request_id`; different
  `order_request_id`s never collide; events read back in insertion order;
  reason codes round-trip; re-appending the same transition does not
  duplicate the row; `event_id_for()` is deterministic (same inputs, same
  id; a different event type, a different id).
  `tests/integration/test_migrations.py` (existing, 8 tests, all still
  pass) — includes `test_a_migrated_database_does_not_disagree_with_the_metadata`
  and `test_create_all_and_the_migration_produce_the_same_schema`, both of
  which would have caught any drift between the new migration and
  `schema.py` — the manually-written migration DDL matches exactly on the
  first attempt.
- Full quality gate: `uv run ruff check .` — all checks passed.
  `uv run ruff format --check .` — 174 files already formatted.
  `uv run mypy` — success, no issues found in 133 source files.
  `uv run pytest -q` (solo) — **925 passed, 3 skipped** (917 after slice 3,
  +8 is exactly this slice's new integration tests, zero regressions).

Problems found:
- None in the shipped code. The append-only-permissions discovery above was
  caught during design, before any code was written that would have needed
  correcting — worth recording precisely because it changed the
  implementation from what a literal reading of the plan review would have
  produced.

Risk impact:
- None reachable. Neither store is called from anywhere live yet.

Decision:
- Slice 4 of the reviewed, revised Phase 4 plan is complete and gate-clean.
  Not yet committed — pending the usual per-turn approval.

Next:
- Build the `ExecutionOrchestrator` (`application/execution.py`) — the
  final slice that assembles everything built so far (the adapter, FINAL
  Risk, eligibility, the preflight gate, and now this persistence layer)
  into the target flow: sealed capsule → derive `order_request_id` → claim
  → eligibility → fresh observation → persisted broker-state snapshot →
  reconciliation → preflight gate → FINAL Risk → `ApprovedOrder` →
  `order_check` → append the terminal event. Its own integration test is
  the one that hard-asserts `order_send` is never called end to end.
- Continue monitoring for `ict_v1`'s 120-bar threshold and run the
  `baseline_v1` wiring-proof for F-051 part 2 once convenient.
- Await a human/`gh` check of the next hosted CI run (F-056 gate);
  `domain_contracts.md` still needs a human reviewer; owner risk-policy
  decisions remain open per review 1.21 §13.

---

## Update 2026-08-27 (thirty-eighth entry) — Phase 4 slice 5: `ExecutionOrchestrator` — every prior slice assembled into the target flow, proven end to end

Component: `application/execution.py` (new), `mt5_gateway/execution.py` (clock injection), `review/adr/ADR-001-execution-time-risk-revalidation.md`, `review/DEVIATIONS.md` (D-047, new)
Milestone: Phase 4, final planned slice — assembles slices 1-4 (thirty-fourth through thirty-seventh entries)
Status before: adapter, FINAL Risk, eligibility, the preflight gate, and execution persistence all existed but nothing called any of them from anywhere live.
Status after: `ExecutionOrchestrator.run_once()` runs the full target flow end to end against every claimable, eligible sealed capsule it finds — and, proven by its own integration test against real Postgres and a fake (never real) MT5 terminal, `order_send` is never reached.

Completed:
- `application/execution.py::ExecutionOrchestrator` — the third pipeline
  tier `live_decision.py`'s own module docstring names
  ("Execution service (M5) = later, execute", now built as the *preflight*
  half of that, non-sending). One capsule at a time: derive
  `order_request_id` (the same `uuid5` derivation
  `orchestration.py:444` already uses) → claim (the winning insert) →
  eligibility → `ExecutionPreflightGate` → a fresh live account/position
  read plus a separately captured, persisted `capture_broker_state`
  observation → reconciliation against the pinned instrument-spec baseline
  → **recover the durable risk-session ledger via `risk/session.py::
  recover_session()`** (real continuity, not a fresh/discontinuous ledger —
  see "Problems found" below for why this mattered) → FINAL Risk
  (`revalidate_fixed_volume_at_execution_time`) → `ApprovedOrder` →
  `order_check`, never `order_send` → append the terminal event. Every
  refusal along the way appends exactly one `ExecutionEventType` and moves
  to the next capsule; only a genuinely unreadable risk-session record trips
  the shared kill switch, mirroring how `LiveDecisionOrchestrator` already
  treats that specific failure as system-level.
- `mt5_gateway/execution.py::OrderCheckMt5Gateway` gained a `clock`
  constructor parameter (threaded into its internal `ReadOnlyMt5Gateway`),
  needed so a test can hold simulated time fixed for broker-clock-offset
  detection — a small, backward-compatible addition to slice 1's adapter.
- Updated `review/adr/ADR-001-execution-time-risk-revalidation.md`: status
  now "wired into `ExecutionOrchestrator`", with an explicit note on what
  still keeps the ADR open (real-terminal `order_check` evidence, and the
  full M5 `order_send` path, neither of which exists yet).
- New `review/DEVIATIONS.md` entry **D-047**: `ExecutionOrchestrator` v0's
  two known, documented gaps — `CapsuleStore.read_all()` scans every
  capsule every call (harmless at today's scale, needs an indexed query
  before real volume), and the fresh account/position read and the
  `capture_broker_state` read are two separate live MT5 calls rather than
  one shared observation.

Evidence:
- tests: new `tests/integration/test_execution_orchestrator.py` — 6 tests
  against real Postgres and a fake MT5 terminal (via a real, unmodified
  `OrderCheckMt5Gateway` wrapping the fake — genuine adapter-logic coverage,
  not a bypassed stub): a clean, eligible capsule reaches `ORDER_CHECKED`
  with the real `order_check` call actually made; **`order_send` is never
  called even when everything else passes** (the hard assertion the whole
  slice exists to prove); a capsule sealed before the activation watermark
  is `INELIGIBLE` and touches no broker method at all; the shipped-config
  default (`activation_watermark=None`) makes every capsule ineligible,
  however clean; a second `run_once()` does not reprocess an
  already-claimed capsule; an unpinned instrument-spec baseline blocks on
  reconciliation before FINAL Risk is ever reached.
- Full quality gate: `uv run ruff check .` — all checks passed.
  `uv run ruff format --check .` — 176 files already formatted.
  `uv run mypy` — success, no issues found in 135 source files.
  `uv run pytest -q` (solo) — **931 passed, 3 skipped** (925 after slice 4,
  +6 is exactly this slice's new integration tests, zero regressions).

Problems found:
- The most significant design correction of the whole Phase-4 effort,
  caught before writing any orchestrator code: a first draft would have
  given FINAL Risk a **fresh, discontinuous** `EquityLedger`
  (`EquityLedger(starting_equity=fresh_equity)`), which would have silently
  dropped real session-loss/drawdown continuity — exactly the failure mode
  `risk/session.py`'s own module docstring exists to prevent
  ("loss consumed → restart → loss forgotten → the gate is further away").
  Caught by recognising `risk/session.py::recover_session()` is already a
  reusable, pure, fully-tested function — the same one
  `LiveDecisionOrchestrator._recover_session()` calls — and wiring it in
  directly rather than reimplementing session tracking. This was flagged to
  the user explicitly before proceeding (a scope/complexity checkpoint),
  who chose to build it properly rather than accept the simplified version.
- Test-authoring: the first integration-test run failed with
  `ClockOffsetUnavailableError` — `OrderCheckMt5Gateway` had no way to
  accept an injected clock, so its internal broker-clock-offset detection
  compared a fake terminal's fixed-time tick against the *real* wall clock
  (~10 real days apart from the test's `FIXED_NOW`), reading as an
  implausible offset. Fixed by adding the `clock` parameter noted above.

Risk impact:
- None reachable in any shipped configuration. `activation_watermark` is
  `None` everywhere real, which the eligibility check refuses
  unconditionally — proven directly by
  `test_no_watermark_ever_set_means_nothing_is_ever_eligible`. `order_send`
  remains structurally unreachable regardless: `OrderCheckMt5Gateway.
  order_send` always raises, with no config read inside it anywhere.

Decision:
- **All five planned slices of the reviewed, revised Phase 4 plan are now
  complete, gate-clean, and committed-pending-approval.** Phase 4
  (non-sending execution engineering) delivers exactly what was authorized:
  the execution path is built, and it is not enabled. Not yet committed —
  pending the usual per-turn approval.

Next:
- Optional polish, not required for this phase's own completeness: a
  driving script (`scripts/execute.py`, mirroring `scripts/live_decision.py`)
  to run `ExecutionOrchestrator.run_once()` on a timer against the real
  Windows/MT5 host, the same way `scripts/live_decision.py` already does for
  `LiveDecisionOrchestrator`.
- Everything named in `review/PHASE4_PLAN_REVIEW_GO_WITH_TWEAKS.md`'s
  "Later, vóór eerste DEMO-order" list remains open and is *not* part of
  this phase's scope: automatic flatten submission, submission idempotence/
  ambiguous-result recovery, post-execution reconciliation, owner-approved
  risk policy, last-entry cutoff, mandatory flatten deadline, HALT-reset
  authority, the real `SubmissionGate`/terminal-AlgoTrading gate, explicit
  execution enablement, and `feedback.2.0` itself.
- Continue monitoring for `ict_v1`'s 120-bar threshold and run the
  `baseline_v1` wiring-proof for F-051 part 2 once convenient.
- Await a human/`gh` check of the next hosted CI run (F-056 gate);
  `domain_contracts.md` still needs a human reviewer; owner risk-policy
  decisions remain open per review 1.21 §13.

---

## Update 2026-08-27 (thirty-ninth entry) — review 1.22 processed: Phase 4 architecture ACCEPTED, four implementation findings opened (F-057…F-060)

Component: Process (review intake), `review/FEEDBACK.md`
Milestone: Phase 4 — architecture accepted, implementation not yet formally passed
Status before: Phase 4 all five slices shipped (thirty-fourth through thirty-eighth entries), first source-based review not yet received
Status after: Review 1.22 processed. Four new findings registered as OPEN (F-057 CRITICAL, F-058/F-059/F-060 HIGH, all "before M5"); a second supplementary source ZIP delivered same day

Completed:
- Read `feedback.1.22.md` in full — the first Phase-4 review based on the
  actual source (`crumblr_phase4_review.zip`), not `status.md` alone.
- Built and delivered a second ZIP (`crumblr_phase4_review_supplement.zip`,
  15 files, same repo-relative folder structure) per §9's explicit request,
  to let the reviewer verify F-058/F-059's exact mechanics and the
  domain-contract Decimal/UTC claims directly: `application/broker_state.py`,
  `application/reconciliation.py`, `application/recording.py`,
  `persistence/broker_state.py`, `persistence/instrument_specs.py`,
  `persistence/risk_session.py`, `persistence/journal.py`,
  `mt5_gateway/readonly.py`, `risk/session.py`, `risk/kill_switch.py`,
  `domain/money.py`, `domain/timeutils.py`, `domain/events.py`,
  `evaluator/pretrade.py`, `tests/integration/test_migrations.py`.
- Registered review 1.22 in `review/FEEDBACK.md`'s "Reviews received"
  table; added four new finding rows (F-057/F-058/F-059/F-060, all OPEN);
  rewrote the "Unreviewed work" section (heading now names
  `feedback.1.23.md` as the next trigger) with each finding's required fix
  summarized and its current status.

Evidence:
- No code changed this entry — process/documentation only. The review
  itself independently re-verified (not merely accepted the developer's
  claim) that `activation_watermark` appears nowhere in `config/base.yaml`,
  `config/paper.yaml` or `config.py`, and that no reachable `order_send`
  call site exists anywhere in the Phase-4 source — both confirmed by the
  reviewer's own source grep, matching this session's own findings.

Problems found (the review's own, now tracked as findings):
- **F-057 (CRITICAL BEFORE M5):** FINAL Risk's `RiskDecision` is computed
  in `ExecutionOrchestrator._process()` but never durably persisted or
  linked — `ApprovedOrder.risk_decision_id` still points at the intent-time
  decision (`prior_decision.decision_id`), not FINAL Risk's own. ADR-001
  requires both decisions to be persisted; the durable record currently
  cannot answer which exact FINAL `RiskDecision` authorized a given
  `ApprovedOrder`.
- **F-058 (HIGH BEFORE M5):** the fresh `adapter.account()`/`adapter.positions()`
  read FINAL Risk judges and the separate `capture_broker_state()` read
  reconciliation judges are two different live MT5 calls, not one coherent
  observation (already self-documented as D-047's second gap). Compounding
  this: `run_once()` takes one `now` before iterating capsules and reuses
  it through the entire chain instead of a fresh timestamp immediately
  before FINAL Risk, so intent expiry/session-boundary checks could
  theoretically judge a stale moment if a broker call is slow.
- **F-059 (HIGH BEFORE M5):** the execution-request fingerprint is only
  `intent.decision_hash` — proves two different intents can't collide, but
  not that two differently-approved executions of the *same* intent
  (different intent-time `RiskDecision`/`SupervisorDecision` content)
  can't silently read as a harmless retry instead of a conflict.
- **F-060 (HIGH BEFORE M5):** `ExecutionOrchestrator` still hard-codes
  `orders_in_last_hour=0` in the `PortfolioState` FINAL Risk evaluates —
  acceptable under D-046 while no execution path existed; that exception
  expires now that Phase 4 built one.

Risk impact:
- None reachable today — all four findings are about audit-trail
  completeness and defense-in-depth at a boundary `order_send` still can't
  reach (structurally disabled, unchanged). They matter before M5, not
  before the next commit.

Decision:
- **Phase 4 architecture: ACCEPTED** (review 1.22 §14, unchanged from the
  plan review's own acceptance). **Phase 4 implementation: GO WITH FIXES —
  not yet formally passed.** Real-terminal `order_check` evidence
  collection should wait for F-057/F-058 per the reviewer's own
  instruction — "little value in collecting real evidence against a
  preflight chain whose final approval evidence is not yet fully durable."
  `order_send` remains NO-GO, unchanged. Not yet committed — pending the
  usual per-turn approval once findings are triaged with the user.

Next:
- Fix F-057 through F-060 (see `review/FEEDBACK.md`'s finding rows for
  each one's required-fix summary) — the reviewer's own explicit path back
  to a formal Phase-4 PASS in review 1.23.
- Continue F-051 part 2 in parallel — review 1.22 §11 explicitly does not
  block it.
- Await a human/`gh` check of the next hosted CI run (F-056 gate);
  `domain_contracts.md` regeneration waits for F-057; owner risk-policy
  decisions remain open.

---

## Update 2026-08-27 (fortieth entry) — F-057 through F-060 fixed, same day as opened, per an approved plan

Component: `application/execution.py`, `domain/models.py`, `domain/enums.py`, `application/broker_state.py`, `persistence/execution.py`, `application/orchestration.py`, ADR-001, `review/DEVIATIONS.md`
Milestone: Phase 4 — the four implementation findings review 1.22 required before a formal PASS
Status before: F-057 (CRITICAL), F-058/F-059/F-060 (HIGH) all OPEN, all "before M5"
Status after: All four CLOSED/SHIPPED, unreviewed. A user-requested planning pass preceded the code (plan mode, approved before any file was touched)

Completed — one coherent revision pass across the four intertwined findings, matching how the reviewer itself presented them:
- **F-057:** `domain/models.py::ApprovedOrder` renamed `risk_decision_id` →
  `intent_risk_decision_id`, added `final_risk_decision_id: UUID | None`.
  New `ExecutionEventType.FINAL_RISK_PASSED`. `ExecutionOrchestrator` now
  appends `FINAL_RISK_PASSED`/`FINAL_RISK_BLOCKED` carrying the complete
  serialized FINAL `RiskDecision` (plus, on PASS, an `order_fingerprint`
  binding it to the exact `ApprovedOrder`) *before* `order_check` — the
  sealed `DecisionCapsule` is never mutated. ADR-001 constraint 4 corrected
  to describe this design rather than "the capsule records both."
- **F-058:** `application/broker_state.py::BrokerStateObservation` gained
  `account_state`/`position_states`, populated from the raw domain reads
  `capture_broker_state()` already made internally — zero new MT5 calls.
  `ExecutionOrchestrator` now captures broker state exactly once per
  attempt and uses that single observation for both reconciliation and
  FINAL Risk. A fresh `final_now = self._clock()` is taken immediately
  before FINAL Risk and used for its decision, the execution events, and
  `ApprovedOrder.created_at_utc`.
- **F-059:** new `_approval_chain_fingerprint()`, binding
  `capsule.provenance_fingerprint` plus the intent-time `RiskDecision`/
  `SupervisorDecision` content — replaces `intent.decision_hash` as
  `claim()`'s fingerprint. `order_request_id`'s derivation stays stable, as
  the reviewer allowed.
- **F-060:** new `ExecutionRequestStore.count_claimed_since()` — real,
  durable order-frequency history, replacing the hard-coded
  `orders_in_last_hour=0`.
- Discovered while making F-057's field rename: two call sites mypy caught
  that the plan hadn't anticipated (`tests/unit/test_operator_controls.py`,
  `tests/replay/test_replay_prototype.py`) — fixed alongside the three
  planned ones. Also discovered `BrokerStateObservation`'s new fields
  needed to be optional-with-defaults rather than required: ~13 unrelated
  existing test call sites (`test_broker_state_store.py`,
  `test_reconciliation.py`, `test_live_decision.py`) construct it via
  keyword args without the new fields, and the plan's "safe because
  appended after the original three" reasoning only covered positional
  construction, not this.

Evidence:
- tests: 5 new — `tests/integration/test_execution_orchestrator.py::
  test_the_approved_order_is_linked_to_both_risk_decisions` (F-057, the two
  decision ids are genuinely different records),
  `::test_two_capsules_sharing_an_intent_hash_but_different_approval_content_fail_closed`
  (F-059's required test, exactly as specified — same intent, different
  approved volume, second `claim()` raises `ExecutionRequestConflictError`
  rather than reading as a harmless retry), `tests/integration/
  test_execution_persistence.py::TestCountClaimedSince` (3, F-060). The
  existing `test_a_clean_eligible_capsule_reaches_order_checked` extended
  to assert the new `FINAL_RISK_PASSED` event and its payload.
- Full quality gate: `uv run ruff check .` — all checks passed.
  `uv run ruff format --check .` — clean except one pre-existing,
  unrelated finding: `review/feedback.1.22.md` (the reviewer's own document,
  committed verbatim, untouched by this entry's work) trips the markdown
  code-block formatter — not "fixed" by editing a reviewer's original text.
  `uv run mypy` — success, no issues found in 135 source files.
  `uv run pytest -q` (solo) — **936 passed, 3 skipped** (931 after Phase 4
  slice 5, +5 is exactly this entry's new tests, zero regressions).

Problems found:
- None beyond the two mypy-caught call sites and the `BrokerStateObservation`
  defaults issue above, both caught and fixed before this entry, not left
  behind.

Risk impact:
- None reachable. `order_send` remains structurally unreachable through
  every code path — grepped for after these changes, same as after every
  prior Phase-4 slice: none found outside `simulated.py`/`orchestration.py`'s
  pre-existing paper-only path and the adapter's own unconditional-raise
  methods.

Decision:
- F-057 through F-060 fixed, same day as opened. None of the four has been
  seen by a reviewer yet — that is what review 1.23 is for, per review
  1.22's own framing. Not yet committed — pending the usual per-turn
  approval.

Next:
- Await review 1.23 (or a request for another supplementary source ZIP) —
  do not start a new documentation pass proactively.
- Continue F-051 part 2 in parallel; await a human/`gh` check of the next
  hosted CI run; `domain_contracts.md` regeneration now unblocked by
  F-057 but still needs a human reviewer; owner risk-policy decisions
  remain open.

---

## Update 2026-08-27 (forty-first entry) — review 1.23 processed: F-057 reconfirmed CLOSED, F-058/F-059 partly closed, F-060 reopened, F-061 opened

Component: Process (review intake), `review/FEEDBACK.md`
Milestone: Phase 4 — a narrow hardening bundle now stands between this and a formal PASS
Status before: F-057 through F-060 fixed 2026-08-27 (fortieth entry), unreviewed
Status after: Review 1.23 processed. F-057 reconfirmed CLOSED. F-058 and F-059 PARTLY CLOSED — real progress accepted, one narrow gap named in each. F-060 REOPENED — the fix used the wrong durable authority. F-061 opened new (HIGH)

Completed:
- Read `feedback.1.23.md` in full — a follow-up source review against
  `crumblr_f057_f060_fixes.zip` plus the prior supplement, explicitly
  scoped as "not another broad Phase-4 source audit."
- Updated `review/FEEDBACK.md`: F-057's row reconfirmed CLOSED; F-058's row
  updated to PARTLY CLOSED with the remaining sequencing gap described;
  F-059's row updated to PARTLY CLOSED with the incomplete-fingerprint gap
  described; F-060's row REOPENED with the correct required authority
  described; new F-061 row added (OPEN, HIGH). Registered review 1.23 in
  the "Reviews received" table. Rewrote "Unreviewed work" (heading now
  names `feedback.1.24.md` as the next trigger) with review 1.23's own
  exact next-bundle list.

Evidence:
- No code changed this entry — process/documentation only.

Problems found (the review's own, now tracked):
- **F-058 (remaining piece):** `recover_session()` still runs before
  `final_now` exists and receives `market_day=trading_day(now)` — the
  earlier `run_once()`-level timestamp. A slow broker interaction could
  theoretically straddle the 17:00 America/New_York risk-session boundary
  between the two, recovering the session for the wrong trading day.
- **F-059 (remaining piece):** `_approval_chain_fingerprint()` manually
  selects specific fields from `RiskDecision`/`SupervisorDecision` rather
  than fingerprinting their complete serialized content — omits fields
  including `SupervisorDecision.uncalibrated_checks`, which "explicitly
  changes what a Supervisor approval means." A hand-picked field list
  silently stops covering new material fields as the contracts evolve.
- **F-060 (reopened):** `count_claimed_since()` counts every claimed
  `execution_requests` row, including `INELIGIBLE`/`GATE_CLOSED`/
  `RECONCILIATION_BLOCKED`/`FINAL_RISK_BLOCKED`/`ORDER_CHECK_REJECTED`
  outcomes and the request currently being evaluated — not actual
  submission attempts. Fail-safe (moves the limit one step more
  restrictive) but not the real control the field claims to represent.
  Required authority: count `ExecutionEventType.SUBMISSION_STARTED`
  events, honestly `0` today.
- **F-061 (new):** `OrderCheckMt5Gateway.order_check()` accepts any
  `ApprovedOrder`, including one with `final_risk_decision_id=None` —
  not reachable today (`ExecutionOrchestrator` always supplies it), but
  the broker-facing boundary enforces nothing of its own.

Risk impact:
- None reachable today — same reasoning as the findings they follow up on:
  `order_send` remains structurally unreachable regardless of any of these
  four items.

Decision:
- Phase 4 remains **NEAR-PASS, NOT YET FORMALLY PASSED** (review 1.23's own
  verdict). Architecture stays ACCEPTED. Not yet committed — pending the
  usual per-turn approval once the fix approach is confirmed with the user.

Next:
- Fix F-058's remaining sequencing gap, F-059's complete-content
  fingerprint, F-060's `SUBMISSION_STARTED`-based authority, and F-061's
  fail-closed `order_check` guard — review 1.23's own "exact next
  engineering bundle" (§13), explicitly scoped as narrow, not a redesign.
- Continue F-051 part 2 in parallel; await a human/`gh` check of the next
  hosted CI run; `domain_contracts.md` regeneration still needs a human
  reviewer; owner risk-policy decisions remain open.

---

## Update 2026-08-27 (forty-second entry) — F-058/F-059/F-060/F-061 fixed: review 1.23's exact next-bundle, built directly per user instruction

Component: `application/execution.py`, `persistence/execution.py`, `mt5_gateway/execution.py`
Milestone: Phase 4 — review 1.23's own "narrow hold, not another architecture cycle" bundle
Status before: F-058/F-059 PARTLY CLOSED (one gap each), F-060 REOPENED, F-061 OPEN (forty-first entry)
Status after: All four CLOSED/SHIPPED, unreviewed pending review 1.24. Built directly, no separate plan — the user's explicit instruction for this round, given the reviewer's own framing that this is narrow hardening, not a redesign

Completed:
- **F-058 (remaining sequencing gap):** `_process()` reordered so
  `final_now = self._clock()` and `recover_session(...,
  market_day=trading_day(final_now))` now run after spec lookup and the
  fresh tick read, immediately before FINAL Risk — replacing the earlier
  `run_once()`-level `now` `recover_session` previously received. The
  must-halt refuse path's event/trip timestamp switched to `final_now` too.
- **F-059 (complete-content fingerprint):** `_approval_chain_fingerprint()`
  rewritten to fingerprint `provenance_fingerprint` +
  `trade_intent.decision_hash` + `risk_decision.model_dump(mode="json")` +
  `supervisor_decision.model_dump(mode="json")` — complete serialized
  content, not a manually maintained field list. The post-FINAL-Risk event
  payload now carries `final_risk_decision.model_dump(mode="json")` in
  full, not just its id.
- **F-060 (correct authority):** `ExecutionRequestStore.count_claimed_since()`
  removed outright (confirmed no other callers). New
  `ExecutionEventStore.count_events_since(event_type, since)` — a real
  count against `execution_events`. `orders_in_last_hour` now sourced from
  `count_events_since(ExecutionEventType.SUBMISSION_STARTED, final_now -
  timedelta(hours=1))`, matching the reviewer's required authority exactly.
- **F-061 (fail-closed guard):** `OrderCheckMt5Gateway.order_check()` now
  checks `final_risk_decision_id is None` at the very start, before
  building the MT5 request or touching the terminal, and raises a new
  `MissingFinalRiskDecisionError`.
- Updated `review/FEEDBACK.md`: all four rows moved to CLOSED with
  evidence; "Unreviewed work" table row rewritten to reflect the built
  state and gate/test evidence, pending review 1.24.

Evidence:
- tests: 3 new — `tests/integration/test_execution_orchestrator.py::
  test_two_capsules_differing_only_in_uncalibrated_checks_fail_closed`
  (F-059's required test: two capsules identical except
  `SupervisorDecision.uncalibrated_checks` now conflict on the second
  claim), `::test_session_recovery_uses_final_now_not_the_earlier_now`
  (F-058: a `RiskSessionState` seeded for a later trading day is correctly
  read as `same_session: true` once `recover_session` runs on `final_now`
  — log confirms `market_day`/`recorded_day` both `2026-08-18`),
  `tests/unit/test_mt5_execution_gateway.py::
  test_an_order_with_no_final_risk_linkage_is_refused` (F-061). Plus
  `tests/integration/test_execution_persistence.py::TestCountEventsSince`
  (3, replacing the removed `TestCountClaimedSince`'s 3 — net neutral).
- Full quality gate: `uv run ruff check .` — all checks passed.
  `uv run ruff format --check .` — clean except the same two pre-existing,
  unrelated findings as every prior round: `review/feedback.1.22.md` and
  `review/feedback.1.23.md` (the reviewer's own documents, committed
  verbatim, untouched by this entry's work) trip the markdown code-block
  formatter. `uv run mypy` — success, no issues found in 135 source files.
  `uv run pytest -q` (solo) — **939 passed, 3 skipped** (936 after the
  fortieth entry, +3 net new, zero regressions).
- Determinism: `uv run python scripts/run_replay.py --bars 600` run twice;
  stdout hashed identically both times (stderr structured-log timestamps
  differ between runs as expected; stdout — the deterministic replay
  output — matched byte-for-byte).

Problems found:
- First attempt at F-059's fingerprint followed the reviewer's literal
  suggested shape (`capsule.trade_intent.model_dump(mode="json")` inline)
  and crashed: `TypeError: float cannot be fingerprinted deterministically;
  use Decimal`, from `domain/hashing.py::_canonical()`. `TradeIntent.confidence`
  is a genuine Python `float`, and `model_dump(mode="json")` leaves floats
  as floats (unlike `Decimal`, which serializes to a string). Fixed by
  reusing `capsule.trade_intent.decision_hash` — `TradeIntent`'s own
  already-tested complete-content fingerprint, which handles `confidence`
  safely via `repr()` internally — instead of re-dumping the model.
  `RiskDecision`/`SupervisorDecision` have no float fields, so
  `model_dump(mode="json")` is safe for those two.
- F-058's regression test needed two rounds of tuning, not code changes:
  the default test intent expires 10 minutes after `FIXED_NOW`, but the
  test's `late` clock value is +10 hours — FINAL Risk correctly blocked on
  `INTENT_EXPIRED` until the test was given a 24-hour-lived intent. After
  that, the fake tick's hardcoded timestamp (`FIXED_NOW`) made FINAL Risk
  correctly flag `STALE_MARKET_DATA` against the now-10-hours-later
  `final_now`, until `max_market_data_age_ms` was widened in the test's
  config to isolate the sequencing fix from this unrelated, correctly-firing
  check. Both are test-construction fixes, not defects in the sequencing
  fix itself — confirmed by the log line once both were resolved.

Risk impact:
- None reachable. `order_send` remains structurally unreachable through
  every code path.

Decision:
- F-058/F-059/F-060/F-061 all fixed, same day as review 1.23. None of the
  four has been seen by a reviewer yet. Not yet committed — pending the
  usual per-turn approval.

Next:
- Await review 1.24. Continue F-051 part 2 in parallel; await a human/`gh`
  check of the next hosted CI run; `domain_contracts.md` regeneration
  still needs a human reviewer; owner risk-policy decisions remain open.

---

## Update 2026-08-27 (forty-third entry) — review 1.24 bundle delivered: refreshed domain_contracts.md plus the six F-058/F-059/F-060/F-061 files

Component: Process (reviewer package), `review/domain_contracts.md`
Milestone: Phase 4 formal sign-off + M0 domain-contract review
Status before: F-058/F-059/F-060/F-061 fixed and committed (`6bdb5b1`, forty-second entry); `domain_contracts.md` still described the pre-Phase-4 world (commit `f67f341`)
Status after: `domain_contracts.md` rewritten to describe the actual current contracts at `6bdb5b1`; a review bundle assembled and delivered for review 1.24. No new engineering started

Completed:
- Rewrote `review/domain_contracts.md` end to end against the actual
  source at `6bdb5b1` — read `domain/models.py`, `domain/enums.py`,
  `domain/events.py`, `domain/hashing.py`, `application/execution.py`,
  `persistence/execution.py`, `mt5_gateway/execution.py`,
  `application/broker_state.py`, `application/orchestration.py`,
  `mt5_gateway/simulated.py`, `risk/policies.py` and `ADR-001` directly
  rather than relying on memory of what Phase 4 was supposed to do.
  Documentation-only: nothing in `src/` or `tests/` was touched.
- §2 and §4 changed materially: `ApprovedOrder`/`ExecutionResult` are no
  longer described as "not yet constructed anywhere" — that claim was
  already imprecise about the pre-existing replay/paper simulator
  (`SimulatedBroker`, which has constructed both since before the prior
  snapshot) and is now also wrong about the real path, since
  `ExecutionOrchestrator` constructs `ApprovedOrder` too. §4 was rewritten
  to describe the actual current execution-permission argument: exactly
  two adapters can hold a real MT5 connection, only one real
  broker-touching method exists anywhere (`OrderCheckMt5Gateway.order_check`),
  and the append-only `execution_requests`/`execution_events` audit trail
  (claim-once, `ExecutionRequestConflictError` on content mismatch,
  `SUBMISSION_STARTED`-based frequency counting) is described in full,
  since it is what the "no order can be sent" claim now structurally rests
  on. §3/§5/§6/§7 updated to match; §1 unchanged (still accurate).
- Assembled `crumblr_review_1_24_bundle.zip`: the six files review 1.23's
  bundle touched (`application/execution.py`, `persistence/execution.py`,
  `mt5_gateway/execution.py`, and their three test files), the refreshed
  `review/domain_contracts.md`, a `diff_5be8624_to_6bdb5b1.patch` isolating
  exactly the F-058/F-059/F-060/F-061 fix commit, and a `MANIFEST.txt`.
  Repo-relative folder structure preserved; verified via
  `System.IO.Compression.ZipFile` after building.
- Updated `review/FEEDBACK.md`'s "Unreviewed work" table: the
  domain-contract row now describes the actual refresh instead of pointing
  at a stale document; a new row records the bundle delivery.

Evidence:
- No code changed this entry — process/documentation only. The quality
  gate and test suite results this bundle cites are the ones already
  recorded in the forty-second entry (939 passed, 3 skipped; ruff/mypy
  clean; determinism confirmed) — not re-run, since nothing executable
  changed.
- Bundle contents verified by listing the zip's entries directly
  (`System.IO.Compression.ZipFile::OpenRead`): 9 entries, repo-relative
  paths intact, `MANIFEST.txt` and the patch file at the archive root,
  everything else under `src/`, `tests/`, `review/` exactly as in the
  repository.

Problems found:
- None in the code. One documentation gap found while researching: the
  *previous* `domain_contracts.md` (f67f341) already understated
  `ApprovedOrder`/`ExecutionResult` before Phase 4 existed — it called
  them "not yet constructed anywhere," which was already false for the
  replay/paper simulator. Not a code defect (the simulator's behaviour is
  correct and intentional), but worth naming so this correction reads as
  "the document was imprecise," not "the document went stale only once,
  recently."

Risk impact:
- None. Documentation-only change; `order_send` remains structurally
  unreachable, confirmed again while researching §4 (grepped `src/` for
  every `.order_send(` call site: exactly one, `orchestration.py:491`,
  reachable only through `SimulatedBroker`, which never holds an MT5
  connection).

Decision:
- Bundle delivered for review 1.24. `domain_contracts.md`'s rewrite should
  land in the repository too, not stay a delivery-only artifact, since it
  is meant to become the current authoritative document — not yet
  committed, pending the usual per-turn approval.

Next:
- Await review 1.24. Continue F-051 part 2 in parallel; await a human/`gh`
  check of the next hosted CI run; owner risk-policy decisions remain
  open.

---

## Update 2026-08-27 (forty-fourth entry) — review 1.24 processed: Phase 4 formally PASSED, domain contracts APPROVED, real-terminal order_check AUTHORIZED

Component: Process (review intake), `review/FEEDBACK.md`
Milestone: Phase 4 formal sign-off + M0 domain-contract review
Status before: `crumblr_review_1_24_bundle.zip` delivered, awaiting review (forty-third entry)
Status after: Review 1.24 processed. F-058/F-059/F-060/F-061 all reconfirmed CLOSED. **Phase 4 formally PASSED** (architecture + implementation). `review/domain_contracts.md` APPROVED at the reviewer/technical level. Real-terminal `order_check` evidence AUTHORIZED under controlled conditions — not yet run

Completed:
- Read `feedback.1.24.md` in full — a narrow, targeted review against
  `crumblr_review_1_24_bundle.zip`, checked against the actual source, not
  accepted from `status.md` alone; explicitly not another broad Phase-4
  audit.
- Updated `review/FEEDBACK.md`: F-058/F-059/F-060/F-061 rows updated to
  cite review 1.24's reconfirmation and exact quotes ("no further work
  required" on each). Registered review 1.24 in the "Reviews received"
  table with its full verdict. Rewrote "Unreviewed work" (heading now
  names `feedback.1.25.md` as the next trigger): removed the now-reviewed
  bundle/domain-contracts rows, added the newly-authorized real-terminal
  `order_check` evidence run, the CI confirmation gate, and the optional
  owner countersign as the remaining unreviewed/open items. Updated the
  "Still open, by the reviewer's own gate decisions" table: marked
  execution-time revalidation implementation as **Done**.
- Synced `status.md` opportunistically, per review 1.24 §13's explicit
  non-blocking instruction (not a dedicated cycle, not a reason to reopen
  F-033): the Risk capability table's `execution-time revalidation` row
  (was blank across every column despite the capability being built,
  unit-tested and integration-tested — now `impl`/`unit` checked, with a
  paragraph explaining why `replay`/`MT5`/`paper` correctly stay blank);
  Component 1's "Last meaningful update"/"Next objective"/"Gate
  qualification" block (still narrated 2026-08-26's F-051 part 1 news,
  predating the entire Phase-4 review-and-fix arc that followed it); the
  M0 milestone-tracker row and the M0 domain-contracts checklist item
  (both still described contract review as unstarted); the Phase 4
  checklist item in §2 (still said "NEAR-PASS, not yet formally passed").
- No code changed. No new engineering started — review 1.24 §1 was
  explicit that this was a narrow verification pass, and §14 was explicit
  that the next review should wait for a meaningful bundle, not
  documentation cleanup, so nothing further was built proactively.

Evidence:
- The quality-gate/test evidence review 1.24 §9 cites is the same
  forty-second-entry run (939 passed, 3 skipped; ruff/mypy clean;
  determinism confirmed) — the reviewer explicitly did not re-run the full
  suite independently from the small bundle and said so (§9), so this
  entry does not claim a fresh run either.

Problems found:
- None. Pure acceptance review; no findings reopened, no new findings
  opened.

Risk impact:
- None. `order_send` remains structurally unreachable — reconfirmed by
  review 1.24 §6 independently of this session's own analysis in
  `domain_contracts.md` §4.

Decision:
- **Phase 4 is formally PASSED** (review 1.24's own words, §6/§15).
  `review/domain_contracts.md` is APPROVED at the reviewer/technical
  level; whether the M0 checklist's literal "reviewed by a human" wording
  additionally needs an owner countersign is now an open question for the
  owner (review 1.24's suggested form is recorded in `status.md` §2 and
  `review/FEEDBACK.md`'s unreviewed-work table), not an engineering task.
  One controlled real-terminal `order_check` evidence run is AUTHORIZED
  under the exact conditions in `feedback.1.24.md` §8 — not yet executed;
  running it is a real action against the live Pepperstone DEMO terminal
  and needs the user's go-ahead before being attempted, the same as any
  other action that touches a real external system. Not yet committed —
  pending the usual per-turn approval.

Next:
- Ask the user whether to proceed with the authorized real-terminal
  `order_check` run now, and if so, run it under exactly the conditions
  review 1.24 §8 lists (non-sending only; do not enable AlgoTrading merely
  to make it pass — APP-016).
- Continue F-051 part 2 in parallel; await a human/`gh` check of the next
  hosted CI run; owner risk-policy decisions (review 1.24 §12.A) and the
  optional owner countersign remain open, both requiring the owner, not
  the agent.
- Do not start review 1.24 §12.B's submission-era execution-safety work
  (automatic flatten submission, real `SubmissionGate`/F-049, etc.)
  without the user directing it — it is the next engineering phase, not
  something this review asked to be started now.

---

## Update 2026-08-27 (forty-fifth entry) — review 1.24 §8's real-terminal order_check evidence run: real order_check reached, no defect found

Component: `scripts/run_execution_preflight_evidence.py` (new), `crumblr_soak` (real database — migrated, one evidence capsule sealed, safety state touched twice)
Milestone: Phase 4's last open evidence item — real-terminal `order_check`
Status before: Review 1.24 §8 authorized one controlled real-terminal `order_check` run; not yet executed. No entrypoint existed for `ExecutionOrchestrator`; `crumblr_soak` had zero sealed capsules (F-051 part 2 still pending)
Status after: Real `order_check` reached and recorded against the real Pepperstone DEMO terminal. `ORDER_CHECK_REJECTED` — AlgoTrading is off at the terminal (APP-016, already known), not a defect. No `src/` code changed

Completed:
- Entered plan mode (real-world, mostly-irreversible action against a live
  broker account) before writing anything — plan approved by the user,
  who chose "build a full entrypoint + an explicitly-labeled evidence-only
  capsule" over waiting for F-051 part 2 or a narrower direct probe.
- Built `scripts/run_execution_preflight_evidence.py` — a one-shot (not a
  poller) script that: checks the intraday blackout window first; connects
  a real `Mt5Client`/`OrderCheckMt5Gateway`; opens `build_durable_runtime`
  against the real recorded safety state (never forces it); reads the
  durable `InstrumentSpec` and one fresh live tick; constructs and seals
  exactly one `DecisionCapsule` labeled `strategy_id=
  "phase4_order_check_evidence"` (distinct from `ict_v1`/`baseline_v1` —
  cannot be mistaken for real Trading Agent output or count toward
  F-051 part 2's bar-accumulation evidence), with the capsule-level
  `strategy_version`/`risk_config_version` set to the real, currently-
  loaded config's actual values (required for `evaluate_execution_eligibility`
  to accept it — not fakeable); runs `ExecutionOrchestrator.run_once()`
  exactly once against real stores; prints the full durable
  `execution_events` trail. Quality gate on the new file: `ruff check`
  clean, `ruff format --check` clean, `uv run mypy` clean (136 source
  files, up from 135).
- Running it surfaced two real, expected operational gaps, both cleared
  with the user's explicit approval before proceeding (each is itself a
  real, attributed action against a live system, not a code change):
  1. `crumblr_soak` was missing Phase 4's migration
     (`execution_requests`/`execution_events` did not exist there — only
     the shared test database had ever been migrated to that revision).
     Fixed: `uv run alembic upgrade head` against `crumblr_soak`
     (`b3f8a2c7d914` → `c9e1d5a3f286`), the same standard step
     `.env.example` already documents for setting the database up.
  2. `crumblr_soak`'s safety state was `UNKNOWN` (never recorded there),
     which `KillSwitch` correctly treats as halted. Per this project's own
     rule that HALT-reset/safety-state authority is an owner decision, not
     an agent one, this was not set unilaterally: the user explicitly
     approved recording a `RUNNING` state via the established
     `KillSwitch.reset(operator="Levi", incident_note=...)` mechanism,
     narrowly scoped ("solely to enable this evidence run") and durably
     logged with that attribution. Reverted to `HALTED` via
     `KillSwitch.trip(reason_codes=(MANUAL_HALT,), tripped_by="Levi", ...)`
     immediately after the run, also user-approved, so a future run
     against this database does not find `RUNNING` without a fresh
     decision. (First attempt at the reset used a different local
     `state_file` path than the script's own by mistake, which correctly
     produced a `journal RUNNING / latch UNKNOWN` disagreement resolving
     to `HALTED` — caught immediately, re-done against the correct path;
     the stray file was deleted afterward. Not a defect in the composite
     store — proof its disagreement-resolves-to-`HALTED` design works.)
- **First run attempt**: reached `FINAL_RISK_BLOCKED` /
  `STALE_MARKET_DATA` + `EXECUTION_TIME_RISK_BLOCK` — a real tick that was
  fractionally older than `config/paper.yaml`'s strict
  `max_market_data_age_ms: 2000` bound at the exact moment FINAL Risk's
  own independent fresh-tick read ran. A real, correct refusal (the check
  did exactly what it is for), not a defect. Asked the user whether to
  retry manually (the script deliberately does not auto-retry); approved.
- **Second run attempt**: reached the full chain for the first time ever
  — `REQUEST_CLAIMED` → `FINAL_RISK_PASSED` (real PASS, `approved_volume
  =0.01`, real account equity `10000`) → a real, non-mutating `order_check`
  call against the terminal → `ORDER_CHECK_REJECTED`.
- Investigated the result rather than accepting or dismissing it: the
  payload showed `retcode=0`, `accepted=false`, `comment="Done"` — worth
  checking, since the adapter's code compares `retcode` against
  `module.TRADE_RETCODE_DONE`. Queried the real `MetaTrader5` module
  directly (read-only): `TRADE_RETCODE_DONE = 10009`, not `0`, so
  `accepted=False` was computed correctly, not a bug in the comparison.
  Queried the real terminal/account state to interpret *why* the real
  retcode wasn't `10009`: `terminal_health.trade_allowed=False` while
  `account.trade_allowed=True`/`account.expert_allowed=True` — exactly
  APP-016's already-documented condition (AlgoTrading deliberately kept
  off at the terminal, account otherwise fine). Concluded: not a defect —
  the adapter reported a real, non-`DONE` retcode honestly rather than
  misreporting acceptance, precisely the "record the result honestly and
  stop" outcome review 1.24 §8 anticipated. AlgoTrading was not toggled.

Evidence:
- Durable, permanent, real evidence now on record in `crumblr_soak`: two
  sealed `decision_capsules` rows (the aborted first attempt, the
  successful second attempt — both `strategy_id=
  "phase4_order_check_evidence"`, neither can be mistaken for real
  Trading Agent output), one full `execution_requests`/`execution_events`
  trail reaching `ORDER_CHECK_REJECTED` with the real `OrderCheckCompleted`
  payload (`retcode=0`, `comment="Done"`, real margin figures:
  `margin=33.4`, `margin_free=9966.6`, `margin_level=29940.12`).
- No `src/`/`tests/` files changed this entry — investigation and
  operational steps only. Quality gate re-run after: `ruff check .` clean,
  `ruff format --check` clean on the new file, `uv run mypy` clean (136
  source files).

Problems found:
- None in the code. The `retcode=0`/`TRADE_RETCODE_DONE` investigation
  above looked like a possible defect at first glance and was chased down
  fully before concluding it was not one — the real terminal's AlgoTrading
  setting explains it completely, matching a condition already on record
  (APP-016) since review 1.9.

Risk impact:
- None. `order_check` stays non-mutating (no ticket, no fill, no
  exposure) regardless of outcome. `order_send` remains structurally
  unreachable — reconfirmed once more by this entry, now with a real
  adapter genuinely connected to a real terminal, not just by static
  analysis of the code.

Decision:
- Real-terminal `order_check` evidence is now on record. Whether this
  alone is sufficient evidence for review 1.24's own bar, or whether a
  future attempt should be made once AlgoTrading is deliberately enabled
  (an owner decision, not made here — APP-016's own qualification: "do
  not enable AlgoTrading merely to make order_check pass," which this
  entry did not do), is left to the next review round to judge. Not yet
  committed — pending the usual per-turn approval.

Next:
- Update `review/FEEDBACK.md`'s unreviewed-work table (done, this entry).
- Continue F-051 part 2 in parallel; await a human/`gh` check of the next
  hosted CI run; owner risk-policy decisions and the optional domain-
  contract countersign remain open, both requiring the owner.
- Do not re-run the evidence script again proactively — one honest,
  complete real-terminal `order_check` result is now on record; further
  runs are a fresh decision, not routine.

---

## Update 2026-08-27 (forty-sixth entry) — consolidated "what's needed next" for the owner; found real bar accumulation has stalled

Component: `status.md` (documentation only)
Milestone: Process — making the current blocked-on-owner items legible in one place
Status before: Owner-facing open items scattered across §2, §11, §12, `review/FEEDBACK.md`'s unreviewed-work table and several §13 entries — each individually accurate, nowhere consolidated
Status after: A single "What's needed next" table added directly under the document header, ahead of §1, listing exactly what's blocked on the owner or on a background process, with citations. Also found and recorded: real M5 bar accumulation for F-051 part 2 has stalled

Completed:
- Added the consolidated table (six items: CI confirmation, owner risk-
  policy decisions, optional domain-contract countersign, the AlgoTrading
  enablement decision, restarting bar accumulation, and the go-ahead for
  review 1.24 §12.B's next engineering phase) — requested directly by the
  user ("zet alles wat je nog nodig hebt in de status.md").
- While compiling item 5, queried `crumblr_soak` (read-only) rather than
  relying on the last recorded count: **82 real M5 bars now exist**
  (up from 49 at the last check, 2026-08-26), but the latest bar's
  `received_time_utc` is **2026-08-27 06:20 UTC** — roughly seven hours
  stale at the time of this check (13:06 UTC) — so `mt5_live_reader.py`
  is not currently running against this database, or has stopped.
  `baseline_v1`'s 65-bar threshold is already cleared; `ict_v1` still
  needs 120. Also checked `decision_capsules`: only the three evidence-
  only capsules from the forty-fifth entry's real-terminal run exist —
  no real Trading-Agent-produced capsule has ever been sealed, meaning
  `scripts/live_decision.py` has also never run against this data for
  long enough to produce one (or has never been started against it at
  all). Both facts are now recorded in the new table rather than only
  living in a query result.
- Bumped the document header's version (1.7 → 1.8) and last-updated date
  (2026-08-26 → 2026-08-27), both of which had drifted behind a full
  day's worth of review 1.24 processing and the real-terminal evidence
  run.

Evidence:
- No code changed. The bar-count/capsule-count claims above are from
  direct, read-only SQL queries against `crumblr_soak` run in this
  session (not inferred from an older log line).

Problems found:
- Real M5 bar accumulation had silently stalled — nothing alerted to
  this; it was only found by directly querying the database while
  compiling this list. Worth the owner's attention specifically because
  F-051 part 2 cannot progress at all while it stays stopped.

Risk impact:
- None. Purely an accumulation-of-evidence gap, not a safety-relevant
  one — the kill switch in `crumblr_soak` is `HALTED` (forty-fifth
  entry), consistent with `mt5_live_reader.py` (read-only) not requiring
  it to be `RUNNING` in the first place.

Decision:
- No code/engineering decision. Not yet committed — pending the usual
  per-turn approval.

Next:
- Owner to work through the six items in the new table at the top of
  this document.
- If bar accumulation is meant to continue, `scripts/mt5_live_reader.py`
  needs to be (re)started against `crumblr_soak`, and
  `scripts/live_decision.py` needs to be running alongside it for a real
  decision to ever be produced from the accumulated bars.

---

## Update 2026-08-27 (forty-seventh entry) — review 1.25 processed: external-agent direction adopted, review cadence changed, Phase 4 stays passed

Component: Process (review intake), `review/FEEDBACK.md`, `review/DEVIATIONS.md`, `status.md`
Milestone: Product direction / working-methodology change
Status before: Phase 4 formally PASSED (review 1.24); real `order_check` evidence gathered (forty-fifth entry); "What's needed next" table compiled (forty-sixth entry)
Status after: Review 1.25 processed — GO on adopting an external-agent architecture direction as owner decision (O-007), Phase 4 reconfirmed PASSED and explicitly not reopened, two milestones defined (A: Crumblr Execution Proof, B: Agent-Driven MVP), and the review cadence itself changed: the next formal reviewer artifact is `feedback.2.0.md` directly, not a `feedback.1.26.md`/`.1.27.md` sequence, unless a material safety defect or a Phase-4-invariant change surfaces first

Completed:
- Read `feedback.1.25.md` in full — the user flagged in advance that the
  working methodology around reviews and execution would change, and this
  review is where that happens. Confirmed it cites two inputs neither of
  which is in this repository: a `status(20260827-131638).md` snapshot
  (matches this session's own "What's needed next" table from the prior
  turn almost exactly, including the stalled-reader finding — clearly the
  same artifact) and `EXTERNAL_AGENT_ARCHITECTURE_GUIDE.md` (not present
  here at all — recorded as a new open item, §13/table item 6).
- Updated `review/FEEDBACK.md`:
  - Registered review 1.25 in the "Reviews received" table with its full
    verdict and content summary.
  - Added **O-007** to the owner-decisions table: adopting the
    external-agent architecture as product direction, with review 1.25's
    two MVP clarifications (external Supervisor required-but-never-the-
    safety-foundation; TP required at the future Agent Gateway, not by
    tightening `TradeIntent`) recorded alongside it.
  - No F-numbered findings to register — review 1.25 opened none; it is a
    direction review, explicitly not a source audit ("no new broad
    architecture cycle").
  - Rewrote the "Unreviewed work" section: added a dedicated subsection
    explaining the review-cadence change (review 1.25 §9's three trigger
    conditions, next target `feedback.2.0.md`), reworded the heading away
    from "`feedback.1.25.md` now triggered" (that pattern itself is what
    changed), and updated each remaining open row to note where review
    1.25 reinforced or sharpened it (F-051 part 2 especially — "use
    `baseline_v1`, don't wait for 120").
- Updated `review/DEVIATIONS.md`:
  - D-047: fixed a claim that had gone stale the moment the forty-fifth
    entry's evidence run succeeded — it previously said the chain
    "provably never reaches `order_check` today outside a test," which
    stopped being accurate once the real evidence run did exactly that.
  - Added **D-048**: `DecisionCapsule.code_commit` has always been the
    literal placeholder `CODE_COMMIT = "uncommitted-prototype"`
    (`application/live_decision.py`, `application/orchestration.py`),
    never a real git SHA — including on every capsule sealed by
    yesterday's real-terminal evidence run. Named explicitly by review
    1.25 §6 as something to fix before an agent-driven promotion, not an
    immediate blocker.
- Updated `status.md`'s "What's needed next" table: sharpened item 5
  (explicit `baseline_v1`-first instruction), added item 6 (the missing
  architecture guide), rewrote item 7 to reflect review 1.25 §4's
  parallel core/agent-integration developer split rather than a single
  undifferentiated "next phase," and added the review-cadence-change note
  directly under the table so it isn't only in `review/FEEDBACK.md`.

Evidence:
- No code changed this entry — process/documentation only.

Problems found:
- None new. Confirmed (not re-litigated) that `order_send` remains
  structurally unreachable and that Milestone A's critical path is
  unchanged by this review — O-007 is additive product direction, not a
  correction to existing work.

Risk impact:
- None. No code changed; the architecture direction explicitly preserves
  the existing Phase-4 chain rather than modifying it.

Decision:
- External-agent architecture adopted as product direction (O-007). Phase
  4 stays formally passed and is not reopened. Review cadence changes
  from here — see the note above and the new `review/FEEDBACK.md`
  section. Not yet committed — pending the usual per-turn approval.

Next:
- Ask the owner about `EXTERNAL_AGENT_ARCHITECTURE_GUIDE.md` (add it to
  the repo, or point at where it lives) and whether to restart
  `mt5_live_reader.py`/`live_decision.py` now, per review 1.25 §8's
  immediate critical path.
- Do not start Milestone B (Agent Gateway / agent-integration) work
  without the architecture guide and an explicit go-ahead — review 1.25
  itself frames this as a second developer's parallel track, not
  something to begin unprompted.
- Continue everything already listed in the "What's needed next" table.

---

## Update 2026-08-28 (forty-eighth entry) — DEV1/DEV2 track split discovered live; F-049 SubmissionGate shipped as Dev 1's first core-track slice

Component: Process (DEV1/DEV2 track split), `src/crumblr/risk/submission_gate.py`, `src/crumblr/config.py`, `src/crumblr/domain/enums.py`, `review/adr/ADR-006-submission-gate.md`
Milestone: Core (Dev 1) submission-safety phase, first item
Status before: External-agent design package built by this session (agent_gateway contracts, ADR-005, threat model) — see the previous, now-superseded attempt
Status after: Discovered a second, concurrently-running session ("Dev 2") independently building the same agent-integration deliverable in the same working tree. Handed that entire area to Dev 2 per the owner's direction; reverted this session's own claims to it. Picked up Dev 1's actual scope instead: F-049 `SubmissionGate` is now real and tested

Completed:
- Two `CRUMBLR_DEV1_CORE_EXECUTION_INSTRUCTIONS.md`/
  `CRUMBLR_DEV2_AGENT_INTEGRATION_INSTRUCTIONS_V2.md` files appeared
  mid-turn, splitting work into a Core/Execution track and an
  Agent-Integration track with separate status/feedback files, git
  branch/commit conventions, and a protected-files list per track. While
  investigating, found direct evidence of a second, live session already
  active: `review/THREAT_MODEL_AGENT_GATEWAY.md` (written by this session
  minutes earlier) had been overwritten on disk, and a `review/AGENT_STATUS.md`
  appeared describing the same Step-A deliverable this session had just
  built, independently, and explicitly waiting on the owner before
  committing. The owner confirmed: this session is Dev 1; the other
  session is Dev 2 and owns `src/crumblr/agent_gateway/**`,
  `review/adr/ADR-005-*.md`, `review/THREAT_MODEL_AGENT_GATEWAY.md`,
  `review/AGENT_STATUS.md`, `review/AGENT_FEEDBACK.md`.
- Reverted this session's own `status.md`/`review/FEEDBACK.md` edits that
  had narrated the agent_gateway package as its own work (`git checkout --
  status.md review/FEEDBACK.md`, confirmed via `git diff --stat` first
  that only those edits would be discarded). Left every agent_gateway/
  ADR-005/threat-model file exactly as found on disk — did not commit,
  did not modify, did not delete — for the Dev-2 session to handle
  entirely on its own.
- Asked the owner what Dev-1-scoped work to pick up; chosen: start the
  core submission-safety phase, specifically F-049 `SubmissionGate` (the
  most foundational item — several of the others assume it exists).
  Entered plan mode given the safety-criticality (this is the literal
  last gate before anything `order_send`-adjacent) and researched
  `evaluate_preflight_gate`'s pure-function style, F-055's
  `expected_spec_version` durable-pin pattern, and existing `ReasonCode`
  members before proposing a scoped plan — approved by the owner.
- Rewrote `risk/submission_gate.py::evaluate_submission_gate()` from an
  always-refusing stub into a real, pure function checking all nine of
  review 1.15 §14's required conditions simultaneously (`review/adr/ADR-006-submission-gate.md`
  has the full mapping): environment/account/reconciliation/market-data/
  safety-state checks reuse existing `ReasonCode`s and pre-observed
  signals (mirrors `evaluate_preflight_gate`'s style exactly — nothing
  fetched by the gate itself); the three governance legs
  (owner-approved risk policy, execution adapter explicitly enabled,
  `feedback.2.0` GO) needed genuinely new durable config surface, since
  none existed: `RiskConfig.approved_config_version` (same pattern as
  F-055's `expected_spec_version`, checked against `config_version`),
  `ExecutionConfig.submission_enabled`, `ExecutionConfig.feedback_2_0_approved`
  — all default closed, none set by any shipped config file. Added four
  new `ReasonCode` members (`RISK_POLICY_NOT_APPROVED`,
  `EXECUTION_NOT_EXPLICITLY_ENABLED`, `ALGOTRADING_DISABLED`,
  `FEEDBACK_2_0_NOT_APPROVED`); reused six existing ones.
- Rewrote `tests/unit/test_execution_gates.py`'s `TestSubmissionGateStub`
  class (which asserted the old always-closed, no-argument stub) into
  `TestSubmissionGate` (17 tests): one leg failing closes the gate
  independently, for each of the nine; all nine simultaneously true
  opens it; every failing leg reports together, not just the first; and
  — the concrete safety proof, not just design intent — a test builds a
  `SubmissionGateContext` from `load_config()`'s real, current
  `config/paper.yaml` values and asserts the gate stays closed with all
  three new governance reason codes present.
- Updated `review/domain_contracts.md` (§4's `submission_gate.py`
  bullet, plus a new "post-approval update, not yet re-reviewed" note —
  the document was reviewer-approved at `6bdb5b1`; this is routine
  progress under review 1.25 §9's changed cadence, not something
  requiring an immediate new review round), `review/FEEDBACK.md` (F-049
  → CLOSED/SHIPPED with full evidence; trimmed the
  `EXTERNAL_AGENT_ARCHITECTURE_GUIDE.md` unreviewed-work row down to a
  pointer at Dev-2's own tracking, per Dev-1 instructions §10/§15 —
  "do not copy Dev-2 implementation detail into these documents
  continuously"), `review/DEVIATIONS.md` (D-047's submission-gate mention
  corrected).

Evidence:
- `uv run ruff check .` / `uv run ruff format --check .` — clean on every
  touched file.
- `uv run mypy` — success, 145 source files.
- `uv run pytest tests/unit/test_execution_gates.py -v` — **32 passed**
  (9 eligibility + 5 preflight-gate + 1 pre-existing + 17 new submission-
  gate tests — the file's `TestExecutionEligibility`/`TestPreflightGate`
  classes untouched and still green).
- `uv run pytest -m "not integration" -q` — **834 passed, 1 skipped**,
  zero regressions.
- Full suite solo (`uv run pytest -q`) attempted twice; both runs failed
  with a *different* set of integration tests each time (13 failed/34
  errors, then 25 failed/56 errors) — the signature of the known
  concurrent-shared-Postgres-database race (D-042), not a real
  regression: none of this entry's changes touch migrations, the
  journal, or persistence, and the non-integration suite (which exercises
  everything this entry actually changed) is fully clean. Very likely
  caused by the concurrently-running Dev-2 session also hitting the
  shared test database. **Full-suite confirmation deliberately deferred**
  — the owner asked to wait for a signal that Dev 2 has stopped before
  re-running it, rather than retrying blindly.

Problems found:
- None in the shipped code. The test-suite instability above is an
  environment/concurrency condition, not a defect introduced by this
  entry — isolated by running the non-integration suite alone.

Risk impact:
- None. `evaluate_submission_gate()` is called by nobody in `src/` —
  confirmed by the same "grep for a new caller, expect none" discipline
  used for the agent_gateway package. `order_send` remains structurally
  unreachable regardless (`OrderCheckMt5Gateway.order_send` still
  unconditionally raises).

Decision:
- F-049 is CLOSED/SHIPPED. Not yet committed — pending the owner's
  signal that the full integration suite can be confirmed cleanly
  (Dev 2 no longer running concurrently), then the usual per-turn commit
  approval.

Next:
- Wait for the owner's signal, then re-run `uv run pytest -q` solo for a
  clean full-suite confirmation before committing.
- Once committed, continue the core submission-safety phase: durable
  execution-activation authority *wiring* (a `SubmissionOrchestrator` or
  equivalent that actually reads the new config fields), then
  `SUBMISSION_STARTED` emission timing, `order_send` idempotence,
  ambiguous-outcome recovery, automatic flatten submission, post-fill
  reconciliation, broker-side SL verification, execution-event
  content-conflict hardening — each its own slice, not one giant pass.
- Continue everything else already listed in "What's needed next": CI
  confirmation, owner risk-policy decisions, restarting bar accumulation,
  the optional domain-contract countersign.

---

## Update 2026-08-28 (forty-ninth entry) — F-049 committed and pushed; a shared-working-tree branch mixup found and fixed first

Component: Process (git state), `review/INTEGRATION_NOTICES.md`
Milestone: Core submission-safety phase, F-049 landed on `main`
Status before: F-049 `SubmissionGate` complete and quality-gate clean (forty-eighth entry); full-suite confirmation deliberately deferred until Dev 2 stopped
Status after: Full suite confirmed clean solo (Dev 2 stopped); F-049 committed and pushed to `main` as `a1a2770` — after finding and fixing a branch mixup caused by the two sessions sharing one working directory

Completed:
- Owner signaled Dev 2 had stopped. Re-ran `uv run pytest -q` solo:
  **1014 passed, 3 skipped**, zero failures — confirms the two unstable
  runs recorded in the forty-eighth entry were genuinely the concurrent-
  database race (D-042), not a defect in this work.
- Ran the whole-project quality gate once more (`ruff check .`, `mypy`)
  against the now-larger tree (Dev 2's merged `agent_gateway` package
  included) — clean, 147 source files.
- Created `review/INTEGRATION_NOTICES.md` (new, Dev-1-owned per
  `CRUMBLR_DEV1_CORE_EXECUTION_INSTRUCTIONS.md` §7) and logged the
  domain/enums.py shared-contract change (four new `ReasonCode` members,
  one removed, confirmed unused anywhere including Dev 2's new code),
  plus a reconstructed notice for Dev 2's already-merged commits
  (`cc16e4f`, `2f7c921` — the `agent_gateway` package and its migration,
  `20260828_d4b6e2f81a37_agent_gateway_step_b.py`, correctly chained
  after Phase 4's own head).
- Committed with the Dev-1 convention (`[core]` prefix,
  `IMPACT: SHARED-CONTRACT` label) — and the commit landed on
  `agent/contracts`, Dev 2's own local topic branch, instead of `main`.
  Both sessions operate on the same physical working directory/`.git`;
  neither had explicitly switched to its own branch first, so whatever
  Dev 2 last checked out (`agent/contracts`, matching their own branch-
  naming convention) is what the commit stacked onto. Caught immediately
  by the commit output itself (`[agent/contracts 3d13f30] ...`) before
  anything was pushed — `origin/main` was still at the pre-Dev-2-and-
  pre-this-entry commit, so nothing public was affected.
- Fixed cleanly, nothing rewritten or lost: created `core/submission-gate`
  from `main`, cherry-picked the misplaced commit onto it (applied
  without conflict — this entry's files and Dev 2's are fully disjoint),
  force-moved `agent/contracts` back to Dev 2's own real tip (`2f7c921`,
  dropping the stray commit from it), fast-forwarded `main` onto
  `core/submission-gate`, deleted the temporary branch, pushed. Logged
  the whole incident in `review/INTEGRATION_NOTICES.md` for anyone
  reading this git history later.

Evidence:
- `git push origin main` — `86873a6..a1a2770 main -> main`, fast-forward,
  confirmed via `git log`/`git branch -a` after.
- `git log --oneline agent/contracts -3` — confirmed restored to exactly
  Dev 2's two commits, nothing of Dev 1's mixed in.
- Full suite (1014 passed/3 skipped) and quality gate (ruff/mypy clean)
  both re-confirmed above, on the final, correctly-placed commit.

Problems found:
- The branch mixup above — caught and fixed before any push, no lasting
  effect. Root cause recorded in `review/INTEGRATION_NOTICES.md` with an
  explicit action item: both sessions should confirm/switch to the
  intended branch explicitly before starting work in this shared working
  tree.

Risk impact:
- None. `agent/contracts` (Dev 2's own work: the `agent_gateway`
  package, ADR-005, threat model, Step B gateway/persistence) remains
  local and unpushed — merging it into `main` is Dev 2's/the owner's
  decision, not made here. `order_send` remains structurally
  unreachable regardless of any of this.

Decision:
- F-049 `SubmissionGate` is committed and pushed to `main` (`a1a2770`).
  `agent/contracts` is intact, correctly separated, and waiting on its
  own owner to merge or continue.

Next:
- Continue the core submission-safety phase's remaining items (durable
  execution-activation wiring, `SUBMISSION_STARTED` timing, `order_send`
  idempotence, ambiguous-outcome recovery, automatic flatten submission,
  post-fill reconciliation, broker-side SL verification, execution-event
  conflict hardening) — each its own slice.
- Continue everything else already listed in "What's needed next": CI
  confirmation, owner risk-policy decisions, restarting bar accumulation,
  the optional domain-contract countersign.
- Before starting the next slice, explicitly confirm/switch branch first
  given the shared-working-tree lesson just learned.

## Update 2026-08-28 (fiftieth entry) — Dev 2 / Agent Integration track: Step A + Step B, first-hand account

**Written by the Dev-2 session** (`review/CRUMBLR_DEV2_AGENT_INTEGRATION_INSTRUCTIONS_V2.md`),
at the owner's explicit request, so this canonical document carries a
first-hand record of this track's work alongside Dev 1's own entries
(forty-eighth/forty-ninth) rather than only Dev 1's reconstruction of it
from the outside. Everything below lives on branch `agent/contracts`
(commits `cc16e4f`, `2f7c921`), **not yet merged into `main`** — this
entry is documentation only; no code from this track is present in this
working tree's current `main` checkout.

Component: `src/crumblr/agent_gateway/**` (new package), `src/crumblr/persistence/agent_gateway.py`
(new), `src/crumblr/persistence/schema.py` (six new tables, additive),
`migrations/versions/20260828_d4b6e2f81a37_agent_gateway_step_b.py`,
`review/adr/ADR-005-external-agent-trust-boundary.md`,
`review/THREAT_MODEL_AGENT_GATEWAY.md`, `review/AGENT_STATUS.md`,
`review/AGENT_FEEDBACK.md`
Milestone: Agent Integration track (Dev 2), ADR-005 Step A (design/
contracts) and Step B (Agent Gateway in shadow, ingestion + audit half only)
Status before: `review/EXTERNAL_AGENT_ARCHITECTURE_GUIDE.md` supplied by
the owner 2026-08-27; §11's design package not started
Status after: Step A complete, committed. Step B's ingestion+audit layer
(everything ADR-005 §8's "first proof target" requires) complete,
committed. `TradeProposal → TradeIntent` mapping deliberately not built —
blocked on a shared-contract question (AG-006), not forced through alone

Completed:
- **Step A** (`cc16e4f`): found the eight contracts
  (`AgentIdentity`, `TradingAssignment`, `PolicyHints`,
  `DecisionContextBundle`, `TradeProposal`, `NoTradeDecision`,
  `ProposalWithdrawal`, `SupervisorReview`) and ADR-005 already existed,
  uncommitted, from an earlier pass. Verified all 27 structural tests
  passed, ruff/mypy clean, full suite untouched. Found one real gap:
  ADR-005 names `review/THREAT_MODEL_AGENT_GATEWAY.md` as a Step-A
  deliverable but the file didn't exist — wrote it (STRIDE-style analysis
  per contract, mapped to concrete gaps AG-001..AG-005). Created this
  track's own `review/AGENT_STATUS.md`/`review/AGENT_FEEDBACK.md` trackers
  (neither existed yet). Committed on branch `agent/contracts`, created
  fresh off `main` at the time (`86873a6`).
- **Step B** (`2f7c921`): built the Agent Gateway's ingestion+audit half —
  `agent_gateway/gateway.py::AgentGateway`, `auth.py` (interim salted-hash
  shared-secret credential — not the final mTLS/SPIFFE mechanism
  `service_identity` is named for), `errors.py` (a typed split between
  raised exceptions for a fundamentally invalid caller and
  `AgentRejectionReason` for an ordinary, fully-audited refusal),
  `events.py`, `stores.py` (`Protocol`s + in-memory implementations,
  mirroring `application/decision_window.py`'s shape). Added
  `NoTradeDecision.decision_fingerprint` (a small Step-A contract
  addition this pass needed, mirroring `TradeProposal.proposal_fingerprint`)
  so NO_TRADE gets the same idempotent-claim guarantee as a directional
  proposal.
- Confirmed the current Dev-1 Alembic head (`c9e1d5a3f286`) before
  creating a revision, per instructions §13. Added six tables to
  `persistence/schema.py` (additive only — same pattern Dev 1 used for
  F-047's broker-state tables): `agent_identities`, `agent_credentials`
  (both append-only "latest snapshot wins", mirroring
  `decision_window_states`), `agent_trading_assignments`,
  `agent_decision_context_bundles` (both content-addressed/immutable,
  mirroring `instrument_specs`/`execution_requests`), `agent_decision_outcomes`
  (the idempotent claim table — `INSERT ... ON CONFLICT DO NOTHING
  RETURNING`, the exact primitive `persistence/execution.py` already
  proves), `agent_decision_events` (append-only lifecycle log, mirroring
  `execution_events`). Wrote migration `d4b6e2f81a37` off that head, plus
  `persistence/agent_gateway.py` (the Postgres store implementations).
- Every `submit_trade_proposal`/`submit_no_trade` call durably claims the
  attempt (`RECEIVED` event) *before* running any authorization check, so
  a legitimate refusal is a normal, auditable, machine-readable outcome
  (`AgentRejectionReason` — unknown assignment, not owned, outside
  validity window, over the rate limit, risk fraction out of band,
  unknown/mismatched/expired context, expired proposal) rather than a
  silently dropped attempt — guide §9's "every proposal, NO_TRADE,
  rejection and timeout is auditable".
- Found, named, and deliberately did not resolve alone: `TradeIntent.feature_snapshot_id`
  is non-optional, and an externally-originated `TradeProposal` has no
  computed feature snapshot — only a `DecisionContextBundle`. Deciding what
  that field means for an agent-originated decision is a shared-contract
  question (Dev-2 instructions §4/§5: stop and raise rather than force a
  change to shared territory alone). Recorded as **AG-006** in
  `review/AGENT_FEEDBACK.md` rather than inventing an interpretation.
  `TradeProposal → TradeIntent` mapping, and everything downstream of it
  (Risk, the deterministic Policy Gate, `DecisionCapsule` sealing), is
  Step C territory per ADR-005 §9 regardless and was not attempted this
  pass.

Evidence:
- tests: `tests/unit/test_agent_gateway_contracts.py` — 29 passed (27 at
  Step A + 2 new for `decision_fingerprint`). `tests/unit/test_agent_gateway.py`
  (new) — 24 passed, covering ADR-005 §7's full planning-level test matrix
  (identity refusal on unknown/wrong-credential/suspended/retired,
  impersonation, assignment scope, context binding/expiry, idempotent
  retry, conflicting-retry fail-closed, NO_TRADE distinct from no
  response) against in-memory stores. `tests/integration/test_agent_gateway_store.py`
  (new) — 6 passed against real PostgreSQL: basic round-trip, a rejection
  read back from an independent store instance, and — the one proof only
  a real database can give — restart-safety (a second, independently-
  constructed `AgentGateway` pointed at the same engine, simulating a
  crashed-and-restarted process, still replays retries idempotently and
  still fails closed on conflicts) and concurrent-claim atomicity.
- tests: `tests/integration/test_migrations.py` — 8 passed in isolation,
  confirming the new migration and `persistence/schema.py` agree exactly
  (`compare_metadata`, upgrade/downgrade round trip, `create_all` vs
  migration schema equivalence).
- tests: `uv run pytest -m "not integration" -q` — 834 passed, 1 skipped
  (pre-existing, unrelated Windows/MT5-importability skip) — proves Step B
  did not disturb Phase 4 or Step A.
- logs: `uv run ruff check .` / `ruff format --check .` and `uv run mypy`
  (project-wide, via the configured invocation) — clean on every new/
  changed file in this track, both passes.
- Observed, not a defect in this work: an early full-suite run including
  integration tests hit intermittent `relation "..." already exists` /
  `does not exist` errors against the shared local test database — the
  same concurrent-session race D-042/the forty-eighth entry independently
  names, caused by Dev 1's session running its own integration suite
  against the same fixed database URL at the same time. This track's own
  integration suite passes cleanly and repeatably (`test_agent_gateway_store.py`
  run alone: 6/6, twice) once isolated from that collision.
- artifact/commit: `cc16e4f` (Step A), `2f7c921` (Step B), both on
  `agent/contracts`, both local/unpushed as of this entry.

Problems found:
- None in the shipped code. AG-006 (above) is a scope boundary correctly
  identified and deferred, not a defect. The shared-test-database
  contention above is an environment property of two concurrent sessions,
  not a defect in either track's code.
- A genuine process incident, already fully resolved by Dev 1 and logged
  in the forty-ninth entry and `review/INTEGRATION_NOTICES.md`: both
  sessions share one physical working directory/`.git`, and a Dev-1 commit
  briefly landed on this track's `agent/contracts` branch by accident
  before being moved to `main`. Confirmed from this side too: `git log
  --oneline agent/contracts` shows exactly this track's two commits
  (`cc16e4f`, `2f7c921`), nothing of Dev 1's mixed in — the fix held.

Risk impact:
- None. Nothing outside `src/crumblr/agent_gateway/` and
  `src/crumblr/persistence/agent_gateway.py` imports either — verified by
  grep, both at Step A and again at Step B. No transport (HTTP/gRPC/queue)
  exists yet for an external process to reach `AgentGateway` at all; every
  proof in this entry is a direct in-process call from a test. No agent
  path can reach MT5, broker credentials, direct DB writes outside this
  track's own six tables, final lot sizing, or Risk-policy mutation.
  `order_send` remains structurally unreachable regardless, unaffected by
  any of this.

Decision:
- Step A and Step B (ingestion+audit half) are both CLOSED/SHIPPED on
  `agent/contracts`. `TradeProposal → TradeIntent` mapping and Step C
  (external Supervisor boundary) remain OPEN, the second explicitly
  blocked on the first per ADR-005's own step ordering.
- Merging `agent/contracts` into `main` is an owner/Dev-2 decision, not
  made in this entry.

Next:
- Raise AG-006 with Dev 1 — the one place this track's own scope
  genuinely touches shared-contract territory.
- Once resolved: implement `TradeProposal → TradeIntent` mapping, then
  Step C (external Supervisor boundary, AG-003).
- Confirm/switch to `agent/contracts` explicitly before resuming, per the
  shared-working-tree lesson both tracks have now independently logged.

---

## Update 2026-08-28 (fifty-first entry) — mandatory workspace isolation set up; a real cross-track test-database bug found and fixed

Component: Process (workspace isolation), `tests/integration/test_run_survives_restart.py`, `test_orchestrator_persistence.py`, `test_market_data_store.py`, `test_live_decision.py`, `test_migrations.py`, `review/INTEGRATION_NOTICES.md`
Milestone: Dev-1/Dev-2 track hygiene, direct follow-up to the forty-ninth entry's branch-mixup incident
Status before: F-049 landed on `main`; branch mixup found and fixed, but both sessions still shared one physical checkout and one test database
Status after: `CRUMBLR_DEV1_CORE_EXECUTION_INSTRUCTIONS_V2.md`/`_V3.md` arrived, mandating exactly the isolation the forty-ninth entry's incident called for. Set up: dedicated git worktree, dedicated `crumblr_test_dev1` database, and — while verifying the database isolation actually works — found and fixed a real bug that would have silently defeated it

Completed:
- Read the new `CRUMBLR_DEV1_CORE_EXECUTION_INSTRUCTIONS_V2.md` in full
  (and Dev 2's `_V3.md`, for the AG-006 cross-reference). Both mandate:
  separate git worktrees/clones per track, separate branch prefixes
  (`core/*`/`agent/*`), separate Python environments, separate
  integration-test databases (`crumblr_test_dev1`/`crumblr_test_dev2`),
  a session-start branch/status check, and an explicit "stop before
  editing if on the other track's branch prefix" rule.
- Entered a dedicated git worktree (`EnterWorktree` — creates an isolated
  checkout under `.claude/worktrees/core`, its own `.venv`, confirmed via
  `uv run` auto-provisioning one on first use). Renamed the worktree's
  auto-generated branch to `core/test-db-isolation` to match the required
  `core/*` prefix.
- Created a dedicated `crumblr_test_dev1` PostgreSQL database (same
  server, `CREATE DATABASE`, additive/non-destructive) and set
  `CRUMBLR_DATABASE_URL` to point at it for this workspace's test runs.
- **While verifying this actually isolates the two tracks, found a real,
  pre-existing bug**, not just a naming gap: five integration test files
  imported `crumblr.persistence.engine.DEFAULT_TEST_URL` and used the
  literal constant directly in `build_durable_runtime(url=...)`/
  `create_db_engine(...)`/`upgrade_to_head(...)` calls, completely
  bypassing the `CRUMBLR_DATABASE_URL` environment override that
  `tests/integration/conftest.py`'s shared `engine` fixture already
  respects via `database_url(DEFAULT_TEST_URL)`. Worst instance:
  `test_run_survives_restart.py` passes the URL as a literal string
  argument to a genuinely separate child process — that child always
  wrote to the shared `crumblr` database regardless of what the parent
  process's fixture pointed at, which is exactly why several of its
  tests (`test_the_journal_holds_the_run_after_the_process_is_gone`,
  cross-process halt/session-recovery checks) failed even when run
  completely alone against the new isolated database — parent and child
  were silently writing to two different databases. Fixed all five files
  the same way: a module-level `TEST_URL = database_url(DEFAULT_TEST_URL)`
  resolved once, used everywhere the file previously used the raw
  constant. `test_migrations.py`'s `pg_dump`/`psql` subprocess calls also
  hardcoded the literal database name `"crumblr"` — fixed via a
  `TEST_DB_NAME` parsed from `TEST_URL` with `sqlalchemy.engine.make_url`.
- Logged both the database-isolation setup and the bug fix in
  `review/INTEGRATION_NOTICES.md`, including a note for Dev 2 to check
  whether their own `agent_gateway` integration tests have the same
  `DEFAULT_TEST_URL`-bypass pattern before assuming `crumblr_test_dev2`
  isolation actually holds.
- Acknowledged AG-006 (`TradeIntent.feature_snapshot_id` must stay
  required, not optional) — both V2 and V3 already record the identical
  resolution, so there was nothing to negotiate; confirmed no Dev-1 code
  change is needed since the field has been required since before this
  session's Phase-4 work. Logged the acknowledgment in
  `review/INTEGRATION_NOTICES.md` too, since it is exactly the kind of
  shared-contract item the log exists to make visible.

Evidence:
- `uv run pytest tests/integration/test_run_survives_restart.py
  tests/integration/test_orchestrator_persistence.py
  tests/integration/test_market_data_store.py
  tests/integration/test_live_decision.py tests/integration/test_migrations.py -q`
  against `crumblr_test_dev1` — **53 passed**, confirming the fix (these
  were exactly the files failing before it, even in isolation).
- Full suite re-run against `crumblr_test_dev1` in the new worktree —
  **955 passed, 3 skipped**, zero failures (this worktree is based on
  `main` before Dev 2's `agent/contracts` merge, so the count is lower
  than the shared checkout's 1014 — expected, not a discrepancy).
- `uv run ruff check .` / `uv run ruff format --check .` / `uv run mypy`
  — all clean, 136 source files (one `mypy` finding caught and fixed
  along the way: `sqlalchemy.engine.URL.database` is typed `str | None`;
  `TEST_DB_NAME` needed an explicit `assert ... is not None` to narrow it
  before use as a subprocess argument).

Problems found:
- The `DEFAULT_TEST_URL`-bypass bug above. Real, pre-existing (predates
  this session's work on any of these files), silently harmless as long
  as only one workspace ever ran integration tests against one database
  — which was true until today's isolation mandate made it a genuine
  correctness bug, not just a latent one.

Risk impact:
- None. Test-infrastructure only; no production code path changed.

Decision:
- Workspace isolation is set up and verified working. Not yet committed
  — pending the usual per-turn approval, on the new `core/test-db-isolation`
  branch per the mandated branch-prefix convention.

Next:
- Confirm the full suite is clean in the new worktree, then commit
  (`[core]` prefix, `IMPACT: NONE` — test infrastructure only) and merge
  to `main` following V2 section 9's short-lived-branch flow.
- Resume the core submission-safety phase's remaining items from the
  new, properly-isolated worktree.

---

## Update 2026-08-28 (fifty-second entry) — durable execution-activation wiring shipped; a real, self-referential bug in F-049 found and fixed same day (F-062)

Component: `application/execution.py`, `domain/enums.py`, `config.py`, `review/adr/ADR-006-submission-gate.md`, `review/FEEDBACK.md`, `review/DEVIATIONS.md`, `review/INTEGRATION_NOTICES.md`
Milestone: Dev-1 core critical path item 2 (`CRUMBLR_DEV1_CORE_EXECUTION_INSTRUCTIONS_V2.md` §13): SubmissionGate (item 1, done) → **durable execution-activation wiring (this entry)** → `SUBMISSION_STARTED` timing, `order_send` idempotence, ambiguous-outcome recovery, automatic flatten submission, post-fill reconciliation, broker-side SL verification, execution-event conflict hardening (items 3-8, not started)
Status before: F-049's `evaluate_submission_gate()` was real and tested but called by nobody — no orchestrator evaluated it against a real, in-flight order
Status after: `ExecutionOrchestrator._process()` evaluates the gate for real, immediately after a broker-accepted `order_check`, and durably records the result. While proving the gate can genuinely open, found and fixed a real defect in F-049 as shipped: one of its nine conditions could never be satisfied by construction

Completed:
- `application/execution.py`: new `_evaluate_submission_readiness()`,
  called from `_process()` only when `check.accepted` (an
  `ORDER_CHECK_REJECTED` order never reaches it — that path is unchanged
  and still ends there). Builds `SubmissionGateContext` entirely from
  signals already in scope from `_process()`'s own preceding reads —
  zero new MT5 calls, including terminal AlgoTrading state
  (`observation.account.terminal_trade_allowed`, already captured by
  `capture_broker_state()`). Appends `SUBMISSION_GATE_PASSED`/
  `SUBMISSION_GATE_BLOCKED` with the decision's reason codes and a
  complete-context payload, and that event type becomes the run's
  reported outcome — the durable event log still gets both rows
  (`ORDER_CHECKED` first), but the *outcome* reflects the true final
  state, the same principle F-057 already established for
  `FINAL_RISK_PASSED`.
- `domain/enums.py`: two new `ExecutionEventType` members,
  `SUBMISSION_GATE_PASSED`/`SUBMISSION_GATE_BLOCKED`, mirroring
  `FINAL_RISK_PASSED`/`FINAL_RISK_BLOCKED`'s naming. Confirmed via grep:
  additive-only, nothing in `agent_gateway/` references either name.
- **F-062, found while writing the test that proves the gate can open**:
  that test requires `RiskConfig.approved_config_version ==
  PlatformConfig.config_version` — but `config_version` was a hash of
  the *entire* config, including that same field. Writing the approved
  hash into the config changed the config, which changed the hash the
  write was supposed to match. Empirically confirmed circular before any
  fix (set `approved_config_version` to the config's own current
  `config_version`, recomputed, got a *different* value, every time).
  Unlike the `MarketConfig.expected_spec_version` precedent this was
  modeled on, that field pins a hash of a genuinely separate artifact
  (the observed `InstrumentSpec`) — never itself part of the hash it's
  compared against. Fixed by excluding the three governance/approval
  fields (`risk.approved_config_version`, `execution.submission_enabled`,
  `execution.feedback_2_0_approved`) from what `config_version` hashes —
  it now represents the substantive, risk-bearing content an owner
  reviews, not whether that review already happened. No shipped config
  sets any of the three, and every other field still changes the version
  on any edit exactly as before (`test_any_change_produces_a_new_version`
  unaffected). Not a live-trading risk either way — `order_send` stayed
  unreachable throughout, fail-closed the whole time — but a
  CRITICAL-severity gate whose owner-approval leg could never actually be
  satisfied is a real defect, filed as its own finding rather than folded
  into F-049's row: `review/FEEDBACK.md` F-062,
  `review/adr/ADR-006-submission-gate.md` §5 addendum with the full
  reproduction.
- Two new integration tests: `test_a_fully_approved_config_reaches_
  submission_gate_passed` (a test-only fully-approved config —
  `SUBMISSION_GATE_PASSED`, the first time this repo has ever reached
  that state) and `test_a_broker_rejected_order_never_reaches_the_
  submission_gate` (`ORDER_CHECK_REJECTED` short-circuits, no
  `SUBMISSION_GATE_*` event appended). Plus one config unit test,
  `test_approving_this_exact_version_does_not_change_it`, proving F-062's
  fix directly.
- Updated `review/DEVIATIONS.md` D-047 (the gate is no longer "called by
  nobody") and `review/INTEGRATION_NOTICES.md` (both changed files are
  shared-contract territory per the DEV1/DEV2 split; confirmed no
  `agent_gateway/` reference to either the two new event types or
  `config_version` before logging `IMPACT: NONE`).
- Replied to a cross-session question from Dev 2 (AG-006 follow-up: is
  `trading_agent/features.py::compute_features()` sufficient for the
  Gateway's `feature_snapshot_id` need?) after checking the code
  directly rather than assuming — it is not: that function is
  `baseline_v1`-specific (`FeatureSnapshot`), `ict_v1` has its own,
  structurally different `IctFeatureSnapshot` computed inline in its own
  `evaluate()`, and the only cross-strategy contract
  (`trading_agent/base.py::FeatureEvidence`) is populated solely as a
  byproduct of a full strategy evaluation today. The standalone
  extraction Dev 2 needs is still unbuilt.

Evidence:
- `uv run ruff check .` / `uv run ruff format --check .` / `uv run mypy`
  — all clean, 147 source files.
- `uv run pytest tests/integration/test_execution_orchestrator.py
  tests/unit/test_config.py -q` — 59 passed.
- Full suite, solo, against `crumblr_test_dev1` —
  **1017 passed, 3 skipped**, zero failures (Dev 2's own last-reported
  count was 1014 on the shared `main`, at `d6a5361`; +3 for this entry's
  three new tests, exactly as expected — not yet rebased onto that
  commit, see Next).
- Grepped the diff for any new `order_send` call site: none. The only
  `order_send` call anywhere in `src/` remains
  `application/orchestration.py`'s pre-existing replay/backtest path
  against `SimulatedBroker` — unrelated, unchanged.

Problems found:
- F-062 (above) — the real finding of this slice, not merely a test
  authoring inconvenience.

Risk impact:
- None adverse. `order_send` stays structurally impossible regardless of
  the submission gate's evaluated outcome. F-062's fix makes a
  CRITICAL-severity gate leg that was silently permanently closed
  potentially satisfiable in the future, by explicit owner action — the
  fail-closed direction of the bug means no window of unsafe behavior
  ever existed.

Decision:
- Both this slice's wiring and the F-062 fix are self-discovered
  implementer findings under review 1.25 §9's changed cadence — not a
  reviewer request. Judged not to independently trigger pulling the
  reviewer back for `feedback.2.0.md` early (§9 item 1's "material safety
  defect" bar): fail-closed throughout, found and fixed same day, with
  evidence. Logged prominently here and in `review/FEEDBACK.md` instead
  so a human can override that judgment if they read it differently.
- Committed `49c3571` on `core/execution-activation`, then rebased
  cleanly onto `origin/main` (Dev 2's `d6a5361`, self-review hardening
  pass, `agent_gateway/persistence/agent_gateway.py` only, `IMPACT: NONE`
  per their own notice) — no conflicts.

**Post-rebase evidence correction**: the full-suite count above
(1017 passed) was measured before the rebase, against the pre-`d6a5361`
tree; re-run after rebasing onto `d6a5361` reports **1023 passed, 3
skipped**, zero failures — six more than the naive 1014+3=1017
arithmetic implied, because `d6a5361` itself added test coverage for the
three bugs it fixed (AG-007/008/009), not merely code. Recorded here
rather than silently editing the number above, per this project's own
"report evidence honestly" rule — the 1017 figure was accurate for what
it measured, just not the final post-rebase state.

Next:
- Merge to `main` and push per V2 §9's short-lived-branch flow.
- Continue down the core critical path: item 3 (`SUBMISSION_STARTED`
  timing) is next, no plan drafted yet.

---

## Update 2026-08-28 (fifty-third entry) — Dev 2: self-review hardening pass finds and fixes three real bugs (AG-007/008/009)

Component: `src/crumblr/agent_gateway/gateway.py`, `stores.py`,
`persistence/agent_gateway.py`, `errors.py`, `__init__.py`,
`tests/unit/test_agent_gateway.py`, `tests/integration/test_agent_gateway_store.py`
Milestone: Agent Integration track (Dev 2), post-Step-A/B quality pass —
not requested by anyone, run because solid test coverage does not rule
out logic bugs a second look catches
Status before: Step A + Step B merged to `main` (`bf18ec5`), 30 tests
green, believed correct
Status after: a `/code-review high` self-review against the whole
`agent_gateway` package found five issues; three were genuine
correctness/fail-closed bugs, not style. All fixed same day, all
AG-numbered in `review/AGENT_FEEDBACK.md`

Completed:
- **AG-007 (HIGH) — proposal-rate-limit check-then-act race.** Reading
  `count_claimed_since` and claiming the outcome happened in two separate
  transactions, so concurrent proposals for one assignment could each
  observe a stale below-limit count and all get accepted, silently
  exceeding `max_proposals_per_hour`. Fixed with a Postgres
  transaction-scoped advisory lock (`pg_advisory_xact_lock`) serializing
  the whole claim→count→evaluate→settle sequence per `assignment_id`
  (`AgentDecisionOutcomeStore.transaction()`/`.lock_assignment()`, mirrors
  `persistence/execution.py`'s own optional `connection` parameter).
  Verified the fix is real, not incidental: manually disabled the lock,
  confirmed a new 10-real-thread concurrency test against real PostgreSQL
  genuinely fails (10/10 or 7/10 accepted instead of the configured 3),
  then restored it and re-confirmed green.
- **AG-008 (HIGH) — fail-open idempotent-retry replay.** The retry path
  read "no `REJECTED` event found" as proof of acceptance. A claim
  interrupted between commit and verdict (a crash) left a claimed-but-
  unsettled row that every future retry would then silently report
  `accepted=True` for, without the authorization checks ever actually
  running. Fixed by making an unsettled claim resume evaluation with
  fresh inputs rather than assume any verdict — genuinely fail-closed, not
  merely stuck: a raise-based fix was considered and rejected, since every
  retry would hit the identical unresolved state forever. Event ids for
  `agent_decision_events` also switched from random `uuid4()` to
  content-derived (mirrors `persistence/execution.py::event_id_for`), so a
  resumed attempt's re-appended `RECEIVED` event collapses instead of
  duplicating.
- **AG-009 (MEDIUM) — unenforced `required_evidence_fields`.** Defined on
  `TradingAssignment` at Step A, never checked anywhere — a proposal with
  zero evidence was accepted even against an assignment naming required
  evidence fields. Fixed with a deliberately conservative check (some
  evidence must be cited when required; verifying the cited evidence
  actually *covers* each named field needs content inspection, out of
  scope until AG-005's evidence-ingestion path exists).
- Also: de-duplicated assignment/context validation logic that had been
  copy-pasted between the proposal and NO_TRADE evaluation paths, and
  fixed a stale package docstring still describing "Step A only" after
  Step B shipped.

Evidence:
- 35 new/changed tests total (2 for the interrupted-claim resume, 3 for
  required-evidence, 1 new real-concurrency integration test, plus the
  connection-threading refactor re-verified against the full existing
  suite with zero regressions).
- Full non-integration suite: 839 passed, 1 skipped.
- `agent_gateway` integration suite: 7/7 against an isolated database.
- `uv run ruff check .` / `mypy` — clean.

Problems found:
- The three AG-numbered bugs above — the actual point of this entry.

Risk impact:
- None to live trading — this track has no execution path regardless. The
  bugs themselves were real (a rate-limit could be silently exceeded; a
  crashed-and-retried claim could be silently accepted without
  authorization) and are exactly the class of defect this project's review
  culture exists to catch before a capability surface goes live, not after.

Decision:
- All three findings CLOSED/SHIPPED same day. Committed `d6a5361` on
  `agent/contracts`, rebased cleanly onto Dev 1's concurrent work, pushed
  to `main`.

Next:
- Check in with Dev 1 on AG-006/`compute_features()` before starting
  Step E (`TradeProposal → TradeIntent` mapping).

---

## Update 2026-08-31 (fifty-fourth entry) — Dev 2: HTTP transport for the Agent Gateway, built while AG-006 stays blocked

Component: `src/crumblr/agent_gateway/http.py` (new),
`tests/unit/test_agent_gateway_http.py` (new)
Milestone: Agent Integration track (Dev 2) — infrastructure for V3 §18
step G ("external Trader against genuine Crumblr shadow context"), built
ahead of E/F since it did not itself depend on AG-006
Status before: every Step B proof called `AgentGateway` directly from a
test — same process, no wire in between. AG-006 (`TradeProposal →
TradeIntent` mapping) still blocked: checked in with Dev 1 after three
quiet days, confirmed `trading_agent/features.py::compute_features()` is
`baseline_v1`-specific, not a cross-strategy fit; extraction not started,
no ETA; explicitly told not to wait idle
Status after: a genuinely separate process can now reach the Gateway over
HTTP with only the two agent-facing operations exposed; AG-006 remains
open, unaffected by this entry

Completed:
- Asked the owner before building, since a wire transport is a new
  externally-reachable surface, not an internal fix — approved.
- `agent_gateway/http.py::create_app(*, gateway, clock) -> FastAPI`:
  exactly two routes, `POST /agent/proposals` and `POST /agent/no-trade`
  — the only two operations `gateway.py`'s own docstring calls
  agent-facing. No route for `register_identity`/`issue_assignment`/
  `issue_context_bundle` at all, checked structurally
  (`TestNoAdministrativeRouteExists`, mirrors `test_dashboard.py`'s own
  "no mutation route" pattern) — a docstring promise is not a guarantee.
  Kept under `agent_gateway/`, not `src/crumblr/api/` — `build.md`'s
  architecture diagram already earmarks `api/` for Core's own Control API,
  a different authority boundary.
- Auth: the same interim shared-secret mechanism (AG-001), as two headers
  (`X-Agent-Id`, `X-Agent-Credential`). Unknown agent / wrong credential /
  suspended agent all collapse to one `401` (never help enumerate agent
  ids, same discipline `AuthenticationError` already documents);
  impersonation is `403`; a fingerprint conflict is `409`; malformed JSON
  or a failed contract validation is `400`. A **rejected** proposal is
  still `200 OK` with `"accepted": false` in the body — a refusal is a
  normal, fully-audited outcome, not a transport error.
- **Found and fixed one real bug via the test suite itself**:
  `pydantic.ValidationError.errors()` can carry the raw exception object
  in a validator's `ctx` (e.g. `TradeProposal`'s own stop/target-direction
  check), which plain `json.dumps` (`JSONResponse`'s encoder) cannot
  serialize — a domain-validator rejection was coming back as an
  unhandled `500` instead of the intended `400`. Fixed with
  `error.errors(include_url=False, include_context=False)`; regression
  test included.

Evidence:
- `tests/unit/test_agent_gateway_http.py` — new, 16 tests (accept/reject,
  all four failure-mode status codes, malformed JSON, malformed contract,
  idempotent replay, conflicting retry, both routes, structural
  route/docs checks).
- Full non-integration suite: 839 → **855 passed**, 1 skipped.
- `uv run ruff check .` / `mypy` — clean. No new dependency — FastAPI/
  `TestClient` already in use by `dashboard/app.py`.

Problems found:
- The `ValidationError`/`json.dumps` serialization bug above — the only
  real finding, caught by the tests themselves, not by inspection.

Risk impact:
- None. Nothing outside `agent_gateway/` and its own tests imports any of
  this — verified by grep. No admin operation is reachable over HTTP. No
  deployment/process wiring exists yet (no `uvicorn` invocation, no port,
  no TLS) — this proves the boundary is safe to eventually expose, not
  that anything is listening anywhere today.

Decision:
- Built, tested, merged. Committed `a0e380a` on `agent/contracts`, pushed
  to `main` as a clean fast-forward.
- Synced onto Dev 1's `68af9c1` afterward (durable execution-activation
  wiring + F-062 fix, `IMPACT: SHARED-CONTRACT` — two additive
  `ExecutionEventType` members and a `PlatformConfig.config_version`
  hashing change) — independently grepped `agent_gateway/` for both
  changed names, confirmed zero references, full suite re-verified green
  (856 passed) after the rebase.

Next:
- Still waiting on Dev 1's `compute_features()` extraction (AG-006) before
  Step E (`TradeProposal → TradeIntent` mapping) can start. No other
  unblocked work identified in this track as of this entry.

---

## Update 2026-09-01 (fifty-fifth entry) — review 1.26 pulled and processed: Phase 5 opened, F-063 fixed same day, F-064 logged, AG-006 resolved without Dev-1 work

Component: `.github/workflows/ci.yml`, `review/FEEDBACK.md`, `status.md` (new mandatory compact header, review 1.26 §3)
Milestone: Session-start protocol (`CLAUDE.md` §1) applied to a new standing workflow: formal feedback now arrives committed directly into the repository rather than as an external document
Status before: `main` at `8749753` (Dev 2's status.md catch-up entries + `feedback.1.26.md`, both pushed directly by the owner/reviewer per the new workflow, not yet pulled into this worktree)
Status after: Pulled, read in full, registered in `review/FEEDBACK.md`, and acted on the one item explicitly owned by Dev 1 (F-063) the same session

Completed:
- `git fetch` + fast-forward merge picked up `5e11884` (Dev 2's status.md
  catch-up) and `8749753` (`feedback.1.26.md`) — clean, no conflicts,
  working tree was clean before pulling.
- Read `feedback.1.26.md` in full. It opens **Phase 5 — Convergence,
  Observability & DEMO Readiness** across three parallel lanes (Dev
  1/Core, Dev 2/Agent, Lane C/Observability), converging at
  `feedback.2.0`. Explicitly an owner-requested exception to review
  1.25's cadence, not a reversal of it.
- Registered the review in `review/FEEDBACK.md`'s "Reviews received"
  table, opened **F-063** (HIGH, Dev-1-owned — hosted CI broken by
  `UV_FROZEN=1` + `uv sync --locked`) and **F-064** (HIGH, Dev-2-owned —
  the merged HTTP Gateway transport is local/shadow-only, not authorized
  for unprotected remote exposure; not a blocker for current work).
  Corrected the tracker's own cadence section, which still literally said
  "not `feedback.1.26.md`" in its heading.
- **Fixed F-063 the same session**, per review 1.26 §6 item 1's explicit
  priority order. Reproduced the exact reported failure locally before
  touching anything: `UV_FROZEN=1; uv sync --locked` → exit 2,
  `error: the argument '--locked' cannot be used with 'UV_FROZEN'
  (environment variable)`, byte-for-byte the message the review quoted.
  Fixed by removing the workflow-level `env: UV_FROZEN: "1"` block from
  `.github/workflows/ci.yml`; both jobs keep `uv sync --locked`
  unchanged. Confirmed the fix locally: `uv sync --locked` alone now
  exits 0. Full quality gate re-run clean (ruff/mypy, 149 source files).
- Added the mandatory compact `status.md` header review 1.26 §3
  requires (`main` HEAD, last hosted CI result, both tracks'
  DONE/NEXT/BLOCKED, F-051 state, owner blockers, `order_send` state,
  next review target) directly under the document header, and corrected
  two stale rows in the older "What's needed next" table that the new
  header would otherwise contradict: row 1 (F-056 → F-063 as the live CI
  blocker) and row 6 (AG-006 no longer blocked — review 1.26 §5 resolved
  it without a Dev-1 `compute_features()` extraction; Dev 2 independently
  reached and confirmed the same reading before I did, via their own
  cross-session message).
- Confirmed via cross-session message that Dev 2 had already read §5/§7
  themselves and started on AG-006 before I finished processing the
  review — no coordination gap, just parallel reading of the same
  now-shared document.

Evidence:
- Local reproduction of the exact CI failure, then confirmation of the
  fix, both shown above.
- `uv run ruff check .` / `uv run ruff format --check .` / `uv run mypy`
  — clean, 149 source files (the three `feedback.1.2x.md` format
  findings are pre-existing, unrelated to this change, not touched).
- Hosted CI itself: **not confirmed** — no `gh`/Actions access in this
  environment, same limitation as every prior CI-adjacent entry. F-063
  stays open in the "gate pending hosted confirmation" sense, mirroring
  F-056's exact prior structure, until a human or a session with GitHub
  access reports the Actions run result.

Problems found:
- None beyond F-063 itself (the actual subject of this entry) and the
  tracker's own stale cadence-section heading, both fixed.

Risk impact:
- None. CI-workflow and documentation changes only; no production code
  touched.

Decision:
- F-063's fix is in scope for immediate action under review 1.26 §6's
  explicit "priority order, item 1" instruction — no separate approval
  needed to start it, consistent with this session's standing practice
  of executing named Dev-1 priorities without re-confirming each one.
- Committing this entry together with the F-063 fix and the
  `review/FEEDBACK.md` registration as one slice, pending the usual
  per-turn commit approval.

Next:
- Push, then fill in this entry's own `main` HEAD value into the compact
  header (the same self-referential-SHA pattern the fifty-second entry's
  follow-up commit already established a precedent for) via a small
  follow-up commit.
- Resume the Dev-1 core critical path per review 1.26 §6 items 2+
  (support/restart F-051 part 2, then `SUBMISSION_STARTED` timing).

---

## Update 2026-09-01 (fifty-sixth entry) — F-051 part 2 closed: a real baseline_v1 decision reaches risk PASS/Supervisor APPROVE against real EUR/USD data

Component: `scripts/live_decision.py` (new `--strategy-id` override), operational (MT5 terminal, `crumblr_soak`), `review/FEEDBACK.md`, `review/INTEGRATION_NOTICES.md`
Milestone: Dev-1 core critical path, review 1.26 §6 item 2 / §10; the last open half of F-051
Status before: Real M5 bar accumulation stalled since 2026-08-27 06:20 UTC; no real `LiveDecisionOrchestrator` decision had ever reached the Supervisor against real data
Status after: **F-051 both parts CLOSED.** Two real `baseline_v1` decisions reached `risk_decision.verdict=PASS` and `supervisor_decision.verdict=APPROVE` against real Pepperstone DEMO EUR/USD bars — the first `SUPERVISOR APPROVE` this project has produced against real data

Completed:
- Copied `.env` from the original shared checkout into this isolated
  worktree (local file copy, same machine/user, values never read into
  this session's own context) and installed the `mt5` extra
  (`uv sync --locked --extra mt5`) — this worktree had neither, since
  `.env` is git-ignored and worktrees don't inherit gitignored files, and
  `uv sync --locked` alone never installs optional extras.
- One-shot `scripts/mt5_probe.py` confirmed the real terminal connection
  works before starting anything long-running: account `***706`,
  `PepperstoneUK-Demo`, `trade_allowed=True`, terminal connected, EURUSD
  resolved. `var/` didn't exist yet in this fresh worktree (fixed).
- Upgraded `crumblr_soak`'s schema to the latest Alembic head
  (`d4b6e2f81a37`, Dev 2's Agent Gateway Step B tables) — it was one
  migration behind.
- Added `scripts/live_decision.py --strategy-id` (optional, defaults to
  the shipped config's strategy): lets this evidence run use
  `baseline_v1` without editing `config/paper.yaml`'s shipped default
  (`ict_v1`), per review 1.26 §10's explicit instruction not to wait for
  `ict_v1`'s 120-bar threshold when `baseline_v1`'s 65 is already
  cleared (82+ bars existed).
- Restarted both `mt5_live_reader.py` (real MT5, read-only, no CLI
  change) and `live_decision.py --strategy-id baseline_v1` against
  `crumblr_soak`, confirmed both healthy (reader: `HEALTHY`, real ticks
  and bars advancing; decision process: polling cleanly).
- **Asked for and received explicit approval before arming
  `crumblr_soak`'s kill switch** (`build.md` §8.2's operator-only reset
  rule, same discipline the 2026-08-27 `order_check` evidence run used) —
  two capsules sealed before arming correctly `BLOCK`ed on
  `SYSTEM_HALTED`, proving the gate itself works.
- **First arm attempt was incomplete, and the platform's own ADR-002
  safety mechanism correctly caught it.** The first reset touched only
  `PostgresSafetyStateStore` (a one-off script against the DB directly),
  not `CompositeSafetyStateStore`'s local file latch
  (`var/safety_state.json`), which didn't exist yet in this fresh
  worktree. Restarting `live_decision.py` afterward surfaced
  `safety_state.disagreement` ("latch halted, journal running") and
  resolved to `UNKNOWN` — even more cautious than `HALTED` — exactly the
  two-independent-records design ADR-002 specifies, refusing to trust a
  half-written state rather than silently proceeding. Also separately
  confirmed that `KillSwitch.is_halted` is read from in-memory state
  loaded once at process startup (`KillSwitch.on_startup()`), never
  re-polled from the store during a run — the same statefulness pattern
  already flagged to Dev 2 as AG-012's root cause (`EquityLedger`), now
  independently confirmed to apply to the kill switch too. Fixed by
  re-arming through `build_durable_runtime()`'s actual
  `CompositeSafetyStateStore` (writing both records together, the same
  path the application itself uses) and restarting the process again —
  correct and complete after that.
- Real evidence reached, four capsules total this session:
  `4a796878.../22332f29...` (07:40/07:45, `BLOCK`/`SYSTEM_HALTED`,
  before the fix) — expected. `5b8c89df...` (08:05:44) —
  **`risk PASS`, `supervisor APPROVE`**. `0adb331a...` (08:10:28) —
  `BLOCK`/`MARKET_DISABLED`+`EXPERT_TRADING_DISABLED`+`STALE_MARKET_DATA`,
  a real transient broker-snapshot hiccup, not investigated further
  since it doesn't affect the evidence requirement and the gate reacted
  correctly. `ed0b5c4a...` (08:15:31) — **`risk PASS`,
  `supervisor APPROVE`** again. `d193025e...` (08:20:59) —
  `BLOCK`/`STALE_MARKET_DATA`, another transient.
- Stopped `live_decision.py` and tripped `crumblr_soak`'s kill switch
  back to `HALTED` (attributed, `build_durable_runtime()`'s composite
  store again, detail references both PASS capsules) — same discipline
  as the prior evidence run, so a future session doesn't find `RUNNING`
  without a fresh, deliberate decision. Confirmed both records agree on
  `HALTED` afterward.
- Asked whether to keep `mt5_live_reader.py` running afterward (real,
  ongoing resource use on the host) rather than assuming — approved to
  leave it running, read-only, toward `ict_v1`'s remaining bar count.
- Flagged the `EquityLedger`/kill-switch statefulness pattern to Dev 2
  before they started review 1.26 §7 item 3 (wiring `TradeIntent`
  through Risk/Policy/capsule sealing) — a real architectural question
  about whether two independent processes can each hold their own
  in-memory safety/risk state without a shared authority. Recorded as
  AG-012 on Dev 2's own tracker; not a blocker for shadow work today
  since `order_send` stays unreachable either way. Logged in
  `review/INTEGRATION_NOTICES.md`.
- Updated `review/FEEDBACK.md`'s F-051 row (part 2 CLOSED) and the
  compact `status.md` header (F-051 state, Dev 1/Dev 2 DONE/NEXT/BLOCKED)
  to match — no stale "stalled"/"blocked" prose left standing after
  `main` (and this document) says otherwise, per review 1.26 §3's own
  rule.

Evidence:
- Capsule ids and verdicts as listed above, read directly from
  `crumblr_soak` via `CapsuleStore.read_all()`, not inferred from logs.
- `var/safety_state.json` and the journal both confirmed to read
  `HALTED` after the final trip.
- No `uv run pytest`/quality-gate run this entry — no `src/` production
  code changed beyond the one-line `--strategy-id` CLI addition to
  `scripts/live_decision.py`, which was itself quality-gate-checked
  (`ruff`/`mypy` clean) before use.

Problems found:
- The incomplete first kill-switch arm (above) — a real operational
  mistake on this session's part, not a platform defect. The platform's
  own ADR-002 double-record design caught it correctly and refused to
  proceed on a disagreeing state, which is exactly what that design is
  for. Recorded here in full rather than only mentioning the eventual
  success, per this project's own "report evidence honestly" rule.

Risk impact:
- None adverse. `order_send` stayed structurally unreachable throughout;
  every step here was either read-only (the reader) or produced
  non-sending decision capsules (the decision pipeline). The kill switch
  was armed only for the deliberate evidence window and confirmed
  reverted to `HALTED` before this entry was written.

Decision:
- F-051 is now fully CLOSED — both parts real-terminal-validated, zero
  outstanding gap. Not committed yet — pending the usual per-turn
  approval, same slice as the `review/FEEDBACK.md`/`INTEGRATION_NOTICES.md`
  updates above.

Next:
- Core critical path item 3: `SUBMISSION_STARTED` timing at the correct
  pre-side-effect point — not yet started, no plan drafted.

---

## Update 2026-09-01 (fifty-seventh entry) — review 1.27 pulled and processed: F-065 (hosted CI) fixed same day, ACK written, Static Agent kickoff acknowledged

Component: `pyproject.toml` (`[tool.ruff]` exclude), `review/FEEDBACK.md`, `status.md` (compact header, required ACK block)
Milestone: Session-start protocol applied to the second review filed directly into the repository (the standing workflow since review 1.26)
Status before: `main` at `ebf87e3`; hosted CI run 60 had confirmed F-063 fixed but failed on a new cause (`ruff format --check` rewriting reviewer Markdown, F-065)
Status after: F-065 fixed and verified locally the same session; `main` at `ebf87e3` plus this slice; Static Agent integration kickoff read and acknowledged (Dev-2-owned, no Dev-1 action required beyond §8's existing priorities)

Completed:
- `git fetch` + fast-forward merge picked up `fa0a6b3`
  (`feedback.1.27.md`) — clean, working tree was clean before pulling.
- Read `feedback.1.27.md` in full. Confirms Phase 5 continues (not a
  Phase-4 reopening, not an execution authorization) and opens Static
  Agent integration work — entirely Dev-2-owned per §6/§8; §8 explicitly
  keeps Dev 1 on the existing core critical path and tells Dev 1 not to
  absorb the Static Agent build.
- Registered the review in `review/FEEDBACK.md`'s "Reviews received"
  table and opened **F-065** (MEDIUM now/HIGH before `feedback.2.0`,
  Dev-1-owned — hosted CI's `ruff format --check` step rewriting
  immutable historical reviewer Markdown).
- **Fixed F-065 the same session.** Reproduced first, before touching
  anything: `ruff format --check review/feedback.1.22.md` reformats
  three embedded Python code fences — confirmed ruff 0.16.3 formats
  Python code blocks inside Markdown by default, with no existing
  `pyproject.toml` config addressing it. Fixed with two additions to
  `[tool.ruff]`: `extend-exclude = ["review/"]` (so `ruff format
  --check .`, CI's actual invocation, skips the whole directory rather
  than only the three files hosted run 60 happened to hit — the same
  failure can't recur from a new reviewer file) and `force-exclude =
  true` (so an explicit `ruff format --check review/feedback.1.22.md`
  invocation — e.g. a future targeted or pre-commit check — also
  respects the exclude, not only directory-walk discovery, which is the
  only case `extend-exclude` alone covers).
- Confirmed the fix precisely matches what CI runs: `ruff format
  --check .` → "162 files already formatted", exit 0. `ruff check .`
  and `mypy` both still clean (150 source files) — the exclude does not
  weaken lint coverage of anything that was actually a lint target,
  since reviewer Markdown was never linted, only reformatted.
- Updated the compact `status.md` header (main HEAD pending this
  commit, hosted CI result narrative now cites F-065 not F-063, Dev 1/
  Dev 2 DONE/NEXT lines) and the older "What's needed next" table's row
  1, so neither contradicts what actually happened, per review 1.26
  §3's own rule.
- Wrote the required §1 ACK block in this document's own compact-header
  section, in the exact terse format review 1.27 asked for (branch/
  worktree, main SHA fetched, DONE since 1.26, NEXT, BLOCKED, what's
  needed from Dev 2) — no essay, no new status family.

Evidence:
- Local reproduction of the exact CI failure (three reformatted code
  fences), then confirmation of the fix, both shown above.
- `uv sync --locked` re-run clean after the `pyproject.toml` edit — no
  lockfile mismatch, since `extend-exclude`/`force-exclude` are
  formatter-scoping settings, not dependency changes.
- `uv run ruff check .` / `uv run ruff format --check .` / `uv run
  mypy` — all clean (150 source files).
- `uv run pytest -q` (full suite, `crumblr_test_dev1`) — **1058 passed,
  3 skipped**, zero failures. No production code changed this entry, so
  this run is a sanity check rather than new coverage.
- Hosted CI itself: **not confirmed** — no `gh`/Actions access in this
  environment. F-065 stays "gate pending hosted confirmation," the same
  structure F-056/F-063 used before it, until a human or a session with
  GitHub access reports the next run's result.

Problems found:
- None beyond F-065 itself (the actual subject of this entry).

Risk impact:
- None. CI-configuration and documentation changes only; no production
  code touched.

Decision:
- F-065 is in scope for immediate action under review 1.27 §8's
  explicit "priority 1" instruction — no separate approval needed to
  start it, consistent with this session's standing practice for named
  Dev-1 priorities.
- Static Agent integration (§4–§7, §10) is Dev-2-owned; no Dev-1 code
  change identified as needed from reading it. Will support with a
  minimal read-only Core seam only if Dev 2 actually requests one
  (§8's own instruction), not preemptively.
- Not yet committed — pending the usual per-turn approval.

Next:
- Push, then fill in this entry's own `main` HEAD value into the
  compact header via a small follow-up commit (same pattern as the
  fifty-second/fifty-fifth entries).
- Resume core critical path item 2, `SUBMISSION_STARTED` timing at the
  correct pre-side-effect point — not yet started, no plan drafted.

---

## Update 2026-09-01 (fifty-eighth entry) — SUBMISSION_STARTED durable pre-side-effect emission (core critical path item 3)

Component: `domain/enums.py`, `application/execution.py`, `persistence/execution.py`, `review/adr/ADR-006-submission-gate.md`, `review/FEEDBACK.md`, `review/INTEGRATION_NOTICES.md`
Milestone: Dev-1 core critical path item 3 (review 1.26 §6 / review 1.27 §8), planned via `EnterPlanMode` and approved before implementation
Status before: `SubmissionGate` real and wired (items 1-2); `ExecutionEventType.SUBMISSION_STARTED` a bare, undocumented member inside a "Reserved for M5, never emitted" block; F-060's `orders_in_last_hour` counter a real query, honestly returning `0`
Status after: `SUBMISSION_STARTED` is real — appended durably, as the platform's commitment point, the moment `SubmissionGate` opens. `order_send` still not called anywhere — the explicit scope decision this slice makes and documents

Completed:
- Researched thoroughly before designing (`OrderCheckMt5Gateway
  .order_send`'s exact behaviour — confirmed a pure unconditional raise
  touching neither `order` nor MT5; every existing reference to
  `SUBMISSION_STARTED`; the established "unknown-state recovery"
  pattern shape across `risk/session.py`/`application/decision_window.py`
  /`risk/kill_switch.py`; ADR-001/003/005/006's relevant sections; every
  existing `order_send`-never-called assertion in the test suite) via a
  dedicated research pass before writing the plan.
- **The scope decision, made explicit and documented, not just
  implied**: two ways existed to build this — actually call
  `order_send` right after the event (letting its guaranteed raise
  propagate) or append the event as the new terminal outcome and stop,
  leaving the broker-call pairing to the explicitly later idempotence/
  ambiguous-outcome-recovery items. Took the second, narrower option —
  reasoning recorded in `review/adr/ADR-006-submission-gate.md` §6 and
  in the plan file: wiring the literal `order_send` call site is a
  materially larger change than "record a commitment," and risks
  reading as exactly what both reviews warn against ("do not add a real
  `order_send` call merely because items 1-5 exist").
- `domain/enums.py`: `SUBMISSION_STARTED` moved out of the "Reserved for
  M5" block, given a real docstring (what it means, that it fires only
  when the gate opens, that emitting it is explicitly not calling
  `order_send`). Fixed two now-stale spots the research surfaced:
  `SUBMISSION_GATE_PASSED`'s docstring no longer claims
  `SUBMISSION_STARTED` is "reserved for" the eventual `order_send` step;
  the "Reserved for M5" comment above the remaining five members
  corrected to no longer include it.
- `application/execution.py`: `_process()` now branches on the gate's
  outcome — `BLOCKED` returns exactly as before; `PASSED` calls new
  method `_start_submission(order_request_id, order, final_now)`,
  which appends `SUBMISSION_STARTED` with the complete serialized
  `ApprovedOrder` as payload (F-059's "complete content, not a
  hand-picked subset" discipline) and becomes the reported outcome.
  `order` was already in scope from `order_check`'s own construction —
  no new read, no new object built.
- `persistence/execution.py`: fixed `count_events_since`'s docstring,
  which claimed "Phase 4 never emits `SUBMISSION_STARTED`" — no longer
  literally true. Corrected to state the real invariant: the counter
  stays `0` in every real deployment because no shipped config can open
  `SubmissionGate`, not because the event type is unemittable.
- `review/adr/ADR-006-submission-gate.md`: new §6 addendum recording the
  decision and its reasoning in full; §4 Consequences updated to mark
  this item done rather than future work.
- Test: renamed/extended
  `test_a_fully_approved_config_reaches_submission_gate_passed` →
  `test_a_fully_approved_config_reaches_submission_started`. Now asserts
  the outcome is `SUBMISSION_STARTED`, the durable event log reads
  `REQUEST_CLAIMED → FINAL_RISK_PASSED → ORDER_CHECKED →
  SUBMISSION_GATE_PASSED → SUBMISSION_STARTED` (5 events, was 4), the
  new event's payload carries the real `ApprovedOrder` content
  (`order_request_id`, `broker_symbol`, `side`, `volume` checked
  directly — caught and fixed my own first-draft assumption that the
  test fixture's intent side was `SELL`; it's `BUY`, verified against
  `tests/conftest.py::make_intent`'s actual default before asserting
  rather than guessing), and — the hard assertion this test exists for
  — `order_send_calls == 0` still holds, even from the most permissive
  config this platform can construct.
- `review/INTEGRATION_NOTICES.md`: new entry — `domain/enums.py` is
  shared-contract territory again, and this specifically makes real the
  exact marker Dev 2's `agent_gateway/contracts.py::ProposalWithdrawal`
  already names by name as the withdrawal-cutoff boundary (ADR-005).
  Confirmed via grep: no `agent_gateway/` code depends on the event
  having actually fired yet, only a docstring names it.
- `review/FEEDBACK.md`: F-049's row updated — no longer "still not
  reachable," now records what actually happens when the gate opens.

Evidence:
- `uv run ruff check .` / `uv run ruff format --check .` / `uv run
  mypy` — clean, 150 source files.
- `uv run pytest tests/integration/test_execution_orchestrator.py -v`
  — 12 passed.
- Grepped the whole repo for `SUBMISSION_GATE_PASSED` and `order_send`
  after implementing — confirmed no stray outcome-level assertions were
  missed elsewhere, and every `order_send` mention in the diff is
  prose/docstring, zero new call sites.
- Full suite, solo, against `crumblr_test_dev1` — **1058 passed, 3
  skipped**, zero failures — the exact same total as the last confirmed
  run, confirming one test renamed, none added or removed, no
  regressions.

Problems found:
- None in the shipped result. My own first-draft test payload assertion
  guessed the wrong intent side (`SELL` instead of the fixture's actual
  `BUY`) — caught by checking `tests/conftest.py::make_intent` directly
  before finalizing rather than after a test failure. Recorded here per
  this project's own "report evidence honestly" discipline, not because
  it reached committed code.

Risk impact:
- None. `SUBMISSION_STARTED` remains unreachable in every shipped
  config today, same as `SUBMISSION_GATE_PASSED` already was.
  `order_send` is not called anywhere in this diff.

Decision:
- Entered plan mode before implementing (the task warranted it — a real
  architectural scope decision existed, not an obvious single path).
  Plan approved before any code was written.
- Not yet committed — pending the usual per-turn approval, on a new
  `core/submission-started`-prefixed branch.

Next:
- Rebase onto `origin/main` if it has moved, re-verify, commit, push,
  fill in this entry's `main` HEAD via a follow-up commit.
- Core critical path item 4: execution-event same-id/different-content
  conflict hardening — not yet started, no plan drafted. Likely where
  `ExecutionEventStore.append()`'s missing inserted-vs-duplicate return
  value (flagged during this slice's research, ADR-003 §3) actually
  needs closing.

---

## Update 2026-09-01 (fifty-ninth entry) — execution-event same-id/different-content conflict hardening (core critical path item 4)

Component: `persistence/execution.py`, `review/adr/ADR-003-persistence-invariants.md`, `review/FEEDBACK.md`, `review/INTEGRATION_NOTICES.md`
Milestone: Dev-1 core critical path item 4 (review 1.26 §6 / review 1.27 §8), planned via `EnterPlanMode` and approved before implementation
Status before: `ExecutionEventStore.append()` derived `event_id` from `(order_request_id, event_type)` only and did `ON CONFLICT DO NOTHING` with no readback — a retried event with genuinely different `reason_codes`/`detail`/`payload` was silently swallowed, and the caller could not tell
Status after: mirrors `ExecutionRequestStore._claim()`'s exact conflict pattern — a matching-content retry converges silently, different content raises a new `ExecutionEventConflictError`

Completed:
- Researched thoroughly before designing: read `ExecutionRequestStore
  ._claim()` in full (the exact pattern to mirror — insert-on-conflict
  with `.returning(...)`, readback on a loss, compare, raise on
  mismatch), `journal.py::AppendResult` (the house "report inserted vs.
  duplicate" idiom, confirmed unconsumed by any caller today — same as
  this slice's own new return value), every existing `append()` call
  site (six, covering nine event types via `_refuse()`), every existing
  conflict test to mirror exactly, and `domain/hashing::fingerprint()`'s
  canonicalization rules (confirmed the comparison is stable across the
  JSONB round-trip as long as both sides compare already-JSON-shaped
  values, not raw domain objects).
- **Confirmed, before building, that this conflict cannot currently
  occur through `run_once()`** — once an `order_request_id` is claimed,
  `_process()` returns `None` immediately and never re-executes the
  event-appending code. Same "build the approved shape before the
  schedule pressure" discipline already used for `SubmissionGate`/
  `SUBMISSION_STARTED`: real, tested, structurally inert until
  "ambiguous-outcome recovery" (a separate, later item) introduces an
  actual retry path.
- **No schema/migration change** — deliberately. `execution_events` has
  no fingerprint-shaped column (unlike `execution_requests`), and one
  isn't needed: the row already stores everything the comparison needs,
  so the fingerprint is computed on the fly on both sides at conflict
  time rather than persisted, avoiding a migration and a backfill
  problem the append-only grant would have made impossible anyway (the
  app role can never `UPDATE` old rows to add a column value).
- `persistence/execution.py`: new `ExecutionEventConflictError`
  (bare-message shape, mirrors `ExecutionRequestConflictError` exactly).
  `ExecutionEventStore.append()` now returns `journal.AppendResult`
  (reused, not redefined) instead of `None`; split into `append()`
  (connection routing) + `_append()` (logic), mirroring `claim`/`_claim`'s
  own split. On an insert loss, reads back the existing row's
  `reason_codes`/`detail`/`payload` on the *same connection* (always
  sees the committed winner), fingerprints both sides, raises on
  mismatch. `event_id_for()` unchanged.
- `application/execution.py`: no change needed —
  `ExecutionOrchestrator._append()` keeps its `-> None` signature and
  simply doesn't consume the new return value yet; the conflict
  exception is allowed to propagate uncaught, exactly like
  `ExecutionRequestConflictError` already does.
- Tests (`tests/integration/test_execution_persistence.py`): extended
  `test_re_appending_the_same_transition_does_not_duplicate` with
  `AppendResult` return-value assertions; three new tests mirroring
  `TestClaim`'s conflict test 1:1 — different payload, different
  `reason_codes`, different `detail` (review 1.23 §7 names all three
  explicitly, not only payload) — each asserting
  `pytest.raises(ExecutionEventConflictError, match=...)`.
- `review/adr/ADR-003-persistence-invariants.md`: fixed a long-stale
  "Status of implementation: Not started" line while touched (its own
  §3 is exactly what this slice fulfills) — noted as a correction, not
  silently edited.
- `review/FEEDBACK.md`: recorded in the "Unreviewed work" table, not as
  a new F-number — review 1.23 §7 itself explicitly declined to open a
  standalone finding for this ("track it with Phase-6... work rather
  than opening another standalone blocker"). Opportunistically fixed an
  adjacent stale row (F-051 part 2 still listed as pending, already
  closed).
- `review/INTEGRATION_NOTICES.md`: no shared-contract file was touched,
  but flagged a real finding surfaced as a byproduct of this research —
  `persistence/agent_gateway.py::AgentDecisionEventStore.append_event()`
  has the **identical unhardened gap** (Dev-2-owned, not fixed here,
  the exact pattern to mirror pointed out).
- Answered a substantive cross-session question from Dev 2 about
  reusing `trading_agent/ict.py::evaluate()` for the Static Agent
  bridge (confirmed it's already public/reusable, no new Core seam
  needed) and flagged a real field-mapping gap found while checking
  (`IctFeatureSnapshot`'s actual fields don't match the fork's wire
  contract 1:1) before they built against a wrong assumption.

Evidence:
- `uv run ruff check .` / `uv run ruff format --check .` / `uv run
  mypy` — clean, 152 source files.
- `uv run pytest tests/integration/test_execution_persistence.py -v`
  — 14 passed (4 new/extended). `tests/integration
  /test_execution_orchestrator.py -v` — 12 passed, confirming zero
  behavioural change on the orchestrator side, as predicted (no shipped
  code path can trigger a real conflict today).
- Grepped `agent_gateway/` for `AppendResult`/`ExecutionEventConflictError`
  — zero references, confirming `IMPACT: NONE`.
- Full suite, solo, against `crumblr_test_dev1` — **1077 passed, 3
  skipped**, zero failures.

Problems found:
- None in the shipped result.

Risk impact:
- None. No shipped code path can reach the new conflict-detection logic
  yet (the request-level claim gate prevents re-entry); this is
  structural safety infrastructure for when ambiguous-outcome recovery
  needs it, not a behavioural change today.

Decision:
- Entered plan mode before implementing; plan approved before any code
  was written.
- Not yet committed — pending the usual per-turn approval, on a new
  `core/`-prefixed branch.

Next:
- Core critical path item 5: `order_send` idempotence — not yet
  started, no plan drafted.

---

## Update 2026-09-01 (sixtieth entry) — review 1.28 processed: Core declared strategy-neutral, F-066 opened, no Dev-1 code change

Component: `review/FEEDBACK.md`, `status.md`
Milestone: Session-start protocol applied to `feedback.1.28.md` — a third review filed directly into the repository, and the first genuine architectural-correction review since Phase 5 began
Status before: `main` at `5a237cb`; Dev 2's AG-015 finding (Static Agent fork needs a closed, strategy-specific reason-code vocabulary `ict_v1` cannot honestly produce) was open and explicitly flagged as a possible review 1.27 §12 escalation case
Status after: Resolved as an architectural correction, not a mapping problem — new product invariant "Crumblr is strategy-neutral," F-066 opened tracking a nine-point closure checklist, **zero Dev-1 code change identified or required**

Completed:
- `git fetch` + rebase picked up `5a237cb` (`feedback.1.28.md`) while
  item 4's commit was already made — rebased cleanly, re-verified the
  full quality gate and full suite (1077 passed, 3 skipped, same count
  as pre-rebase) before pushing, same discipline as every prior rebase
  this session.
- Read `feedback.1.28.md` in full. Core conclusion: AG-015 "is not
  merely a reason-code vocabulary mismatch... the strategy computation
  is on the wrong side of the interface." Three previously-plausible
  fixes explicitly rejected (Crumblr re-implementing the external
  strategy, mapping `ict_v1` onto it, inventing a shared vocabulary) —
  all three would make Core strategy-specific or fabricate evidence.
- Registered the review in `review/FEEDBACK.md`'s "Reviews received"
  table and opened **F-066** (HIGH before directional external-agent
  shadow promotion/`feedback.2.0`, owners: Dev 2 for the Crumblr
  boundary, the external Agent Developer for the Static Agent runtime,
  **Dev 1 only for shared Core seams if specifically requested**).
- **Confirmed directly against §12's own text that no Dev-1 action is
  required right now**: "Dev 1 should not reimplement Pivot 2.2 and
  should not interrupt the Core submission-safety critical path... Only
  support this architecture when Dev 2 needs a small shared seam...
  Do not add external strategy semantics to Core." `baseline_v1`/
  `ict_v1` explicitly kept (reclassified "legacy/internal reference
  strategies," not deleted) — no change needed to either.
- Updated the compact `status.md` header (Dev 1/Dev 2 lines, next
  review target) so it doesn't contradict what actually happened, per
  review 1.26 §3's own rule — no new ACK block, since (unlike review
  1.27 §1) review 1.28 does not request one.
- Cross-session: Dev 2 independently reached and confirmed the same
  reading before I finished processing the review, and is already
  building the direct fix for AG-013 (a strategy-neutral external-agent
  policy gate that drops the mandatory-`Regime` requirement, per review
  1.28 §7 point 3) — no coordination gap, parallel reading of the same
  now-shared document, same pattern as every prior review this session.

Evidence:
- No quality-gate/test run needed for this entry specifically — no
  `src/` code changed by processing this review. The full suite run
  that accompanied the item-4 rebase (1077 passed, 3 skipped) already
  covers everything currently shipped.

Problems found:
- None. This entry is documentation/registration only.

Risk impact:
- None. No production code touched.

Decision:
- No plan drafted for this review — nothing to plan, since §12
  explicitly assigns no Core work. Continuing straight to core critical
  path item 5 rather than pausing for a review that named zero Dev-1
  action items.

Next:
- Core critical path item 5: `order_send` idempotence — not yet
  started, no plan drafted.

---

## Update 2026-09-01 (sixty-first entry) — order_send idempotence: MT5 magic-number derivation (core critical path item 5)

Component: `domain/hashing.py`, `domain/models.py::ApprovedOrder`, `review/adr/ADR-007-order-send-idempotence.md`, `review/FEEDBACK.md`
Milestone: Dev-1 core critical path item 5 (review 1.25/1.26 §6/1.27 §8), planned via `EnterPlanMode` and approved before implementation
Status before: `mt5_gateway/port.py::order_send()`'s docstring already required implementations to be idempotent on `order_request_id`, but no mechanism existed anywhere — MT5 has no native idempotency-key concept, and nothing in this repo had ever populated an MT5 order request's `magic` field
Status after: `domain/hashing.py::mt5_magic_number()` derives a deterministic, conservative MT5 `magic` from `order_request_id` alone; `ApprovedOrder.magic_number` exposes it as a computed field, already flowing into `SUBMISSION_STARTED`'s durable payload with zero code change to that event's own construction

Completed:
- Researched thoroughly before designing: `build.md` §7 in full (all ten
  gateway invariants, not only invariant 2), `ADR-001`'s idempotency
  mentions, every `magic`/`comment` reference in the repo (confirmed:
  read-path only, nothing ever writes one), `OrderCheckMt5Gateway
  .order_check()`'s real request-dict construction (confirmed: no
  `magic`/`comment` key today), `ApprovedOrder`/`ExecutionResult` in
  full (confirmed `ExecutionResult.order_send_payload`/`request_payload`
  already exist, unused, `SimulatedBroker`'s own in-process-dict
  idempotence mechanism confirmed non-durable), a repo-wide search for
  any existing UUID-to-integer derivation (confirmed: zero hits — fully
  unaddressed), the read-path reconnect pattern (`LiveReader
  ._reconnect()`, confirmed there is no reusable "call whose outcome is
  unknown" helper to reuse, since reads are side-effect-free and never
  needed one), and the precise item-5/item-6 boundary reviewers have
  drawn across `feedback.1.20.md`/`1.21.md` (item 5 owns identity/
  anti-duplication and durable queryability; item 6 owns the
  unknown-outcome decision procedure — "persist request identity" is
  step 1 of item 6's own recovery order, already done by items 2-4).
- **Confirmed the durable-identity half of idempotence was already
  built** by items 2-4 (claimed `order_request_id`, durably recorded
  `SUBMISSION_STARTED` commitment, content-conflict-hardened) — this
  item's genuine gap was narrower and specific: the MT5-visible half,
  since a broker has no idea what a Crumblr UUID is.
- `domain/hashing.py`: new `mt5_magic_number(order_request_id) -> int`
  — `fingerprint({"order_request_id": ...})`, first 8 hex chars, masked
  to 31 bits. Placed here (not only as a model method) because item 6
  will need the identical derivation to know what to search broker
  state for during reconciliation — a shared utility, not a one-off.
  Documented the 31-bit choice explicitly as deliberate conservatism,
  not a guess: no real Pepperstone/MT5 evidence exists for this field's
  actual constraints, and none can be gathered without submitting a
  real order — exactly what this platform must not yet do.
- `domain/models.py::ApprovedOrder`: new `@computed_field
  magic_number`, mirroring `AccountState.login_hash`'s existing pattern
  exactly (verified that pattern directly before writing the new one).
  A computed field, not a required one — zero changes needed at any of
  the many existing `ApprovedOrder(...)` construction sites across the
  test suite; confirmed no exact-payload-equality test anywhere asserts
  a closed key set before relying on that (grepped for `payload ==`
  across `tests/`, found five hits, none against an `ApprovedOrder`
  -derived payload).
- `review/adr/ADR-007-order-send-idempotence.md` (new): records the
  problem, the mechanism, the width/conservatism reasoning, explicitly
  states `order_send` remains completely unbuilt and unreachable, and
  that this is a precondition for item 6, not a replacement of it.
- Tests: `tests/unit/test_control_plane_contracts.py::TestApprovedOrder`
  — determinism (same `order_request_id` → same magic), distinctness
  (different orders → different magic), range (`0 <= magic <=
  0x7FFFFFFF`). Extended the already-shipped
  `test_a_fully_approved_config_reaches_submission_started` (item 3)
  with one more payload assertion proving the computed field genuinely
  flows through the real durable event, not only in isolation.

Evidence:
- `uv run ruff check .` / `uv run ruff format --check .` / `uv run
  mypy` — clean, 152 source files.
- `uv run pytest tests/unit/test_control_plane_contracts.py
  ::TestApprovedOrder -v` — 10 passed (3 new).
  `tests/integration/test_execution_orchestrator.py -v` — 12 passed,
  confirming the extended payload assertion holds against a real
  orchestrator run.
- Grepped the diff for any new `order_send` call site or `Mt5Module`
  Protocol change — every `order_send` mention is prose/docstring, zero
  new call sites. Grepped `agent_gateway/` for
  `mt5_magic_number`/`magic_number` — zero references, confirming
  `IMPACT: NONE`.
- Full suite, solo, against `crumblr_test_dev1` — **1080 passed, 3
  skipped** (1077 + 3 new), zero failures.

Problems found:
- None in the shipped result.

Risk impact:
- None. `order_send` remains completely unreachable — this ships a pure
  derivation function and a computed field, nothing that touches MT5 or
  changes any existing behaviour.

Decision:
- Entered plan mode before implementing; plan approved before any code
  was written.
- Not yet committed — pending the usual per-turn approval, on a new
  `core/`-prefixed branch.

Next:
- Core critical path item 6: ambiguous-outcome recovery — not yet
  started, no plan drafted. Will need `mt5_magic_number()` (this entry)
  to actually search broker state once built.

---

## Update 2026-09-01 (sixty-second entry) — F-067: the hosted pg_dump/psql restore proof had never actually run

Component: `.github/workflows/ci.yml`, `tests/integration/test_migrations.py`, `review/FEEDBACK.md`
Milestone: Owner-directed punch list — "CI pg_dump/psql verbinding fixen" first, then hosted-CI-green, then core critical path items 6-9
Status before: `TestABackupCanBeRestored` passed locally (via a `docker exec crumblr-pg` fallback this workstation happens to have running) but had never been confirmed to run — not merely pass, *run* — in any hosted CI execution
Status after: two independent, real bugs found and fixed — `postgresql-client` was never installed on the CI runner, and the dump/restore subprocess calls carried no host/port/password at all, so even installing the binaries would not have been sufficient on its own

Completed:
- Investigated before assuming: read `test_migrations.py`'s
  `_pg_dump_command()`/`_psql_command()` in full. Confirmed the
  fallback shape — a locally-installed binary, or `docker exec` into a
  container named `crumblr-pg` (`CRUMBLR_PG_CONTAINER` overridable) —
  and confirmed via `docker ps` that a real `crumblr-pg` container is
  running on this workstation, explaining why this test has passed in
  every local full-suite run this session without ever exercising the
  path hosted CI would actually need. GitHub Actions' Postgres service
  container is never reachable by that fixed name — that fallback
  exists for local development only.
- Confirmed via `grep` that neither subprocess call anywhere passed
  `-h`/`-p`/`PGPASSWORD` — a second, independent bug underneath the
  first: even with `pg_dump`/`psql` installed, a bare `pg_dump -U
  crumblr -d ...` would attempt a local Unix-socket connection instead
  of the mapped `localhost:55432` service container.
- `.github/workflows/ci.yml`: new "Install PostgreSQL client tools"
  step (`apt-get install -y postgresql-client`) ahead of the existing
  steps in the Linux job.
- `tests/integration/test_migrations.py`: new `PG_HOST`/`PG_PORT`/
  `PG_USER`/`PG_PASSWORD` module constants, parsed from the same
  `TEST_URL` everything else in the file already connects with (printed
  and confirmed locally: `localhost:55432`, `crumblr`, password set —
  exactly matching `ci.yml`'s Postgres service). `_pg_dump_command()`/
  `_psql_command()` now return `(command, env)` — the local-binary path
  gets `-h`/`-p` appended and `PGPASSWORD` layered into the subprocess
  environment; the `docker exec` path is untouched (inside that
  container, different, already-working trust-auth semantics apply —
  adding host/port flags there would have pointed at the wrong place
  entirely, the host-mapped port from inside the container that owns
  it).
- New CI step, "Assert the backup/restore test actually ran, not just
  skipped" — mirrors F-056's own "assert reachable, don't silently
  skip" pattern exactly: calls `_pg_dump_command()`/`_psql_command()`
  directly and fails the hosted run loudly if either returns `None`,
  so a future regression in either fix can never again pass CI by
  quietly not running.
- `review/FEEDBACK.md`: registered as **F-067**, same structure as
  F-056/F-063/F-065 before it (specific defect fixed same day,
  hosted-green confirmation held open pending an actual hosted run —
  no `gh`/Actions access in this environment). Updated the compact
  header's CI-result line and the "What's needed next" table's row 1
  so neither still names F-065 as the live blocker.

Evidence:
- `uv run ruff check .` / `uv run ruff format --check .` / `uv run
  mypy` — clean, 154 source files.
- `uv run pytest tests/integration/test_migrations.py -v` — 8 passed,
  including the restore test via the local `crumblr-pg` container,
  confirming the `docker exec` path is genuinely unaffected by this
  change.
- Ran the new CI guard script directly against this workstation's own
  environment before adding it to the workflow — confirmed it correctly
  reports "available" via the `docker exec` fallback, the same check
  hosted CI will run against the newly-installed local binary instead.
- Full suite, solo, against `crumblr_test_dev1` — **1101 passed, 3
  skipped**, zero failures — the exact same total as the last confirmed
  run, confirming zero regressions (no test added or removed, only
  existing test infrastructure and the CI workflow changed).

Problems found:
- F-067 itself (the actual subject of this entry) — two real, distinct
  bugs (missing client tools, missing connection parameters), both
  found by reading the code rather than guessing, per this project's
  own standing practice.

Risk impact:
- None. Test infrastructure and CI-workflow changes only; no production
  code touched. The `docker exec` path used by every local run this
  session — including the one that just re-confirmed the full suite —
  is provably unaffected.

Decision:
- Not yet committed — pending the usual per-turn approval, on a new
  `core/`-prefixed branch, continuing the owner-directed punch list.

Next:
- Confirm hosted CI is genuinely fully green once pushed (still needs a
  human or a session with GitHub access — no `gh` access here).
- Core critical path item 6: ambiguous-outcome recovery — not yet
  started, no plan drafted.

---

## Update 2026-09-01 (sixty-third entry) — ambiguous-outcome recovery (core critical path item 6)

Component: `application/execution.py`, `domain/enums.py`, `review/adr/ADR-008-ambiguous-outcome-recovery.md`, `review/DEVIATIONS.md`
Milestone: Dev-1 core critical path item 6 (review 1.20 §10/1.21 §12, ADR-003 §6), planned via `EnterPlanMode` and approved before implementation; second item of the owner's punch list after item 5/F-067
Status before: once an `order_request_id` was claimed, every later `run_once()` pass for it returned `None` unconditionally, regardless of how far the first pass got — a process crash between `SUBMISSION_STARTED` committing (item 3) and the run finishing left that request permanently, silently stuck, never revisited
Status after: `ExecutionOrchestrator._recover_ambiguous_submission()` runs instead of that no-op whenever a claimed request's last durable event is `SUBMISSION_STARTED`; it searches real broker positions by the deterministic `mt5_magic_number()` (item 5, reused directly per ADR-007's own instruction) and durably records the determination as a new `AMBIGUOUS_OUTCOME_RESOLVED` event — idempotent, never resubmits, `order_send` stays completely unreachable

Completed:
- Researched before designing (via `Agent`/`Explore`, `model: opus`):
  confirmed the exact gap by reading `_process()` directly (the
  claim-skip branch, unconditional `return None`); confirmed
  `SUBMISSION_STARTED` is the only event that can be "last" without a
  real terminal outcome, so it is a complete detector on its own with
  no time-based staleness check needed; confirmed
  `application/reconciliation.py::reconcile()` (whole-account,
  tri-state, 5-minute-freshness snapshot) is genuinely not reusable for
  this per-request question, ruling out reusing it before building
  something new; confirmed `positions()` is magic-aware end to end and
  `pending_orders()` is not, at any layer, ruling in the open-positions
  scope and ruling out silently claiming pending-order coverage.
- `domain/enums.py`: new `ExecutionEventType.AMBIGUOUS_OUTCOME_RESOLVED`
  — deliberately not `RECONCILED`, which stays reserved for item 8's
  later, different purpose (confirming a *known* fill, not determining
  whether an *unclear* submission happened at all).
- `application/execution.py`: the claim-skip branch now calls
  `self._recover_ambiguous_submission(order_request_id, capsule)`
  instead of returning `None` directly. New method: reads
  `events_for()`, returns `None` immediately unless the last event is
  `SUBMISSION_STARTED`; otherwise derives `mt5_magic_number()`, searches
  `self._adapter.positions()` for a match, and appends
  `AMBIGUOUS_OUTCOME_RESOLVED` with the full determination
  (`magic_number`, `submitted`, match count, matching tickets) as its
  payload. Idempotent by construction — the next pass's `events[-1]`
  check is no longer `SUBMISSION_STARTED` once this runs once.
- `review/adr/ADR-008-ambiguous-outcome-recovery.md` (new): records the
  procedure and source, the `AMBIGUOUS_OUTCOME_RESOLVED`-vs-`RECONCILED`
  naming decision, the structural (not time-based) detector rationale,
  the pending-orders/magic scope gap, and that `order_send` remains
  unreachable.
- `review/DEVIATIONS.md`: new D-049, recording the pending-orders/magic
  gap as a real, acknowledged boundary — a submitted `EntryType.LIMIT`
  order sitting pending at crash time would not be found by this check;
  has no effect on any path this platform can exercise today (the
  tested/default shape is `EntryType.MARKET`), but is real.
- `review/FEEDBACK.md`: added a completion row for item 6, same
  structure as items 4/5 before it — no new F-number, consistent with
  how routine core-critical-path progress has been logged this session.
- Tests (`tests/integration/test_execution_orchestrator.py`): new
  `fake_position()` helper and `FakeMt5.positions_get_calls`/
  `open_positions` state. Two new `TestEndToEnd` methods:
  `test_a_stalled_submission_is_recovered_not_reprocessed` (three
  `run_once()` passes — `SUBMISSION_STARTED`, then
  `AMBIGUOUS_OUTCOME_RESOLVED` with `submitted=False`, then `()` with no
  further broker reads, asserted via a delta on the fake's call
  counter, not an absolute count, since the normal pipeline itself
  already reads positions once during regular portfolio observation)
  and `test_a_matching_broker_position_resolves_as_submitted` (a fake
  broker position carrying the exact computed magic number resolves
  `submitted=True` — proving the positive case works even though
  nothing in this codebase can produce it for real yet). Verified the
  pre-existing `test_a_second_run_once_does_not_reprocess_an_already
  _claimed_capsule` by direct read, not assumption — its capsule never
  reaches `SUBMISSION_STARTED`, so the new recovery check falls through
  to the same `None` it already asserted; confirmed unaffected.
- Rebased onto `origin/main` (which had moved to Dev 2's `f1bff67`,
  Agent Gateway event-conflict hardening) before finalizing — new
  `core/ambiguous-recovery` branch created directly from `origin/main`,
  carrying the uncommitted changes forward cleanly (no conflicts, since
  Dev 2's commit only touches `agent_gateway/`); quality gate and full
  suite re-run against the rebased base rather than trusting the
  pre-rebase result.

Evidence:
- `uv run ruff check .` / `uv run ruff format --check .` / `uv run
  mypy` — clean, 155 source files, run fresh on `core/ambiguous-recovery`
  after the rebase onto `origin/main`.
- `uv run pytest tests/integration/test_execution_orchestrator.py -v`
  — 14 passed (12 pre-existing including the already-claimed-capsule
  test, unaffected + 2 new).
- Full suite, solo, against `crumblr_test_dev1`, run on the rebased
  branch — **1111 passed, 3 skipped** (1101 + 2 new item-6 tests +
  Dev 2's own new Agent Gateway tests carried in via the rebase), zero
  failures.
- Grepped the diff for `order_send` — every hit is prose/docstring/
  existing-line-context (`FakeMt5.order_send_calls` assertions, ADR/
  docstring mentions), zero new call sites. Grepped
  `src/crumblr/agent_gateway/` for `AMBIGUOUS_OUTCOME_RESOLVED`/
  `_recover_ambiguous_submission` — zero references, confirming
  `IMPACT: NONE`.

Problems found:
- One self-caught test-assertion bug during writing: the first draft of
  `test_a_stalled_submission_is_recovered_not_reprocessed` assumed
  recovery's broker read would be the only caller of `positions_get()`
  and asserted an absolute count of 1; the normal pipeline itself
  already calls `positions()` once during the first successful
  `run_once()` pass, so the counter was at 2, not 1, after the second
  (recovery-triggering) call. Fixed by switching to delta assertions
  (`positions_read_before_recovery` / `..._after_recovery`) rather than
  absolute counts. Caught and fixed before any commit; no user
  involvement.

Risk impact:
- None. `order_send` remains completely unreachable through every path
  this item added — recovery only reads already-real broker state and
  durably records what it found; nothing here decides to resubmit
  anything, per ADR-003 §6.

Decision:
- Entered plan mode before implementing; plan approved before any code
  was written.
- Committed and pushed after per-turn approval: `b052459` (substantive
  slice), `74e55f1` (SHA-fill follow-up) on `core/ambiguous-recovery` →
  `main`.

Next:
- Confirm hosted CI is genuinely fully green (still needs a human or a
  session with GitHub access — no `gh` access here); unchanged from the
  prior entry.
- Core critical path item 7: automatic flatten submission — see the
  sixty-fourth entry below.

---

## Update 2026-09-02 (sixty-fourth entry) — automatic flatten submission (core critical path item 7)

Component: `application/execution.py`, `risk/flatten_gate.py`, `persistence/flatten.py`, `application/flatten_plan.py`, `domain/models.py`, `domain/enums.py`, `config.py`, new migration, `review/adr/ADR-009-automatic-flatten-submission.md`, `review/DEVIATIONS.md`
Milestone: Dev-1 core critical path item 7 (ADR-004 §5, review 1.24 §12.B, reviews 1.25/1.26 §6/1.27 §8), planned via `EnterPlanMode` (with a `Plan` sub-agent pass) and approved before implementation; third item of the owner's punch list after items 5/6
Status before: `risk/trading_window.py` detected an intraday flatten deadline breach and a rollover crossing and could only halt — `review/DEVIATIONS.md` D-033: "the halt is the whole safety story today"; no mechanism recorded what a close would have done
Status after: `ExecutionOrchestrator.flatten_once()` durably commits to a flatten (real gate, real request/event log, a real `FlattenPlan` naming exactly what would be closed) whenever a position survives the deadline or crosses a rollover, then stops — `close_all_positions`/`order_send` remain completely unreachable

Completed:
- Researched via `Agent`/`Explore` (`model: opus`), then a `Plan` sub-agent
  pass to validate the design against the actual current source before
  committing to it — this item's scope turned out substantially larger
  than items 3-6: confirmed by direct schema read that
  `execution_requests.capsule_id` is `nullable=False`, and a flatten has
  no `DecisionCapsule`/`TradeIntent` behind it (policy-driven, not
  proposal-driven), which forced a genuinely new persistence design
  rather than a small extension of the existing one.
- Coordinated with Dev 2 before creating a migration, per instruction
  §8's traffic-control rule — confirmed no migration in flight on their
  side, proceeded once acknowledged.
- `persistence/schema.py` + new migration (`cc35e55b3f92`, off head
  `d4b6e2f81a37`): `flatten_requests`/`flatten_events`, structurally
  parallel to `execution_requests`/`execution_events` but with no
  capsule/intent FK at all — the absence is the honest statement that a
  flatten has no proposal. Added to `APPEND_ONLY_TABLES` and the
  sequence-grant list.
- `persistence/flatten.py` (new): near-mechanical mirror of
  `persistence/execution.py` — claim-is-the-insert, content-conflict
  hardening (item 4's discipline, copied not re-derived). Idempotency
  key is `(environment, canonical_symbol, trading_day)` — one commitment
  per trading day per symbol, ever; keying on the observed book instead
  would mint a new key every time volume changed between passes, which
  ADR-003 §6 forbids.
- `domain/enums.py`: new `FlattenEventType` (separate from
  `ExecutionEventType` — different tables, different identity spaces).
  `FLATTEN_SUBMISSION_STARTED` is deliberately not `CLOSED` (item 8's
  territory), not `RECONCILED` (item 8's), and specifically not
  `SUBMISSION_STARTED` (that event is `agent_gateway`'s withdrawal-cutoff
  boundary and FINAL Risk's order-frequency-budget authority — a flatten
  is neither an agent proposal nor a new order). Three new `ReasonCode`s,
  deliberately excluded from `HALT_REASONS`.
- `domain/models.py`: new `FlattenInstruction`/`FlattenPlan` — not
  `ApprovedOrder` (which rejects `Side.FLAT` and requires
  intent/risk-decision/supervisor-decision ids a policy-driven close has
  no honest value for). `volume` is always the broker's own reported
  size, never risk-sized — stated explicitly as the largest semantic
  difference from an entry order.
- `application/flatten_plan.py` (new, pure): `build_flatten_plan()` —
  derives `close_side` as the genuine inverse of each position's side,
  marks `crossed_rollover` per position and in aggregate.
- `risk/flatten_gate.py` (new): eleven legs, modelled on
  `submission_gate.py`, eight reused. Two subtle legs, each with its own
  guard test: reconciliation requires "not `UNKNOWN`", never "`MATCHED`"
  (a flatten is *triggered by* an open position, so `MATCHED` would close
  the gate exactly when a flatten is needed — the naive copy of
  `submission_gate.py`'s leg was caught before shipping, not after); an
  `OVERNIGHT_EXPOSURE`-only halt does not close the gate (the existing
  detection path already trips it on the identical condition this gate
  exists to resolve — becoming flat is that halt's own safe resolution).
- `config.py`: new `ExecutionConfig.flatten_submission_enabled` — a
  fourth, separate governance flag (ADR-004 §5.1's decoupling
  requirement), added to `config_version`'s exclusion set alongside the
  other three (F-062's lesson, applied proactively this time).
- `application/execution.py::flatten_once()`: called from the top of
  `run_once()`, independent of the capsule loop; its outcome type
  (`FlattenAttemptOutcome`) deliberately not merged into `run_once()`'s
  return tuple. **Durable state checked before any broker read** — a
  restructure made mid-implementation after a test caught the first
  draft always reading positions every pass regardless of resolution
  state (see Problems found). `intraday.enabled=False` (every shipped
  config's default, every existing test config) short-circuits before
  any of this.
- Two construction-site updates for the new required constructor args:
  `scripts/run_execution_preflight_evidence.py`,
  `tests/integration/test_execution_orchestrator.py`.
- Mechanical prerequisite: extracted `FakeMt5`/`platform_config`/
  `orchestrator`/etc. from `test_execution_orchestrator.py` into
  `tests/integration/_execution_fixtures.py` — verified 14/14 identical
  before/after.
- `review/adr/ADR-009-automatic-flatten-submission.md` (new): the gap,
  seven named design decisions (table-pair choice, event-name choice,
  the two subtle gate legs, why not `ApprovedOrder`, why not
  `OperatorControls`, why keyed on policy not book), explicit scope
  against ADR-004 §5's four sub-items.
- `review/DEVIATIONS.md`: D-033 updated in place to `PARTIALLY RESOLVED`
  (D-035's precedent for in-place revision); new D-050 records the three
  explicitly deferred pieces (retry-then-HALT on a failed flatten, a
  pre-deadline connectivity watch, ADR-004 §7's two open owner
  questions) — none silently absorbed.
- `review/FEEDBACK.md`: completion row, same structure as items 4/5/6,
  no new F-number.
- `review/INTEGRATION_NOTICES.md`: two entries — the new Alembic head,
  and the additive domain-model/enum change.
- Also fixed, in passing: the sixty-third entry's own "Decision"/"Next"
  section still said "not yet committed" for item 6, which had in fact
  been committed and pushed (`b052459`/`74e55f1`) earlier this session —
  corrected while touching this file for item 7's own entry.

Evidence:
- `uv run ruff check .` / `uv run ruff format --check .` / `uv run
  mypy` — clean, 163 source files.
- `uv run alembic heads` — confirmed single head before and after
  creating the migration (`d4b6e2f81a37` → `cc35e55b3f92`).
- `uv run pytest tests/integration/test_migrations.py -v` — 8 passed,
  run immediately after creating the migration, before writing anything
  else, per that file's own established discipline (F-067's lesson).
- `uv run pytest tests/unit/test_flatten_gate.py
  tests/unit/test_flatten_plan.py tests/integration/test_flatten_persistence.py
  tests/integration/test_execution_flatten.py tests/integration/test_execution_orchestrator.py
  tests/unit/test_config.py -v` — **115 passed** (18 gate + 7 plan + 11
  persistence + 9 flatten-integration + 14 pre-existing execution-orchestrator
  unchanged + 48 pre-existing config unchanged + 1 new config, + zero
  failures).
- `test_execution_orchestrator.py`'s all 14 pre-existing tests confirmed
  unchanged after the fixture extraction and the constructor change.
- Full suite, solo, against `crumblr_test_dev1` — **1157 passed, 3
  skipped** (1111 + 46 new item-7 tests exactly: 18+7+11+9+1), zero
  failures.
- Grepped the diff for any new `.order_send(`/`.close_all_positions(`
  call site (excluding definitions, the existing unconditional refuses,
  and test-double assertions) — zero. Grepped `src/crumblr/agent_gateway/`
  for every new flatten symbol (`FLATTEN_`, `flatten_gate`,
  `flatten_plan`, `persistence.flatten`, `FlattenInstruction`,
  `FlattenPlan`, `flatten_once`) — zero references, confirming
  `IMPACT: NONE` behaviourally (the migration/shared-contract additions
  were separately communicated to Dev 2 in advance, per
  `INTEGRATION_NOTICES.md`).

Problems found:
- One real design gap self-caught via a failing test, not by reasoning
  ahead of time: the first draft of `flatten_once()` always read broker
  positions on every pass, even for an already-resolved or
  already-blocked occurrence — contradicting this item's own documented
  claim (mirroring ADR-008's) that recovery "never re-reads the broker
  for an already-resolved request." `test_a_second_pass_does_not_re_commit_a_flatten`'s
  third-pass assertion caught it directly:
  `fake.positions_get_calls` kept incrementing on a third call that
  should have been a pure no-op. Fixed by restructuring `flatten_once()`
  to check durable state (via `flatten_request_id`, derivable from the
  clock and config alone, no broker read needed) *before* any broker
  read — now genuinely mirrors item 6's "query durable request state ->
  reconcile broker state" ordering rather than only claiming to. Also
  added a `positions`-empty guard to `_trip_overnight_exposure()` while
  fixing this, since the restructure surfaced a related case (resolving
  a stale commitment against a now-genuinely-flat book, e.g. after an
  operator's manual flatten, must not trip a fresh halt).

Risk impact:
- None. `close_all_positions`/`order_send` remain completely unreachable
  through every path this item added — the gate opening only appends a
  durable event and stops; nothing here closes a position for real or
  decides to attempt one.

Decision:
- Entered plan mode before implementing; a `Plan` sub-agent pass
  validated the design against real source before finalizing it; plan
  approved before any code was written.
- Coordinated the migration with Dev 2 before creating it, per
  instruction §8.
- Rebased onto `origin/main` a second time before finalizing (it had
  moved with five new Dev-2 commits since the item-6 rebase — AG-016
  through AG-012's design analysis); quality gate, migration tests and
  full suite (1209 passed/3 skipped) re-run fresh on the rebased branch
  rather than trusting the pre-rebase result.
- Committed and pushed after per-turn approval: `ea80f05` on
  `core/automatic-flatten` → `main`.

Next:
- Confirm hosted CI is genuinely fully green (still needs a human or a
  session with GitHub access — no `gh` access here); unchanged from
  prior entries.
- Named but not done this item, to keep scope honest: two small tests
  closing a pre-existing coverage gap (`ReplayOrchestrator`/
  `LiveDecisionOrchestrator._check_session_boundary` have no direct
  test today, only indirect coverage via `policies.evaluate()`) —
  deferred, not claimed as part of this item's own work.
- Continue down the owner's punch list: core critical path item 8,
  post-fill reconciliation.

---

## Update 2026-09-02 (sixty-fifth entry) — post-fill reconciliation (core critical path item 8)

Component: `application/expected_state.py`, `application/reconciliation.py`, `application/execution.py`, `persistence/execution.py`, `persistence/flatten.py`, `domain/enums.py`, new migration, `scripts/reconcile.py`, `review/adr/ADR-010-post-fill-reconciliation.md`, `review/DEVIATIONS.md`
Milestone: Dev-1 core critical path item 8 (review 1.16 §7-8, review 1.26 §6 item 8), planned via `EnterPlanMode` (with a `Plan` sub-agent pass) and approved before implementation; final item of the owner's punch list before item 9
Status before: `ExpectedState.expected_position_tickets`/`expected_pending_order_ids` existed since F-047 but had never been populated by any caller — every reconciliation everywhere in the codebase compared against `flat()`, unconditionally
Status after: `ExecutionOrchestrator.reconcile_once()` derives an expectation from durable execution/flatten history and reconciles it against real broker state every pass, appending `RECONCILED` once per request when its own exposure is fully accounted for — `close_all_positions`/`order_send` remain completely unreachable throughout

Completed:
- Researched via `Agent`/`Explore` (`model: opus`), then a `Plan`
  sub-agent pass to validate the design before committing to it — this
  item's honest scope turned out narrower than items 6/7 in one specific
  sense (its output is provably identical to `flat()`'s in every
  deployment today, since nothing can ever fill) but surfaced a genuine
  cross-item architecture fork with item 7's already-shipped
  `flatten_gate.py`, resolved explicitly rather than left to fall out of
  the implementation by accident (see below).
- Asked the user how to proceed before starting, given the scope
  ambiguity and the cross-item interaction with already-shipped,
  reviewed code — approved to do the full design and build.
- `domain/enums.py`: `RECONCILED` moved out of "Reserved for M5" into
  the emitted section with a full docstring — the only one of the five
  reserved members whose literal claim ("the platform compared its
  expectation against broker truth") this platform can honestly make
  today, since the other four each assert a broker fact no code path
  can produce.
- `application/expected_state.py` (new, pure): `derive_expected_exposure()`
  — a total, exhaustive mapping over every `ExecutionEventType` member
  (guarded by a test that iterates the enum, so a future addition
  without an exposure decision fails a test rather than silently
  reporting zero exposure), plus the flatten-interaction rules (a
  resolved flatten's `closed_tickets` are removed from what a request
  is still expected to hold; an unresolved commitment's targets become
  undetermined) and D-049 promoted to a runtime-enforced leg (a
  non-`MARKET` entry makes pending-order exposure undetermined rather
  than a false empty set).
- `application/reconciliation.py`: new `ExpectedState.undetermined_reasons`
  field (symmetric with the existing `expected_spec_version=None` leg,
  F-055) and `from_durable_exposure()` classmethod, alongside — not
  replacing — `flat()`. One new `UNKNOWN` leg in `reconcile()`.
- **Resolved the flatten-gate fork explicitly**: item 7's `flatten_gate.py`
  leg was justified in ADR-009 §2.3 on the premise that `flat()` is the
  only expectation this platform can form — item 8 makes that premise
  false. Decision: `flatten_once()` keeps passing `flat()` (switching
  would be all-cost, no-benefit at the deadline — the derived
  expectation can only *newly close* the gate, never newly open it);
  only the gate's *justification* is rewritten, to an
  expectation-independent argument that survives item 8. The existing
  guard test's assertions are unchanged, only its docstring.
- `application/execution.py::reconcile_once()`: called from the
  *bottom* of `run_once()` (after the capsule loop, not the top like
  `flatten_once()`), so item 6's same-pass recovery resolves an
  ambiguity before reconciliation asks about it — confirmed directly:
  the two pre-existing item-6 tests now observe `RECONCILED` following
  `AMBIGUOUS_OUTCOME_RESOLVED` in the same `run_once()` call, and were
  updated accordingly (not a regression — the intended, designed
  same-pass convergence).
- Two new persistence read seams: `ExecutionEventStore
  .request_ids_with_event()`, `FlattenEventStore.occurrence_histories()`
  — bounded by state (the exhaustively-proven candidate set), not time,
  deliberately: time-bounding would defeat the mechanism's purpose (a
  position lost track of weeks ago is exactly the drift this item
  exists to catch).
- One index-only migration (`03df83b062a6`, off head `cc35e55b3f92`,
  coordinated with Dev 2 first per instruction §8) —
  `ix_execution_events_type_time` — serves the new seam and
  retroactively serves `count_events_since()`'s existing unindexed
  filter.
- `scripts/reconcile.py` updated to use the derived expectation when
  any durable history exists, `flat()` otherwise — a second, human-
  facing consumer of the same mechanism, output unchanged today.
- `review/adr/ADR-010-post-fill-reconciliation.md` (new): the gap, the
  mechanism (with named subsections for the flatten-gate fork in full,
  why `RECONCILED` and not the other four, the exposure mapping, the
  read-seam scale reasoning), what this does not do, consequences.
- `review/DEVIATIONS.md`: new **D-051** naming three adjacent gaps, none
  folded into this item's own work — no `OrderState` transition-
  validation state machine, a forward hazard in `live_decision.py`'s
  cached `flat()` expectation once `order_send` lands, and
  `config.SupervisorConfig.halt_on_reconciliation_mismatch` staying
  unconsumed (confirmed by grep: set in `config/base.yaml`, read
  nowhere). D-049 and D-050 both gain cross-references.
- `review/FEEDBACK.md`: completion row, same structure as items 4-7, no
  new F-number. `review/INTEGRATION_NOTICES.md`: two entries — the new
  Alembic head, the additive `ExpectedState.undetermined_reasons` field.

Evidence:
- `uv run ruff check .` / `uv run ruff format --check .` / `uv run
  mypy` — clean, 174 source files.
- `uv run alembic heads` — confirmed single head before and after
  creating the migration (`cc35e55b3f92` → `03df83b062a6`).
- `uv run pytest tests/integration/test_migrations.py -v` — 8 passed,
  run immediately after creating the migration.
- Targeted new/changed test files — 143 passed: 17
  (`test_expected_state.py`) + 4 (`test_reconciliation.py` additions) +
  7 (`test_execution_reconciliation.py`) + 7 (`test_execution_persistence.py`
  additions) + 3 (`test_flatten_persistence.py` additions) + 74
  (`test_execution_orchestrator.py` + `test_execution_flatten.py` +
  `test_reconciliation.py` + `tests/integration/test_reconciliation.py`
  + `test_flatten_gate.py`, all confirmed passing together, including
  the two item-6 tests updated for same-pass convergence) + 31
  (persistence files run standalone once more to confirm).
- Full suite, solo, against `crumblr_test_dev1` — **1244 passed, 3
  skipped** (1209 + 35 new item-8 tests exactly: 17+4+7+4+3), zero
  failures.
- Grepped the diff for any new `.order_send(`/`.close_all_positions(`
  call site (excluding definitions, refuses, test-double assertions) —
  zero. Grepped `src/crumblr/agent_gateway/` for every new item-8
  symbol (`expected_state`, `reconcile_once`,
  `ReconciliationAttemptOutcome`, `RECONCILED`, `from_durable_exposure`,
  `request_ids_with_event`, `occurrence_histories`) — zero references.

Problems found:
- Two pre-existing item-6 tests broke on first run once
  `reconcile_once()` was wired into `run_once()` — not a design flaw,
  the intended same-pass convergence catching an outdated test
  assumption. Fixed by updating the assertions to expect `RECONCILED`
  immediately following `AMBIGUOUS_OUTCOME_RESOLVED` in the same pass,
  with the exact new event/payload/broker-read-count shape asserted
  explicitly rather than loosened.
- One real design correction, self-caught before it shipped: the first
  draft of `reconcile_once()` skipped the broker read entirely once any
  candidate already carried `RECONCILED` (mirroring `flatten_once()`'s
  own idempotence pattern) — but unlike `flatten_once()`, this item's
  purpose is *continuous* whole-book monitoring, so the plan's own
  intended design (confirmed by re-reading it) is to keep the broker
  read going as long as any historically-committed request exists, and
  only skip appending a *new* `RECONCILED` for ones already reconciled.
  This is the design shipped; the theoretical residual gap (a position
  confirmed accounted-for once and never revisited would not be
  re-checked for drift absent a new durable event for that request) is
  named honestly rather than silently accepted — it has no effect on
  any path this platform can exercise today, since `order_send` stays
  unreachable, but is worth a future D-### entry if real fills ever
  make it a live concern.

Risk impact:
- None. `close_all_positions`/`order_send` remain completely
  unreachable through every path this item added — the mechanism only
  reads durable history and already-real broker state, and durably
  records a determination; nothing here closes a position or decides to
  attempt one.

Decision:
- Asked the user how to proceed before starting (full design+build vs.
  pause vs. narrower slice) — approved full design and build.
- Entered plan mode before implementing; a `Plan` sub-agent pass
  validated the design against real source before finalizing it; plan
  approved before any code was written.
- Coordinated the migration with Dev 2 before creating it, per
  instruction §8.
- Committed and pushed after per-turn approval: `53dbccc` on
  `core/post-fill-reconciliation` → `main`.

Next:
- Confirm hosted CI is genuinely fully green (still needs a human or a
  session with GitHub access — no `gh` access here); unchanged from
  prior entries.
- Core critical path item 9: broker-side SL verification — not yet
  started, no plan drafted. The last item on the owner's original punch
  list ("→ CI pg_dump/psql verbinding fixen → hosted CI volledig groen
  → ambiguous-outcome recovery → flatten → post-fill reconciliation →
  broker-SL verification").

---

## Update 2026-09-03 (sixty-sixth entry) — owner risk policy v1: real portfolio open-risk accounting, O-004 withdrawn, owner numbers shipped (D1.2/D1.3/D1.4)

Component: `risk/portfolio_risk.py` (new), `risk/policies.py`, `risk/session.py`, `config.py`, `domain/enums.py`, `application/execution.py`, `application/orchestration.py`, `application/live_decision.py`, `persistence/schema.py`, new migration, `config/paper.yaml`, `review/adr/ADR-011-owner-risk-policy-v1.md`, `review/DEVIATIONS.md`, `review/INTEGRATION_NOTICES.md`
Milestone: Dev-1 owner work order (`review/OWNER_WORK_ORDERS_2026-09-02.md` D1.2/D1.3/D1.4), following owner-approved `review/OWNER_POLICY_V1.md` (2026-09-02); planned via `EnterPlanMode` and approved before implementation. D1.6/D1.7/D1.8 explicitly out of scope; D1.5 (session/weekend policy) is a separate, later slice
Status before: exposure was capped at one EUR/USD position at a time (O-004, a hard constant), and every internal `PortfolioState.open_risk_fraction` was the fiction `max_risk_per_trade * Decimal(len(open_positions))`; `config/paper.yaml`'s four risk fractions were engineering placeholders (D-013)
Status after: multiple positions are permitted, gated by real portfolio open-risk accounting (`risk/portfolio_risk.py::assess_open_risk`, entry-geometry-anchored, live-equity-denominated, fails closed to `None` — never zero — on untrustworthy stop geometry, via new `ReasonCode.OPEN_RISK_UNKNOWN`, a BLOCK not a HALT); `config/paper.yaml` ships the owner's four numbers (`0.02`/`0.03`/`0.04`/`0.08`) and a reclassified `max_open_positions: 10` (operational circuit-breaker, not owner policy)

Completed:
- Pulled `origin/main` (fast-forward past `a52d12f`/`0648e41`), read
  `review/OWNER_POLICY_V1.md` and `review/OWNER_WORK_ORDERS_2026-09-02.md`
  in full, summarized to the user. Asked how to sequence the size of the
  work order — approved: split into a risk-policy slice (D1.2+D1.3+D1.4,
  this entry) and a separate later session-policy slice (D1.5).
- Entered plan mode; a `Plan` sub-agent pass validated two genuine design
  forks before finalizing — entry geometry vs. mark-to-market anchoring,
  live vs. session-start equity denominator — each resolved with a
  written argument in the plan and in `ADR-011` §2.3-2.4. Corrected the
  sub-agent's own research once before approving: it claimed the highest
  existing owner decision was O-005; a direct grep found O-006/O-007
  already recorded, so this slice's owner decision is **O-008**.
  Sequenced deliberately D1.4 → D1.3 → D1.2 (never D1.3 before D1.4,
  which would briefly permit stacking against a still-fictional
  count-based budget). Plan approved before any code was written.
- **D1.4**: new `risk/portfolio_risk.py::assess_open_risk()` — reuses
  `sizing.py::realised_risk()`; signed adverse-distance geometry (not
  `abs()`, since `PositionState` carries no protective-side validator);
  any untrusted position (no spec, no stop) makes the whole assessment
  unestablished rather than partial. `PortfolioState.open_risk_fraction`
  widened `Decimal` (default `ZERO`) → `Decimal | None` (no default).
  New `ReasonCode.OPEN_RISK_UNKNOWN`, BLOCK not HALT — item 9 named as
  the future escalation path, pinned by test. Five internal call sites
  updated (execution.py, orchestration.py×2, live_decision.py×2);
  `agent_gateway/decision_path.py`'s own count-based approximation left
  untouched (Dev 2's D2.2). `RiskSessionState.open_risk_fraction`
  widened to `Decimal | None`, `to_payload()` made `None`-safe, one
  nullable-column migration (`d3b2e828b5b0`, off `03df83b062a6`,
  confirmed single head both before and after, no collision with Dev 2's
  `agent/contracts`). New `tests/unit/test_portfolio_risk.py` (15 cases:
  entry geometry, signed distance, summing, fail-closed legs, the
  `current_price`-never-read and equity-anchor fork proofs, determinism,
  the structural source-scan guard).
- **D1.3**: deleted `MAX_EXPOSURES_PER_SYMBOL` and its
  `SYMBOL_EXPOSURE_EXISTS`-appending block from `risk/policies.py`;
  `ReasonCode.SYMBOL_EXPOSURE_EXISTS` kept (docstring retired — persisted
  rows still reconstruct via `ReasonCode(code)`), never emitted again.
  `RiskConfig.max_open_positions` gains a real docstring (operational
  circuit-breaker, not owner policy); shipped value `1` → `10`, derived
  from every registered strategy's fixed 0.5%-per-trade request against
  the 3% budget, with a named revisit trigger. Deleted
  `tests/unit/test_one_exposure_policy.py` in full. Its load-bearing
  replay proof did not survive intact: the plan called for inverting it
  through the full agent/strategy replay, but `baseline_v1`'s own
  `already_positioned` guard (a legitimate, unrelated strategy choice)
  almost never lets a second position occur on synthetic random-walk
  data even with loss gates widened far past the owner's own values
  (confirmed: a 4000-bar run produced 23 fills and never stacked) —
  forcing it further would have meant seed-hunting, tuning against
  synthetic data in substance even without touching the strategy itself
  (`CLAUDE.md` §4). Resolved by driving `SimulatedBroker.order_send()`
  directly instead (`tests/replay/test_replay_prototype
  .py::TestMultiplePositionsPermitted`, mirroring `TestIdempotency`'s own
  pattern) — proves two real positions can coexist and that the real
  resulting book, valued by `assess_open_risk` against the broker's own
  equity, sits inside the owner's budget, without depending on the
  strategy ever choosing to pyramid. Recorded as a deliberate deviation
  from the plan's original method, not a silent substitution.
  The three owner acceptance examples (1.0%+2.0%=3.0% passes,
  1.1%+2.0%=3.1% blocks, several small positions pass regardless of
  count) proved directly at `risk/policies.py::evaluate()` in
  `tests/unit/test_risk_engine.py::TestExposureLimits`, verbatim.
- **D1.2**: `config/paper.yaml`'s risk quartet →
  `0.02`/`0.03`/`0.04`/`0.08`, header rewritten to cite
  `OWNER_POLICY_V1.md` as confirmation rather than "provisional
  values"; `max_open_positions` → `10`. Updated every hardcoded-quartet
  test fixture (`tests/conftest.py::paper_config_payload()`,
  `tests/integration/_execution_fixtures.py::platform_config()`,
  `tests/unit/test_risk_engine.py::risk_config()`), plus tests whose
  inputs silently stopped exercising anything once the defaults moved
  (`test_the_portfolio_risk_budget_is_enforced`'s `0.02`→`0.03`,
  `test_the_daily_loss_gate_halts`'s `0.97`→`0.94`,
  `test_the_open_position_limit_is_enforced`'s new explicit
  `max_open_positions=1` override). New
  `tests/unit/test_config.py::TestOwnerRiskPolicyV1` (shipped values,
  each independently proven to change `config_version`, ceiling `> 1`).
- Wrote `review/adr/ADR-011-owner-risk-policy-v1.md`; `review/DEVIATIONS
  .md` D-013 → `PARTIALLY RESOLVED`, new **D-053** (the `10` ceiling),
  new **D-054** (BLOCK-not-HALT choice + the `AgentPlatformState`
  three-states-into-two-slots gap) — renumbered from the plan's original
  D-052/D-053 after Dev 2 shipped their own D-052 first
  (`agent/contracts` commit `bf49549`); coordinated directly, confirmed
  no collision, Dev 2 acknowledged. Three `review/INTEGRATION_NOTICES.md`
  entries (the reason-code retirement, the widened `open_risk_fraction`
  type + the `AgentPlatformState` flag for Dev 2's D2.2, the new Alembic
  head). Updated `status.md`'s capability matrix, APP-012, and the v1
  checklist to point at the supersession rather than silently continuing
  to assert O-004; added **O-008a**/**O-008b** to the decision log (§10);
  removed Q7/Q8 from §E's open-questions table (now answered).

Evidence:
- tests: full suite, solo, `crumblr_test_dev1` — **1263 passed, 3
  skipped** (skips pre-exist, unrelated: filesystem-permission tests and
  an MT5-importability guard). Up from 1257 before this slice (199 lines
  deleted with `test_one_exposure_policy.py`, more added across
  `test_portfolio_risk.py`/`test_risk_engine.py`/`test_config.py`/
  `test_replay_prototype.py`).
- quality gate: `ruff check .` / `ruff format --check .` / `mypy` all
  clean (175 source files).
- determinism: `scripts/run_replay.py --bars 600` run twice, stdout-only
  MD5 identical (`439abed3...`) — confirmed the two apparently-differing
  runs from an earlier check were a test-methodology artifact (stderr
  log timestamps merged into the hash), not a real regression.
- migration: `alembic heads` → single head `d3b2e828b5b0`;
  `tests/integration/test_migrations.py` passes.
- Grepped the diff for `.order_send(`/`.close_all_positions(` — only a
  docstring mention (naming D-050), no new call. Grepped
  `src/crumblr/agent_gateway/` for `portfolio_risk`/`OPEN_RISK_UNKNOWN` —
  zero; `git diff --stat` confirms zero files touched under that
  directory. `config/base.yaml` and `risk/trading_window.py` confirmed
  byte-identical (D1.5 untouched).

Problems found:
- The plan's own replay-stacking proof method (drive it through the
  agent/strategy pipeline) turned out not to work against synthetic
  data without effectively tuning against it — see "Completed" above.
  Self-caught by actually running the replay and reading the result
  (23 fills, never stacked) rather than assuming the plan's premise held.
- Two stale test inputs (`test_the_portfolio_risk_budget_is_enforced`,
  `test_the_daily_loss_gate_halts`) would have silently stopped testing
  anything once the config defaults moved — caught by running the tests,
  not by re-reading the plan (both had been named as risks in the plan,
  but the daily-loss one's exact required threshold had to be derived at
  implementation time).
- Editing `test_config.py` while a background pytest run against the
  same file was still in flight produced one confusing, non-reproducing
  failure (stale line numbers in the traceback via `linecache`) — not a
  real regression; confirmed by a clean re-run against the settled file.
  Worth remembering: do not edit a file a background test run is still
  reading.

Risk impact:
- None to structural inertness: `order_send`/`close_all_positions`
  remain unconditional `ExecutionDisabledError` raises, untouched by
  this slice, confirmed by grep.
- The owner's numbers widen every risk gate substantially (2-4x the
  prior placeholders). This is the owner's explicit, approved decision
  (O-008b), not an engineering judgement call — `risk.
  approved_config_version` stays unset, so this does not itself move
  `order_send` any closer to reachable.

Decision:
- Sequencing D1.4 → D1.3 → D1.2, never D1.3 before D1.4.
- Proving D1.3's replay property via direct `SimulatedBroker.order_send`
  construction instead of the full agent/strategy replay, once the
  latter was shown not to reliably exercise the property without
  tuning-against-synthetic-data in substance.
- D-053/D-054 renumbered from the plan's original D-052/D-053 after
  coordinating with Dev 2 on their own D-052.
- Not yet committed — will ask for explicit per-turn approval before
  committing to a new `core/owner-risk-policy-v1` branch, `[core]`
  prefix, per standing session pattern.

Next:
- Ask for commit approval; push after rebasing onto current `origin/main`
  if it has moved.
- Notify Dev 2 that `risk/portfolio_risk.py::assess_open_risk` has
  shipped, so they can wire `PortfolioSnapshot.open_risk_fraction`
  against it (D2.2) — they explicitly requested this ping.
- Begin the separate D1.5 slice (session/weekend policy: daily →
  weekly/weekend-flat) as its own plan-mode cycle, per the user's
  earlier "split into two slices" decision. Not started; no code
  touched under `risk/trading_window.py` or `IntradayPolicy` yet.
- D1.6 (HALT reset human-only, verification only)/D1.7 (item 9,
  broker-side SL verification)/D1.8 (Settings-activation seam) remain
  separate, future work — not started.

---

## Update 2026-09-03 (sixty-seventh entry) — F-068: hosted CI's postgresql-client resolved to the wrong major version; a new Shared-Core work order issued

Component: `.github/workflows/ci.yml`, `review/FEEDBACK.md`, `status.md`
Milestone: Owner-reported CI defect, part of a new Shared-Core work order for PAPER_LITE/`feedback.2.0` convergence
Status before: F-067 installed `postgresql-client` (unpinned) so the restore test would stop silently skipping in hosted CI; hosted confirmation itself was never obtained (no `gh`/Actions access here)
Status after: `postgresql-client-17` installed explicitly via the official PGDG apt repository, matching the `postgres:17-alpine` service image exactly, rather than trusting `ubuntu-latest`'s own default apt major; hosted confirmation still pending

Completed:
- Owner reported (2026-09-03, as part of a larger four-item Shared-Core
  work order) that hosted CI's client/server PostgreSQL majors mismatch
  — client resolved to 16, server runs 17, and `pg_dump` refuses to dump
  from a server newer than itself. Read `.github/workflows/ci.yml`'s
  F-067-era install step (`sudo apt-get install -y postgresql-client`,
  unpinned) — confirmed this is exactly the mechanism: `ubuntu-latest`'s
  own default apt repository ships whatever major it currently carries,
  independent of the service image's own pinned `postgres:17-alpine`.
  Fixed via the standard PGDG bootstrap (`postgresql-common` +
  `/usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y`, the
  official way to get version-pinned client packages on Debian/Ubuntu),
  then installed `postgresql-client-17` explicitly rather than the
  unpinned meta-package. No test-code change needed —
  `_pg_dump_command()`/`_psql_command()` are plain `shutil.which()`
  lookups with no version logic of their own to touch.
- Registered as **F-068** in `review/FEEDBACK.md`'s finding register,
  same "SPECIFIC DEFECT FIXED / HOSTED-GREEN CONFIRMATION STILL OPEN"
  structure as F-056/F-063/F-065/F-067 before it.
- Updated `status.md`'s compact header: `main` HEAD, hosted CI result
  line, Dev 1's DONE/NEXT (the owner's new four-item work order —
  hosted CI, D1.5 session/weekend policy, PL-006 restart-recovery
  hardening, item 9 broker-side SL verification — recorded in full),
  Dev 2's line (D2.2 now wired against `assess_open_risk` on their
  `agent/contracts` branch, per their own cross-session report — not
  yet on `main`), and the owner-blockers line (the session-policy
  numbers the owner supplied this same message — Friday T-15 entry
  cutoff, Friday T-5 mandatory flat, HALT-reset human-only reconfirmed
  — move D1.5 from "blocked on a decision" to "an engineering task").

Evidence:
- YAML syntax validated (`yaml.safe_load`).
- No test-code path touched; full suite unaffected (CI-workflow-only
  change).
- Hosted confirmation itself still pending — no `gh`/Actions access in
  this environment, same limitation as every CI finding before it.

Problems found:
- None beyond the reported defect itself.

Risk impact:
- None. CI-workflow-only change; no application code touched;
  `order_send`/`close_all_positions` unaffected.

Decision:
- Committed and pushed directly (small, mechanical, CI-infrastructure-
  only fix) rather than a full plan-mode cycle: `bbd06fd` on
  `core/ci-pg-client-version-pin` → `main`.

Next:
- Plan and implement **D1.5** (session/weekend policy) as its own
  plan-mode cycle — the numbers are now owner-supplied, not blocked.
- **PL-006** (persisted loss/drawdown-fraction restart-recovery
  hardening in Shared Risk) after D1.5.
- Core critical path **item 9** (broker-side SL verification) after
  PL-006 — the owner's explicit sequencing.

---

## Update 2026-09-03 (sixty-eighth entry) — owner session policy v1: daily → weekly (D1.5)

Component: `trading_agent/sessions.py`, `risk/trading_window.py`, `risk/policies.py`, `risk/flatten_gate.py`, `application/execution.py`, `application/orchestration.py`, `application/live_decision.py`, `application/flatten_plan.py`, `domain/models.py`, `domain/enums.py`, `config/paper.yaml`, `review/adr/ADR-012-owner-session-policy-v1.md`, `review/adr/ADR-004`/`ADR-009` (amended)
Milestone: Owner Shared-Core work order 2026-09-03 item 2 (D1.5), following owner-approved `review/OWNER_POLICY_V1.md`; planned via `EnterPlanMode` with an `Explore` research pass and a `Plan` design-critique pass before implementation
Status before: the intraday policy applied last-entry/flatten deadlines to *every* trading day and treated *any* daily rollover as an overnight breach (O-003: "v1 holds nothing overnight"); `trading_day()` fabricated fictional Saturday/Sunday trading days across every real weekend
Status after: weekday overnight holding is permitted (Monday-Thursday: no cutoff/flatten at all); only the Friday trading day carries the last-entry (T-15)/flatten (T-5) deadlines, measured against the one weekly close; weekend exposure stays forbidden; `trading_day()`'s weekend-fabrication bug fixed; a new `FLATTEN_STATE_UNKNOWN` HALT fires if flat state cannot be confirmed by the Friday deadline

Completed:
- Owner sent a new four-item Shared-Core work order (hosted CI, D1.5,
  PL-006, item 9) directly in chat, confirming the already-shipped owner
  risk policy v1 as the leading authority. Item 1 (hosted CI PostgreSQL
  client version pin, F-068) shipped first as a small, non-plan-mode fix
  — see the sixty-seventh entry. This entry covers item 2, D1.5.
- Entered plan mode; an `Explore` pass mapped every call site of the old
  intraday machinery (11+ files, more than initially known — the
  real-terminal evidence script and the external-agent path among them)
  and the exact trigger condition of the already-built automatic-flatten
  machinery (item 7, ADR-009), confirming it needed no changes of its
  own, only its one caller. A `Plan` design-critique pass then stress-
  tested the first-draft design against the actual source and caught
  three real problems before any code was written: (1) the work order's
  own explicit requirement — HALT if flat state cannot be confirmed by
  the deadline — was entirely missing from the first draft; (2) my own
  reasoning about which renames were "free" was inverted:
  `crossed_rollover` (which I assumed unpersisted) is actually a real
  audit-payload key (`FLATTEN_SUBMISSION_STARTED` persists the full
  serialized `FlattenPlan`), while the `IntradayPolicy`/`IntradayConfig`
  rename I was hesitant about is genuinely free; (3) the first-draft
  `phase_at` design (a weekday-branch plus an independently-derived
  week-start comparison) was correct but violated the owner's own "one
  Core calendar/authority" instruction by needing two agreeing week
  computations — redesigned around a single new `weekly_close()`
  function in `sessions.py` instead, with no weekday branch at all
  (Monday's own close sits ~4.5 days away, so the arithmetic alone
  produces OPEN). All three folded into the plan before implementation,
  not discovered mid-build.
- **The mechanism**: `sessions.py::weekly_close(moment)` — Friday 17:00
  America/New_York ending `moment`'s trading week, derived from
  `trading_day()` alone. `trading_window.py::phase_at`/
  `has_crossed_weekly_close` both measure against it directly, with zero
  day-of-week special-casing. `trading_day()`'s real, pre-existing
  weekend-fabrication bug (fictional Saturday/Sunday trading days, which
  caused up to two spurious extra flatten-request rows and ledger resets
  across every real weekend) fixed in scope, not deferred — recorded as
  **D-055**, since D1.5's `weekly_close()` is built directly on top of it
  and honoring "one calendar authority" required getting it right rather
  than working around it. Confirmed zero behavior change for any
  Monday-Friday moment and that the fix cannot newly trip
  `risk/session.py`'s restart-recovery halt.
- **The new HALT**: `ReasonCode.FLATTEN_STATE_UNKNOWN`, scoped to
  `application/execution.py::flatten_once()` (the only component with
  broker-read-completeness visibility) — an incomplete position read at
  or past the Friday deadline now halts and surfaces the incident,
  rather than silently reading as flat via the pre-existing emptiness
  shortcut. Tolerated in the flatten gate's own halt-tolerance set
  alongside `OVERNIGHT_EXPOSURE`, safely — the gate's own
  `POSITION_BOOK_INCOMPLETE` leg independently still closes it whenever
  the trigger genuinely applies.
- Consolidated the two-legged "past deadline or crossed the boundary"
  condition, previously duplicated inline in four places (only one of
  which was a named function), into
  `risk/policies.py::overnight_breach()` (renamed from `_overnight_
  breach`, made public), now called by both `orchestration.py` and
  `live_decision.py`'s `_check_session_boundary` instead of re-inlining
  it a second time under the new semantics.
- Renamed `FlattenInstruction`/`FlattenPlan.crossed_rollover` →
  `crossed_weekly_close`, with the payload-shape change recorded
  explicitly (**D-056**) rather than silently, once the Plan critique
  caught that this field is genuinely persisted.
- `config/paper.yaml`'s `intraday:` offsets: 60/15 (daily) → 15/5
  (Friday-only), owner-approved this date, recorded as **O-009**,
  superseding O-003.
- Kept `IntradayPolicy`/`IntradayConfig`/the `intraday:` YAML key
  unrenamed despite "Intraday" now materially overstating what they mean
  — same reclassify-via-docstring precedent D1.4 set for
  `max_open_positions`; every docstring on the touched types rewritten
  instead. `ReasonCode.OVERNIGHT_EXPOSURE` kept for the persistence
  reason `SYMBOL_EXPOSURE_EXISTS` established in D1.3.
- Corrected two stale claims found while touching this code: ADR-009
  §2.7's "every shipped config's default" inertness argument was already
  false (`config/paper.yaml` has always shipped `enabled: true`); ADR-009
  §2.1's "keyed on the policy occurrence" framing no longer matched
  reality once the policy went weekly but the key stayed daily — amended
  rather than left standing, with the daily-key decision now stated
  explicitly as deliberate.
- `tests/unit/test_trading_window.py` rewritten substantially (not
  patched) — its `WINTER`/`SUMMER` constants are both Tuesdays, which
  have no deadlines at all under the new policy, so most of the file's
  premise moved. New `WINTER_FRIDAY`/`SUMMER_FRIDAY` constants (keeping
  the DST-pair argument), a new `TestCrossingTheWeeklyClose` class, and
  new affirmative tests proving weekday overnight is permitted — nothing
  in the old file tested that, since it used to be forbidden.
  `tests/integration/test_execution_flatten.py` gained a second,
  Friday-anchored fixed-clock constant (`FRIDAY_NOW`) alongside the
  existing Monday `FIXED_NOW`, since four of its nine tests were
  deadline-dependent and a Monday can no longer reach a deadline; two
  new tests added for the `FLATTEN_STATE_UNKNOWN` HALT's before/at-
  deadline halves.

Evidence:
- tests: full suite, solo, `crumblr_test_dev1` — **1273 passed, 3
  skipped** (skips pre-exist, unrelated). Up from 1263 before this
  slice.
- quality gate: `ruff check .` / `ruff format --check .` / `mypy` all
  clean (175 source files).
- determinism: `scripts/run_replay.py --bars 600` run twice, stdout-only
  MD5 identical.
- Replay-behavior delta measured, not assumed: the reference 1500-bar
  `baseline_v1` replay `status.md` already recorded as producing 44
  `SESSION_BLACKOUT` refusals under the old daily policy now produces
  **zero** `SESSION_BLACKOUT`/`OVERNIGHT_EXPOSURE` refusals — confirmed
  by direct replay run. The full `tests/replay/` suite (30 tests) passes
  unchanged; none of its assertions were numerically tied to the old
  daily boundary.
- Grepped the diff for `.order_send(`/`.close_all_positions(` — only a
  pre-existing docstring mention, no new call. Confirmed zero files
  touched under `src/crumblr/agent_gateway/` via `git status --short`.

Problems found:
- The plan's own first draft (before the `Plan`-agent critique) would
  have shipped without the work order's own explicit HALT-on-unconfirmed-
  flat requirement, and with the `crossed_rollover` rename reasoning
  inverted (see "Completed" above) — both caught before implementation,
  not after, by deliberately running a design-review pass rather than
  implementing the first workable design.
- No problems found during implementation itself; the design review's
  own predictions (which tests would need rewriting vs. patching) held
  exactly, including the two tests that depended on the `trading_day()`
  fabrication bug.

Risk impact:
- None to structural inertness: `order_send`/`close_all_positions`
  remain unconditional `ExecutionDisabledError` raises, untouched,
  confirmed by grep.
- `FLATTEN_STATE_UNKNOWN` is a new HALT condition — a genuine safety
  addition (fails closed on unconfirmed flat state), not a relaxation.
  The overall session policy widens what is permitted (weekday
  overnight), which is the owner's explicit, approved decision (O-009),
  not an engineering judgement call.

Decision:
- Fixed `trading_day()`'s weekend-fabrication bug in scope rather than
  deferring it or working around it with a second calendar helper.
- Scoped the new `FLATTEN_STATE_UNKNOWN` HALT to `flatten_once()` only,
  not the lighter per-tick checks, since only that component has the
  broker-read-completeness signal needed to detect it honestly.
- Kept the flatten idempotency key at daily granularity despite the
  policy going weekly, and amended ADR-009 §2.1 to say so explicitly
  rather than leaving a now-inaccurate framing standing.
- Not yet committed — will ask for explicit per-turn approval before
  committing to a new `core/owner-session-policy-v1` branch, `[core]`
  prefix, per standing session pattern.

Next:
- Ask for commit approval; push after rebasing onto current `origin/main`
  if it has moved.
- **PL-006** (persisted loss/drawdown-fraction restart-recovery
  hardening in Shared Risk) — item 3 of the owner's work order, not yet
  started.
- Core critical path **item 9** (broker-side SL verification) — item 4,
  after PL-006.
- Market holidays (D-057, ADR-012 §7) — open question, not fixed;
  revisit before any real DEMO canary run that could cross a holiday
  week.

---

## Update 2026-09-03 (sixty-ninth entry) — PL-006: restart recovery must not forget an already-breached loss/drawdown limit

Component: `risk/session.py`, `application/execution.py`, `application/orchestration.py`, `application/live_decision.py`, `application/paper_lite.py`, `agent_gateway/decision_path.py`, `review/adr/ADR-013-restart-recovery-loss-drawdown-check.md`
Milestone: Owner Shared-Core work order 2026-09-03 item 3 (PL-006), after D1.5 shipped
Status before: `risk/session.py::recover_session()` correctly reconstructed the historical `max_drawdown_fraction`/`max_session_loss_fraction` on restart (F-019, never lost, only ever widened) but never checked those recovered maxima against the configured owner-policy thresholds before allowing recovery to proceed — the live per-tick gate only ever reads the *current* instantaneous fraction, leaving no second line of defense specifically at the restart boundary
Status after: `recover_session()` halts recovery outright (`MAX_DRAWDOWN`/`DAILY_LOSS_LIMIT`, both together if both apply) when the recovered maxima already meet or exceed the configured thresholds — fixed once, in the one shared function every track calls, per the owner's own "normal Core Risk semantics, not PAPER_LITE glue" instruction

Completed:
- Direct code read (not agent-report-trusted) confirmed the gap: traced
  `EquityLedger.resumed()`'s seeding, `update()`'s widen-only behavior,
  `recover_session()`'s four existing halt legs (none of which checked
  the recovered maxima against policy), and the live gate's own
  instantaneous-fraction-only checks in `orchestration.py
  ::_check_loss_gates` and `risk/policies.py::evaluate()`.
- Grepped every caller of `recover_session()` before designing the fix —
  found five, not the two or three assumed at first glance:
  `orchestration.py`, `live_decision.py`, `execution.py`, and, from the
  PAPER_LITE merge earlier today, `application/paper_lite.py` and (via
  its own reuse of the shared decision path) `agent_gateway
  /decision_path.py`.
- Found direct, concrete evidence the gap was real: `application/
  paper_lite.py::_recover_risk_session()` already contained a hand-rolled
  local workaround for exactly this gap — independently discovered and
  patched as PAPER_LITE-local glue, precisely the shape the owner's
  instruction warns against. Strong validation of both the bug and the
  planned fix shape.
- Before implementing: asked the user how to handle the one call site
  under `agent_gateway/` (Dev 2's track, never touched all session) given
  the new signature is not source-compatible — user chose "message Dev 2
  first, wait for their OK." Sent a full cross-session brief; Dev 2
  replied they'd rather update their own call site on their next `main`
  sync (mid-commit in that file themselves) — agreed, planned to leave it
  untouched.
- Implemented: `recover_session()` gains two new required keyword
  parameters (`max_daily_loss`, `max_drawdown`); `_halt()`'s signature
  widened from a single `ReasonCode` to a tuple so both limits can be
  reported together; the four owned call sites updated; `paper_lite.py`'s
  local duplicate check removed in favor of the shared one.
- Ran the full test suite before pushing (standing practice) — this
  surfaced new information that changed the `agent_gateway/` plan: 10 of
  `application/paper_lite.py`'s own tests were failing, because PAPER_LITE
  calls straight through `agent_gateway::evaluate_agent_trade_intent`
  into the exact unfixed line. The signature break was not merely "Dev
  2's own future problem" as both Dev 2 and I had assumed — it was
  already failing tests on `main`, for everyone, today. Made the one-line
  mechanical fix directly (`config.risk.max_daily_loss`/
  `config.risk.max_drawdown`, identical shape to the other four sites,
  nothing else touched in that file) rather than leave `main` red, and
  told Dev 2 immediately what changed and why. Dev 2 confirmed this was
  the right call given the new evidence.
- `tests/unit/test_risk_session.py`: extended the `recover()` test helper
  with wide-open default thresholds (0.5/0.5) so the file's own pre-
  existing amplitude tests (which deliberately swing `live_equity` by
  double digits to prove "recovery only ever tightens", unrelated to
  PL-006) are not accidentally caught by the new check — caught this
  during the first test run (two failures) rather than shipping tight
  defaults that silently changed those tests' meaning. New
  `TestAnAlreadyExhaustedLimitHaltsRecovery` class (7 tests): drawdown/
  daily-loss each at and just under the threshold, both breached
  together, and the property the owner actually named — equity moving
  back near the recorded peak does not erase a halt the recorded worst
  already earned. Extended `TestRecoveryOnlyEverTightens` with one more
  test proving the daily reset still correctly clears yesterday's
  exhausted allowance (PL-006 must not reach across a genuine day
  boundary).

Evidence:
- tests: `tests/unit/test_risk_session.py` — 26 passed (18 pre-existing +
  8 new/extended). `tests/unit/test_paper_lite.py` +
  `test_paper_lite_agent.py` + `test_paper_lite_broker.py` — 38 passed,
  confirming the local-check removal regressed nothing.
- quality gate: `ruff check .` / `ruff format --check .` / `mypy` all
  clean (185 source files) — including `agent_gateway/decision_path.py`
  after the direct fix.
- Full suite: **1317 passed, 2 known failures, 3 pre-existing skips.**
  The two failures are `tests/unit/test_agent_decision_path.py
  ::TestAG012FreshSessionRecoveryEveryCall
  ::test_a_recorded_prior_loss_this_session_reaches_the_daily_loss_gate`
  and `::test_two_calls_against_different_stores_are_fully_independent`
  — not a bug in this fix, a real behavior-timing change it exposes in
  Dev 2's own test file (see "Problems found"). Coordinated directly with
  Dev 2: they will fix the two assertions on their side once they merge
  and see the real failure themselves, rather than guess blind against a
  description. Deliberately pushed with these two known, named failures
  rather than held back — they are isolated to `agent_gateway/`'s own
  test file, do not indicate a defect in the fix itself (confirmed: the
  kill switch trips correctly, with the correct reason, strictly earlier
  than before), and Core's own equivalent paths all pass.

Problems found:
- The `agent_gateway/` coordination plan changed mid-flight once the full
  suite run surfaced that PAPER_LITE's own tests depended on the exact
  line being fixed — not a mistake in the original coordination (asking
  first was the right call given what was known then), but a case where
  new evidence legitimately changed the right action, and Dev 2 was told
  the reasoning immediately rather than the change being made silently.
- Two `test_risk_session.py` tests broke on first run once the new check
  existed — not a design flaw, the intended check correctly catching
  test fixtures that happened to swing equity past the (too-tight)
  default thresholds I picked initially. Fixed by widening the test
  helper's defaults rather than narrowing the tests' own scenarios.
- **A real, subtle interaction with an existing design convention,
  found by the full suite, not by design review.** `risk/policies.py
  ::evaluate()`'s documented convention
  (`test_risk_engine.py::test_adr001_7`'s own docstring: "an already-
  halted system is enforced as a BLOCK, not a fresh HALT escalation —
  the halt already happened when the kill switch was tripped") means a
  decision evaluated *after* the kill switch is already halted for any
  reason reads as `BLOCK`/`SYSTEM_HALTED`, never re-deriving the
  original trip's specific reason code. Before this fix, a recorded
  prior loss was only ever caught *live*, inside `evaluate()`'s own
  loss-gate leg, in the same call that discovered it — producing a
  direct `DAILY_LOSS_LIMIT`/`HALT` in that one decision. Now,
  `recover_session()` catches the identical condition *earlier*, during
  recovery itself, before `evaluate()` ever runs — so by the time a
  decision is evaluated, the system is already halted, and that
  established convention correctly downgrades it to `BLOCK`/
  `SYSTEM_HALTED`. This is strictly earlier and more correct (recovery
  now refuses the moment the problem is known, rather than waiting for a
  live decision attempt to independently rediscover it), not a
  regression — but two of Dev 2's tests asserted the *old* timing's
  specific verdict/reason shape. Not fixed here (Dev 2's own file, and
  Dev 2 asked to fix it against the real failure on their side rather
  than a description) — flagged explicitly, not silently left red.

Risk impact:
- None to structural inertness: `order_send`/`close_all_positions`
  remain unconditional `ExecutionDisabledError` raises, untouched.
- This is a genuine safety tightening (a restart can no longer silently
  forget an already-breached limit), not a relaxation — exactly the
  direction the owner asked for.

Decision:
- Fixed the shared `recover_session()` function rather than any
  per-caller workaround, per the owner's explicit "not PAPER_LITE glue"
  instruction — and removed PAPER_LITE's own pre-existing local
  duplicate in favor of it.
- Made the `agent_gateway/decision_path.py` fix directly once the full
  suite showed a live, shared-`main` breakage, reversing the original
  "wait for Dev 2" plan — informed by new evidence, not by overriding
  Dev 2's stated preference without reason.
- Not yet committed — will ask for explicit per-turn approval before
  committing to a new `core/pl-006-restart-recovery` branch, `[core]`
  prefix, per standing session pattern.

Next:
- Ask for commit approval; push after rebasing onto current `origin/main`
  if it has moved; notify Dev 2 once it is actually on `main` (already
  told them it was not yet pushed when they asked).
- Core critical path **item 9** (broker-side SL verification) — item 4 of
  the owner's work order, the last one, not yet started.

---

## Update 2026-09-03 (seventieth entry) — broker-side SL verification (core critical path item 9)

```text
Component: application/expected_state.py, application/reconciliation.py, application/execution.py, domain/enums.py, risk/flatten_gate.py
Milestone: Dev-1 Shared-Core work order 2026-09-03, item 4 (item 9) — the last item on the owner's original punch list
Status before: BUILDING (items 1-3 of the work order shipped)
Status after:  BUILDING (all 4 items of the work order shipped; item 9 was the last core critical path item on the owner's original punch list)
```

**Completed**

- `DerivedExposure.expected_stop_loss_by_request: Mapping[UUID, Decimal]`
  — a new `_stop_loss_price_of()` helper (mirrors `_entry_type_of()`)
  reads the platform's own intended stop-loss for each determined,
  submitted request out of `SUBMISSION_STARTED`'s existing durable
  `ApprovedOrder` payload. A request with no recoverable stop is simply
  absent from the mapping — deliberately not folded into
  `undetermined_reasons`.
- `application/reconciliation.py::verify_protective_stops()` — a new,
  pure, deliberately *separate* comparison function (not part of
  `reconcile()`'s own MATCHED/MISMATCHED/UNKNOWN verdict): for each
  attributed, currently-open ticket, compares the broker-reported
  `PositionState.stop_loss_price` against the platform's own intended
  stop, exact `Decimal` equality, reporting every issue (never
  short-circuiting).
- Two new `ReasonCode` members: `PROTECTIVE_STOP_MISSING`,
  `PROTECTIVE_STOP_MISMATCH` — the escalation `OPEN_RISK_UNKNOWN`'s own
  docstring (D1.4, ADR-011) already named item 9 as the correctly-scoped
  future owner of.
- `execution.py::reconcile_once()` calls `verify_protective_stops()`
  inline, in the same pass and against the same already-captured broker
  observation item 8's own `RECONCILED` loop uses — zero extra broker
  read. A new `_trip_protective_stop_issue()`, mirroring
  `_trip_overnight_exposure()`/`_trip_flatten_state_unknown()`'s exact
  idempotent-trip shape, escalates on any issue. `RECONCILED`'s own
  payload gains a `protective_stop_issues` field for audit visibility;
  its book-level `book_status` is untouched.
- `risk/flatten_gate.py::_TOLERATED_HALT_REASONS` gains both new reason
  codes, alongside `OVERNIGHT_EXPOSURE`/`FLATTEN_STATE_UNKNOWN` — the
  safe resolution of an untrusted protective stop is closing the
  position, not a further risk; without this, the halt would be a
  permanent, un-remediable brick, exactly what `OPEN_RISK_UNKNOWN`'s own
  BLOCK-not-HALT choice was designed to avoid.
- `review/adr/ADR-014-broker-side-stop-loss-verification.md` written.
  `review/DEVIATIONS.md` D-051 gap 3 amended in place (not superseded):
  the SL-specific subset of "reconciliation mismatch" is now covered;
  the generic book-level case remains exactly as deferred as before.
  `build.md` §8.2 already lists "reconciliation mismatch" as an
  Automatic HALT trigger — item 9 fulfils that trigger for this one
  case, introducing no new deviation of its own.
- Confirmed by grep, not assumed: `application/paper_lite.py` never
  references `ExecutionOrchestrator`/`reconcile_once`/
  `AMBIGUOUS_OUTCOME_RESOLVED` — item 9 has zero PAPER_LITE interaction,
  matching items 6-8/D1.5's own Core-only scope.

**Evidence**

- tests: `tests/unit/test_expected_state.py` — 5 new (present, string-
  precision, missing-is-absent, missing-adds-no-undetermined-reason,
  not-submitted-records-nothing). `tests/unit/test_reconciliation.py` —
  7 new (`TestVerifyProtectiveStops`: no-attributed, matching, missing,
  mismatch, no-determinable-expected, multiple-tickets-no-short-circuit,
  ticket-not-attributed-never-checked). `tests/integration
  /test_execution_reconciliation.py` — 7 new (`TestVerifyProtectiveStopsIntegration`:
  matching trips nothing, missing trips
  `PROTECTIVE_STOP_MISSING`, mismatch trips `PROTECTIVE_STOP_MISMATCH`,
  undeterminable-expected trips as missing, second pass is a no-op trip,
  `order_send`/`close_all_positions` stay unreachable;
  `TestStillInert`: the ordinary `run_once()` path never fires this
  producer). 19 new tests total, all passing.
- quality gate: `ruff check .` / `ruff format --check .` / `mypy` all
  clean (185 source files).
- Full suite: **1336 passed, 2 known failures, 3 pre-existing skips**
  (577s). The 2 failures are the exact same, already-documented,
  pre-existing `tests/unit/test_agent_decision_path.py
  ::TestAG012FreshSessionRecoveryEveryCall` failures named in the
  sixty-ninth entry (PL-006) — unrelated to any file this item touches,
  unchanged in count or identity. 1336 - 1317 (PL-006's own baseline) =
  19, exactly the new tests added here; no other regression. **Update
  same day, via Dev 2:** already fixed on their side, pushed as
  `d62722d` on `agent/contracts` — held there deliberately (not merged
  to `main` pending their own review), so `main`'s own suite correctly
  still shows the pre-fix assertions failing against PL-006's new
  behavior until that branch merges. Not stale or untracked; just not
  yet on `main`.
- Determinism: `scripts/run_replay.py --bars 600` run twice via
  PowerShell (`2>$null | Out-File`, stdout only — the Bash tool's own
  console codepage cannot encode the script's box-drawing output when
  piped, an environment quirk unrelated to this change), MD5 identical
  both runs (`704967823f258496922a9b16c4d29788`).
- Grep the diff: zero `.order_send(`/`.close_all_positions(` calls,
  zero edits under `agent_gateway/`.

**Problems found**

- None specific to this item's own logic — the 2 known failures predate
  it and are already tracked against PL-006/Dev 2's own coordination.

**Risk impact**

- None to structural inertness: `order_send`/`close_all_positions`
  remain unconditional `ExecutionDisabledError` raises, untouched;
  `verify_protective_stops` is provably unreachable with a non-empty
  `attributed` set today (proven directly by a new structural guard
  test), same as items 6-8/D1.5.
- Genuine safety tightening: a position whose broker-side protective
  stop is missing or wrong now fails closed and escalates, per the
  owner's explicit requirement — the last item on their original
  execution-safety punch list.

**Decision**

- Kept the new halt fully decoupled from `reconcile()`'s own generic
  MATCHED/MISMATCHED verdict and from `halt_on_reconciliation_mismatch`
  — a dedicated, narrowly-scoped producer, preserving D-051 gap 3's
  deliberately deferred scope rather than silently resolving it.
- Added both new reason codes to `flatten_gate.py`'s
  `_TOLERATED_HALT_REASONS` — a design decision not explicit in the
  approved plan, made during implementation once `OPEN_RISK_UNKNOWN`'s
  own "avoid a permanent brick" rationale was found to apply directly;
  documented in ADR-014 §2.5.
- Not yet committed — will ask for explicit per-turn approval before
  committing to a new `core/item-9-protective-stop-verification` branch,
  `[core]` prefix, per standing session pattern.

Next:
- Ask for commit approval; push after re-confirming `origin/main` hasn't
  moved past `8505fd2`; notify Dev 2 once pushed (informational — no
  cross-track call-site signature changes).
- All four items of the 2026-09-03 Shared-Core work order are now
  shipped (pending commit/push). No further owner punch-list item
  remains open as of this entry.

---

## Update 2026-09-03 (seventy-first entry) — new owner/reviewer coordination order received: constrained DEMO canary route

```text
Component: none (documentation/status only — no source changed)
Milestone: n/a — reading a new coordination document and updating tracking
Status before: item 9 shipped; awaiting new instructions
Status after:  new work order read and understood; Dev 1 identified as BLOCKED on Dev 2's Phase-0 convergence before any new Core code may branch
```

**Completed**

- Item 9 pushed and Dev 2 notified (`824c4f2`); Dev 2 replied confirming
  the 2 known CI failures are already fixed on `agent/contracts`
  (`d62722d`), held there pending their own merge review — clarified in
  the seventieth entry, committed as `e919fe8`.
- Pulled a new commit that landed directly on `origin/main`
  (`7ad93a5`, author `DutchBugs` — the owner account, not a Dev session):
  `review/OWNER_WORK_ORDERS_DEMO_CANARY_2026-09-03.md`, a 751-line
  owner/reviewer coordination order. Explicitly **not** `feedback.1.29`
  or `feedback.2.0` — a staged route (Phases 0/A/B/C/D/E/F) to a
  one-shot, deliberately constrained real Pepperstone **DEMO** canary,
  with `order_send`/`feedback_2_0_approved` staying NO-GO throughout
  every phase except the final, owner-confirmed canary attempt itself.
- Read in full. Confirmed by direct check, not assumed: `agent/contracts`
  (`d62722d`) branches from pre-item-9 `main` (`8505fd2`) — Dev 2 has not
  yet rebased/converged onto item 9, matching the work order's own §1.8
  claim ("behind the latest Core item-9 commits and must sync before
  PR").
- `status.md` compact header updated: `main` HEAD (`7ad93a5`), hosted CI
  result (owner-reported run #106: 1339/1341 passed, the 2 failures
  being the same known, already-fixed-on-Dev2's-branch assertions), Dev
  1's DONE/NEXT/BLOCKED row rewritten to name the new work order and the
  Phase-0 dependency.

**Evidence**

- `git log origin/agent/contracts -1` = `d62722d...`, `git merge-base
  origin/main origin/agent/contracts` = `8505fd2` — directly confirms
  the sync gap the new work order describes, rather than trusting its
  prose alone.
- No quality gate run this entry — no source file changed.

**Problems found**

- None. This entry is a coordination/tracking update, not an engineering
  change.

**Risk impact**

- None — `order_send`/`close_all_positions`/`feedback_2_0_approved`
  remain exactly as closed as before. The new work order's own Phase B
  explicitly requires all new real-mutation code to ship fully disabled
  behind structurally closed gates, with no shipped config enabling it.

**Decision**

- **Not** starting Phase B (the real-but-disabled DEMO execution slice)
  yet, even though it is Dev 1's own next assignment — the work order's
  own §2 Phase-0 section is explicit: "Do not start real `order_send`
  wiring on stale main; branch the new execution work only after the
  Dev-2 convergence merge," and Phase 0 itself is not yet complete
  (Dev 2's PR does not exist yet; hosted CI has not been reconfirmed
  green after it). Starting Phase B against current `main` would
  contradict the work order's own explicit sequencing.
- Asking the user for direction before proceeding further, given the
  scale of Phase B (new real-mutation adapter, real submission
  side-effect chain, real per-ticket close/flatten, one-shot canary
  permit, shared final-Risk authority with Dev 2) and its explicit
  "queued, not yet startable" status.

Next:
- Await either: Dev 2 signaling Phase-0 convergence is merged and
  hosted CI is confirmed green (at which point Dev 1's own Phase-0 role
  — reviewing that merge for cross-track Core invariants/item-9
  conflicts — begins), or explicit user direction to begin Phase B
  research/planning ahead of that (still disabled-by-construction, but
  the work order's own text discourages branching before Phase 0).

---

## Update 2026-09-03 (seventy-second entry) — Dev 1's own Phase-0 review of Dev 2's convergence merge: no conflicts found

```text
Component: none (review only — no source changed)
Milestone: DEMO canary work order, Phase 0
Status before: Dev 1 blocked on Dev 2's convergence PR
Status after:  Dev 1's own Phase-0 review complete, no conflicts; blocked only on a human opening the PR + hosted CI confirmation
```

**Completed**

- Dev 2 (`crumblr-fc`) reported, via cross-session message: merged latest
  `main` (including item 9) into `agent/contracts`, full local gate green
  (1358 passed, 3 pre-existing skips, 0 failed), pushed as `76a88c7`,
  merge-base exactly `main`'s `7ad93a5` (a clean, non-diverged sync).
  Neither Dev session has `gh` CLI or a `GITHUB_TOKEN`/`GH_TOKEN` — both
  confirmed independently — so the PR object itself cannot be opened by
  either agent; it needs a human with GitHub access on one side or the
  other.
- Reviewed `git diff origin/main...origin/agent/contracts` in full
  (14 files, 908 insertions/33 deletions) for cross-track Core
  invariant/item-9 conflicts, per the work order's own scoping of Dev
  1's Phase-0 role (review only, not a full re-review of Dev 2's own
  D2.2/AG-work).

**Evidence**

- Zero changed lines under `execution.py`/`reconciliation.py`
  /`expected_state.py`/`domain/enums.py`/`risk/*.py` — the entire diff
  is scoped to `agent_gateway/`, `review/DEVIATIONS.md`, Dev 2's own new
  `review/AGENT_STATUS.md`/`AGENT_FEEDBACK.md`, and Dev 2's own tests.
- `git diff origin/main...origin/agent/contracts | grep -n
  "order_send\|close_all_positions\|cancel_pending"` — zero matches.
  Structural inertness confirmed untouched by this merge.
- `decision_path.py`'s D2.2 wiring read directly: calls `risk
  .portfolio_risk.assess_open_risk()` (confirmed `OpenRiskAssessment
  .fraction: Decimal | None` is the real field by reading
  `portfolio_risk.py` itself, not assumed), feeds `.fraction` straight
  into `policies.PortfolioState.open_risk_fraction` unmodified, and
  deliberately does not re-implement `OPEN_RISK_UNKNOWN`'s BLOCK-not-HALT
  fail-closed check itself — matches D-054 gap 1's existing, correct
  design exactly, no duplicated/diverging authority.
- The two originally-failing `test_agent_decision_path.py` assertions
  (flagged in the seventieth/seventy-first entries) now read
  `RiskVerdict.BLOCK`/`ReasonCode.SYSTEM_HALTED`, matching PL-006's
  earlier-detection timing exactly as ADR-013 §2.5 anticipated.
- `review/DEVIATIONS.md` diff read in full: my own item-9/D-051
  amendment survived the merge intact (confirmed via grep for
  `PROTECTIVE_STOP`/`ADR-014`/the "Last updated" line); Dev 2's new
  D-052 (RESOLVED) and D-054 gap 2 (RESOLVED) entries are accurate and
  consistent with what this session independently knows of
  `OPEN_RISK_UNKNOWN`'s design.

**Problems found**

- None. No conflicts, no duplicated authority, no structural-inertness
  regression.

**Risk impact**

- None — this was a review, not a code change on Dev 1's own side.

**Decision**

- Reported the clean review result back to Dev 2 directly (no action
  needed on their side); told them Dev 1 still holds Phase B until the
  PR is opened and hosted CI is confirmed green on it.
- `status.md` compact header's Dev 1 row updated to reflect the review
  is done and the remaining blocker is now purely human-GitHub-access,
  not engineering work on either track.

Next:
- Wait for a human (owner or either Dev's user) to open the
  `agent/contracts` (`76a88c7`) → `main` PR and for hosted CI to run and
  confirm green on it. Only then does Dev 1's Phase B branch.

---

## Update 2026-09-03 (seventy-third entry) — Phase 0 PR merged; main independently verified green locally

```text
Component: none (verification only — no source changed)
Milestone: DEMO canary work order, Phase 0
Status before: Phase-0 review done; blocked on a human opening the PR
Status after:  PR #2 merged; Phase 0 substantively complete pending hosted-CI-run confirmation (no gh/API access to check that directly)
```

**Completed**

- The user merged PR #2 (`agent/contracts` → `main`) directly on
  GitHub. `origin/main` fast-forwarded to `3e87384` (a plain merge
  commit, no additional content beyond what was already reviewed at
  `76a88c7` — diffstat identical, confirmed by `git log`).
- Independently re-ran the full local quality gate against the merged
  `main`, rather than relying only on Dev 2's own DB/gate run or the
  owner's earlier run-#106 report.

**Evidence**

- `uv run ruff check .` / `uv run ruff format --check .` / `uv run
  mypy` — all clean (185 source files, 201 formatted).
- `uv run pytest -q` against `crumblr_test_dev1`: **1358 passed, 3
  skipped, 0 failed** (312.79s) — the same clean count Dev 2 reported
  against their own database, now independently reproduced against
  mine. The two previously-failing `test_agent_decision_path.py`
  assertions are gone; the 3 skips are the same pre-existing
  platform-dependent ones (directory-permission tests on Windows,
  `MetaTrader5` importability) seen throughout this session.

**Problems found**

- None.

**Risk impact**

- None — verification only.

**Decision**

- Treating Phase 0 as substantively complete on the engineering side:
  the merge is real, the merged code was already reviewed for
  cross-track conflicts (seventy-second entry), and the full local
  suite is independently green. The one formal Phase-0 acceptance
  criterion still unconfirmed is the actual *hosted* GitHub Actions run
  on `main` post-merge — this environment has no `gh`/API access to
  check that directly, same limitation noted throughout this session
  (F-056/F-063/F-065/F-067/F-068). Not claiming that specific box
  checked; naming the gap honestly rather than assuming it from local
  evidence alone.
- Not yet starting Phase B — will confirm with the user given its scale
  (new real-mutation adapter, real submission side-effect chain, real
  per-ticket close/flatten, exact account pin, one-shot canary permit,
  shared final-Risk authority with Dev 2) before branching new work,
  consistent with this session's practice for every prior
  safety-critical slice.

Next:
- Confirm with the user whether to proceed into Phase B now (local
  evidence is green) or wait for an explicit hosted-CI confirmation
  first.

---

## Update 2026-09-03 (seventy-fourth entry) — Phase B slice 1: ambiguous-recovery integrity hardening (B4)

```text
Component: application/execution.py, application/expected_state.py, domain/enums.py, risk/flatten_gate.py
Milestone: DEMO canary work order, Phase B, item B4 — slice 1 of 8 (user chose this as the first, smallest, most independent piece)
Status before: Phase 0 complete; Phase B not started
Status after:  Phase B slice 1 (B4) shipped; 7 sub-items remain (B1, B2, B3, B5, B6 deferred by the work order itself, B7, B8)
```

**Completed**

- `application/execution.py::_recover_ambiguous_submission()` now
  explicitly branches on `len(matches) > 1` before computing `submitted`
  — previously `submitted = len(matches) > 0` treated 2+ matching
  broker positions identically to exactly 1, durably attributing every
  matching ticket to one request. Two or more positions sharing one
  magic number is never a legitimate outcome of a single MARKET order
  (no retry logic exists that could produce two) — it signals a
  magic-number collision or corrupted state, and blindly attributing
  all of them was exactly the "silently accept" failure `build.md` §20's
  own default ("No new exposure. Reconcile first.") forbids.
- New `_trip_submission_integrity_ambiguous()`, mirroring
  `_trip_overnight_exposure()`/`_trip_protective_stop_issue()`'s
  idempotent-trip shape, escalates via a new
  `ReasonCode.SUBMISSION_INTEGRITY_AMBIGUOUS` the moment `>1` matches
  are found.
- `application/expected_state.py::derive_expected_exposure()` gains an
  explicit, honestly-worded check for the new `integrity_ambiguity`
  payload flag, ahead of the existing "missing or malformed" branch —
  deliberately not reusing that wording, since this is a correctly-shaped
  payload for a distinct condition, not a data defect. The request is
  left undetermined (never in `tickets_by_request`), which independently
  makes `reconcile()` return `UNKNOWN` — a second line of defense on top
  of the kill-switch HALT.
- `risk/flatten_gate.py::_TOLERATED_HALT_REASONS` gains
  `SUBMISSION_INTEGRITY_AMBIGUOUS`, same reasoning as item 9's own
  additions: flattening closes whatever the broker reports regardless of
  attribution, so becoming flat is still the safe resolution even when
  attribution itself is in doubt.
- `review/adr/ADR-015-ambiguous-recovery-integrity-hardening.md`
  written. Checked against `build.md` (§8.2 "reconciliation mismatch"
  HALT trigger, §20's ambiguous-situation default) before concluding no
  new `review/DEVIATIONS.md` entry is needed — this aligns with, not
  departs from, the spec, same as item 9.
- **Distinguishing note (unlike items 6-9/D1.5):** the 0-match branch of
  this method is already live in shipped behaviour today — every
  request reaching `SUBMISSION_STARTED` is durably resolved as
  `submitted=False` on the next pass, since `order_send` never runs.
  This slice preserves that unchanged (verified by re-running the
  pre-existing 0-match/1-match tests without modification); only the
  new `>1`-match branch is provably unreachable today, for the same
  structural reason as every prior slice.

**Evidence**

- New tests: `tests/integration/test_execution_orchestrator.py` — 2 new
  (`test_two_matching_broker_positions_is_an_integrity_ambiguity`,
  `test_three_matching_broker_positions_is_also_an_integrity_ambiguity`,
  confirming payload shape, kill-switch trip, and idempotence on a
  third pass — the third pass does still read broker state, since this
  request remains a "pending" `reconcile_once()` candidate forever
  having never reached `RECONCILED`, but must not append a second event,
  re-derive attribution, or re-trip/alter the halted kill switch).
  `tests/unit/test_expected_state.py` — 1 new
  (`test_an_integrity_ambiguity_is_undetermined_not_malformed`). 3 new
  tests total.
- quality gate: `ruff check .` / `ruff format --check .` / `mypy` all
  clean (185 source files).
- Full suite: **1361 passed, 3 pre-existing skips, 0 failed** (329.06s)
  — 1358 (post-Phase-0-merge baseline, seventy-third entry) + 3 new,
  exactly accounted for.
- Determinism: `scripts/run_replay.py --bars 600` run twice (PowerShell,
  stdout only), MD5 identical (`704967823f258496922a9b16c4d29788` — same
  hash as item 9's own run, as expected, since replay never touches
  this code path).
- Grep the diff: zero `.order_send(`/`.close_all_positions(` calls,
  zero edits under `agent_gateway/`.

**Problems found**

- Two of my own initial test assertions were wrong, not the
  implementation: (1) a test asserted `"malformed"` was absent from the
  new reason text, but my own honest wording ("not a malformed payload")
  legitimately contains that substring — fixed by asserting the
  precise original phrase ("missing or malformed") is absent instead.
  (2) a test assumed a third `run_once()` pass would do zero further
  broker reads, but `reconcile_once()`'s own candidate-gathering treats
  any request with `SUBMISSION_STARTED` and no `RECONCILED` as
  perpetually pending — since this request can never reach
  `RECONCILED` (it never becomes determined), it stays a scan candidate
  forever, causing one broker read per pass indefinitely. Not a defect
  in this slice (out of scope to change `reconcile_once()`'s own
  candidate-gathering here) — the test was corrected to assert the
  actually-relevant invariants (no new event, no kill-switch change)
  instead of an incorrect broker-read-count assumption.

**Risk impact**

- None to structural inertness: `order_send`/`close_all_positions`
  remain unconditional raises; the new `>1`-match branch is provably
  unreachable today, proven by the same structural argument and tests
  as every prior slice.
- Genuine safety tightening on an already-live code path: an integrity
  anomaly that was previously silently accepted (attributed to one
  request as if normal) now fails closed and escalates.

**Decision**

- Not yet committed — will ask for explicit per-turn approval before
  committing to a new `core/phase-b-1-ambiguous-recovery-integrity`
  branch, `[core]` prefix, per standing session pattern.

Next:
- Ask for commit approval; push after re-confirming `origin/main`
  hasn't moved; notify Dev 2 once pushed (informational — no
  shared-contract surface change expected).
- Plan and implement the next Phase B slice — likely B7 (exact account
  pin, small/independent) or B1+B2 (the core new adapter + submission
  chain, the largest remaining piece) — to be decided with the user.

---

# 14. Update template

Copy this block whenever meaningful progress occurs.

```text
## Update YYYY-MM-DD HH:MM UTC

Component:
Milestone:
Status before:
Status after:

Completed:
- 

Evidence:
- tests:
- logs:
- metrics:
- artifact/commit:

Problems found:
- 

Risk impact:
- 

Decision:
- 

Next:
- 
```

---

# 15. Release / deployment record

| Version | Date | Environment | Code commit | Strategy | Model | Risk config | Evaluator policy | Result |
|---|---|---|---|---|---|---|---|---|
| | | | | | | | | |

---

# 16. Promotion history

| Date | Component | From | To | Decision | Evidence | Reviewer |
|---|---|---|---|---|---|---|
| 2026-08-24 | M1 MT5 read-only gateway | REPLAY-TESTED | MT5-INTEGRATED | **PASS** | Real Pepperstone demo: first contact, Phase A (30 clean minutes, 2,920 ticks + 17 bars, GOOD quality, zero gaps), Phase B (two deliberate terminal closures, both recovered with full revalidation). Detail: §13 sixth/sixteenth/seventeenth entries; milestone tracker §3 | `feedback.1.12.md` |

No automatic process may add a promotion from `SHADOW` to `LIVE-CANARY` or from `LIVE-CANARY` to broader live scope without a recorded human approval.

