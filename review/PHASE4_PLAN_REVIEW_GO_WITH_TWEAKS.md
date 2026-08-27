# Phase 4 plan — GO met verplichte tweaks

**Project:** Crumblr — Autonomous EUR/USD Trading Platform  
**Onderwerp:** Review op voorgesteld plan voor Phase 4 — non-sending execution engineering  
**Besluit:** **JA / GO, mits onderstaande aanpassingen vóór implementatie worden verwerkt**

De voorgestelde richting is in de basis goed en mag worden uitgevoerd.

Belangrijk uitgangspunt blijft:

> **Trader proposes. Risk Engine constrains. Supervisor vetoes. Execution Service executes. Reconciliation verifies.**

De aparte Execution Service is de juiste architectuur. `LiveDecisionOrchestrator` blijft MT5-vrij en `order_send` blijft in deze fase fysiek onmogelijk.

Wel moeten vóór implementatie onderstaande punten worden aangepast.

---

## 1. FINAL execution-time Risk: volume behouden of BLOCK

De voorgestelde logica waarbij FINAL Risk opnieuw een kleiner volume kan kiezen, wordt niet geaccepteerd.

De bestaande regel blijft:

```text
eerder goedgekeurd volume
→ exact behouden
OF
→ BLOCK
```

FINAL Risk doet dus geen nieuwe sizing.

FINAL Risk controleert of het reeds goedgekeurde vaste volume bij de actuele situatie nog veilig is:

```text
actuele executable prijs
actuele spread
actuele equity
actuele exposure
actuele stop
broker/instrument state
trading window
reconciliation
```

Als het volume nog veilig is:

```text
PASS met hetzelfde volume
```

Als het niet meer veilig is:

```text
BLOCK
```

Nooit omhoog en ook niet omlaag resizen in FINAL Risk.

---

## 2. Scheid `order_check` preflight van latere `order_send` toestemming

Gebruik niet één configflag zoals `demo_order_submission_approved` om in deze fase alleen `order_check` mogelijk te maken.

Maak twee aparte gates.

### Nu

```text
ExecutionPreflightGate
```

Mag alleen bepalen of de veilige preflightketen mag doorgaan naar:

```text
FINAL Risk
→ ApprovedOrder
→ order_check
```

### Later

```text
SubmissionGate
```

Deze gate mag pas OPEN worden voor echte order submission wanneer minimaal alles tegelijk waar is:

```text
environment = DEMO
account/server verified
market data HEALTHY
broker state fresh + COMPLETE
reconciliation MATCHED
safety RUNNING
owner-approved risk policy
execution adapter explicitly enabled
terminal AlgoTrading enabled
feedback.2.0 GO
```

In de huidige Phase-4 slice blijft:

```text
order_send = technisch onmogelijk
```

---

## 3. `ApprovedOrder` pas construeren ná FINAL Risk

De volgorde moet zijn:

```text
TradeIntent
→ intent-time Risk PASS
→ Supervisor APPROVE
→ fresh execution context
→ FINAL Risk PASS
→ ApprovedOrder
→ order_check
```

Niet:

```text
ApprovedOrder
→ FINAL Risk
```

`ApprovedOrder` moet semantisch betekenen dat de volledige pre-execution approval chain is doorlopen.

---

## 4. Execution persistence als immutable request + append-only events

Niet uitsluitend:

```text
1 ExecutionResult per order_request_id
+ ON CONFLICT DO NOTHING
```

Voorkeursmodel:

```text
execution_requests
```

met één immutable logische orderrequest:

```text
order_request_id
request fingerprint
decision/capsule identity
created_at
```

en daarnaast append-only:

```text
execution_events
```

bijvoorbeeld:

```text
REQUEST_CLAIMED
GATE_BLOCKED
FINAL_RISK_BLOCKED
ORDER_CHECKED
ORDER_CHECK_REJECTED

later:
SUBMISSION_STARTED
SUBMITTED
BROKER_ACK
FILLED
RECONCILED
CLOSED
```

Belangrijke invariant:

```text
zelfde order_request_id + andere inhoud
→ ERROR / HALT / fail closed
```

Nooit stilletjes negeren via `ON CONFLICT DO NOTHING`.

---

## 5. Claim/persist vóór externe brokerinteractie

De Execution Service moet vóór een broker-side call eerst de request duurzaam registreren en atomisch claimen.

Gewenste volgorde:

```text
derive order_request_id
→ persist immutable request
→ atomic claim
→ verify state
→ broker interaction
→ append outcome
```

Dit voorkomt dat twee workers of een restart hetzelfde request onafhankelijk verwerken.

---

## 6. Oude shadow approvals mogen nooit later uitvoerbaar worden

Voeg harde execution eligibility toe.

Een oude capsule met:

```text
Risk PASS
Supervisor APPROVE
```

mag niet ineens uitvoerbaar worden wanneer later de execution gate wordt aangezet.

Een request moet minimaal voldoen aan:

```text
decision is recent enough
decision ontstond na execution activation/watermark
decision hoort bij huidige strategy/config version
decision valt binnen toegestaan execution/trading window
decision is nog niet verlopen
```

Oude approvals worden expliciet:

```text
EXPIRED / NOT_EXECUTABLE
```

---

## 7. FINAL Risk gebruikt een verse synchrone broker/markt-observatie

Voor de finale execution-check niet:

```text
gebruik persisted state als die "fresh enough" lijkt
```

maar:

```text
Execution Service
→ fresh MT5 tick
→ fresh account observation
→ fresh positions
→ fresh pending orders
→ current InstrumentSpec
→ persist exact broker snapshot
→ reconcile dit snapshot
→ FINAL Risk
```

Prijsbasis:

```text
BUY  → actuele ask
SELL → actuele bid
```

Controleer minimaal:

```text
spread
equity
margin/context
open exposure
positions/pending orders
stop distance
instrument spec
trading window
reconciliation
```

---

## 8. Naamgeving adapter

Voorkeur:

```text
OrderCheckMt5Gateway
```

of:

```text
ExecutionPreflightGateway
```

boven:

```text
ExecutionCapableMt5Gateway
```

In deze slice mag de adapter:

```text
read broker state
order_check
```

en moet hij blijven weigeren:

```text
order_send
cancel_pending_orders
close_all_positions
```

---

# Wat uit het oorspronkelijke plan wél expliciet akkoord is

De volgende architectuurkeuzes moeten behouden blijven:

```text
LiveReader
= observe/persist

LiveDecisionOrchestrator
= decide

Execution Service
= preflight/execute later
```

Verder akkoord:

- `LiveDecisionOrchestrator` blijft vrij van MT5 execution dependencies.
- De Execution Service wordt een aparte class/process/orchestrator.
- `order_check` mag als echte MT5 server-side dry run worden gebouwd.
- `order_send` blijft in deze fase fysiek geweigerd.
- `cancel_pending_orders` en echte flatten submission mogen in deze slice nog geweigerd blijven.
- Execution-resultaten worden niet achteraf in een sealed DecisionCapsule gemuteerd.
- ADR-001 wordt geïmplementeerd door bestaande Risk-logica opnieuw te gebruiken, niet door een tweede Risk Engine te bouwen.
- De shipped configuratie blijft fail-closed.
- Er blijven structurele tests die bewijzen dat `order_send` nergens bereikbaar is.

---

# Gewenste aangepaste Phase-4 flow

```text
sealed approved decision
        ↓
Execution Service
        ↓
derive durable order_request_id
        ↓
persist immutable execution request
        ↓
atomic claim
        ↓
eligibility / freshness check
        ↓
fresh MT5 broker + market observation
        ↓
persist broker snapshot
        ↓
reconciliation
        ↓
ExecutionPreflightGate
        ↓
FINAL Risk
        ↓
PASS?
 ├─ NO  → append BLOCK outcome
 └─ YES
        ↓
construct ApprovedOrder
        ↓
order_check
        ↓
append ORDER_CHECK result
        ↓
STOP
```

In deze Phase-4 slice:

```text
NO order_send
NO real order ticket
NO fill
NO broker exposure
```

---

# Later, vóór eerste DEMO-order

De volgende stukken blijven verplicht vóór `feedback.2.0`:

```text
automatic flatten submission
submission idempotence / ambiguous-result recovery
post-execution reconciliation
owner-approved risk policy
last-entry cutoff
mandatory flatten deadline
HALT-reset authority
terminal AlgoTrading gate
explicit execution enablement
full F-049 submission multi-gate
```

Pas daarna:

```text
feedback.2.0
→ GO
→ eerste gecontroleerde autonome DEMO-canary
```

---

# Definitief besluit

**JA — plan accepteren met bovenstaande tweaks.**

De developer mag Phase 4 bouwen zodra deze correcties in het implementatieplan zijn verwerkt.

De belangrijkste niet-onderhandelbare punten zijn:

```text
1. FINAL Risk = same volume or BLOCK
2. ApprovedOrder pas ná FINAL Risk
3. immutable request + append-only execution events
4. persist/claim vóór brokerinteractie
5. historische approvals nooit later uitvoerbaar
6. finale execution-context altijd fresh en coherent
7. order_check-gate ≠ toekomstige order_send-gate
```

Met deze aanpassingen is de richting **GO**.
