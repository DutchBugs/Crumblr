# feedback.1.10.md — Continuous Reader Readiness & Real-Soak Gate

**Project:** Autonomous EUR/USD Trading Platform  
**Review version:** 1.10  
**Date:** 2026-08-24  
**Reviewed artifact:** `status.md` v1.4  
**Previous review:** `feedback.1.9.md`  
**Overall verdict:** **GO — EXECUTE THE REAL READ-ONLY SOAK NOW**  
**M1 verdict:** **IMPLEMENTATION READY; REAL SOAK/RECONNECT EVIDENCE STILL REQUIRED**  
**M2 verdict:** **PASSED**  
**Dashboard v0:** **GO AFTER / ALONGSIDE THE REAL SOAK, READ-ONLY ONLY**  
**M5 / P2 verdict:** **NO-GO**  
**Scope note:** This review evaluates the supplied status document and its stated evidence. Source code and runtime logs were not independently inspected.

---

## 1. Executive review

The project is progressing in the right direction.

Review 1.9 has been processed, O-005 is recorded, the demo entity question is closed for development, AlgoTrading remains deliberately disabled, and the missing continuous reader has now been implemented.

The new M1 read path reportedly includes:

```text
real MT5 read-only gateway
→ continuous ticks
→ M5 bars
→ persistence sink
→ freshness state
→ disconnect detection
→ reconnect
→ account revalidation
→ instrument/spec revalidation
```

All five reconnect cases requested in review 1.9 F-034 are reported as passing against a scripted terminal.

The full suite is now:

```text
686 passed
3 explained platform skips
0 failures
```

That is meaningful progress.

However:

> The continuous reader has still not run as a continuous reader against the real Pepperstone terminal.

Therefore the next milestone is an **evidence run**, not another feature cycle.

---

# 2. Review 1.9 findings

## F-033 — stale current-state sections

**Status:** REOPENED / PARTLY RESOLVED

The developer states that F-033 was closed, but the supplied `status.md` still contains current-state sections that predate the newly built continuous reader.

Examples include statements equivalent to:

```text
continuous read is still unbuilt
reconnect behaviour is still unimplemented
implement continuous reader is still the next action
```

while the newest update log says the reader and reconnect state machine are built and unit-tested.

The repository/build checklist also still contains older statements such as CI never run / no remote despite the remote already existing.

### Required fix

Use one rule consistently:

```text
current/top sections = present truth
update log           = historical truth
```

Update, at minimum:

```text
Overall health
Component 1 current status
M1 milestone row
MT5 checklist
current risk table
Next actions
repository/CI state
```

Do not edit old chronological entries.

---

## F-034 — reconnect with full revalidation

**Status:** IN PROGRESS — IMPLEMENTED/UNIT-TESTED, NOT REAL-TERMINAL VALIDATED

The required test cases are reported as implemented:

```text
disconnect → same account → recover
wrong account/server → fail closed
symbol spec change → detect
no ticks → stale
terminal restart → guard re-run
```

This is the correct behavior.

F-034 closes only after the real soak includes at least one deliberate interruption and demonstrates the same fail-safe behavior against the actual MT5 terminal.

---

## F-035 — Dashboard v0 boundary

**Status:** OPEN / APPROVED

Dashboard v0 has not yet been built.

Its requirement remains:

```text
read-only
no MetaTrader5 import
no broker credentials
no order controls
no HALT reset
no risk config mutation
```

It may start now, but it must not replace or delay the real M1 soak.

---

# 3. New findings

## F-036 — Reader acknowledgement must never equal automatic restoration of health

**Severity:** HIGH DESIGN BEFORE M5  
**Status:** OPEN

`LiveReader` has a sticky `UNHEALTHY` state that can be cleared through an explicit operator acknowledgement.

That is reasonable for an operational reader, but the semantics must be precise.

An operator acknowledgement means:

```text
"I have seen the incident"
```

It must not mean:

```text
"the broker/data state is safe again"
```

### Required invariant

After `UNHEALTHY`, an acknowledgement may clear the human-acknowledgement latch, but transition back to `HEALTHY` must still require successful fresh validation of:

```text
terminal connected
demo environment
server/account identity
currency
leverage
margin/account mode
EURUSD mapping
instrument specification
fresh market data
```

If validation fails:

```text
remain UNHEALTHY
```

### Required tests

```text
wrong account → UNHEALTHY
operator acknowledge without fixing account
→ must NOT become HEALTHY

wrong spec → UNHEALTHY
acknowledge
→ full validation still required

valid state restored + acknowledge/revalidation
→ HEALTHY may resume
```

This matters later because a dashboard/operator workflow must never be able to convert acknowledgement into a safety override.

---

## F-037 — MT5 timestamp semantics must be verified against the real feed before M1 is qualified

**Severity:** HIGH DATA INTEGRITY  
**Status:** OPEN  
**Related deviation:** D-039

The reader currently assumes the timestamps returned by the MT5 tick/bar APIs represent UTC correctly.

That assumption has not yet been verified against the real feed.

This is safety-relevant because the platform uses time for:

```text
stale-data detection
M5 bar boundaries
gap detection
event ordering
intraday policy
audit/replay
```

A timestamp offset error can make stale data look fresh or place data in the wrong trading window.

### Required evidence during real soak

For real Pepperstone data:

```text
capture MT5 tick time / millisecond time where available
capture local receive time in UTC
capture M5 bar open time
compare expected M5 boundaries
verify monotonic/order behavior
verify the observed offset is consistent with UTC semantics
```

Do not “correct” timestamps using a hard-coded broker timezone unless evidence proves that is necessary.

### Acceptance

D-039 can close only with recorded real-terminal evidence.

---

# 4. Continuous-reader architecture assessment

The reported split is good:

```text
LiveReader
= market-data acquisition + persistence + health

Replay/Decision Orchestrator
= decisions

Dashboard
= observation
```

Keep these responsibilities separate.

Particularly positive:

- `LiveReader` is not the trading orchestrator.
- stale data may self-clear only when genuinely fresh data returns;
- validation disagreement becomes sticky `UNHEALTHY`;
- bar conflicts are treated as integrity failures;
- first successful connection now reads immediately rather than waiting a cycle;
- no-data-since-connect can become stale instead of remaining falsely healthy.

Do not merge these layers to make the dashboard easier to build.

---

# 5. The next task is a real soak, not more simulation

The developer should now run the new reader against the actual Pepperstone demo terminal.

## Phase A — normal read

First run without deliberately breaking anything.

Suggested initial evidence window:

```text
30–60 minutes during an active FX session
```

The purpose is not performance testing.

Record:

```text
start/end UTC
ticks received
M5 bars received
database rows persisted
latest tick age
data gaps
conflicting rows
gateway errors
reconnect count
health transitions
instrument spec versions
```

Verify that actual Pepperstone ticks and bars exist in PostgreSQL.

---

## Phase B — deliberate interruption

Then perform one controlled interruption while the owner is present.

Preferred first test:

```text
reader healthy
→ close/restart MT5 terminal or otherwise cause a known terminal disconnect
→ reader detects loss
→ state no longer HEALTHY
→ terminal restored
→ reconnect
→ full account + instrument guard re-runs
→ fresh data resumes
```

Do not combine several failure modes in the first test.

After the simple terminal-restart case passes, network interruption may be tested separately if useful.

---

## Phase C — evidence

Record the sanitized run result in the project.

Do not commit:

```text
account login
password
raw credential-bearing output
```

Do record:

```text
timestamps
health transitions
reconnect attempts
guard result
symbol/spec
tick/bar counts
gaps/errors
database evidence
```

---

# 6. M1 qualification recommendation

M1 should pass only when the real soak demonstrates:

```text
[ ] continuous real ticks
[ ] real M5 bars
[ ] persistence of real market data
[ ] stale detection
[ ] controlled disconnect detected
[ ] reconnect succeeds
[ ] account guard re-runs after reconnect
[ ] instrument spec revalidated
[ ] timestamp semantics verified
[ ] no execution path touched
```

If those pass, review 1.11 may recommend formal M1 qualification.

---

# 7. Dashboard v0

The owner still wants a visual surface.

That remains a good idea.

The developer may start it in parallel once the real reader has produced actual rows.

### Minimum v0

```text
CRUMBLR
DEMO / READ ONLY
EXECUTION DISABLED
```

Show:

```text
MT5 connectivity
reader health
last tick / age
EURUSD bid / ask / spread
current M5 bar
Pepperstone demo/account-mode facts
safety state
latest signal / NO_TRADE
latest risk decision
latest supervisor decision
reconnect count
last gateway error
uncalibrated checks
```

Preferred data path:

```text
PostgreSQL / read-only application health state
→ dashboard
```

Not:

```text
dashboard → MetaTrader5
```

A simple Streamlit implementation over localhost/VS Code port forwarding is still acceptable for v0.

---

# 8. CI and M0

M0 is still unnecessarily lingering.

The project now has a remote repository and a Windows host.

Therefore:

> Run CI rather than taking the old local-development exception.

Then only domain-contract human review should remain.

The status document still says CI has never executed; the next review should contain the actual CI result, pass or fail.

---

# 9. Owner decisions still waiting — but not blocking today's M1 soak

Before M5/P2, the owner still needs to approve:

```text
risk per trade
max daily loss / drawdown
intraday final entry cutoff
mandatory flatten timing
```

These do not need to delay the current read-only M1 evidence run.

AlgoTrading remains OFF.

---

# 10. Gate decisions

## M0
**Verdict:** GO WITH CONDITIONS — RUN CI + DOMAIN CONTRACT REVIEW

## M1
**Verdict:** REAL-SOAK GATE NOW

Implementation is ready enough to test against reality.
Do not add another simulation layer first.

## M2
**Verdict:** PASSED

## M3
**Verdict:** CORRECTNESS ONLY

## M4
**Verdict:** REPLAY-TESTED / REAL DATA NOW AVAILABLE SOON

## M5
**Verdict:** NO-GO

## M6
**Verdict:** FEATURE FREEZE

## M7
**Verdict:** SAFETY WORK ONLY

## Dashboard v0
**Verdict:** GO — READ ONLY

## P2
**Verdict:** NO-GO

---

# 11. Required next action order

```text
1. Process feedback.1.10.md.
2. Fix stale current-state/status sections (F-033).
3. Confirm F-036 acknowledgement semantics with tests.
4. Run real reader normally against Pepperstone.
5. Prove real ticks + M5 bars are persisted in PostgreSQL.
6. Verify MT5 timestamp semantics (F-037 / D-039).
7. Perform one deliberate terminal interruption.
8. Prove reconnect + full revalidation against real MT5.
9. Record sanitized soak evidence.
10. Run CI and record result.
11. Build Dashboard v0 from read-only platform/PostgreSQL state.
12. Prepare domain-contract package for M0 closure.
13. Build reconciliation after M1 evidence is stable.
14. feedback.1.11.md for M1 qualification/dashboard review.
15. feedback.2.0.md before any order_send.
```

---

# 12. What not to do

Do not:

```text
enable AlgoTrading
create an execution adapter
call order_send
add dashboard trading buttons
tune the strategy from this first data sample
add ICT v2
add brokers/markets
```

---

# 13. Final reviewer statement

The project has reached the point where more unit tests alone give diminishing returns.

The important question is now:

> Can the read-only platform stay attached to a real MT5 terminal, notice failure, recover safely and preserve an accurate record of what actually happened?

The code to test that now exists.

Run the test.
