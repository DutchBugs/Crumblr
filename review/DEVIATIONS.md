# Deviations from build.md

`build.md` is the specification and is not edited to match the code. Where the
implementation departs from it, the departure is recorded here with its
rationale, so the gap stays visible and reviewable instead of being quietly
normalised.

Each entry is stable and citable (`D-001`). Status is one of:

- **deliberate** — a considered choice; challenge the rationale, not the fact
- **provisional** — correct enough for now, must change before a named gate
- **pending** — specified but not yet built

Last updated 2026-08-24 (M1 first-contact runbook; Windows host available).

---

## 0. The ICT entry model

Added at the user's instruction: trading must follow the ICT scheme, enforced
as conditions. This section exists because the methodology is the largest
single source of judgement calls in the codebase.

### D-020 — ICT terms had to be given one exact definition each
- **Status:** deliberate
- **Spec:** build.md §9.3 stage A suggests trend-following, breakout,
  mean-reversion or a volatility filter. ICT is none of these and is not
  mentioned.
- **Code:** `src/crumblr/trading_agent/{structure,imbalance,liquidity,sessions,ict}.py`
- **Why:** ICT is normally applied discretionarily — a trader looks at a chart
  and judges. A platform cannot judge, so each term is pinned to one reading:
  a *swing* is a strict fractal with `strength` bars either side; a *sweep* is
  a bar that pierces a still-untouched level and closes back inside it; a
  *structure shift* is a close beyond the swing that was current at that time,
  where the structure before the break was not already running that way; a
  *fair value gap* is a three-bar imbalance whose middle bar ranged at least
  1.5×ATR; *premium/discount* and *OTE* are measured on the impulse leg from
  the swept level to the extreme reached.
- **Watch for:** these are choices, not facts. Each is documented at its
  definition site. A reviewer disagreeing with one is disagreeing with a
  judgement call that has been made explicit — which is the point of writing
  them down. Changing one changes what `ict_v1` means and requires a new
  version.

### D-021 — Every clause is separately switchable
- **Status:** deliberate
- **Code:** `IctConditions` — ten booleans, all required by default except
  `require_order_block` and `require_liquidity_target`
- **Why:** "required condition" is only meaningful if removing it changes the
  outcome. Toggling one and re-running measures what that clause contributes;
  a fused rule cannot be measured at all.
- **Watch for:** the toggles are an experiment tool, not a production dial.
  Relaxing one in production creates a different strategy that needs its own
  evaluation record. Two are off by default for stated reasons — a valid
  displacement leg does not always leave an opposing candle, and a setup with
  no qualifying liquidity target falls back to a reward multiple rather than
  declining.

### D-022 — Killzones use exchange-local time, not fixed UTC windows
- **Status:** deliberate
- **Code:** `src/crumblr/trading_agent/sessions.py`, via `zoneinfo`
- **Why:** killzones are defined in New York local time. A hard-coded UTC
  window is wrong for about half the year, and wrong by a full hour in windows
  that are only two to three hours wide. The United States and Europe also
  switch on different dates, so for a few weeks a year the two sessions sit at
  an unusual offset to each other.
- **Watch for:** covered by tests that assert the same UTC hour lands in the
  right zone in both January and July.

### D-023 — ICT produces very few setups on synthetic data
- **Status:** provisional — cannot be resolved without real data
- **Observed:** roughly 3 intents per 12,000 M5 bars (about six weeks)
- **Why:** the model requires a full confluence, and a random walk with a
  session volatility profile rarely produces one. Whether that rate is right
  is not answerable on this data.
- **Watch for:** **this is the most likely place for pressure to distort the
  work.** A sparse strategy makes for a poor demo, and the temptation is to
  loosen conditions until the numbers look better — which would be fitting to
  noise. The rate has deliberately not been tuned. The model is instead tested
  against hand-constructed setups in `tests/unit/test_ict_strategy.py`, where
  the correct answer is known by construction, and the replay tests that need
  trade volume run `baseline_v1` instead.

### D-024 — Three defects found while building the model, all corrected
- **Status:** deliberate (recorded so the reasoning is auditable)
- Entry timing: the first version checked location at the moment of
  displacement, when price is at the extreme of the move. Every long was
  rejected for being in premium — correctly. The missing clause was the entry
  trigger: price must have retraced back *into* the gap. Fixed by
  `require_price_in_zone`, with the setup lookback widened from 8 to 24 bars so
  the setup stays armed while that retracement plays out.
- Sweep semantics: pools were built from every swing in 400 bars, so 87% of
  killzone windows appeared to contain a sweep. A level already traded through
  no longer holds resting liquidity; filtering those out took the rate to about
  12%.
- Reference range: premium, discount and OTE were measured against an
  arbitrary recent swing range rather than the impulse leg being retraced,
  which made both filters meaningless.

### D-025 — `baseline_v1` is retained as the benchmark
- **Status:** deliberate
- **Why:** build.md §9.2 requires that a model unable to beat a simple baseline
  after costs is not promoted. Removing the baseline would remove the
  comparison. The strategy registry holds both; `strategy_id` in configuration
  decides which one executes, and the supervisor independently gates which ids
  it will approve.

---

## A. Contracts made stricter than specified

These tighten `build.md` rather than relax it. A reviewer should still check
that the tightening cannot reject something legitimate.

### D-001 — `stop_loss_price` is mandatory on BUY/SELL intents
- **Status:** deliberate
- **Spec:** §6.2 declares `stop_loss_price: Decimal | None`
- **Code:** `src/crumblr/domain/models.py` — a directional `TradeIntent`
  without a stop fails validation
- **Why:** §6.2 also states that final quantity is derived from stop distance.
  An intent with no stop is therefore not merely risky, it is unsizeable — the
  risk engine has no denominator. Rejecting it at the contract boundary is
  clearer than rejecting it later as `INVALID_STOP`.
- **Watch for:** this forecloses strategies that manage exit purely by time or
  signal reversal. If such a strategy is wanted, the contract needs a separate
  exit-policy field rather than a nullable stop.

### D-002 — `requested_risk_fraction` is nullable, and must be null for FLAT
- **Status:** deliberate
- **Spec:** §6.2 declares it a required `Decimal`
- **Code:** `TradeIntent.requested_risk_fraction: RiskFraction | None`
- **Why:** §6.2 also permits `side: FLAT`, which is a close instruction and
  creates no new exposure. A required positive risk fraction on a FLAT intent
  would be meaningless data that a later reader could misinterpret as intent to
  open.
- **Watch for:** two shapes of the same contract. The validators enforce that
  directional and FLAT intents are each internally consistent, but a reader
  skimming the model sees an optional field.

### D-003 — `decision_hash` is computed, not supplied
- **Status:** deliberate
- **Spec:** §6.2 lists `decision_hash: str` among the agent's fields
- **Code:** a `@computed_field` derived from the decision content, excluding
  `intent_id`
- **Why:** a hash the producer supplies is a claim; a hash the contract derives
  is a fact. This makes two identical decisions hash identically regardless of
  who generated them, which is what supports duplicate detection and replay
  verification. Integrity against storage tampering is then a comparison
  between the stored column and the recomputed value, done at the repository
  boundary.
- **Watch for:** the repository layer that performs that comparison does not
  exist yet — see D-012. Until it does, the hash proves reproducibility but not
  that a stored row is unaltered.

### D-004 — `MarketSnapshot` carries a `snapshot_id`
- **Status:** deliberate
- **Spec:** §6.1 does not list one
- **Code:** required field, derived in replay from symbol and event time
- **Why:** §11 requires the capsule to reference `market_snapshot_id`. Without
  an id on the snapshot itself there is nothing stable to reference.

### D-005 — The reason-code vocabulary is larger than the specified list
- **Status:** deliberate
- **Spec:** §6.3 gives nine codes as examples
- **Code:** `src/crumblr/domain/enums.py` defines roughly thirty-five
- **Why:** §8.1 lists around twenty distinct blocking conditions and §10.1 adds
  more. Collapsing them onto nine codes would mean an operator reading
  `INVALID_STOP` cannot tell a policy-floor violation from a broker
  stops-level violation. Each code names one condition.
- **Watch for:** codes are persisted, so renaming one is a migration. The set
  should be reviewed before the paper campaign starts, because alerting will be
  built on it.

### D-006 — One event type beyond the specified thirteen
- **Status:** deliberate
- **Spec:** §23 lists thirteen core events
- **Code:** adds `DecisionCapsuleSealed`
- **Why:** §11 requires the capsule to be persisted as an immutable record. The
  act of sealing it is itself an auditable event.

---

## B. Structure

### D-007 — Package is `src/crumblr/…`, not `src/…`
- **Status:** deliberate
- **Spec:** §5 shows `src/domain/`, `src/risk/`, and so on
- **Code:** everything sits under a `crumblr` package
- **Why:** the specified layout claims top-level import names like `domain`,
  `risk` and `api`, which collide easily and make the code non-installable
  alongside anything else. Sub-package names and responsibilities are otherwise
  exactly as specified.

### D-008 — `FeatureSnapshot` lives in `trading_agent`, not `domain`
- **Status:** provisional — revisit at M7
- **Why:** it is currently produced and consumed only by the agent. Other
  components reference it by id and hash, which the capsule already carries.
- **Watch for:** when the evaluator computes drift on feature distributions it
  will need this type, at which point it belongs in `domain`.

---

## C. Deliberate simplifications in the prototype

The load-bearing weaknesses. A reviewer assessing whether performance numbers
mean anything should start here.

### D-009 — Market data is synthetic, not historical
- **Status:** provisional — must change before gate P1
- **Spec:** §13 assumes historical data and, for short horizons, tick data
- **Code:** `src/crumblr/market_data/synthetic.py`, a seeded random walk with
  scripted volatility regimes
- **Why:** the platform had to be exercisable before a broker is chosen, and a
  generator with injectable faults can produce a stale tick or a spread spike
  on demand, which historical data cannot.
- **Watch for:** **no performance number from this system currently means
  anything.** Any P&L is a property of the seed. The generator validates
  control flow, not edge.

### D-010 — The fill model resolves intrabar ordering by assumption
- **Status:** provisional — must change before gate P1
- **Spec:** §13.1 requires modelling bid/ask, spread, commission, swaps,
  latency, slippage, rejection and partial fills
- **Code:** `src/crumblr/mt5_gateway/simulated.py` models bid/ask, spread and
  slippage. It does **not** model swap, commission, latency, order rejection or
  partial fills. When a bar touches both stop and target, it assumes the stop.
- **Why:** the pessimistic assumption is the honest one without tick data, and
  the missing costs belong to the M3 cost model.
- **Watch for:** unmodelled costs bias results optimistically. Swap in
  particular matters for anything held overnight, which §29 Q4 has not yet
  answered.

### D-026 — "Daily" loss was measured from the start of the run
- **Status:** fixed 2026-08-17, recorded because the failure mode is instructive
- **Was:** `EquityLedger.start_new_session()` existed but nothing called it, so
  `max_daily_loss` measured loss since the run began. It was a total-loss cap
  wearing a daily label: once tripped it could never clear.
- **Now:** the orchestrator rolls the baseline when the FX trading day changes,
  at 17:00 New York time rather than midnight UTC.
- **Watch for:** the equivalent question for a live system is what happens
  across a restart. With the ledger in memory (D-011) a restart resets the
  daily baseline, which is the wrong direction to fail in.

### D-028 — Four of the supervisor's seven checks could not fire
- **Status:** PARTLY RESOLVED 2026-08-17 (review F-002)
- **Spec:** §10.1 lists the pre-trade questions the evaluator must answer
- **Original gap:** in the running configuration only three of seven checks
  could produce a veto, and realistically only one did. Two of the four inert
  ones were worse than inert: the orchestrator passed a hard-coded `0` for
  active incidents and a hard-coded `True` for reconciliation, so the
  supervisor reported approvals as though reconciliation had been checked when
  the answer had been fixed before the question was asked.

  | Check | Live now? | Note |
  |---|---|---|
  | Strategy id allowed | yes | |
  | Model version allowed | yes | |
  | Unknown regime | yes | the one that fires in practice |
  | Confidence in range | **no** | policy band is 0.0–1.0 and the contract already constrains confidence to exactly that range |
  | Signal frequency | **no** | threshold 20/hour against an M5 cadence that permits at most 12 (D-015) |
  | Active incident | partially | no longer hard-coded; `IncidentStatus.UNKNOWN` halts, but no incident register exists to report anything else |
  | Reconciliation mismatch | partially | no longer hard-coded; `ReconciliationStatus.UNKNOWN` halts, and replay reports `MATCHED` because the simulated broker's book is the only book |

- **Current state:** the two hard-coded inputs are gone. Both are tri-state
  with an explicit `UNKNOWN`, both default to it, and `UNKNOWN` fails closed
  above the policy switch (`tests/unit/test_fail_closed_safety.py`).
- **Remaining gap:** the confidence band and the frequency threshold are still
  configured to values nothing can fall outside — see D-015 and EV-002. They
  need recalibrating against the M5 decision cadence now fixed by owner
  decision O-002, not deleting. Neither the incident register nor the
  reconciliation loop exists, so those two checks can currently only say
  "known clear" in replay or "unknown" everywhere else.
- **Gate affected:** P2. A supervisor whose vetoes cannot fire is not evidence
  of a supervisor that works.

### D-027 — The risk engine is a gate, not a monitor
- **Status:** PARTLY RESOLVED 2026-08-17 (review F-008)
- **Spec:** §8.2 requires three *separately controlled* operator actions —
  `HALT NEW ORDERS`, optional `CANCEL PENDING`, and `FLATTEN POSITIONS`
- **Original gap:** `KillSwitch` implemented the first. The second and third
  did not exist. Every rule in `risk/policies.py` is evaluated *before* an
  order is placed, so once a position was open the only thing that closed it
  was the stop or target lodged with the broker.
- **Current state:** the three controls exist in `risk/operator_controls.py`,
  separately authorised, separately logged, and tested for decoupling in both
  directions. A halt still deliberately does not close positions — §8.2 is
  explicit that combining the three into one ambiguous button is wrong.
- **Remaining gap:** nothing *automatic* manages an open position — no
  trailing stop, no time-based exit — and there is no interface through which
  an operator would actually invoke the controls (M8). None of it has been
  exercised against a real broker. Owner decision O-003 adds a requirement
  here: intraday-only trading needs a mandatory flatten before the session
  boundary, and that boundary is not yet defined or tested.
- **Gate affected:** M5 is blocked on the flatten path being real; M8 on the
  interface. O-003's cut-off times must be specified before M5.

### D-029 — Alembic was not set up; the schema was created from nothing
- **Status:** RESOLVED 2026-08-18 (review 1.4 F-020, review 1.6 F-023)
- **Spec:** build.md §4.1 lists Alembic for "versioned schema changes"
- **Original gap:** `persistence/engine.py:bootstrap_schema` ran
  `metadata.create_all`. Defensible while the database was disposable; it
  stopped being so the moment D-030 closed and ordinary runs began writing a
  journal, sealed capsules, risk-session snapshots and raw market data.
- **Current state:** baseline revision `ce70efeb9fe9` creates the eight tables
  the code expects. `build_durable_runtime(create_schema=True)` runs the
  migrations rather than `create_all`, so the ordinary local path exercises the
  same mechanism a deployment would. `tests/integration/test_migrations.py`
  asserts that a migrated database and the application's metadata do not
  disagree — the failure mode that otherwise passes every test until a query
  reaches a column no migration made — and that a `pg_dump` can be restored
  into a database from which the run is still reconstructable.
- **Remaining gap:** `bootstrap_schema` still exists and the test fixtures
  still use it, because dropping and recreating per test is faster than
  migrating. That is only defensible while the two produce the same schema,
  and there is a test asserting exactly that. No backup *schedule* exists —
  the restore is proven, the operational routine is not.
- **Gate affected:** M5. Backup and restore discipline is a precondition for
  paper evidence being worth keeping.

### D-030 — The orchestrator did not write to the journal
- **Status:** RESOLVED 2026-08-18 (review 1.5 step 1, finding F-018)
- **Original gap:** `persistence/` was implemented and tested against a real
  PostgreSQL, and nothing used it. `application/orchestration.py` accumulated
  capsules in a Python list and constructed a bare `KillSwitch()`. The M2
  guarantees were real and unreachable from the running system.
- **Current state:** the orchestrator writes every stage of the §3 flow
  through a `RunRecorder`. `JournalRecorder` commits a window's events and its
  sealed capsule in one transaction; a halt is flushed immediately rather than
  waiting for the window to close. `application/bootstrap.py` assembles the
  journal, the ADR-002 composite safety store and the risk-session store, and
  `KillSwitch.on_startup` recovers from them. Event ids are derived from the
  event type, its window and its payload, so re-running an identical replay
  converges on the history already stored instead of doubling it.
  Evidence: `tests/integration/test_orchestrator_persistence.py` (11 tests),
  `tests/integration/test_run_survives_restart.py` (8 tests, real child
  processes).
- **Remaining gap:** warm-up windows and raw market data are still not
  journalled (D-031); schema migration is still absent (D-029); nothing here
  has met a broker.
- **Gate affected:** M2. This was its acceptance criterion — "events can be
  replayed in original order" — and it is now met for the decision path.

### D-031 — The journal carries decisions; the market store carries observations
- **Status:** RESOLVED 2026-08-18 for storage (review 1.6 F-022); the feature
  values remain outside it
- **Spec:** build.md §26 M2 lists "raw market storage" and a "normalized bar
  pipeline"; §12.1 requires raw and derived data to be stored separately
- **Original gap:** the `events` table recorded the decision flow and nothing
  about the market those decisions were made from. Two consequences: a window
  that ended before the strategy had enough history produced no event at all,
  so the system had no record of ever having seen those bars; and
  `MarketSnapshotReady` was a registered event type with no producer.
- **Current state:** `market_ticks` and `market_bars` hold what the system saw,
  written by the orchestrator on the ordinary path, for **every** window
  including the warm-up ones. Each row names its source and, for a bar, its
  origin — broker, aggregated, or synthetic — so a derived bar can never be
  mistaken for a delivered one. `market_data/pipeline.py` aggregates ticks into
  bars with a versioned transformation identity that includes the price basis,
  and detects gaps, out-of-order arrivals, duplicates and crossed quotes
  without inventing a bar to cover any of them.

  The separation §12.1 asks for is in the tables, not in the writer:

      event journal = what the system did
      market store  = what the system saw

- **Remaining gap:** three things.
  1. **Feature values are still not stored.** A capsule carries the feature set
     version and a hash of the values; the values themselves exist only in the
     process that computed them. The hash proves a later recomputation matches,
     which is not the same as being able to see what the strategy saw.
  2. `MarketSnapshotReady` still has no producer. A snapshot carries its whole
     rolling window of up to 400 bars, so emitting one per decision would store
     the same bar several hundred times; the bars are in `market_bars` and the
     window is reconstructable from them.
  3. The tick→bar pipeline is fully tested but **not on the replay path**,
     because the generator emits bars directly rather than a tick stream. It is
     the path M1 will use for Pepperstone ticks and has never processed a real
     one.
- **Gate affected:** M2's data deliverables are now met for ticks and bars.
  Feature storage is M7 work. The pipeline meets a real feed at M1.

### D-032 — Risk-session recovery trusts local state, not broker history
- **Status:** provisional — must close before M5
- **Spec:** review 1.5 §4 step 2 — "once MT5 exists, broker/account history
  should be part of reconstruction rather than blindly trusting local state"
- **Gap:** `risk.session.recover_session` reconstructs the daily-loss and
  drawdown state from the persisted record plus the account's *current* equity
  and position count. It does not read the broker's trade history, so a loss
  realised while the platform was down is invisible to it.
- **What is in place:** the recovery is written so that it can only ever be
  more conservative than the record — worst-case values are seeded from the
  record and then widened by the live reading, never narrowed. A position-count
  disagreement between the record and the account halts in both directions,
  which is the reconciliation rule of review 1.5 step 8 in miniature.
- **Watch for:** the seam is the `live_equity` / `live_open_positions`
  arguments. They are answered today by the simulated broker and must be
  answered at M5 by reconciliation against MT5 — not by whichever source is
  more convenient at the call site.
- **Gate affected:** M5.

### D-033 — The intraday flatten is detected, not performed
- **Status:** provisional — must close before M5
- **Spec:** owner decision O-003; review 1.6 F-025; `ADR-004`
- **Gap:** the system refuses new entries outside the trading window and halts
  when exposure survives the flatten deadline, but it does not *close* the
  position. Closing needs the execution path, which is M5.
- **Why it is shaped this way:** refusing to open is safe and can ship now;
  promising to close is a promise this system cannot yet keep. A policy that
  claimed otherwise would read as though positions were being managed out when
  nothing was managing them.
- **Watch for:** the halt is the whole safety story today. If someone reads
  "intraday-only" as "the platform closes positions at the boundary", they are
  reading something that is not there yet. ADR-004 §5 lists what M5 must add,
  including the behaviour when a flatten fails and when the broker is
  unreachable near the deadline.
- **Gate affected:** M5.

### D-035 — The MT5 gateway is written but has never met a terminal
- **Status:** PARTIALLY RESOLVED 2026-08-24 — one real connection made (account
  read, symbol resolution, instrument spec, position read all succeeded
  against a real Pepperstone terminal; status.md §13). **Still open:**
  continuous bar/tick read and observed reconnect behaviour (HANDOVER.md
  §4.5) — one successful connection is not M1 in full
- **Code:** `mt5_gateway/client.py`, `mt5_gateway/readonly.py`; 41 tests in
  `tests/unit/test_mt5_readonly_gateway.py`
- **Original gap:** no MT5 adapter existed at all; `BrokerPort` had only a
  simulated implementation.
- **Current state:** the read-only adapter is implemented and tested against a
  fake terminal that mimics MT5's actual convention — failures return
  `None`/`False` with the reason in `last_error()`. The tests prove the adapter
  logic: account verification, symbol discovery, float-to-Decimal conversion,
  error propagation, and that execution is structurally impossible.
- **Remaining gap:** **nothing here has run against MetaTrader 5.** Spread,
  fills, reconnect behaviour, the real symbol name, the account mode and the
  actual field names on `account_info`/`symbol_info` are all broker facts that
  a fake cannot establish. The maturity ladder calls this IMPLEMENTED and
  UNIT-TESTED, never MT5-INTEGRATED.
- **Watch for:** the fake was written from the documented MT5 API, not from
  observation. Where the real terminal disagrees, the fake is wrong and the
  adapter probably is too. First contact should be treated as discovery, and
  any disagreement recorded as a deviation rather than patched silently
  (review 1.5 step 7).
- **Gate affected:** M1.

### D-036 — M1 is read-only by construction, not by configuration
- **Status:** deliberate
- **Code:** `ReadOnlyMt5Gateway.order_send` and its siblings raise
  `ReadOnlyViolationError`
- **Why:** build.md milestone 1 is read-only and review 1.5 step 6 repeats it.
  A flag or a config switch would make "M1 cannot trade" a promise about how
  the class is used. Refusing in the method makes it a property of the type: no
  code path through this gateway reaches the broker's order interface.
- **Watch for:** M5 must not be built by relaxing these methods. Execution
  belongs in a separate adapter that satisfies the same port, so the read-only
  one stays available for shadow mode (build.md §14 gate P3), where reading
  without submitting is the entire point.

### D-037 — MT5 integer enums are stored as strings without being decoded
- **Status:** RESOLVED 2026-08-24 — confirmed against a real terminal, then fixed
- **Original gap:** `ReadOnlyMt5Gateway.instrument` set
  `filling_modes=(str(info.filling_mode),)` and `trade_mode=str(info.trade_mode)`.
  MetaTrader 5 reports both as integers. `filling_mode` is a *bitmask*
  (`SYMBOL_FILLING_FOK=1`, `IOC=2`, `BOC=4`), so stringifying it yields the
  digit, not a name. `trade_mode` is likewise an enum where `4` means FULL.
- **What first contact showed:** the real EURUSD instrument on
  `PepperstoneUK-Demo` reported `filling_mode=2`, `trade_mode=4`. Decoded per
  the documented mapping: `filling_mode=2` → `IOC`; `trade_mode=4` → `FULL`.
  Both match what the documentation predicted — the mapping itself was never
  the problem, only the fact that the gateway skipped it.
- **Current state:** the decode tables and functions moved to
  `src/crumblr/mt5_gateway/enums.py`, a single module imported by both the
  gateway and `scripts/mt5_probe.py` — the two no longer carry copies that can
  drift apart, which is how this shipped wrong the first time. `instrument()`
  now stores `decode_filling_modes(...)` and `decode_enum(...)` output
  instead of `str(int)`. The fake terminal in
  `tests/unit/test_mt5_readonly_gateway.py` now supplies real integers
  (`filling_mode=3`, `trade_mode=4`) instead of the pre-decoded strings that
  let the old bug pass its own tests; three new tests assert the decode,
  including an unrecognised-value case (`UNKNOWN(n)`, never a guess).
- **Remaining gap:** none for this specific mapping. The broader D-035 gap —
  everything else about this terminal's behaviour beyond one connection —
  still stands.
- **Gate affected:** M1. Closed as a blocker for M5 as well: a filling mode
  the broker does not allow would otherwise have been rejected at
  `order_send` on the strength of a digit that only accidentally looked
  plausible.

### D-034 — The Pepperstone entity is unresolved
- **Status:** RESOLVED FOR THE DEMO ENVIRONMENT ONLY 2026-08-24, by O-005 —
  **NOT resolved for any future live account**
- **Spec:** owner decision O-001, refined by O-005 (review 1.9 §2, F-028)
- **Original gap:** O-001 named **Pepperstone EU**. The MT5 server supplied by
  the owner on 2026-08-18 is **`PepperstoneUK-Demo`**. Those are different
  entities.
- **What first contact showed:** the real account's `company` field reads
  **`Pepperstone Limited`** (review 1.7/1.8 F-028's required evidence). This
  session recorded that as evidence without inferring a conclusion from it
  (review 1.8 §7 warns explicitly against that inference) — the reviewer/owner
  decision O-005 that follows is what actually closes this, not the evidence
  alone.
- **O-005, the resolution:** for the **current demo/development environment
  only**, the entity is **Pepperstone Limited (UK)**. This amends O-001's
  "Pepperstone EU" shorthand for demo purposes; it does not rewrite history
  (O-001 stays in the record as originally written) and it does **not**
  pre-select the entity for a live account, which requires its own review
  against the owner's residence and the actual live-account documentation
  before any live decision is made.
- **Why it matters:** the regulator differs, and with it the retail leverage
  cap and the swap treatment. The configured guard expects 1:30, which is the
  ESMA/FCA retail cap in both cases today — so the discrepancy does not
  currently show up as a mismatch, which is exactly why it needed resolving
  deliberately rather than being noticed later.
- **Code:** `config/paper.yaml` records the supplied server, resolved entity
  and the O-005 scope note. `account_guard.expected_currency` and
  `expected_leverage` are checked against `account_info()` at M1, so a wrong
  account halts rather than trades — and did not, on this run.
- **Watch for:** the resolution is still only `company` + `server`, not an
  account statement or a contract note — sufficient for a demo integration
  decision, explicitly not sufficient for a live one. Any future live-account
  work must treat the entity as open again and re-verify from live
  documentation, not carry O-005 forward by assumption.
- **Gate affected:** M1 — closed by O-005. Live promotion — open again by
  design; see O-005 above.

### D-038 — The continuous reader checks margin mode outside `AccountState`
- **Status:** deliberate, scoped narrower than review 1.9 F-034 could be read
- **Spec:** review 1.9 F-034 requires reconnect revalidation to include
  "account mode" alongside server/currency/leverage/demo status
- **Gap:** `AccountState` (the contract `ReadOnlyMt5Gateway.account()` returns
  and `AccountGuardConfig` checks against) does not carry `margin_mode` — only
  the first-contact probe reads it, ad hoc, the way `application/live_reader.py`
  now also does. A config-declared `expected_margin_mode` on
  `AccountGuardConfig`, checked inside `_verify_account` the same way currency
  and leverage are, would be the more complete version of this — every caller
  of `gateway.account()` would benefit, not just the reader.
- **Why it is shaped this way:** extending a persisted domain contract
  (`AccountState`) and a config schema is a larger, more consequential change
  than this session judged the moment called for. `LiveReader` instead
  captures the margin mode it observes on its *first* successful connection
  and compares every reconnect against that, which is enough to catch a
  changed account without a new config field or contract change.
- **Watch for:** this means margin-mode drift is only caught *after* a
  successful first connection in this process's lifetime — a wrong mode
  present from the very first connect would not be flagged as a mismatch, only
  recorded as the new baseline. `AccountGuardConfig.expected_margin_mode` is
  the fix if that gap ever matters in practice; O-005 recorded the current
  value (`RETAIL_HEDGING`) so it is on record either way.
- **Gate affected:** M1 (F-034's revalidation requirement — met, in a
  narrower form than the fullest reading of the finding).

### D-039 — MT5 tick and bar timestamps are assumed UTC, unverified
- **Status:** provisional — closes when the soak test (review 1.9 §5) runs
- **Spec:** build.md §12.2 requires UTC-only internal timestamps
- **Gap:** `ReadOnlyMt5Gateway.ticks()`/`.bars()` convert `copy_ticks_from`'s
  `time`/`time_msc` and `copy_rates_from_pos`'s `time` with
  `datetime.fromtimestamp(..., tz=UTC)` — treating the terminal's clock as UTC
  outright. `positions()` already made the identical assumption for
  `position.time` before this session, unremarked; this session's code
  inherits it rather than resolving it, because nothing has read continuous
  data from the real terminal yet to check it against.
- **Why it matters:** if the Pepperstone server clock is not UTC (some MT5
  servers run at UTC+2/+3, broker-dependent), every tick and bar timestamp
  this reader persists would carry a constant offset — silently correct-looking,
  wrong by a fixed amount, and exactly the kind of thing D-035 exists to name
  before it is trusted.
- **What is in place:** nothing beyond the assumption. The soak test is the
  first opportunity to compare a stored tick's `event_time_utc` against the
  wall clock and settle this the way D-037 was settled — by observation, not
  by re-reading documentation more carefully.
- **Watch for:** record the comparison in `status.md` §13 when the soak test
  runs. If the offset is nonzero, this becomes a real defect in `readonly.py`,
  not a documentation update.
- **Gate affected:** M1.

### D-040 — `Decimal(repr(...))` broke on real MT5 data: numpy 2.x scalars are not plain floats
- **Status:** RESOLVED 2026-08-24 — found on the first real soak attempt, fixed
  the same session
- **Spec:** build.md §21 / domain money rules — MT5 floats convert to
  `Decimal` via `repr`, never via a direct `Decimal(float)` construction
- **Original gap:** `_to_decimal` in `mt5_gateway/readonly.py` checked
  `isinstance(value, float)` and called `Decimal(repr(value))`. That was
  written and tested against `account_info()`/`symbol_info()`, which return
  named tuples with genuine Python `float` attributes, and against test fakes
  built the same way. `copy_ticks_from` and `copy_rates_from_pos` — this
  session's new code, first run for real 2026-08-24 — return **numpy
  structured arrays**. `numpy.float64` subclasses Python's `float`, so the
  `isinstance` check passed, but numpy 2.x gives scalars their own `__repr__`:
  `repr(numpy.float64(1.167))` is `"np.float64(1.167)"`, which
  `Decimal()` cannot parse. Every single tick crashed the reader on `bid`.
- **Why it wasn't caught first**: every unit test for `ticks()`/`bars()`
  built rows from `SimpleNamespace` with plain Python floats — a faithful
  stand-in for the account/symbol calls this pattern was proven against, and
  an unfaithful one for `copy_ticks_from`/`copy_rates_from_pos`, which no
  test had exercised with a real or realistic numpy array before the soak.
  The exact class of gap D-035 names for the whole adapter, materialised.
- **Fix:** `_to_decimal` now does `Decimal(repr(float(value)))` — `float()`
  strips the numpy wrapper (or is a no-op on an already-plain float) before
  `repr` ever sees it. Two new tests build genuine `numpy.dtype` structured
  arrays (not `SimpleNamespace`) for both `ticks()` and `bars()`, so this
  cannot regress silently again.
- **Gate affected:** M1. Blocked the first soak-test attempt outright.

### D-041 — A large tick batch could exceed PostgreSQL's bound-parameter limit
- **Status:** RESOLVED 2026-08-24 — found on the second real soak attempt
  (immediately after D-040), fixed the same session
- **Spec:** build.md §26 M2 — raw tick storage, with no documented ceiling on
  batch size implied or intended
- **Original gap:** `MarketDataStore._record_ticks` built one `INSERT`
  statement for the entire batch via `pg_insert(market_ticks).values(rows)`.
  `market_ticks` binds 14 parameters per row; PostgreSQL refuses any
  statement bound to more than 65535 parameters total — a hard ceiling of
  4681 rows per statement. `LiveReader`'s default tick lookback (5 minutes)
  on its first read, against a real, actively-quoting EUR/USD feed, returned
  enough ticks to cross it: `psycopg.OperationalError: sending query and
  params failed: number of parameters must be between 0 and 65535`.
- **Why it wasn't caught first:** every existing test inserted a handful of
  ticks — nothing exercised a batch anywhere near the thousands a live feed
  can return in one read. Pre-existing code, written and tested against
  synthetic replay ticks (one bar's worth of ticks at a time) and never
  exercised against continuous real-market volume until this session's first
  two soak attempts.
- **Fix:** `_record_ticks` now chunks the batch into `INSERT`s of at most
  2000 rows (`_TICK_INSERT_CHUNK_SIZE`, comfortably under the 4681-row hard
  ceiling), looping within the same connection/transaction. A new
  integration test inserts 4001 ticks — deliberately more than two chunk's
  worth — in one `record_ticks` call and asserts all of them land.
- **F-038 (review 1.11):** the reviewer correctly pointed out that "runs
  inside the same connection" was an assumption, not a proven contract —
  a shared connection does not by itself prove a failure partway through
  rolls back everything already sent. Now proven: **contract A, batch
  atomic**. `test_a_failure_partway_through_a_multi_chunk_batch_rolls_back_the_whole_batch`
  (`tests/integration/test_market_data_store.py`) injects a failure into the
  second of two chunks against a real PostgreSQL connection and asserts zero
  rows from the batch survive — chunk 1's already-sent rows are rolled back
  with it, because `record_ticks` never commits a caller-supplied connection
  and the whole call runs in one transaction. Documented at the point of
  implementation in `MarketDataStore._record_ticks`'s docstring.
- **Watch for:** `record_bars` was not affected — it already inserts one row
  at a time, well under any parameter ceiling — but the same class of gap
  could exist anywhere else a `Sequence[...]` is bulk-inserted in one
  statement without a stated size assumption. Worth a grep if another bulk
  insert path is added.
- **Gate affected:** M1. Blocked the second soak-test attempt outright.

### D-011 — Kill switch and equity ledger were in-memory
- **Status:** RESOLVED 2026-08-18 for both halves; see the remaining gap
- **Spec:** §8.2 requires a halt to survive; §7 invariant 9 requires read-only
  startup until reconciliation completes
- **Original gap:** both the halt and the equity ledger lived in the process.
  A restart cleared a halt, and — less visibly — it also cleared the
  daily-loss budget the halt thresholds are measured against.
- **Current state:** the halt is durable, fails closed on startup (F-003) and
  is now read through `CompositeSafetyStateStore` on the normal path (ADR-002).
  The ledger is persisted as append-only risk-session snapshots and recovered
  on the first tick of a run, seeded so that recovery can only ever be *more*
  conservative than the record (F-019). Evidence: `risk/session.py`,
  `persistence/risk_session.py`, `tests/unit/test_risk_session.py` (18 tests),
  `tests/integration/test_run_survives_restart.py`.
- **Remaining gap:** recovery consults local state and the account's current
  equity, not broker history — see D-032. Snapshots are written when the
  session becomes more constrained rather than every window, which is
  sufficient but is a cadence choice worth revisiting under a real feed.
- **Gate affected:** M5. The permissive-reset failure is closed; the
  broker-reconciled version of it is not.

### D-012 — No persistence at all
- **Status:** RESOLVED 2026-08-18 for the decision path; partial against §18
- **Spec:** §18 specifies twenty-two tables
- **Original gap:** decision capsules accumulated in a list and were discarded
  when the process exited. Nothing built on the audit trail — drift metrics,
  post-trade evaluation, the promotion scorecard — could be evidenced.
- **Current state:** eight tables exist and are used by the running system:
  `events`, `decision_capsules`, `safety_state_events`, `risk_session_states`,
  `market_ticks`, `market_bars`, `config_versions`, `instrument_specs`. A run is
  reproducible from the `events` table alone
  (`application/reconstruction.py`), the ten ADR-003 invariants are tested
  against a real PostgreSQL, and the schema is versioned by Alembic rather than
  created from nothing (D-029).
- **Remaining gap:** fourteen of the twenty-two do not exist —
  `feature_snapshots`, `orders`, `order_events`, `fills`, `positions`,
  `position_snapshots`, `evaluation_results`, `drift_metrics`, `heartbeats`,
  `incidents`, `accounts`, `strategy_versions`, `model_versions`,
  `deployment_versions`. Each lands with the component that produces it rather
  than as an empty schema ahead of one; `kill_switch_events` from §18 is served
  by `safety_state_events` under a different name.
- **Gate affected:** M2's own data deliverables are met. `feature_snapshots` is
  the one with a live consequence today — see D-031. The rest are M5/M7.

### D-013 — Risk values in `config/paper.yaml` are placeholders
- **Status:** provisional — must be confirmed before gate P2
- **Spec:** §29 Q7-Q8 reserve risk budget and maximum drawdown for a human
- **Code:** 0.5% per trade, 2% daily loss, 10% drawdown, 1 open position
- **Why:** the platform must be loadable to be testable, and no risk value has
  a permissive default.
- **Watch for:** these numbers have never been agreed by anyone. They are
  conservative, which makes them easy to leave unchallenged by accident.

### D-014 — `account_guard.expected_server` was an unmatchable placeholder
- **Status:** RESOLVED 2026-08-18 — the owner supplied the server
- **Original gap:** `UNCONFIGURED-DEMO-SERVER`, which could not match any real
  server. Deliberate: it failed closed until someone set it on purpose.
- **Current state:** `PepperstoneUK-Demo`, alongside `expected_currency: EUR`
  and `expected_leverage: 30`. All three are *checked* against `account_info()`
  rather than relied upon — a mismatch in any of them is `WRONG_ACCOUNT`, which
  halts. Currency and leverage were added because both change what a risk
  budget means without changing anything the strategy or the risk engine can
  otherwise see.
- **Remaining gap:** no terminal has ever been contacted, so every value in
  that block is an unverified claim. The entity question is D-034, and the
  hedging/netting mode is still deliberately unanswered (build.md §29 Q2).
- **Gate affected:** M1.

### D-015 — The supervisor frequency threshold is now explicitly uncalibrated
- **Status:** RESOLVED 2026-08-18 as an honest state (review 1.6 F-024); the
  calibration itself still does not exist
- **Original gap:** the threshold was 12, matching the structural maximum of an
  M5 cadence, so it vetoed 52% of ordinary traffic — a control that refuses
  half of normal operation trains its operator to ignore it. Raising it to 20
  fixed the false positives by making the check unable to fire at all, which
  was worse: an absent control wearing a number.
- **Current state:** `null`. Owner decision O-002 fixed the cadence at M5, which
  removed the "the timeframe is not settled" justification the threshold had
  been living on, and review 1.6 F-024 offered two honest options. This is the
  reviewer's preferred one: the check reports itself as **not in force** rather
  than as passed. Every `SupervisorDecision` carries `uncalibrated_checks`, the
  run report prints which controls were absent, and the confidence band — inert
  for the same reason — is reported alongside it.
- **Remaining gap:** there is still no rate limit. Calibrating one honestly
  needs real EUR/USD observations; calibrating against synthetic trade
  frequency would be fitting a control to a random walk, which build.md and
  finding F-004 both forbid.
- **Gate affected:** P2. A supervisor whose checks cannot fire is not evidence
  of a supervisor that works — but it is now evidence that says so out loud.

### D-016 — Only supervisor layer 1 exists
- **Status:** pending — M7
- **Spec:** §10.2 specifies three layers: deterministic policy, statistical
  monitor, optional LLM analyst
- **Code:** deterministic policy only. The LLM layer is absent by design and
  should stay absent until there is something for it to analyse.

### D-017 — No post-trade evaluation or drift monitoring
- **Status:** pending — M7
- **Spec:** §10.1 requires slippage, MAE/MFE, calibration and drift tracking
- **Code:** the simulated broker records MAE/MFE and slippage per trade, but
  nothing consumes them. `EvaluationCompleted` exists as a contract with no
  producer.

---

## D. Tooling

### D-018 — Ruff's `TCH` rules are deselected
- **Status:** deliberate
- **Code:** `pyproject.toml`, with the reason inline
- **Why:** `TCH` moves imports into `if TYPE_CHECKING` blocks. Pydantic
  resolves annotations at runtime, so a "correctly" moved import becomes a
  `NameError` the first time that type is used as a model field. The import-time
  saving is not worth a runtime failure in a trading process.

### D-019 — CI had never executed
- **Status:** provisional — the blocker is gone; the evidence is not in yet
- **Code:** `.github/workflows/ci.yml` defines lint, format, strict types,
  tests on Linux and Windows, and a gitleaks secret scan
- **Original gap:** the workflow existed but no remote did, so it had never
  run. Every quality claim in `status.md` came from one macOS arm64 machine.
- **Current state:** the repository was pushed to a private remote on
  2026-08-24 (`DutchBugs/Crumblr`, initial commit `fd6a890`), so the workflow
  can now run. Whether it *passes* is a separate question and its first result
  is unrecorded at the time of writing.
- **Remaining gap:** no CI run has been recorded in `status.md` as evidence.
  Until one is, the quality figures still rest on a single developer machine.
- **Watch for:** the Windows job installs the `mt5` extra and is the one that
  matters for the gateway. It has never executed anywhere.
- **Gate affected:** M0 — review 1.6 §5 names a passing CI run or a recorded
  exception as one of the two remaining M0 loose ends.

---

## E. Open specification questions blocking further work

Not deviations — decisions `build.md` §29 explicitly reserves for a human, and
which currently block M1.

| Question | Blocks |
|---|---|
| Q1 broker and MT5 server | instrument registry, account guard, M1 entirely |
| Q2 hedging or netting account | position model, reconciliation logic |
| Q3 strategy horizon | bar interval, D-015 calibration, tick-data need |
| Q4 overnight positions allowed | swap modelling in the cost model |
| Q7 risk budget per environment | D-013 |
| Q8 maximum acceptable drawdown | D-013, promotion scorecard |
| Q12 who may reset a production kill switch | operator workflow around D-011 |
