# Review feedback — tracker

The index between the reviewing agent and the implementing agent.

**Reviewer:** file each review as its own versioned document in this directory,
per the convention set out in `feedback.1.0.md` §1:

```text
feedback.1.0.md   first formal review baseline
feedback.1.1.md   follow-up within the same development phase
feedback.2.0.md   new major review phase
```

Old feedback files are never overwritten or deleted.

**Implementer:** at the start of every session, read the newest review, resolve
everything still `OPEN`, and record the outcome in the register below. A finding
is only moved to `CLOSED` with evidence — a commit, a file, a test.

---

## Reviews received

| Document | Date | Verdict | Findings |
|---|---|---|---|
| [feedback.1.0.md](feedback.1.0.md) | 2026-08-17 | GO WITH CONDITIONS · M5/P2 **NO-GO** | F-001 … F-008 |
| [feedback.1.1.md](feedback.1.1.md) | 2026-08-17 | GO WITH CONDITIONS · M5/P2 **NO-GO** | F-009 … F-012, plus re-evaluation |
| [feedback.1.2.md](feedback.1.2.md) | 2026-08-18 | GO WITH CONDITIONS · M5/P2 **NO-GO** | F-013, plus tracker-semantics recommendation |
| [feedback.1.3.md](feedback.1.3.md) | 2026-08-18 | GO WITH CONDITIONS · M5/P2 **NO-GO** | F-014, F-015 |
| [feedback.1.4.md](feedback.1.4.md) | 2026-08-18 | GO WITH CONDITIONS · M2 **integration incomplete** | F-016 … F-020 |
| [feedback.1.5.md](feedback.1.5.md) | 2026-08-18 | **PROCEED** · M5/P2 **NO-GO** | Owner decisions O-001…O-004; an eight-step work order |
| [feedback.1.6.md](feedback.1.6.md) | 2026-08-18 | GO WITH CONDITIONS · M1 **PREPARE NOW** · M5/P2 **NO-GO** | F-021 … F-025 |
| [feedback.1.7.md](feedback.1.7.md) | 2026-08-23 | GO WITH CONDITIONS · M1 **READY FOR FIRST CONTACT ONCE WINDOWS HOST EXISTS** · M5/P2 **NO-GO** | F-026 … F-029 |
| [feedback.1.8.md](feedback.1.8.md) | 2026-08-24 | **GO — PROCEED TO READ-ONLY MT5 FIRST CONTACT** · M5/P2 **NO-GO** | F-030 … F-032, plus 1.7 reprocessed |
| [feedback.1.9.md](feedback.1.9.md) | 2026-08-24 | **GO — CONTINUE FORWARD** · M1 first contact passed, complete continuous read+reconnect now · Dashboard v0 approved · M5/P2 **NO-GO** | F-033 … F-035, O-005, plus 1.7/1.8 reconfirmed |
| [feedback.1.10.md](feedback.1.10.md) | 2026-08-24 | **GO — EXECUTE THE REAL READ-ONLY SOAK NOW** · M1 implementation ready, real evidence still required · M2 **PASSED** · M5/P2 **NO-GO** | F-033 reopened/reclosed, F-036, F-037 |

> **`feedback.1.4.md` was missing and has been restored** (2026-08-18, review
> 1.6 §2 asked for it). It was added unchanged and not renumbered. While it was
> absent, F-018 and F-019 were actioned from their description in 1.5 §4; the
> restored document confirms that reading, and adds F-016, F-017 and F-020,
> which are recorded below with the rest.

---

## Finding register

Two fields, per the recommendation in `feedback.1.2.md` §4. They answer
different questions and conflating them is how "CLOSED" comes to be misread as
"shipped and validated".

- **Finding** — is the reviewer's concern resolved? `OPEN` · `IN PROGRESS` ·
  `CLOSED` · `ANSWERED` (considered, not acted on, with a reason)
- **Implementation** — what actually exists? `SHIPPED` (code + tests) ·
  `DECIDED` (an ADR, no code yet) · `PENDING M5` / `PENDING M2` · `N/A` for
  documentation-only findings

| ID | Severity | Summary | Finding | Implementation | Evidence |
|---|---|---|---|---|---|
| F-001 | HIGH | Milestone status conflates maturity with gate qualification | CLOSED | N/A — documentation | `status.md` §1 maturity ladder, separate from gate status |
| F-002 | **CRITICAL** | Supervisor receives hard-coded safe state | CLOSED | SHIPPED | `ReconciliationStatus` / `IncidentStatus` with `UNKNOWN`, both defaulting to it; safety gate sits above the policy switch. `tests/unit/test_fail_closed_safety.py` |
| F-003 | HIGH | Kill-switch state does not survive a restart | CLOSED | SHIPPED | Reopened in 1.1 and rightly so — the first evidence built two objects in one interpreter. `tests/integration/test_halt_survives_restart.py` now spawns a real child process |
| F-004 | MED/HIGH | Strategy development runs ahead of evidence | CLOSED | N/A — policy | Freeze recorded in `status.md` §10 and `CLAUDE.md` §4 |
| F-005 | MEDIUM | Inconsistent maturity/progress signals | CLOSED | N/A — documentation | Percentages removed; per-capability maturity columns |
| F-006 | LOW | Local Git initialised despite earlier agreement | CLOSED | N/A — process | Correction accepted in 1.1; local allowed, remote deferred. **The hold was lifted 2026-08-24**: initial commit `fd6a890`, pushed to the private remote `DutchBugs/Crumblr` so the code could reach the Windows MT5 host and CI could finally run |
| F-007 | HIGH | No execution-time risk revalidation | CLOSED | **DECIDED — PENDING M5** | `review/adr/ADR-001-…md`. No code exists |
| F-008 | HIGH | No FLATTEN / cancel-pending control path | CLOSED | SHIPPED — **not MT5-validated** | `risk/operator_controls.py`, `tests/unit/test_operator_controls.py`. Real broker behaviour unproven |
| F-009 | MEDIUM | Stale blockers in `status.md` contradict this tracker | CLOSED | N/A — documentation | APP-001/002 closed in place, APP-003/004 added, EV-001 split |
| F-010 | MEDIUM | M0 exit criteria contained M1 dependencies | CLOSED | N/A — documentation | `status.md` §2 split four ways |
| F-011 | HIGH | ADR-001 must define the price basis of final risk validation | CLOSED | **DECIDED — PENDING M5** | ADR-001 amended; eight required tests specified, none written |
| F-012 | MED/HIGH | Safety-state authority undefined before PostgreSQL | CLOSED | **DECIDED — PENDING M2** | `review/adr/ADR-002-…md`. Composite store not built |
| F-013 | MEDIUM | M0 logging deliverable missing | CLOSED | SHIPPED | `observability/logging.py`, 25 tests in `tests/unit/test_logging.py` covering all seven required properties |
| F-014 | LOW | Stale note in this tracker said logging still missing | CLOSED | N/A — documentation | Rewritten in past tense below; the discovery is retained because how it was found matters |
| F-015 | HIGH | M2 persistence invariants must be explicit before the layer grows | CLOSED | SHIPPED | `review/adr/ADR-003-persistence-invariants.md`; all ten acceptance tests pass against a real PostgreSQL in `tests/integration/test_persistence_invariants.py` |
| F-016 | MEDIUM | FEEDBACK tracker stale after the M2 implementation | CLOSED | N/A — documentation | Register rewritten 2026-08-18; F-012 and F-015 now read SHIPPED. Resolved before the review that raised it was recovered |
| F-017 | MEDIUM | DEVIATIONS held stale present-tense descriptions | CLOSED | N/A — documentation | D-011, D-012, D-027, D-028, D-030 rewritten in the Original gap / Current state / Remaining gap / Gate affected form the finding asks for |
| F-018 | not stated | M2 persistence built but not wired into the orchestrator (D-030) | CLOSED | SHIPPED | `application/recording.py`, `bootstrap.py`, `reconstruction.py`; `tests/integration/test_orchestrator_persistence.py` (11), `test_run_survives_restart.py` (8) |
| F-019 | not stated | Risk-session state may reset in a permissive direction after a restart | CLOSED | SHIPPED | `risk/session.py`, `persistence/risk_session.py`; `tests/unit/test_risk_session.py` (18) plus the restart suite above |
| F-020 | MEDIUM | No schema migration strategy before paper data becomes evidence | CLOSED | SHIPPED | Alembic baseline `ce70efeb9fe9`; `persistence/migrations.py`; `tests/integration/test_migrations.py` (8), including a `pg_dump` → drop → restore that reproduces the run |
| F-021 | MEDIUM | `status.md` current-state sections describe an older reality | CLOSED | N/A — documentation | Top sections rewritten to current truth; the update log keeps the history. Document version 1.2 |
| F-022 | HIGH | Raw market data is not persisted — the journal records what was decided, not what was seen | CLOSED | SHIPPED | `market_ticks` / `market_bars`, `persistence/market_data.py`, `market_data/pipeline.py`; 32 pipeline tests, 14 store tests. Every window an ordinary replay observes is stored, including the warm-up ones that seal no capsule |
| F-023 | MED/HIGH | Migrations, backup and a proven restore | CLOSED | SHIPPED | As F-020. The restore test asserts the journal still reproduces the run and that the market data survived |
| F-024 | MEDIUM | Supervisor frequency check cannot fire now M5 is fixed | CLOSED | SHIPPED — option B | `max_intents_per_hour: null` in `config/base.yaml`; every decision carries `uncalibrated_checks`, and the run report prints which controls were not in force |
| F-025 | HIGH | Owner exposure/intraday decisions must become executable policy | CLOSED (exposure) · **DECIDED — PENDING M5** (flatten) | SHIPPED / PARTIAL | One-exposure rule is a hard constant with the review's four cases tested (`tests/unit/test_one_exposure_policy.py`). Intraday: `ADR-004`, session phases enforced, breach detection halts — **the automatic flatten itself is M5** |
| F-026 | MEDIUM | `status.md` incorrectly said the Pepperstone demo account did not exist | CLOSED | N/A — documentation | `status.md` §1 health line, §2 M1-dependency checklist, §3 current status, §6 milestone tracker, §12 next actions all corrected 2026-08-24. The account login itself is not recorded anywhere in the repository |
| F-027 | MEDIUM | M2 may be held open by an M1/real-feed condition not in `build.md` | CLOSED | N/A — documentation | `build.md` Milestone 2 acceptance is replay order, gap/out-of-order detection and raw-data immutability — no real-feed clause. `status.md` §6 M2 row changed from `NOT PASSED` to `PASSED on its own acceptance evidence`; real-feed validation reassigned to M1, where `build.md` actually places it |
| F-028 | MEDIUM | Pepperstone entity ambiguity must not block first contact but must block legal/live assumptions | CLOSED | N/A — documentation | `review/DEVIATIONS.md` D-034 already recorded the entity as unresolved and provisional; `status.md` now cites F-028 alongside it and does not block the M1 probe on it. Settles from the probe's `company`/`server` output, not by inference |
| F-029 | LOW | Paper-campaign header left Broker/Server blank | CLOSED | SHIPPED | `status.md` §6 campaign header now reads `Broker: Pepperstone`, `Server: PepperstoneUK-Demo`. Campaign status stays `NOT STARTED`; no account login added |
| F-030 | MEDIUM | Full Windows gate (with PostgreSQL) had not run | CLOSED | SHIPPED | Docker + PostgreSQL 17 started on the Windows host, Alembic migrations applied, full suite run: **663 passed, 3 skipped** — the 3 are exactly the platform-dependent skips predicted in the earlier (no-database) run: the Windows-only mypy/`sys.platform` test (F-031 area) and two `test_halt_survives_restart.py` cases where Windows does not enforce POSIX permission bits. `status.md` §13 fifth 2026-08-24 entry |
| F-031 | MEDIUM / SECURITY | First-contact evidence must be sanitized before it enters Git/review artifacts | CLOSED | SHIPPED | `scripts/mt5_probe.py::sanitize_report` redacts `account.login`; new `--sanitized-json` CLI flag; `.gitignore` now excludes raw probe output (`first-contact*.json`, `*.mt5probe.json`, `var/`); `HANDOVER.md` §0.4/§4.3 rewritten to use the sanitized flag and a gitignored `var/` path. Three new tests in `tests/unit/test_mt5_probe.py::TestSanitizeReport` |
| F-032 | MEDIUM | MT5 enum decoding (D-037) must be settled from real observation before instrument specs become authoritative | CLOSED | SHIPPED | First contact 2026-08-24: real terminal reported `filling_mode=2` (→ IOC) and `trade_mode=4` (→ FULL), both matching the documented mapping. Gateway fixed to decode instead of stringify; decode tables shared with the probe in the new `src/crumblr/mt5_gateway/enums.py` so the two cannot drift apart again. `status.md` §13 sixth 2026-08-24 entry; `review/DEVIATIONS.md` D-037 |
| F-033 | MEDIUM | `status.md` current-state sections still lagged behind the first-contact update | **REOPENED 2026-08-24 by review 1.10, then CLOSED again the same day** | N/A — documentation | 1.9's fix was itself incomplete — the MT5 checklist, the M1 milestone row, "Overall health" and the repo/build checklist (stale `(no remote)`, stale test/file counts) all still described the reader as unbuilt after it had shipped. Rewritten to distinguish impl+unit-tested from real-terminal-validated per capability |
| F-034 | HIGH | Reconnect must revalidate broker/account truth before data flow resumes | IN PROGRESS | SHIPPED, unit-tested only | `src/crumblr/application/live_reader.py::LiveReader`. All five required scenarios pass against a scripted fake terminal (`tests/unit/test_live_reader.py`, 16 tests): normal reconnect recovers, wrong account fails closed and stays closed until `acknowledge()`, a changed symbol spec is detected and recorded without silent continuation, silence past threshold becomes STALE and self-clears, every reconnect re-runs the full account guard. **Not yet run against a real terminal** — `scripts/mt5_live_reader.py` exists for that; the soak test (review 1.10 Phase A/B) is the remaining step before this closes |
| F-035 | HIGH DESIGN | UI v0 must remain read-only and outside the broker execution boundary | OPEN UNTIL BUILT | PENDING | Hard boundary specified: reads PostgreSQL/app state only, no `MetaTrader5` import, no credentials, no `order_send`, no HALT reset, no risk-config write, no `TradeIntent` creation, no buttons. Not yet built. Review 1.10 §7 confirms it may start after Phase A produces real rows — not before, not instead |
| F-036 | HIGH DESIGN | Reader acknowledgement must never equal automatic restoration of health | CLOSED | SHIPPED | `LiveReader.acknowledge()` only clears the sticky latch to `DISCONNECTED`; the next `poll_once()` performs a full fresh reconnect + revalidation (account guard, margin mode, symbol resolution, instrument spec) before status can become `HEALTHY` again. A real gap found while verifying this: `SymbolNotFoundError` from a missing/unresolvable symbol was not caught in `_reconnect()` and would have crashed the reader rather than failing closed — fixed, now `UNHEALTHY` like an account mismatch. Three new tests assert acknowledging a still-broken account/symbol does not restore `HEALTHY`, and that a genuinely fixed one does |
| F-037 | HIGH | MT5 timestamp semantics must be verified against the real feed before M1 is qualified | OPEN | PENDING REAL SOAK | Same open question as D-039. Cannot close from code — needs the soak test's comparison of stored `event_time_utc`/`received_time_utc` against wall-clock reality |

**Nothing is `SHIPPED` that has met a real broker.** Every entry above was
exercised against simulated data only. F-018 and F-019 are shipped against a
real PostgreSQL and real child processes, which is a stronger claim than the
rest of the table can make — and still says nothing about MT5.

Review 1.5 does not assign severities to F-018 and F-019; it gives an
execution order instead. They are recorded as "not stated" rather than being
assigned one here, because inventing a reviewer's severity is how a tracker
starts to disagree with the reviews it indexes.

### Note on F-006

Settled in review 1.1: the reviewer accepted the correction and restated the
controlling concern as avoiding an unintended remote/collaboration workflow,
not the existence of a local repository. Local Git allowed, remote deferred.
No commit has been made.

### Found while addressing F-010, then raised as F-013

`build.md` §26 lists **logging** as an M0 deliverable. `structlog` was a declared
dependency, `observability/` was an empty package, and nothing imported either.
It had been treated as delivered because the dependency existed. Surfaced by
checking the gate criteria against the specification rather than against memory,
then formalised by review 1.2 as F-013 and now implemented.

---

## Still open, by the reviewer's own gate decisions

These are not findings against existing work; they are the conditions the
reviewer set for the next gates. Tracked here so they are not lost.

| Item | Gate | Blocked on |
|---|---|---|
| ~~PostgreSQL event persistence~~ | M2 | **Done** 2026-08-18 — storage, then wiring (F-018) |
| ~~ADR-002 authority semantics in the recovery path~~ | M2 | **Done** — `application/bootstrap.py` on the normal path |
| ~~ADR-003 persistence invariants~~ | M2 | **Done** — ten acceptance tests against a real PostgreSQL |
| ~~Restart-safe risk-session state~~ | M5 prerequisite | **Done** 2026-08-18 (F-019) |
| ~~Raw market storage and the bar pipeline~~ | M2 | **Done** 2026-08-18 (F-022) |
| ~~Alembic migrations~~ | before M5 | **Done** 2026-08-18 (F-020, F-023) |
| Automatic flatten at the intraday deadline | M5 | The execution path. Detection ships now; closing a position does not — ADR-004 §5 |
| The two intraday offset values | M5 | An owner decision. `config/paper.yaml` carries provisional numbers nobody has agreed |
| Feature values in storage | M2/M7 | The feature hash and version are journalled; the values are not |
| ~~Pepperstone MT5 demo account~~ | M1 | **Done** 2026-08-24 — created and logged into the Windows terminal once, interactively (F-026) |
| Pepperstone entity: EU or UK? | M1 | O-001 names "Pepperstone EU"; the supplied server is `PepperstoneUK-Demo`. Different regulator, leverage cap and swap treatment. Does not block the probe run (F-028) |
| ~~Hedging or netting~~ | M1 | **Done** 2026-08-24 — `RETAIL_HEDGING`, read from `account_info()` on the real account (build.md §29 Q2) |
| ~~Windows x86-64 MT5 host~~ | M1 | **Done** 2026-08-24 — owner provisioned it |
| Read-only MT5 gateway | M1 | **Written** 2026-08-23, **first contact made** 2026-08-24 — one successful connection, account guard passed. Continuous read and reconnect behaviour (HANDOVER.md §4.5) still open. `mt5_gateway/readonly.py` + `scripts/mt5_probe.py` |
| Broker reconciliation | M5 | M1 |
| Execution-time revalidation *implementation* | M5 | ADR-001 is written; the code lands with the execution engine |
| Intraday cut-off and mandatory flatten times | M5 | An owner decision, per O-003 |
| Supervisor recalibration for the M5 cadence | P2 | O-002 fixes the cadence; D-015 and EV-002 hold the work |

---

## Unreviewed work — `feedback.1.11.md` not yet triggered

`feedback.1.7.md` through `feedback.1.10.md` have all been processed (F-026…
F-037, O-005, above). Review 1.10 §13 names the trigger for
`feedback.1.11.md`: M1 qualification / dashboard review, once the real soak
(Phase A normal run, Phase B deliberate interruption) has produced recorded
evidence.

**Nothing below has been seen by a reviewer yet:**

| Artifact | What it claims | Where to check it |
|---|---|---|
| F-036 fix | `LiveReader.acknowledge()` cannot itself restore HEALTHY; a `SymbolNotFoundError` during reconnect (missing/unresolvable symbol) was uncaught and would have crashed the reader — now `UNHEALTHY`, fail-closed, like an account mismatch | `src/crumblr/application/live_reader.py::_reconnect`; `tests/unit/test_live_reader.py::TestScenario6AcknowledgeIsNotRestoration` (3 tests) |
| F-033, fixed again | The MT5 checklist (was all-unchecked), the M1 milestone row, "Overall health", and the repo/build checklist (stale `(no remote)`, stale counts) rewritten to distinguish impl+unit-tested from real-terminal-validated, per capability | `status.md` Platform checklist, §1, §3, §6 |
| CI | Still not confirmed running — unchanged from the 1.10 entry below | same as below |
| The real soak (Phase A/B) | **Not yet run** as of this entry — the reviewer's stated top priority. Everything above is preparation for it, not a substitute | — |

`feedback.2.0.md` remains mandatory before the first `order_send`, demo
included, and is separate from 1.10.

---

## Owner decisions recorded in review 1.5

Fixed for v1. These are decisions, not findings, and they are indexed here so
that the code can cite them the way it cites a finding.

| ID | Decision | Consequence for the code |
|---|---|---|
| O-001 | Pepperstone EU, **demo**, MT5, EUR/USD is the M1 integration target | No multi-broker routing in v1. Server, symbol, digits, volume steps, stops/freeze levels, filling modes and account mode are **discovered from the actual account**, never hard-coded. Demo identities expire (Pepperstone documents 60 days), so account replacement is a normal configuration change |
| O-002 | The decision timeframe is **M5** | Fixes the cadence the supervisor's frequency threshold must be calibrated against — see D-015 and EV-002. Higher-resolution data may still be collected; M5 is the *decision* cadence |
| O-003 | **No overnight positions** in v1 | Needs an explicit entry cut-off and a mandatory flatten before the session boundary, both specified and tested before M5. "Intraday" must not be quietly read as "midnight UTC" |
| O-004 | **One EUR/USD exposure at a time** | A business rule independent of whether the account is hedging or netting. `max_open_positions` is already 1 in `config/paper.yaml`, but that is a coincidence of a placeholder value and not yet this rule |
| O-005 | Review 1.9 §2 F-028: for the **demo/development environment only**, the Pepperstone entity is **Pepperstone Limited (UK)**, refining O-001's "Pepperstone EU" shorthand after first contact | Closes D-034/APP-013 for M1 demo integration. Does **not** decide the entity for a future live account — that needs its own review against the owner's residence and live-account documentation before any live decision. O-001 is not rewritten; O-005 is recorded as amending it |

Still undecided and deliberately so: paper risk per trade (Q7), maximum
drawdown (Q8), and production halt-reset authority (Q12). Q2 (hedging vs
netting) is no longer undecided — answered 2026-08-24 by `account_info()` on
the real demo account: `RETAIL_HEDGING`. The values presently in
`config/paper.yaml` for Q7/Q8 are placeholders and must not be promoted to
policy by having sat there — see D-013.

---

## Process note — review 1.4 deliberately skipped

**Decision by the project owner, 2026-08-18.** No review is requested between
`feedback.1.3.md` and the M2 implementation.

The reasoning is the reviewer's own: `feedback.1.3.md` §8 recommends triggering
1.4 when *"PostgreSQL M2 design/implementation is available"*, and §9 states the
project has largely exhausted the value of further simulated development. Two
consecutive rounds produced documentation only. There is nothing new for a
review to examine until M2 exists.

What this does **not** change:

- `feedback.2.0.md` remains mandatory before the first real or demo
  `order_send`, and must rely on integration evidence rather than tracker claims.
- M5 and P2 remain NO-GO.
- ADR-002 and ADR-003 are implemented *as part of* M2, not afterwards.

The next review should be numbered **`feedback.1.4.md`** and should treat the M2
implementation as its primary artifact.

**Superseded 2026-08-18.** A review 1.5 arrived, naming a 1.4 as its
predecessor. Whether a 1.4 was written and lost, or whether the numbering
skipped, is not something this repository can answer — see the note above the
finding register. Review 1.5 §8 suggests the next review be numbered
**`feedback.1.6.md`**.

---

## Notes for the reviewer

- **Every finding is CLOSED, but read the Implementation column.** Three are
  `DECIDED` with no code (F-007, F-011, F-012) and one is shipped but unproven
  against a real broker (F-008). The two-field split recommended in 1.2 §4 is
  adopted precisely so that is not glossed over.
- **F-003 was correctly reopened.** The first evidence proved a store
  round-trip, not a restart. It now spawns a real child process. That
  distinction is worth applying to the rest of the suite when reviewing it.
- Two findings were independently identified here before review 1.0 arrived
  (F-002 as `D-028`, F-008 as `D-027` in [DEVIATIONS.md](DEVIATIONS.md)).
  Offered as corroboration, not as a claim of priority.
- **One gap neither review originally caught:** `build.md` §26 required logging
  as an M0 deliverable while only the dependency and an empty package existed.
  It was formalised as F-013 in review 1.2 and is now implemented and tested.
  Kept here because how it was found matters — comparing the gate criteria
  against the specification rather than against memory (review 1.3 F-014).
- **The softest part of the evidence chain remains the fill model.** See `D-010`.
  Intrabar stop/target ordering is an assumption, and swap and commission are
  not modelled. No performance number from this system means anything yet.
- **Reproduce any run** with `uv run python scripts/run_replay.py --bars 4000`.
  Two runs must be byte-identical.
