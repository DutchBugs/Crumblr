# feedback.1.0.md — Architecture & Safety Review

**Project:** Autonomous EUR/USD Trading Platform  
**Review version:** 1.0  
**Review date:** 2026-08-17  
**Reviewed artifacts:** `build.md`, `status.md`  
**Overall verdict:** **GO WITH CONDITIONS**  
**Paper execution / P2 verdict:** **NO-GO**

## 1. Versioning policy

Gebruik de volgende review-ticker:

- `feedback.1.0.md` — eerste formele reviewbaseline.
- `feedback.1.1.md`, `feedback.1.2.md`, enz. — vervolgreviews binnen dezelfde ontwikkelfase.
- `feedback.2.0.md` — nieuwe grote reviewfase, bijvoorbeeld vóór de eerste MT5 demo-order of vóór de autonome paper-campaign.
- Oude feedbackbestanden worden nooit overschreven of verwijderd.

Aanbevolen map:

```text
review/
├─ feedback.1.0.md
├─ feedback.1.1.md
├─ feedback.1.2.md
└─ feedback.2.0.md
```

## 2. Executive review

De engineeringrichting is sterk en de kernarchitectuur is intact:

```text
Trading Agent
    ↓
TradeIntent
    ↓
Deterministic Risk Engine
    ↓
Evaluator / Supervisor
    ↓
Execution Engine
    ↓
MT5 Gateway
    ↓
Broker
```

De implementatie loopt op sommige punten echter voor op de formele gates. Replay, risk, strategy en supervisor-prototypes bestaan al, terwijl de echte MT5 Gateway, PostgreSQL-persistence en broker reconciliation nog niet bewezen zijn.

**Reviewer-richting:** verdere infrastructuurontwikkeling mag doorgaan, maar strategie-uitbreiding moet tijdelijk stoppen. Prioriteit verschuift naar persistence, broker truth, reconciliation, durable safety state en fail-closed supervisor behavior.

## 3. Severity model

```text
CRITICAL
Kan schijnveiligheid of ongecontroleerde exposure veroorzaken.
Blokkeert paper/live execution.

HIGH
Materiële architectuur-, risk- of operationszwakte.
Moet vóór de relevante promotion gate worden opgelost.

MEDIUM
Geen direct exposure-risico, maar verlaagt correctheid of traceerbaarheid.

LOW
Proces-, kwaliteit- of maintainabilityverbetering.
```

## 4. Findings

### F-001 — Milestone status en prototype maturity worden door elkaar gebruikt

**Severity:** HIGH  
**Status:** OPEN

Het project staat formeel nog op M0, terwijl M3, M4, M6 en M7 al gedeeltelijk zijn geïmplementeerd.

Parallel prototypen is toegestaan, maar `BUILDING` mag niet hetzelfde betekenen als gate qualification.

**Required change**

Maak onderscheid tussen:

```text
Implementation maturity
Promotion / gate qualification
```

Aanbevolen maturity states:

```text
SPECIFIED
IMPLEMENTED
UNIT-TESTED
REPLAY-TESTED
MT5-INTEGRATED
PAPER-VALIDATED
SHADOW-VALIDATED
```

Voorbeeld:

```text
M4 Risk Engine
Implementation maturity: REPLAY-TESTED
Gate qualification:      NOT PASSED
Reason:                   No real MT5/broker validation yet
```

**Acceptance**
- `status.md` onderscheidt maturity en gate status.
- Unit/replay tests zijn nooit op zichzelf voldoende voor promotion.

---

### F-002 — Supervisor gebruikt hard-coded veilige state

**Severity:** CRITICAL  
**Status:** OPEN  
**Blocks:** M5 / P2

De supervisor krijgt momenteel veilige placeholders voor state die nog niet werkelijk bestaat, zoals:

```text
open_incident_count = 0
reconciliation_matched = True
```

Daardoor kan een `APPROVE` eruitzien alsof controles daadwerkelijk zijn uitgevoerd.

**Required change**

Gebruik expliciete state:

```text
ReconciliationStatus:
- MATCHED
- MISMATCHED
- UNKNOWN

IncidentStatus:
- CLEAR
- ACTIVE
- UNKNOWN
```

Gedrag:

```text
UNKNOWN reconciliation → VETO of HALT
UNKNOWN incident state  → VETO
```

Kernregel:

> Absence of evidence is not evidence of safety.

**Acceptance**
- Geen permissive defaults voor safety-critical input.
- Missing information wordt expliciet als UNKNOWN gerepresenteerd.
- UNKNOWN kan geen normale approval opleveren.
- Fail-closed gedrag is getest.

---

### F-003 — Kill-switch state is niet persistent over restarts

**Severity:** HIGH  
**Status:** OPEN  
**Suggested issue:** `APP-003`

Een restart kan de huidige in-memory HALT wissen.

Onveilige flow:

```text
critical condition
→ HALT
→ restart
→ HALT forgotten
→ trading could resume
```

**Required change**

Persist system safety state.

Startup moet standaard zijn:

```text
NEW ORDERS = DISABLED
```

totdat minimaal dit bekend en geldig is:

```text
configuration
account identity
environment
previous HALT state
broker state
reconciliation
incident state
```

Onbekende vorige shutdown-state:

```text
FAIL CLOSED
```

**Acceptance**
- HALT overleeft process restart.
- HALT overleeft machine restart.
- Restart kan trading niet automatisch hervatten.
- Alleen operator kan resetten.
- Reset wordt geaudit.

---

### F-004 — Strategieontwikkeling loopt voor op evidence

**Severity:** MEDIUM / HIGH  
**Status:** OPEN

`ict_v1` bevat inmiddels aanzienlijke market-structure-logica terwijl nog geen echte EUR/USD-evidence beschikbaar is.

**Required decision**

Freeze verdere feature-uitbreiding van `ict_v1`.

Wel toegestaan:

```text
bug fixes
determinism fixes
contract fixes
tests op constructed scenarios
```

Niet aanbevolen:

```text
ICT v2
extra confirmations
new discretionary concepts
parameter optimization
ML overlay
synthetic profit optimization
```

Classificatie:

```text
baseline_v1 = infrastructure benchmark
ict_v1      = research challenger
```

Geen van beide is production champion.

---

### F-005 — `status.md` bevat inconsistente maturity/progress-signalen

**Severity:** MEDIUM  
**Status:** OPEN

Voorbeelden:
- Platform-progress wordt op verschillende plekken anders gerapporteerd.
- Risk Engine staat als substantieel gebouwd, terwijl de detailchecklist unchecked is.

**Required change**

Gebruik voor kritieke functies meerdere maturity-stappen, bijvoorbeeld:

```text
Risk-based sizing

[x] implemented
[x] unit tested
[x] replay tested
[ ] MT5 integrated
[ ] paper validated
```

**Acceptance**
- Progress-percentages zijn consistent of worden verwijderd.
- Detailstatus en milestone-overzicht spreken elkaar niet tegen.
- `implemented` is niet hetzelfde als `validated`.

---

### F-006 — Local Git is geïnitialiseerd ondanks eerdere afspraak

**Severity:** LOW / PROCESS  
**Status:** DECISION REQUIRED

De eerdere afspraak was lokaal werken zonder Git-init. Git is inmiddels wel geïnitialiseerd.

Niet terugdraaien; leg het bewust vast:

```text
Local Git repository: ALLOWED
Remote repository:    DEFERRED until collaboration
```

**Acceptance**
- Deviation/decision is vastgelegd.
- Geen remote vereist.
- Secrets, runtime data en logs blijven uitgesloten.

---

### F-007 — Execution-time risk revalidation ontbreekt

**Severity:** HIGH DESIGN RECOMMENDATION  
**Status:** ADR REQUIRED BEFORE M5

Tussen initiële risk approval en `order_send` kan state wijzigen:

```text
price
spread
equity
positions
open risk
kill switch
instrument specification
account identity
market data freshness
```

**Proposed architecture**

```text
Trading Agent
      ↓
Intent Risk Check
      ↓
Supervisor
      ↓
FINAL Execution-Time Risk Check
      ↓
order_check
      ↓
order_send
```

De tweede risk-check mag alleen gelijk of restrictiever zijn.

De supervisor mag nooit een deterministic risk block overrulen.

**Acceptance**
- ADR aangemaakt vóór M5.
- Veranderde state vlak voor execution kan een intent blokkeren.
- Expired intents kunnen niet executen.

---

### F-008 — FLATTEN / position-control path ontbreekt

**Severity:** HIGH  
**Status:** OPEN  
**Blocks:** M5

Vereiste afzonderlijke controls:

```text
HALT NEW ORDERS
CANCEL PENDING ORDERS
FLATTEN POSITIONS
```

Deze acties mogen niet automatisch aan elkaar gekoppeld zijn.

**Acceptance**
- Nieuwe orders kunnen worden gestopt.
- Pending orders kunnen expliciet worden gecanceld.
- Posities kunnen expliciet worden geflattened.
- Elke actie wordt apart gelogd en geautoriseerd.

## 5. Positive findings

Behoud de volgende keuzes:

- Trading Agent blijft geïsoleerd van MT5.
- Agents hebben geen broker credentials.
- `BrokerPort` scheidt simulated en real execution.
- Typed en restrictive domain contracts.
- `Decimal` op monetary boundaries.
- Fail-closed configuration.
- Content hashes voor provenance/version identity.
- Deterministic replay wordt getest.
- Property-based testing is aanwezig.
- Fault injection vindt echte defects.
- Synthetic P&L wordt niet als bewijs gezien.
- LLM heeft geen execution authority.
- Live trading blijft verboden.
- Production readiness blijft 0%.

## 6. Gate decisions

### M0 — Engineering baseline

**Verdict:** GO WITH CONDITIONS

Nog nodig:
- domain-contract human review;
- status semantics corrigeren;
- local Git decision vastleggen;
- CI labelen als lokaal groen / remote niet uitgevoerd.

### M1 — MT5 read-only gateway

**Verdict:** GO WHEN DEPENDENCIES AVAILABLE

Benodigd:
- broker;
- demo account;
- exacte MT5 server;
- Windows x86-64 MT5 host.

### M2 — PostgreSQL / event persistence

**Verdict:** GO NOW

Niet wachten op Windows-host.

Prioriteit:

```text
events
decision capsules
config versions
instrument specs
system safety state
incidents
account snapshots
reconciliation results
```

### M3 — Replay / backtest

**Verdict:** PROTOTYPE CONTINUATION ALLOWED

Geen performance-promotion op synthetische data.

### M4 — Risk Engine

**Verdict:** PROTOTYPE CONTINUATION ALLOWED

Huidige maturity: replay-tested, niet broker-validated.

### M6 — Trading Agent

**Verdict:** FEATURE FREEZE

Behouden:
- `baseline_v1`
- `ict_v1`

Volgende stap is evidence, niet meer strategieconcepten.

### M7 — Evaluator / Supervisor

**Verdict:** GO FOR SAFETY WORK ONLY

Prioriteit:
1. UNKNOWN states;
2. echte incident-state;
3. echte reconciliation-state;
4. persistent HALT;
5. post-trade evaluation;
6. statistical monitor later.

Geen extra LLM-authority.

### M5 — Paper execution

**Verdict:** NO-GO

Minimum prerequisites:
- real MT5 Gateway;
- PostgreSQL persistence;
- broker reconciliation;
- persistent HALT;
- explicit UNKNOWN supervisor behavior;
- real incident inputs;
- real reconciliation inputs;
- demo/live account guard;
- idempotent execution;
- cancel-pending control;
- flatten-position control;
- execution-time risk revalidation decision.

### P2 — Autonomous MT5 demo campaign

**Verdict:** NO-GO

Replay mag doorgaan; unattended paper trading nog niet.

## 7. Required action order

```text
1. Fix status semantics
2. Persist kill-switch/system safety state
3. Implement PostgreSQL event persistence
4. Replace safe supervisor placeholders with UNKNOWN state
5. Record local-Git process decision
6. Create ADR for execution-time risk revalidation
7. Select broker/demo account
8. Provision Windows MT5 host
9. Implement read-only MT5 Gateway
10. Implement broker reconciliation
11. Build cancel/flatten operational controls
12. Re-review before M5
```

## 8. Directive for the development agent

> **Reviewer decision: GO WITH CONDITIONS. Stop further strategy expansion. Prioritize platform truthfulness, persistence and broker integration.**
>
> 1. Separate implementation maturity from promotion-gate status in `status.md`.
> 2. Add `APP-003` HIGH: persist HALT state and fail closed on startup.
> 3. Replace hard-coded safe supervisor inputs with explicit UNKNOWN state.
> 4. Fix inconsistent progress/maturity reporting.
> 5. Freeze `ict_v1` feature development until real EUR/USD data is available.
> 6. Build PostgreSQL persistence now.
> 7. Make M1 MT5 Gateway the next primary real integration milestone.
> 8. Create an ADR for deterministic execution-time risk revalidation before `order_send`.
> 9. Record local Git as allowed, remote Git as deferred.
> 10. Keep M5/P2 at NO-GO until all execution-safety blockers are closed.

## 9. Next review

Volgende bestand:

```text
feedback.1.1.md
```

Trigger:
- bovenstaande findings zijn verwerkt; of
- betekenisvolle M1/M2-progress is gereed.

Iedere volgende review moet eerdere findings expliciet behandelen:

```text
F-001 OPEN / CLOSED
F-002 OPEN / CLOSED
F-003 OPEN / CLOSED
...
```

Aanbevolen major bump:

```text
feedback.2.0.md
```

vóór de eerste MT5 demo-order of vóór start van de autonome paper-campaign.

## 10. Final reviewer statement

De engineeringkwaliteit is veelbelovend, maar het project moet nu voorkomen dat de strategie slimmer wordt voordat het systeem betrouwbaarder wordt.

De hoogste prioriteit is momenteel:

```text
persistence
broker truth
reconciliation
durable safety state
fail-closed supervisor behavior
execution correctness
```

Trading intelligence kan daarna verder worden uitgebreid.

Het systeem moet eerst operationeel voorspelbaar worden onder failure.
