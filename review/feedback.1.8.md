# feedback.1.8.md — Windows Host Integration & MT5 First-Contact Review

**Project:** Autonomous EUR/USD Trading Platform  
**Review version:** 1.8  
**Date:** 2026-08-24  
**Reviewed artifact:** `status.md` v1.4  
**Previous review:** `feedback.1.7.md`  
**Overall verdict:** **GO — PROCEED TO READ-ONLY MT5 FIRST CONTACT**  
**M0 verdict:** **READY FOR CLOSURE DECISION WITH TWO ADMINISTRATIVE CONDITIONS**  
**M1 verdict:** **GO FOR READ-ONLY FIRST CONTACT**  
**M2 verdict:** **READY FOR GATE RECONCILIATION; DO NOT BLOCK ON REAL FEED UNLESS build.md REQUIRES IT**  
**M5 / P2 verdict:** **NO-GO**  
**Scope note:** This review is based on the supplied `status.md` and its stated evidence. Source code, test output and MT5 terminal output were not independently inspected.

---

## 1. Executive review

The project is moving forward materially.

The Windows x86-64 host is now part of the development path. The repository was cloned there, the MT5 Python dependency was installed, lint/type/test gates were run, and a genuine Windows-specific mypy defect was found and corrected.

That is exactly why the Windows host was required before broker integration.

The latest evidence also shows:

```text
read-only MT5 adapter        built
first-contact probe          built
execution path               still unreachable
Windows development host     available
Windows local gate           run
replay determinism           preserved
private remote               established
CI can now execute
```

The project should now stop preparing for first contact and **perform first contact**.

The owner already has a Pepperstone demo account. The immediate remaining practical step is to log that account into the MT5 terminal on the Windows machine and run the read-only probe.

---

## 2. Review 1.7 status

`feedback.1.7.md` had not yet been processed when this status was produced.

Its substantive findings remain relevant and are incorporated below rather than ignored.

### F-026 — demo account incorrectly shown as nonexistent
**Status:** OPEN — MUST BE CORRECTED NOW

### F-027 — M2 gate may contain an M1/real-feed condition
**Status:** OPEN FOR SPEC RECONCILIATION

### F-028 — Pepperstone entity ambiguity
**Status:** OPEN, BUT DOES NOT BLOCK READ-ONLY FIRST CONTACT

### F-029 — paper campaign broker/server header stale
**Status:** OPEN / LOW

---

## 3. Positive findings from the Windows integration

### 3.1 Windows is already finding bugs macOS could not

The Windows run exposed a mypy/platform-specific test defect that did not reproduce on macOS.

This is useful evidence that the two-host development model is already paying for itself.

The fix appears appropriately narrow:

```text
product code unchanged
test platform guard corrected
```

No trading or broker behavior was changed merely to make Windows pass.

That is the right response.

### 3.2 Read-only gateway boundary remains strong

The adapter reportedly cannot execute even if misconfigured.

This remains a strong M1 property:

```text
read-only adapter
!=
execution adapter with a disabled flag
```

Keep that separation permanently.

M5 should introduce a separate execution-capable adapter rather than weakening the read-only one.

### 3.3 First-contact probe is the correct next tool

The new probe turns first contact into a repeatable evidence-producing action instead of an interactive debugging session.

That is desirable.

The first real terminal run should be treated as an observation exercise:

```text
observe
record
compare
open deviations
then change code
```

not:

```text
observe mismatch
patch until green
forget what differed
```

---

## 4. New findings

### F-030 — Full Windows gate has not yet run with PostgreSQL

**Severity:** MEDIUM  
**Status:** OPEN  
**Blocks:** claiming full Windows engineering parity; does not block read-only MT5 discovery

Current Windows evidence is:

```text
587 passed
76 skipped
PostgreSQL not running
```

The project explicitly says the complete PostgreSQL-backed suite has not yet executed on Windows.

**Required action**

Start the local PostgreSQL test environment on Windows and run the full gate.

Record:

```text
pytest full result
mypy
ruff
replay determinism
PostgreSQL integration result
```

**Acceptance**

The Windows host should have a complete local evidence run before M1 is called qualified.

Do not hide unexpected skips behind a green aggregate result.

---

### F-031 — First-contact evidence must be sanitized before it enters Git/review artifacts

**Severity:** MEDIUM / SECURITY  
**Status:** OPEN BEFORE FIRST-CONTACT ARTIFACT IS SHARED

The probe writes a JSON evidence file.

The MT5 account login is an account identifier and should not enter:

```text
Git
review documents
status.md
shared logs
committed first-contact fixtures
```

Passwords/tokens must of course never be present at all.

**Required rule**

Maintain two layers:

```text
local raw probe output
→ protected / ignored / local only

sanitized first-contact evidence
→ safe for status/review/test fixture use
```

At minimum redact or replace:

```text
login/account number
credentials
tokens
filesystem/user-specific secrets
```

Keep technical broker facts such as:

```text
server
demo/live status
currency
leverage
account mode
symbol specs
terminal version
```

**Acceptance**

A developer cannot accidentally commit the full account identifier merely by following the first-contact runbook.

---

### F-032 — MT5 enum decoding must be settled from real observation before instrument specs become authoritative

**Severity:** MEDIUM  
**Status:** OPEN

The developer correctly found that `filling_mode` is not safely represented by simply stringifying the MT5 integer value.

This is a good catch.

The decision not to patch an unobserved mapping before first contact is correct.

**Required first-contact evidence**

Record raw and interpreted values for at least:

```text
symbol_info.trade_mode
symbol_info.filling_mode
order/filling capability flags exposed by the terminal
```

Then:

1. compare against official MT5 semantics;
2. update adapter and fake terminal together;
3. add captured-real-value regression tests.

Until then, those fields must not be treated as authoritative execution instructions.

---

## 5. F-026 — account existence correction

The project owner has already created the demo account.

Current known facts:

```text
Broker brand: Pepperstone
Environment: DEMO
Server: PepperstoneUK-Demo
Leverage: 1:30
Currency: EUR
```

Therefore the status statements saying:

```text
no demo account
create demo account
```

are stale.

Correct state:

```text
Demo account: CREATED
Credentials: LOCAL SECRET ONLY
MT5 terminal login: PENDING / TO PERFORM ON WINDOWS
Terminal verification: PENDING
Account mode: UNKNOWN UNTIL account_info()
Legal entity: UNVERIFIED
```

The full account login must not be copied into this review or status file.

---

## 6. F-027 — M2 gate reconciliation

The status says all four M2 deliverables are now built but keeps M2 open because no real feed has been observed.

This still needs reconciliation against `build.md`.

Reviewer rule:

```text
If build.md says real-feed evidence is an M2 acceptance criterion:
    M2 remains open.

If build.md assigns real-feed validation to M1:
    M2 should be qualified on its own evidence,
    while real-feed validation remains an M1 concern.
```

Do not hold earlier gates open simply because later evidence is stronger.

---

## 7. F-028 — Pepperstone EU versus UK server

The current server claim is:

```text
PepperstoneUK-Demo
```

The earlier owner decision was described as:

```text
Pepperstone EU
```

Do not infer the legal contracting entity from the server string.

**Reviewer decision**

This does **not** block read-only M1 first contact.

Use the configured demo account to discover technical truth.

Keep:

```text
Legal entity: UNVERIFIED
```

until documentary/account evidence establishes it.

It must be resolved before any live-account or regulation-dependent decision.

---

## 8. Immediate next steps — now that Windows exists

The project should proceed in this order.

### Step 1 — Owner logs the existing demo account into MT5 on Windows

This is the only manual credential-bearing step.

Do not send the credentials through Git or review files.

### Step 2 — Start PostgreSQL/Docker on Windows and run the full local gate

Close F-030.

### Step 3 — Run the read-only first-contact probe

Capture and sanitize:

```text
terminal version
demo/live state
server
currency
leverage
account mode
trade permissions
EUR/USD broker symbol
digits
point
tick size
tick value
contract size
volume min/max/step
stops level
freeze level
raw filling mode
decoded filling mode candidate
trade mode
current bid/ask
flat-position behavior
```

### Step 4 — Record every real-vs-fake disagreement

Do not silently patch.

Examples:

```text
field missing
field type differs
enum differs
symbol naming differs
positions_get semantics differ
precision differs
```

Each meaningful discrepancy should become a deviation + regression test.

### Step 5 — Persist real ticks and M5 bars immediately

The storage layer already exists.

This is where the project transitions from synthetic evidence to broker-observed evidence.

### Step 6 — Implement continuous read + reconnect behavior

M1 should prove more than one successful API call.

Test:

```text
continuous tick read
continuous M5/bar retrieval
stale-data detection
terminal restart
network interruption
reconnect
post-reconnect account revalidation
```

### Step 7 — Build real reconciliation

Compare:

```text
local expected account/position state
vs
MT5 observed state
```

Mismatch or unknown:

```text
HALT
```

---

## 9. M0 position

M0 is now close enough that it should not remain open indefinitely.

Two items remain:

```text
domain contract human/reviewer approval
CI execution
```

The repository now has a remote and CI can run, so the earlier “local-only exception” is becoming unnecessary.

**Recommendation:** run CI now.

For domain contracts, provide the current contract package/spec summary to the reviewer and close M0 deliberately.

---

## 10. Gate decisions

### M0
**Verdict:** CLOSE SOON — RUN CI + CONTRACT REVIEW

### M1
**Verdict:** GO FOR READ-ONLY FIRST CONTACT NOW

The only remaining manual action is logging the existing Pepperstone demo account into the Windows MT5 terminal.

### M2
**Verdict:** READY FOR SPEC-BASED GATE DECISION

Resolve F-027 rather than leaving it indefinitely `NOT PASSED`.

### M3
**Verdict:** CORRECTNESS ONLY

### M4
**Verdict:** REPLAY-TESTED / REAL-BROKER VALIDATION PENDING

### M5
**Verdict:** NO-GO

Still no execution.

### M6
**Verdict:** FEATURE FREEZE

### M7
**Verdict:** SAFETY WORK ONLY

### P2
**Verdict:** NO-GO

---

## 11. What NOT to do

Do not use the newly available Windows/MT5 environment as an excuse to jump to orders.

Still prohibited:

```text
order_send
automatic flatten against broker
live account
strategy tuning on first real quotes
ICT v2
multi-broker work
```

First prove the read path.

---

## 12. Required next action order

```text
1. Developer processes feedback.1.7 and feedback.1.8.
2. Correct status: demo account EXISTS; terminal verification pending.
3. Owner logs demo account into MT5 on Windows.
4. Run full Windows + PostgreSQL gate.
5. Run sanitized MT5 first-contact probe.
6. Resolve APP-015 / enum semantics from real observation.
7. Record actual hedging/netting mode.
8. Record actual EUR/USD broker symbol/spec.
9. Persist real ticks/bars.
10. Test continuous read and reconnect.
11. Build/validate reconciliation.
12. Run CI on the remote.
13. Review/approve domain contracts and close M0.
14. Reconcile M2 gate against build.md.
15. feedback.2.0 before any execution adapter or order_send.
```

---

## 13. Next review

The next normal review is:

```text
feedback.1.9.md
```

Trigger it after the first real MT5 read-only evidence and/or CI/domain-contract/M2 gate closure package.

The mandatory major execution review remains:

```text
feedback.2.0.md
```

before any `order_send`, including demo.

---

## 14. Final reviewer statement

The project is no longer waiting on infrastructure.

The Windows host exists, the repo runs there, the MT5 dependency is installed, and the read-only adapter/probe are ready.

The next valuable step is not another simulated feature.

It is:

```text
real Pepperstone MT5 terminal
→ read-only observation
→ sanitized evidence
→ persisted real data
→ reconciliation
```

That is the correct path forward.
