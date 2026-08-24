# feedback.1.5.md — Owner Decisions & Developer Handover

**Project:** Autonomous EUR/USD Trading Platform  
**Review / handover version:** 1.5  
**Date:** 2026-08-18  
**Previous review:** `feedback.1.4.md`  
**Overall direction:** **PROCEED**  
**M5 / P2 verdict:** **NO-GO**  
**Purpose:** Record owner decisions and give the next developer an unambiguous execution order.

---

## 1. Owner decisions — now fixed for v1

The project owner has approved the following decisions.

### O-001 — Primary M1 broker

```text
Primary broker: Pepperstone EU
Environment:    DEMO
Platform:       MetaTrader 5
Market:         EUR/USD
```

Pepperstone is the first integration target for M1.

Do **not** build multi-broker routing in v1.

A second broker may later be used as a benchmark, but Pepperstone is the reference implementation for the first real MT5 integration.

### Important implementation rule

Do not permanently hard-code public example server names or an assumed EUR/USD broker symbol.

After the actual demo account is created, record from that account:

```text
exact MT5 server
account number / identity reference
account trade mode
hedging or netting mode
actual EUR/USD broker symbol
digits
point
tick size
tick value
contract size
volume min/max/step
stops level
freeze level
filling modes
swap fields
trade mode
```

Secrets remain outside Git, logs, prompts and review documents.

### Demo-account lifecycle

Pepperstone currently documents MT4/MT5 demo accounts as expiring after 60 days unless the client has a funded live account and Pepperstone converts the demo to non-expiry.

Therefore the M1/M2 implementation must not assume that a demo account identity lives forever.

Treat account replacement as a normal controlled configuration change.

Official sources checked 2026-08-18:

- https://pepperstone.com/en-eu/ways-to-trade/demo-accounts/
- https://pepperstone.com/en-eu/platforms/trading-platforms/mt5/

---

### O-002 — Initial strategy timeframe

```text
Decision timeframe: M5
```

Five-minute bars are the v1 decision cadence.

This is now the baseline assumption for:

```text
feature generation
ICT setup evaluation
killzones
supervisor frequency calibration
replay
paper evidence
```

Do not introduce M1/M15/H1 strategy variants yet.

Tick data may still be collected and used for execution/data-quality work; M5 refers to the **decision timeframe**, not a ban on higher-resolution market data.

---

### O-003 — Intraday-only v1

```text
Overnight positions: NOT ALLOWED in v1
```

The first autonomous version must close or refuse exposure before the defined trading-day/overnight boundary.

The exact flatten/entry-cutoff times must be made explicit before M5 paper execution.

Rationale:

```text
simpler risk accounting
less swap/financing complexity
less overnight gap exposure
cleaner first paper campaign
```

Do not silently interpret “intraday” as “close at midnight UTC”. The operational FX-day/session boundary must be explicitly defined.

---

### O-004 — One EUR/USD exposure at a time

```text
Maximum concurrent EUR/USD exposure: 1
```

For v1, do not allow stacking multiple directional EUR/USD trades.

Expected policy:

```text
no EUR/USD exposure
→ BUY or SELL may be proposed

existing EUR/USD exposure
→ no second exposure may be opened
```

A `CLOSE` / `FLAT` path may reduce or remove the existing exposure.

This rule applies regardless of whether the Pepperstone account itself is technically configured as hedging or netting.

---

## 2. Account-mode decision still pending

### Q2 — Hedging or netting

Do **not** guess.

When the Pepperstone MT5 demo account is created:

1. inspect the actual account mode;
2. record it in the account/instrument configuration;
3. choose that mode as the **only supported v1 account model**;
4. build reconciliation tests for that model.

Do not implement both modes in parallel merely for future flexibility.

The one-exposure policy from O-004 remains the business rule either way.

---

# 3. Current technical position

The platform currently has a tested PostgreSQL persistence foundation, but the running orchestrator does not yet use it as its normal path.

Current state:

```text
Persistence schema / journal       BUILT
Decision Capsule store             BUILT
Composite safety-state store       BUILT
PostgreSQL invariant tests         PASSING

Orchestrator → persistence wiring  NOT COMPLETE
Persistent recovery path           NOT COMPLETE
Real MT5 Gateway                   NOT BUILT
Broker reconciliation              NOT BUILT
order_send                          NOT PERMITTED
```

Do not skip the persistence-wiring step in order to get to MT5 faster.

---

# 4. Immediate developer work — execute in this order

## Step 1 — Wire M2 persistence into the orchestrator

Close `D-030` / review F-018.

Required:

```text
ReplayOrchestrator / application flow
        ↓
EventJournal
        ↓
PostgreSQL

Decision sealing
        ↓
CapsuleStore
        ↓
PostgreSQL

Startup / kill switch
        ↓
CompositeSafetyStateStore
        ↓
file latch + journal authority
```

The normal application path must use the same persistence abstractions that were already tested separately.

### Required evidence

```text
run
→ persist
→ stop process
→ restart
→ recover state
```

and:

```text
in-memory replay result
==
replay reconstructed from persisted journal
```

where equality is defined by the existing deterministic evidence contract.

---

## Step 2 — Make risk-session state restart-safe

Close review F-019 before M5.

The current equity/daily-loss state may not reset in a permissive direction after restart.

Persist or reconstruct:

```text
trading-day/session id
session-start equity
realized P&L
high-water mark
drawdown state
daily-loss budget consumed
open-risk state
```

Once MT5 exists, broker/account history should be part of reconstruction rather than blindly trusting local state.

Default on disagreement:

```text
UNKNOWN
→ HALT
→ reconcile
```

---

## Step 3 — Clean current-state documentation

Update `FEEDBACK.md` / `DEVIATIONS.md` to reflect the implemented M2 foundation.

Do not erase historical problems.

Use:

```text
Original gap
Current state
Remaining gap
Gate affected
```

especially for:

```text
D-011
D-012
D-027
D-028
D-030
```

---

## Step 4 — Prepare Pepperstone MT5 demo environment

Once the owner has created the account, collect and configure:

```text
broker = Pepperstone EU
platform = MT5
environment = demo
exact server = from actual account
account mode = inspect, do not assume
canonical symbol = EUR/USD
broker symbol = discover dynamically
```

Do not store credentials in the repository.

---

## Step 5 — Provision Windows x86-64 MT5 host

The official MetaTrader5 Python integration remains isolated in the Windows MT5 Gateway process.

The rest of the system must continue to depend on `BrokerPort` / typed boundaries rather than importing `MetaTrader5` directly.

---

## Step 6 — Build M1 read-only MT5 Gateway

First real-broker milestone is read-only.

Implement and prove:

```text
initialize
login / connection validation
version
terminal_info
account_info
last_error

symbols_get / symbol_select
symbol_info
symbol_info_tick
copy_rates
copy_ticks where useful

health
reconnect
shutdown
```

No `order_send`.

No live account.

No order mutation.

---

## Step 7 — Validate v1 assumptions against Pepperstone

Confirm from real demo state:

```text
M5 bars available
EUR/USD symbol mapping
account mode
volume constraints
stop/freeze constraints
filling modes
tick continuity
spread distribution
session behavior
reconnect behavior
```

If the broker contradicts an assumption, record a deviation/ADR rather than silently changing the architecture.

---

## Step 8 — Build reconciliation

The system must compare local authoritative state with MT5 state.

Examples:

```text
local says no position
MT5 says position exists
→ HALT

local says position exists
MT5 says none
→ HALT

account identity changes
→ HALT

unknown broker state
→ HALT
```

Reconcile before any future execution path is enabled.

---

# 5. Decisions that are deliberately NOT being made yet

These remain owner decisions before the relevant gate.

### Before M5/P2

```text
Q7  paper risk per trade
Q8  max drawdown
Q12 production HALT reset authority
```

Do not convert provisional configuration values into approved policy merely because they already exist in YAML.

### Intraday cut-off

O-003 establishes intraday-only trading, but the exact:

```text
last new-entry time
mandatory flatten time
session boundary
```

must still be specified and tested before M5.

---

# 6. Strategy restrictions remain unchanged

Do not add trading sophistication while the platform is crossing from simulated state to broker state.

Keep:

```text
baseline_v1 = infrastructure benchmark
ict_v1      = research challenger
champion    = none
```

No:

```text
ICT v2
new confirmations
parameter optimisation on synthetic data
ML overlay
new timeframe strategy
multi-market work
```

Real EUR/USD evidence comes first.

---

# 7. Gate position after these owner decisions

```text
M0  → near closure; CI/human contract-review housekeeping remains
M1  → NOW UNBLOCKED ON BROKER CHOICE; account creation + Windows host still needed
M2  → persistence foundation built; orchestrator integration still required
M3  → correctness/replay only
M4  → replay-tested; no broker validation
M5  → NO-GO
M6  → FEATURE FREEZE
M7  → SAFETY WORK ONLY
P2  → NO-GO
LIVE → NO-GO
```

Pepperstone selection removes the broker-choice ambiguity, but **does not** make M1 complete.

---

# 8. Mandatory review before execution

The next normal review after persistence integration may be:

```text
feedback.1.6.md
```

If this document itself is treated as 1.5 and persistence wiring is the next implementation cycle.

Before the first real or demo call to:

```text
order_send
```

the project requires:

```text
feedback.2.0.md
```

That review must inspect MT5/demo integration evidence, not merely simulation or tracker claims.

---

# 9. Developer handover — one-screen version

If you read only one section, read this:

```text
OWNER DECISIONS

Broker:              Pepperstone EU demo, MT5
Decision timeframe:  M5
Overnight positions: NO
EUR/USD exposures:   max 1
Hedging/netting:     inspect actual demo account; support one mode in v1

DO NEXT

1. Wire PostgreSQL into the orchestrator
2. Prove restart/recovery from persisted state
3. Make daily-loss/equity risk state restart-safe
4. Create/configure Pepperstone MT5 demo
5. Provision Windows x86-64 MT5 host
6. Build read-only MT5 Gateway
7. Discover actual broker/account/symbol specs
8. Build reconciliation

DO NOT

- call order_send
- start autonomous paper trading
- add strategy features
- add markets
- support multiple brokers
- assume demo/live/account metadata

NEXT MAJOR SAFETY GATE

feedback.2.0.md before first order_send
```
