# feedback.1.7.md — M2 Closure Boundary & M1 First-Contact Readiness

**Project:** Autonomous EUR/USD Trading Platform  
**Review version:** 1.7  
**Date:** 2026-08-23  
**Reviewed artifact:** `status.md` v1.3  
**Previous review:** `feedback.1.6.md`  
**Overall verdict:** **GO WITH CONDITIONS**  
**M1 verdict:** **READY FOR FIRST CONTACT ONCE WINDOWS HOST EXISTS**  
**M2 verdict:** **READY FOR FORMAL GATE RECONCILIATION**  
**M5 / P2 verdict:** **NO-GO**  
**Scope note:** This review evaluates the supplied `status.md` and its stated evidence. Source code and test output were not independently inspected.

---

## 1. Executive review

This cycle contains substantial engineering progress.

Since review 1.6, the project now reportedly has:

- raw tick persistence;
- raw bar persistence;
- a versioned normalized bar pipeline;
- Alembic migrations and restore proof;
- one-EUR/USD-exposure enforcement;
- intraday entry policy and overnight-breach HALT;
- honest supervisor uncalibrated-state reporting;
- account currency/leverage guard;
- a read-only MT5 adapter that refuses execution by construction.

The test count is now 645 and the project explicitly states that nothing has yet run against a real MetaTrader 5 terminal.

That is the correct use of the waiting period before the Windows host is available.

---

## 2. Accepted progress from review 1.6

### F-021 — stale current-state sections
**Status:** SUBSTANTIALLY CLOSED

Most current-state sections now reflect the new architecture and owner decisions. Remaining account-related stale statements are covered by F-026.

### F-022 — raw market-data persistence
**Status:** CLOSED BASED ON DOCUMENTED EVIDENCE

Ticks and bars are persisted on the ordinary path, including warm-up windows. The normalized pipeline tracks origin/version and detects gaps, out-of-order arrivals, duplicates, crossed quotes and conflicting bars.

### F-023 — migrations / restore discipline
**Status:** CLOSED FOR CURRENT STAGE

Alembic is now the durable deployment path and a database restore is reportedly proven to reproduce the run.

### F-024 — supervisor frequency check
**Status:** CLOSED

The frequency and confidence checks are now explicitly uncalibrated rather than falsely reported as passing.

### F-025 — one exposure / intraday policy
**Status:** PARTLY CLOSED, CORRECTLY DEFERRED WHERE EXECUTION IS REQUIRED

Implemented:

- max one EUR/USD exposure;
- entry refusal outside the intraday window;
- HALT if exposure survives rollover.

Automatic flatten remains M5 work because it requires a real execution path.

---

## 3. Read-only MT5 adapter review

The M1 adapter design is directionally strong.

Good choices include:

- deferred `MetaTrader5` import;
- read-only adapter refuses mutating methods by construction;
- MT5 `None`/`False` failure convention becomes explicit exceptions;
- broker symbol is discovered rather than hard-coded;
- `positions_get(None)` is distinguished from a genuinely empty book;
- float values are converted at the adapter boundary before entering Decimal domain models.

The project correctly notes that a fake terminal proves compatibility with the developer's interpretation of the API, not with a real MT5 terminal.

Treat first contact as discovery.

---

## 4. New findings

### F-026 — `status.md` incorrectly says the Pepperstone demo account does not exist

**Severity:** MEDIUM / CURRENT-STATE ACCURACY  
**Status:** OPEN

The project owner has already created the Pepperstone MT5 demo account and supplied these non-secret configuration facts:

```text
Server:   PepperstoneUK-Demo
Leverage: 1:30
Currency: EUR
```

The full login must stay out of review/status documents.

Several current sections still say the demo account does not exist or still needs to be created.

**Required update**

Represent the state as:

```text
Demo account: CREATED
Terminal verification: PENDING WINDOWS/MT5
Server claim: PepperstoneUK-Demo
Currency claim: EUR
Leverage claim: 1:30
Account mode: UNKNOWN until account_info()
Entity/legal contracting entity: UNVERIFIED
```

---

### F-027 — M2 may be held open by an M1/real-feed condition

**Severity:** MEDIUM / GATE SEMANTICS  
**Status:** OPEN FOR SPEC RECONCILIATION

`status.md` says all M2 deliverables now exist, but keeps M2 `NOT PASSED` solely because the data has not yet come from a real feed.

That is correct only if `build.md` explicitly makes real-feed evidence an M2 acceptance criterion.

Otherwise this repeats the class of error from F-010: a later-stage dependency is silently added to an earlier gate.

**Required action**

Compare M2 against the exact `build.md` acceptance criteria.

- If real feed is explicitly required by M2, keep M2 open and cite the criterion.
- If real feed belongs to M1/MT5 integration, qualify M2 based on its own acceptance evidence and mark real-feed validation separately as pending M1.

---

### F-028 — Pepperstone entity ambiguity must not block technical first contact, but must block legal/live assumptions

**Severity:** MEDIUM  
**Status:** OPEN

Current facts:

```text
Owner decision label: Pepperstone EU
Actual demo server:   PepperstoneUK-Demo
```

The server name is evidence of the server name, not proof of the legal contracting entity.

For read-only M1 technical discovery on a demo account, this ambiguity does not need to block first contact once Windows exists.

The legal entity must be resolved before any live-account decision or policy assumption that depends on regulation/account terms.

Record the entity as `UNVERIFIED`, not guessed from the server string.

---

### F-029 — Paper campaign header is stale

**Severity:** LOW / DOCUMENTATION  
**Status:** OPEN

The paper-campaign section still leaves Broker and Server blank even though the project now knows:

```text
Broker brand: Pepperstone
Demo server: PepperstoneUK-Demo
```

Populate those non-secret facts while keeping campaign status `NOT STARTED`.

Do not put the account login there.

---

## 5. Human decisions/actions now

### A. Windows host
This is the main practical blocker to first contact.

Once available:

```text
install MT5
log into the existing demo account
run the Python gateway
perform read-only discovery
```

No execution adapter.

### B. Account mode
Still intentionally unknown.

Read it from the real terminal:

```text
hedging
or
netting
```

Support exactly the observed v1 mode initially.

### C. M0 CI exception
Still awaiting owner decision.

Recommended:

```text
CI workflow authored;
runner execution deferred while project remains local.
```

Actual CI execution becomes mandatory before `feedback.2.0` / first `order_send`.

### D. Domain-contract approval
Still open. Provide the current contract definitions/spec summary for explicit review before formal M0 closure.

### E. Risk budget
Current paper risk numbers remain placeholders. Before M5/P2, the owner still needs to approve:

```text
risk per trade
max daily loss
max drawdown
```

---

## 6. What the developer should do while Windows is still unavailable

Useful remaining non-Windows work is now narrower:

1. Fix F-026 / F-029 current-state documentation.
2. Reconcile the M2 gate against `build.md` (F-027).
3. Store feature values, not only their hash, if that remains part of the intended audit/replay design.
4. Prepare a first-contact checklist/report template for MT5 discovery.
5. Prepare tests that ingest captured MT5 fixture data once first contact occurs.
6. Prepare reconciliation contracts/interfaces, without inventing broker behavior.
7. Prepare the domain-contract package for M0 human review.

Do not create more simulated broker sophistication merely to stay busy.

---

## 7. First-contact acceptance checklist

When Windows becomes available, the first M1 run should be read-only and record at least:

```text
terminal connected
terminal version
account is demo
actual server
account currency
leverage
hedging/netting mode
trade permissions reported by terminal
canonical EUR/USD mapping
actual broker symbol
digits
point
tick size/value
contract size
volume min/max/step
stops level
freeze level
filling/order modes exposed
current bid/ask
M5 bars
tick history availability
positions_get behavior when flat
reconnect behavior after intentional interruption
```

Persist the resulting real ticks/bars immediately.

Any mismatch with fake-terminal assumptions becomes a documented deviation and a real-data test fixture.

---

## 8. Gate decisions

### M0
**Verdict:** READY FOR CLOSURE ONCE CONTRACT REVIEW + CI EXCEPTION/EXECUTION ARE DECIDED

### M1
**Verdict:** ADAPTER READY FOR FIRST CONTACT, GATE NOT PASSED

No real terminal evidence yet.

### M2
**Verdict:** READY FOR FORMAL GATE RECONCILIATION

Do not automatically hold it open for real-feed evidence unless `build.md` explicitly requires that.

### M3
**Verdict:** CORRECTNESS ONLY

### M4
**Verdict:** REPLAY-TESTED / NO PROMOTION

### M5
**Verdict:** NO-GO

### M6
**Verdict:** FEATURE FREEZE

### M7
**Verdict:** SAFETY WORK ONLY

### P2
**Verdict:** NO-GO

---

## 9. Required next action order

```text
1. Update account state: demo exists, terminal verification pending (F-026).
2. Reconcile M2 gate with build.md (F-027).
3. Keep Pepperstone entity UNVERIFIED until evidence exists (F-028).
4. Populate non-secret broker/server facts in paper campaign header (F-029).
5. Decide M0 CI exception.
6. Prepare domain contracts for human/reviewer approval.
7. Finish any remaining audit-data gap such as feature-value persistence.
8. Provision Windows x86-64 host when available.
9. Log into existing Pepperstone demo account.
10. Run read-only MT5 first-contact checklist.
11. Record account mode and actual EUR/USD symbol/specs.
12. Persist real ticks/bars immediately.
13. Build/validate reconciliation from observed MT5 behavior.
14. `feedback.2.0.md` before any execution adapter or `order_send`.
```

---

## 10. Next review

The next normal review should be:

```text
feedback.1.8.md
```

Trigger it on either:

- formal M2 gate reconciliation / M0 closure package; or
- first real MT5 read-only evidence.

The mandatory major review remains `feedback.2.0.md` before any `order_send`.

---

## 11. Final reviewer statement

The project has used the Windows waiting period well.

The application can now record:

```text
what it saw
what it decided
why it decided
what safety state existed
what risk budget remained
```

and recover that state after restart.

The remaining uncertainty is no longer primarily software architecture.

It is broker reality.

That is exactly where the project should be before first MT5 contact.
