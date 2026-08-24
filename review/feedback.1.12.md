# feedback.1.12.md — M1 Qualification & Post-Soak Transition

**Project:** Autonomous EUR/USD Trading Platform  
**Review version:** 1.12  
**Date:** 2026-08-24  
**Reviewed artifact:** `status.md` v1.4 (`status(6).md`)  
**Previous review:** `feedback.1.11.md`  
**Overall verdict:** **GO — M1 QUALIFIED**  
**M0 verdict:** **GO WITH CONDITIONS — CI + DOMAIN CONTRACT REVIEW STILL OPEN**  
**M1 verdict:** **PASSED**  
**M2 verdict:** **PASSED**  
**Dashboard v0:** **GO NOW — READ ONLY**  
**M5 / P2 verdict:** **NO-GO**  
**Scope note:** This review is based on the supplied status document and its stated evidence. Source code, raw database rows, CI output and raw MT5 logs were not independently inspected.

---

## 1. Executive review

M1 has now produced the evidence previous reviews explicitly required.

The project has demonstrated against the real Pepperstone demo terminal:

```text
successful connection
real account validation
real EURUSD discovery
real instrument specification
continuous real ticks
continuous real M5 bars
real PostgreSQL persistence
real timestamp correction and M5 alignment
30 clean minutes of operation
two deliberate terminal interruptions
automatic reconnect
full account/symbol/spec/clock revalidation
fresh data resuming after each reconnect
```

Phase A produced:

```text
duration:          30:06
polls:             360
disconnects:       0
errors:            0
tick rows:         2,920
M5 bars:           17
data quality:      GOOD
bar gaps:          0
M5 alignment:      correct
```

Phase B then deliberately closed the real MT5 terminal twice.

Both interruptions were detected and both recovered automatically. Account guard, symbol discovery, instrument read and broker-clock detection ran again, and fresh market data resumed within seconds.

This is stronger evidence than the minimum M1 acceptance requirement.

**Reviewer decision: M1 PASSED.**

This does not authorize execution.

`order_send` remains prohibited.

---

## 2. Review 1.11 finding status

### F-031 — account identifier in ordinary logs
**Status:** CLOSED

The project now uses both call-site masking and central redaction.

### F-033 — stale current-state documentation
**Status:** REOPENED AGAIN

The chronological update log is current, but several top/current sections still describe the project as if the real soak never happened.

Examples include claims equivalent to:

```text
no real feed has been seen
continuous read has not run against the real terminal
bars/ticks collection is not real-terminal validated
reconnect is not real-terminal validated
connected-account reconnect mitigation is still fake-only
```

Required correction:

```text
M1 = PASSED
data feed = real Pepperstone evidence exists
bars/ticks = MT5-INTEGRATED
reconnect = MT5-INTEGRATED
F-034 = CLOSED
F-037 = CLOSED
```

Also refresh stale test/file counts. Historical update-log entries remain untouched.

This documentation issue does **not** reopen M1.

### F-034 — real reconnect + full revalidation
**Status:** CLOSED

Two real terminal closures were detected and recovered. After reconnect the platform re-resolved EURUSD, re-ran the account guard, re-read the instrument specification and re-measured the broker clock offset. Fresh data resumed.

### F-037 — timestamp semantics
**Status:** CLOSED

The real runs proved the original UTC assumption wrong for this Pepperstone environment. The gateway now detects the broker-clock offset dynamically per connection. The clean run showed all persisted M5 bars aligned exactly to UTC five-minute boundaries with zero continuity gaps.

### F-038 — chunked tick batch failure semantics
**Status:** CLOSED

Real PostgreSQL failure injection proves logical-batch rollback semantics.

---

## 3. Real-soak defect sequence assessment

The soak sequence exposed issues that synthetic testing could not realistically prove:

```text
D-040  real numpy scalar representation
D-041  real tick-volume / PostgreSQL parameter limit
D-042  still-forming MT5 bar
D-039  broker-clock offset
D-042  late post-close tick-volume revision
```

Each defect was diagnosed, fixed and regression-tested before the next run.

Most importantly, the final run produced positive end-to-end evidence: ticks persisted, bars persisted, timestamps normalized, quality GOOD, no gaps, no conflicts, reader healthy.

---

## 4. F-039 — semantic instrument identity must not change merely because it was re-observed

**Severity:** HIGH BEFORE M5 / RECONCILIATION  
**Status:** OPEN

Phase B reports that every reconnect logs `spec_changed` because a fresh `captured_at_utc` changes the instrument-spec fingerprint.

That is useful provenance, but it is not a genuine specification change.

Required separation:

```text
observation identity
= when/where this spec snapshot was captured

semantic spec identity
= digits, point, tick size/value, contract size,
  volume limits/step, stops/freeze, trade/filling modes, etc.
```

A fresh timestamp may create a new observation record. It must **not by itself** create a semantic `spec_changed` event.

Acceptance:

```text
same broker fields + later captured_at
→ semantic spec unchanged

one material broker field changes
→ semantic spec changed

reconnect with identical contract
→ no false spec-change alert
```

---

## 5. F-040 — broker-clock detection must fail closed when its reference tick is stale

**Severity:** HIGH BEFORE UNATTENDED/PAPER OPERATION  
**Status:** OPEN

Dynamic clock-offset detection is preferable to hard-coding `+3h`.

However, deriving the offset from the latest symbol tick versus the platform clock can be unsafe when the latest tick is old, for example outside active trading or after an interruption.

Required guard:

```text
fresh reference tick + stable offset
→ accept

stale/ambiguous reference
→ clock state UNKNOWN
→ reader/data health not HEALTHY
```

Also fail closed on a large unexplained offset jump after reconnect.

This does not invalidate the active-session Phase-A evidence and therefore does not reopen M1. It must be closed before unattended paper operation.

---

## 6. F-041 — operational soak/database reset must remain on the migration path

**Severity:** MEDIUM DATA GOVERNANCE  
**Status:** OPEN

Separating `crumblr` for tests and `crumblr_soak` for real MT5 soak evidence was the correct fix.

However, one preparation path used `bootstrap_schema()` / `create_all` after application tables were dropped while Alembic's version table remained.

The project already decided:

```text
Alembic = durable deployment path
create_all = tests only
```

Required improvement: provide a deliberate soak reset procedure that drops/recreates the soak database and then runs `alembic upgrade head`, or otherwise resets schema and Alembic state coherently.

This does not block M1 qualification.

---

## 7. M1 qualification decision

### Verdict: PASSED

Previous reviews required M1 to prove:

```text
[✓] continuous real ticks
[✓] real M5 bars
[✓] persistence of real market data
[✓] stale/failure detection
[✓] controlled disconnect detected
[✓] reconnect succeeds
[✓] account guard re-runs
[✓] instrument spec revalidated
[✓] timestamp semantics verified
[✓] no execution path touched
```

All are now supported by documented real-terminal evidence.

Record M1 in promotion history as:

```text
M1 MT5 read-only gateway
→ MT5-INTEGRATED
Decision: PASS
Reviewer: feedback.1.12
Date: 2026-08-24
```

No claim of paper validation follows from this.

---

## 8. Dashboard v0 — GO NOW

The owner requested a simple visual interface earlier. The prerequisite real-data evidence now exists.

Dashboard v0 should therefore be the next visible product step, while the developer also closes M0 housekeeping and prepares reconciliation.

Hard boundary:

```text
Dashboard
→ reads PostgreSQL / read-only application health
→ displays state

Dashboard
✗ no MetaTrader5 import
✗ no broker credentials
✗ no BUY/SELL buttons
✗ no order_send
✗ no HALT reset
✗ no risk-policy mutation
```

Minimum useful screen:

```text
CRUMBLR — DEMO / READ ONLY
EXECUTION DISABLED

MT5 connectivity
LiveReader health
last tick age
bid / ask / spread
current closed M5 bar
broker/server/entity
account mode (no login)
reconnect count
last gateway error
data gaps / quality
latest Signal / NO_TRADE
latest RiskDecision
latest SupervisorDecision
HALT state/reason
uncalibrated supervisor checks
```

A recent M5 chart is welcome if it is cheap. Do not let chart polish delay reliability work.

---

## 9. M0 should now be closed deliberately

M0 still has two gates:

```text
CI runner execution
domain-contract human/reviewer approval
```

The old local-only CI exception no longer makes much sense: the project has a remote, macOS and Windows development hosts, and cross-platform bugs have already been found.

Recommendation: run CI now. Then provide the domain-contract package for review and close M0.

---

## 10. Next engineering priority after Dashboard v0

After the read-only dashboard exists, the next safety-critical engineering item should be **reconciliation**.

Start read-only. Compare platform-known state against observed MT5 state:

```text
account identity
positions
symbol
instrument specification
```

Represent:

```text
MATCHED
MISMATCHED
UNKNOWN
```

Anything other than `MATCHED` remains fail-closed.

Do not introduce order submission to test reconciliation.

---

## 11. Owner decisions still required before M5

The following human policy choices remain open:

```text
risk per trade
max daily loss / max drawdown
exact last-entry cutoff
mandatory flatten timing
production HALT-reset authority
```

The existing YAML values remain placeholders until explicitly approved.

These decisions do not block Dashboard v0 or read-only reconciliation.

---

## 12. Gate decisions

### M0
**GO WITH CONDITIONS** — close CI + domain-contract review.

### M1
**PASSED — MT5-INTEGRATED**

### M2
**PASSED**

### M3
**CORRECTNESS / EVIDENCE WORK**

### M4
**REPLAY-TESTED — REAL EXECUTION VALIDATION NOT STARTED**

### M5
**NO-GO**

Still required before any order submission include reconciliation, execution-time risk revalidation, automatic flatten semantics, approved risk policy, execution-adapter separation, account/environment execution guard, CI and `feedback.2.0` GO.

### M6
**FEATURE FREEZE**

### M7
**SAFETY WORK ONLY**

### Dashboard v0
**GO NOW — READ ONLY**

### P2
**NO-GO**

---

## 13. Required next action order

```text
1. Process feedback.1.12.md.
2. Record M1 PASS in current gate/promotion history.
3. Fix stale current-state sections (F-033).
4. Fix semantic spec-change identity (F-039).
5. Add stale-reference protection to broker-clock detection (F-040).
6. Clean up soak database reset/migration procedure (F-041).
7. Build Dashboard v0 from PostgreSQL/read-only health state.
8. Run and record actual CI.
9. Provide domain contracts for reviewer/human approval; close M0.
10. Build read-only reconciliation against real MT5 state.
11. Decide remaining owner risk/intraday/HALT-reset policies before M5.
12. Continue strategy feature freeze.
13. Do not enable AlgoTrading.
14. Do not create/use order_send.
15. feedback.2.0.md before the first demo or live order submission.
```

---

## 14. Next review

Next regular review:

```text
feedback.1.13.md
```

Suggested trigger: Dashboard v0 available, F-039/F-040/F-041 addressed, CI result and/or M0 contract review available, and initial real reconciliation evidence.

The mandatory execution review remains `feedback.2.0.md` before any `order_send`, including demo.

---

## 15. Final reviewer statement

The platform has now done more than connect to MT5.

It has shown that it can observe a real broker continuously, record real market data, normalize broker time, survive real terminal loss, reconnect, revalidate its assumptions and resume data collection.

That is sufficient to close the read-only MT5 milestone.

**M1 is passed. Execution is still not.**
