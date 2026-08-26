# feedback.1.21.md — F-051 Part 1 PASS; Real Trader Evidence Accumulating

**Project:** Autonomous EUR/USD Trading Platform  
**Review version:** 1.21  
**Date:** 2026-08-26  
**Reviewed artifact:** `status(20260826-135816).md`  
**Previous review:** `feedback.1.20.md`  
**Additional owner note:** a background `mt5_live_reader.py` process is still accumulating genuine M5 bars for F-051 part 2; that evidence may therefore change shortly.  
**Overall verdict:** **GO — F-051 PART 1 PASSED; LET PART 2 COMPLETE NATURALLY**  
**M0 verdict:** **OPEN — CI RERUN NOT YET CONFIRMED GREEN + DOMAIN-CONTRACT REVIEW**  
**M1 verdict:** **PASSED**  
**M2 verdict:** **PASSED**  
**F-051 part 1:** **PASSED / REAL-TERMINAL VALIDATED**  
**F-051 part 2:** **IN PROGRESS — WAITING ONLY FOR REAL M5 WARM-UP HISTORY**  
**F-053/F-054/F-055/D-031:** **IMPLEMENTED; RELEVANT REAL EVIDENCE FOR PART 1 ACCEPTED**  
**M5 / first DEMO order:** **NO-GO**  
**Scope note:** This review accepts the supplied status evidence. Source code, raw PostgreSQL rows, GitHub Actions logs and `review/domain_contracts.md` were not independently inspected.

---

## 1. Executive review

This update crosses a major real-world checkpoint.

F-051 part 1 has now run against the current Crumblr stack and the real Pepperstone demo terminal.

Reported successful evidence:

```text
current Windows/MT5 host
PepperstoneUK-Demo
account guard passed
real InstrumentSpec persisted
real BrokerAccountSnapshot persisted
position set COMPLETE
pending-order set COMPLETE
0 open positions
0 pending orders
reconciliation UNKNOWN before spec pin
human-approved semantic spec pin
fresh broker capture
reconciliation MATCHED after pin
zero integration defects found
```

This is accepted.

The remaining F-051 steps require a real decision window with sufficient strategy warm-up bars.

That is **not a defect** and should not trigger new infrastructure work.

The owner reports a background reader is still accumulating those real bars.

Let it finish.

---

## 2. F-051 part 1 — PASS

**Status:** PASSED

This is the strongest reconciliation evidence produced so far.

The reported real account observation was:

```text
balance      EUR 10,000.00
equity       EUR 10,000.00
margin       0
free margin  EUR 10,000.00
mode         RETAIL_HEDGING
currency     EUR
leverage     30

positions       COMPLETE / 0
pending orders  COMPLETE / 0
```

That proves the F-047/F-052 broker-state path against the real terminal rather than only a fake.

The account was genuinely observed flat rather than inferred flat from a missing response.

Accepted.

---

## 3. F-055 — real fail-closed → authorized → MATCHED sequence accepted

F-055 is now operationally proven.

The important sequence happened exactly as designed:

```text
expected_spec_version absent
        ↓
real observed EURUSD spec
        ↓
reconciliation UNKNOWN
        ↓
human review of the observed spec
        ↓
approved semantic spec_version pinned in versioned config
        ↓
fresh broker observation
        ↓
reconciliation MATCHED
```

This is materially better evidence than simply pre-populating a hash before the run.

The reported semantic comparison against the earlier 2026-08-24 observation also behaved correctly:

```text
stable contract fields = same
dynamic tick_value     = changed slightly
spec_version           = stable
```

That is additional real evidence that F-039's semantic identity choice was correct.

### Baseline status

The current Pepperstone DEMO baseline may now be treated as the approved v1 demo/development semantic baseline.

Any later change to that pin remains a reviewed configuration change.

It must never be silently refreshed from the latest broker observation.

---

## 4. Real reconciliation — qualified

For the current flat pre-execution state:

```text
expected positions      0
expected pending orders 0
approved spec baseline  present
fresh observed state    COMPLETE
```

the real broker now returns:

```text
MATCHED
```

This qualifies the **read-only reconciliation v0 for the current flat-account use case**.

Do not overstate that result.

Execution-era reconciliation is still future work because once orders exist:

```text
expected broker state
```

must come from Crumblr's own durable order/position authority, not from `ExpectedState.flat()`.

That transition belongs to Phase 4/M5.

---

## 5. F-051 part 2 — do not interfere with the running evidence collection

**Status:** IN PROGRESS

At the recorded checkpoint:

```text
real stored M5 bars = 49

baseline_v1 minimum = 65
ict_v1 minimum      = 120
```

The owner has started a background `mt5_live_reader.py` run to build enough real history.

Correct action:

> **Do nothing clever. Let the real bars accumulate.**

Do not add a history-backfill subsystem just to shave an hour off this checkpoint.

Do not inject synthetic bars.

Do not lower strategy warm-up requirements.

Do not modify `ict_v1`.

The absence of enough real history is itself honest evidence.

---

## 6. `baseline_v1` is acceptable for the F-051 wiring proof, with one qualification

The one-off `baseline_v1` substitution is acceptable for **F-051's infrastructure/live-shadow wiring proof** because the purpose is to demonstrate:

```text
real closed M5
→ feature calculation
→ Trader contract
→ Risk
→ reconciliation
→ Supervisor
→ durable audit
```

using a strategy that naturally has a shorter warm-up.

However:

```text
baseline_v1 real run
≠
ict_v1 real strategy evidence
```

Therefore if `baseline_v1` completes part 2:

```text
F-051 / F-048 real live-shadow plumbing → may PASS
```

but:

```text
M6 ict_v1 maturity → remains REPLAY-TESTED
```

until `ict_v1` itself later evaluates genuine real-market windows.

Do not mark `ict_v1` MT5-INTEGRATED solely because another implementation of the Trading Agent contract crossed the pipeline.

---

## 7. Acceptance criteria for F-051 part 2

Once the running reader has accumulated enough bars, the follow-up evidence should show:

```text
1. newly closed genuine M5 window exists
2. all warm-up/source bars are REAL origin
3. latest broker state is fresh and usable
4. reconciliation = MATCHED
5. LiveDecisionOrchestrator evaluates the window
6. FeatureEvidence is persisted
7. Trader returns natural NO_TRADE / BUY / SELL
8. SignalGenerated is persisted
9. if intent exists: intent-time Risk executes using real broker context
10. Supervisor receives real reconciliation state
11. capsule/journal chain is traceable
12. decision-window durable state is updated
13. rerunning/restarting does not create an independent duplicate decision
14. no ApprovedOrder is constructed
15. no order_check
16. no order_send
```

A `NO_TRADE` outcome passes.

A Risk BLOCK outcome passes.

A Supervisor VETO/HALT passes if it is the correct outcome from the real context.

The purpose is to validate the path, not manufacture an approval.

---

## 8. F-056 — CI finally did useful work

**Status:** SPECIFIC DEFECT FIXED / CI GATE STILL OPEN

For the first time, GitHub Actions actually executed the workflow.

It found a real environment dependency defect:

```text
numpy
```

was used by D-040 regression tests but was only present locally as an accidental transitive dependency of the MT5 extra.

That is exactly the class of issue CI is supposed to detect.

The reported fix is correct in principle:

```text
numpy>=2.0
→ dev dependency
→ not hidden behind the mt5 extra
```

and the exact CI-like commands then passed locally.

But:

```text
local reproduction of CI
≠
green hosted CI
```

So M0 remains open until the next GitHub Actions run is visibly green.

Do not waive that final rerun now that CI has already demonstrated its value.

---

## 9. Current-state documentation: two tiny sync fixes, no new F-033 cycle

Do not start another documentation session.

When `status.md` is next touched, correct two current-state lines:

### Data section

It still says the InstrumentSpec producer has:

```text
not yet run against the real terminal with this code — F-051
```

F-051 part 1 has now done exactly that.

Update it to:

```text
real-terminal validated 2026-08-26
```

### CI checklist

One current checklist sentence still says the fix:

```text
needs a push and a human check
```

while newer current summary text says the fix is already pushed and only the rerun result remains unconfirmed.

Use the newest truth:

```text
fix pushed
→ hosted rerun result pending
```

No finding reopen is necessary.

---

## 10. Domain contracts still block M0

The status continues to say:

```text
review/domain_contracts.md
```

exists, but the actual document has still not been supplied to this reviewer.

Therefore:

```text
domain-contract approval = OPEN
```

Please include the unchanged document with a future review package.

This can happen independently of the ongoing M5-bar accumulation.

---

## 11. Phase 4 can now start without waiting for the last bars

There is no reason to leave developers idle while the background reader accumulates evidence.

F-051 part 1 has already proven:

```text
broker identity
broker state
spec authority
reconciliation
```

against the real terminal.

Therefore non-sending Phase-4 work may proceed in parallel:

```text
execution adapter interface
ApprovedOrder construction
order_check wrapper
durable order_request_id
ExecutionResult persistence
FINAL execution-time Risk
automatic flatten implementation
execution multi-gate
post-execution reconciliation design
```

Hard rule:

```text
building the execution path
≠
enabling the execution path
```

`order_send` remains prohibited.

---

## 12. Phase-4 submission idempotence now becomes a concrete requirement

F-054 protects:

```text
one M5 decision window
→ one logical decision
```

The execution layer must independently protect:

```text
one approved executable request
→ one logical broker submission
```

Before the execution adapter can be considered complete, ambiguous outcomes must resolve as:

```text
timeout / crash / lost response
        ↓
DO NOT blindly resubmit
        ↓
query durable request state
        ↓
reconcile broker state
        ↓
determine whether the request already took effect
```

This should become an explicit Phase-4 test suite.

---

## 13. Owner policy should now be decided

Before `feedback.2.0`, explicitly approve:

```text
risk per trade
max daily loss
max drawdown
last-entry cutoff
mandatory flatten deadline
HALT-reset authority
```

These decisions can be made while the background reader and Phase-4 development continue.

---

## 14. Dashboard

F-051 part 1 has now validated the real data sources for:

```text
Balance
Equity
Open P/L
Free margin
Positions
Pending orders
Broker-state age/completeness
Reconciliation
```

Therefore those operational panels may now be wired to the existing read-only dashboard.

The live Trader/Risk/Supervisor panel should wait for F-051 part 2 evidence if necessary.

Still:

```text
no visual redesign
no direct MT5 access
no execution controls
```

---

## 15. Current critical path

```text
M1 real MT5 read/reconnect                    ✅
M2 persistence                               ✅
real broker account snapshot                 ✅
real position snapshot COMPLETE              ✅
real pending-order snapshot COMPLETE         ✅
real approved spec baseline                  ✅
real reconciliation UNKNOWN-before-pin       ✅
real reconciliation MATCHED-after-pin        ✅

real Trader/Risk/Supervisor window            ⏳ background bars accumulating
hosted CI rerun green                         ⏳
domain-contract review                       ⏳
owner risk policy                            ⏳

Phase-4 execution engineering                GO TO BUILD
execution adapter                            ❌
order_check                                  ❌
FINAL Risk                                   ❌
automatic flatten                            ❌
submission idempotence                       ❌
feedback.2.0                                 ❌
first autonomous DEMO canary                 🚫
```

---

## 16. Gate decisions

### M0
**OPEN**

Needs:

```text
green hosted CI rerun
domain-contract approval
```

### M1
**PASSED**

### M2
**PASSED**

### F-051 part 1
**PASSED**

### F-051 part 2
**IN PROGRESS / NO DEFECT**

### Read-only reconciliation flat-account use case
**REAL-TERMINAL QUALIFIED**

### F-055
**OPERATIONALLY PROVEN**

### Phase-4 non-sending engineering
**GO NOW**

### First `order_send`
**NO-GO**

Still requires `feedback.2.0 GO`.

---

## 17. Required next action order

Because a background reader is already running, do not serialise everything behind it.

```text
TRACK A — running now
1. Let mt5_live_reader.py accumulate genuine M5 bars.
2. Do not restart/reset the soak DB.
3. Once >=65 bars exist, run baseline_v1 F-051 wiring proof.
4. Record full real decision evidence.
5. If convenient later at >=120 bars, run ict_v1 too.

TRACK B — human/evidence
6. Check the post-F-056 GitHub Actions rerun.
7. Supply review/domain_contracts.md unchanged.
8. Decide owner risk/intraday/HALT-reset values.

TRACK C — engineering in parallel
9. Start Phase-4 non-sending execution architecture.
10. Build durable order_request_id semantics.
11. Build order_check.
12. Build FINAL execution-time Risk.
13. Build automatic flatten.
14. Build execution multi-gate and post-result reconciliation.
15. Add validated broker/reconciliation data to dashboard.

THEN
16. Prepare feedback.2.0.
17. No order_send before feedback.2.0 GO.
```

---

## 18. Next review

Because part 2 is actively accumulating evidence right now, do **not** create another review merely because this file was processed.

The next normal file should be:

```text
feedback.1.22.md
```

triggered by one meaningful bundle, preferably:

```text
F-051 part 2 result
+ hosted CI rerun result
and/or
domain_contracts.md
and/or
first substantial Phase-4 implementation
```

If the background run or first real Trader decision exposes a defect, review immediately.

---

## 19. Final reviewer statement

F-051 is no longer a hypothetical future test.

Half of it has now passed against the real Pepperstone environment, including the most safety-sensitive read-side sequence:

```text
observe broker
→ distrust unapproved spec
→ human-authorize spec
→ observe broker again
→ reconcile MATCHED
```

The remaining live-shadow evidence is currently waiting on nothing more exotic than enough genuine M5 candles.

That is exactly the sort of waiting we should accept rather than engineer around.

Let the reader run.

Use the time to close CI/contracts/owner policy and begin the non-sending execution layer.

The project can now move in parallel instead of putting all progress behind one warm-up counter.
