# feedback.1.11.md — Real Soak Defects, Security Reopen & M1 Evidence Gate

**Project:** Autonomous EUR/USD Trading Platform  
**Review version:** 1.11  
**Date:** 2026-08-24  
**Reviewed artifact:** `status.md` v1.4  
**Previous review:** `feedback.1.10.md`  
**Overall verdict:** **GO — CONTINUE THE REAL SOAK**  
**M1 verdict:** **NOT YET PASSED; TWO REAL-SOAK DEFECTS FOUND AND FIXED, CLEAN SOAK STILL REQUIRED**  
**M2 verdict:** **PASSED**  
**Dashboard v0:** **GO AFTER A CLEAN PHASE A; READ-ONLY ONLY**  
**M5 / P2 verdict:** **NO-GO**  
**Scope note:** This review is based on the supplied `status.md` and its stated evidence. Source code and raw runtime logs were not independently inspected.

---

## 1. Executive review

This is good engineering progress even though the soak has not passed yet.

The first two real Phase-A attempts failed immediately, but both failures exposed weaknesses that synthetic tests had not found:

```text
Attempt 1:
real MT5 numpy scalars
→ Decimal conversion crash
→ D-040 found and fixed
→ real numpy regression tests added

Attempt 2:
real EUR/USD tick volume
→ PostgreSQL parameter ceiling exceeded
→ D-041 found and fixed
→ 4001-tick integration test added
```

This is exactly why the real soak was required before M1 qualification.

The correct next action is **not** to abandon the soak or move to the dashboard. It is to run Phase A again with both fixes present.

---

# 2. Review 1.10 finding status

## F-033 — current-state documentation

**Status:** STILL PARTLY OPEN

The developer did substantial cleanup, and the top MT5 capability table is much better.

However, current-state sections still contradict known decisions.

For example, the current “Next 10 actions” section still presents these as unresolved:

```text
Pepperstone entity
Q2 hedging/netting
```

while the same document already records:

```text
O-005 = Pepperstone Limited (UK) for demo
Q2    = RETAIL_HEDGING
```

This is still the same F-033 class of defect.

### Required

Mark those resolved in the current action list.

Historical update-log text may remain unchanged.

Also refresh test counts after the D-040/D-041 cycle once the full suite has been rerun.

---

## F-036 — acknowledgement must not mean safe again

**Status:** CLOSED BASED ON DOCUMENTED TEST EVIDENCE

The review requirement found a real missing-symbol crash path.

The fix is directionally correct:

```text
acknowledge
≠
HEALTHY
```

A subsequent successful full revalidation is still required.

The new tests cover:

```text
wrong account
missing EURUSD
positive restored-state case
```

Keep this invariant when Dashboard v0 is introduced.

---

## F-037 — real MT5 timestamp semantics

**Status:** OPEN

The real soak has not yet run long enough to produce usable evidence.

D-039 therefore remains unresolved.

Do not close M1 until real tick/bar timestamps have been compared against receive time and expected M5 boundaries.

---

## F-034 — real reconnect/revalidation

**Status:** OPEN

The real deliberate interruption has not yet happened.

Unit evidence remains strong, but Phase B is still mandatory.

---

# 3. Real-soak defect D-040

## Assessment: correctly found and correctly converted into a regression test

The real terminal returned NumPy structured arrays where test fixtures used plain Python objects.

That caused:

```text
numpy.float64
→ repr(value)
→ "np.float64(...)"
→ Decimal()
→ crash
```

The adapter now normalizes the NumPy scalar before decimal conversion, and tests now use genuine NumPy structured arrays.

This is valuable because the fake is now closer to the actual API shape in the exact area that failed.

**D-040 may remain CLOSED.**

---

# 4. Real-soak defect D-041

## Assessment: fix is directionally correct, but transaction semantics need proof

The real feed returned enough ticks in the five-minute lookback to exceed PostgreSQL's parameter count in one statement.

Chunking to 2000 rows is a sensible fix.

However, the status says the chunks run “inside the same connection so the operation is still atomic from the caller's side.”

A shared connection alone does not prove atomicity.

### F-038 — prove chunked tick persistence recovery semantics

**Severity:** HIGH DATA INTEGRITY  
**Status:** OPEN  
**Blocks:** M1 qualification

The store must define and prove what happens if:

```text
chunk 1 succeeds
chunk 2 fails
chunk 3 never runs
```

Choose and test one explicit contract:

### Preferred contract A — batch atomic

```text
all chunks commit
or
zero chunks commit
```

Required test:

```text
inject failure in chunk 2
→ transaction rolls back
→ zero rows from the logical batch remain
```

### Acceptable contract B — partial but convergent

If partial persistence is intentionally allowed:

```text
chunk 1 remains
chunk 2 fails
retry same logical batch
→ idempotently fills missing rows
→ no duplicates
→ final dataset equals intended batch
```

Then the code/documentation must stop calling the operation atomic.

The reviewer does not require one specific implementation, but the semantics must be explicit and proven.

---

# 5. F-031 security finding is reopened

## F-031 — account identifier still appears in runtime logs

**Severity:** HIGH SECURITY / OPERABILITY  
**Status:** REOPENED

The status explicitly records that the first failed soak's raw console output showed the real MT5 login because `Mt5Client` deliberately logs `login`.

That conflicts with the project/reviewer rule already established for first-contact evidence:

```text
full account login
must not enter
shared logs
review artifacts
Git
```

The fact that the login is an identifier rather than a password does not make it useful to expose in routine shared logging.

### Required fix

Structured/runtime logs should use one of:

```text
account_ref = "<redacted>"
account_ref = "***706"
account_ref = stable non-reversible hash/fingerprint
```

Do not log the full broker login.

### Required tests

At minimum:

```text
mt5.connected log does not contain full account login
error/reconnect log does not contain full account login
sanitized health/evidence does not contain full account login
exception formatting does not re-introduce it
```

Raw local MT5/API diagnostics may still exist in protected local-only files when genuinely necessary, but ordinary platform logs must be safe by default.

Do not rely on “remember not to cat the file” as the control.

The software should prevent the disclosure.

---

# 6. Phase A — run it again

After F-031 logging sanitization and F-038 transaction/recovery semantics are addressed or proven, restart Phase A.

Target:

```text
30–60 minutes
active EUR/USD session
real Pepperstone demo terminal
read-only
```

Required evidence:

```text
reader start UTC
reader end UTC
ticks received
M5 bars received
ticks persisted
bars persisted
last tick age
health transitions
data gaps
database conflicts
gateway errors
reconnect count
instrument spec versions
```

Most importantly:

> prove rows in PostgreSQL came from the real Pepperstone feed.

---

# 7. Timestamp verification during Phase A

Use the clean soak to close F-037/D-039.

Compare:

```text
MT5 tick time / time_msc
local receive UTC
M5 bar open time
expected 5-minute UTC boundaries
ordering across multiple polls
```

Check that:

```text
tick age is sensible
bars align to expected M5 boundaries
no unexplained fixed-hour offset exists
timestamps remain monotonic enough for the ingestion semantics
```

Do not invent a broker-time correction unless observation requires one.

---

# 8. Phase B — owner present

After a clean Phase A:

```text
HEALTHY reader
→ deliberately stop/restart MT5 terminal
→ reader detects loss
→ HEALTHY is lost
→ terminal returns
→ reconnect
→ full revalidation
→ account/server/currency/leverage/margin mode/symbol/spec checked
→ fresh data resumes
```

This is the point at which F-034 may close.

The owner should be present for this deliberate interruption.

---

# 9. Dashboard v0

Dashboard v0 remains approved.

Do not start it as a substitute for fixing/finishing the soak.

Once Phase A is clean, it can be built in parallel with preparation for Phase B.

Still required:

```text
read-only
localhost
VS Code port forwarding
no MetaTrader5 import
no credentials
no order buttons
no HALT reset
no risk mutation
```

The first useful dashboard should display the **real rows generated by the successful soak**.

---

# 10. CI / M0

No evidence in this status shows CI has actually run on a hosted runner.

M0 therefore remains open on:

```text
CI execution
human domain-contract review
```

Do not spend the current soak cycle on broad M0 refactoring, but run CI after the D-040/D-041/F-031/F-038 fixes are committed.

---

# 11. Gate decisions

## M0
**GO WITH CONDITIONS — CI + DOMAIN CONTRACT REVIEW STILL OPEN**

## M1
**GO — CONTINUE REAL SOAK, NOT PASSED YET**

Two real defects found and fixed is evidence the soak is doing its job, not evidence that M1 failed as a project.

M1 passes only after a clean Phase A and successful Phase B.

## M2
**PASSED**

D-041 does not automatically reopen M2. It exposed a scale robustness defect in a previously passed persistence component; fix and regression coverage are required, but the milestone's original gate semantics remain intact.

## M3
**CORRECTNESS ONLY**

## M4
**REPLAY-TESTED**

## M5
**NO-GO**

## M6
**FEATURE FREEZE**

## M7
**SAFETY WORK ONLY**

## Dashboard v0
**GO AFTER CLEAN PHASE A**

## P2
**NO-GO**

---

# 12. Required next action order

```text
1. Process feedback.1.11.md.
2. Reopen and fix F-031: remove full MT5 login from ordinary logs.
3. Prove D-041 chunk failure semantics (F-038).
4. Rerun full PostgreSQL test suite.
5. Restart real Phase A for 30–60 clean minutes.
6. Prove real ticks + M5 bars in PostgreSQL.
7. Close D-039/F-037 with timestamp evidence.
8. Record sanitized Phase-A evidence.
9. Build Dashboard v0 if Phase A is clean.
10. With owner present, perform Phase B terminal interruption.
11. Prove reconnect + full revalidation against real MT5.
12. Run CI and record result.
13. Prepare domain contracts for M0 closure.
14. feedback.1.12.md for M1 qualification/dashboard review.
15. feedback.2.0.md before any order_send.
```

---

# 13. What not to do

Do not:

```text
enable AlgoTrading
add an execution adapter
call order_send
interpret the two failed soak attempts as strategy evidence
tune ICT from the current data
build dashboard controls
move to paper execution
```

---

# 14. Final reviewer statement

The soak is already proving its value.

It has found two problems that hundreds of tests did not:

```text
real API data shape
real market-data volume
```

That is exactly what this phase is for.

Fix the security/logging exposure, prove the chunked persistence failure semantics, then run Phase A again until it produces a clean body of real data.

Only then move on.
