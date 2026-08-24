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
| [feedback.1.11.md](feedback.1.11.md) | 2026-08-24 | **GO — CONTINUE THE REAL SOAK** · M1 **NOT YET PASSED, two real-soak defects found and fixed** · M2 **PASSED** · Dashboard v0 **GO AFTER A CLEAN PHASE A** · M5/P2 **NO-GO** | F-033 (partly reopened, reclosed), F-034/F-037 reconfirmed OPEN, F-031 reopened/reclosed, F-038 (new) |
| [feedback.1.12.md](feedback.1.12.md) | 2026-08-24 | **GO — M1 QUALIFIED** · M1 **PASSED** · M2 **PASSED** · Dashboard v0 **GO NOW** · M5/P2 **NO-GO** | F-033 reopened again (fixed a fourth time), F-034/F-037/F-038 reconfirmed CLOSED, F-039/F-040/F-041 (new, all closed same day) |

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
| F-031 | MEDIUM / SECURITY | First-contact evidence must be sanitized before it enters Git/review artifacts | **REOPENED 2026-08-24 by review 1.11, then CLOSED again the same day** | SHIPPED | Original 1.8 fix covered opt-in probe artifacts only (`scripts/mt5_probe.py::sanitize_report`, `--sanitized-json`, `.gitignore`). Review 1.11 found the gap it missed: `Mt5Client.connect()`'s `mt5.connected` line and `ReadOnlyMt5Gateway._verify_account`'s `mt5.account_guard_failed` line both logged the **full, unmasked** login on every ordinary run — routine logs, not opt-in artifacts. Fixed two ways: (1) both call sites now log `account_ref=mask_login(login)` (`***`+last 3 digits) instead of the raw value — `mt5_gateway/client.py::mask_login`; (2) a processor-level backstop in `observability/logging.py` adds `login` to the redacted-key markers already used for passwords, so any future call site that logs a raw `login=` is caught structurally rather than by call-site discipline, which is what the reviewer explicitly asked for ("the software should prevent the disclosure"). `AccountGuardError`'s message (which `LiveReader` copies into `ReaderHealth.last_error`/`detail`, written to the soak's `--json` evidence file) is masked too. Tests: `tests/unit/test_mt5_client.py` (7), `tests/unit/test_mt5_readonly_gateway.py::TestAccountGuard` (+2), `tests/unit/test_logging.py::TestSecretsAreRedacted` (+3, and one existing test updated since it had asserted the old, now-wrong, unmasked behaviour) |
| F-032 | MEDIUM | MT5 enum decoding (D-037) must be settled from real observation before instrument specs become authoritative | CLOSED | SHIPPED | First contact 2026-08-24: real terminal reported `filling_mode=2` (→ IOC) and `trade_mode=4` (→ FULL), both matching the documented mapping. Gateway fixed to decode instead of stringify; decode tables shared with the probe in the new `src/crumblr/mt5_gateway/enums.py` so the two cannot drift apart again. `status.md` §13 sixth 2026-08-24 entry; `review/DEVIATIONS.md` D-037 |
| F-033 | MEDIUM | `status.md` current-state sections still lagged behind the first-contact update | **REOPENED 2026-08-24 by review 1.10, CLOSED, PARTLY REOPENED again 2026-08-24 by review 1.11, CLOSED, REOPENED a third time by review 1.12, then CLOSED again the same day** | N/A — documentation | 1.9's fix was itself incomplete — the MT5 checklist, the M1 milestone row, "Overall health" and the repo/build checklist (stale `(no remote)`, stale test/file counts) all still described the reader as unbuilt after it had shipped. Rewritten to distinguish impl+unit-tested from real-terminal-validated per capability. Review 1.11 found a third instance: §12 "Next 10 actions" still listed the Pepperstone entity and Q2 hedging/netting as open after both were resolved elsewhere (O-005, `RETAIL_HEDGING`). Review 1.12 found a fourth instance: §1 "Overall health" (Data health, MT5 connectivity) and §3's MT5 checklist rows for bars/ticks collection and reconnect behaviour still read as unvalidated against the real terminal after Phase A/B had both succeeded. All rewritten in place; test counts refreshed (718 passed, 3 skipped, after F-039/F-040/F-041) |
| F-034 | HIGH | Reconnect must revalidate broker/account truth before data flow resumes | CLOSED | SHIPPED, real-terminal-validated | `src/crumblr/application/live_reader.py::LiveReader`. All five required scenarios pass against a scripted fake terminal (`tests/unit/test_live_reader.py`, 16 tests). **Phase B run 2026-08-24, owner present:** two deliberate MT5 terminal closures against the live Pepperstone demo, minutes apart. Both times: `live_reader.read_failed` (`IPC send failed`) detected the loss, `mt5.disconnected` followed, the terminal was restored, the reader reconnected on its own (`reconnect_count` 1→2→3), re-resolved the symbol, re-ran the account guard (no `AccountGuardError` — the account was still correct, and a wrong one would have gone `UNHEALTHY` instead of recovering, per the unit-tested scenario), re-detected the broker clock offset fresh both times (180 min, consistent with Phase A's measurement), and correctly flagged the instrument spec as changed on each reconnect rather than silently continuing (`live_reader.spec_changed`). Fresh ticks/bars resumed immediately after each reconnect — the last tick read was 1.6 seconds old at the moment of verification, and both tick and bar counts kept growing through both interruptions (2,920→3,578 ticks, 17→19 bars). `status.md` §13 seventeenth entry |
| F-035 | HIGH DESIGN | UI v0 must remain read-only and outside the broker execution boundary | CLOSED | SHIPPED | Dashboard v0 built 2026-08-24 (review 1.12 §8 trigger): reads PostgreSQL and the `LiveReader` health JSON snapshot only, no `MetaTrader5` import, no credentials, no `order_send`, no HALT reset, no risk-config write, no `TradeIntent` creation, no buttons — checked structurally, not only by intent: `tests/integration/test_dashboard.py::TestReadOnlyBoundary` walks every registered route for a mutating HTTP method and the AST of every file in `src/crumblr/dashboard/` for a forbidden import. Kept as its own package rather than folded into `api/`, which build.md §21 already earmarks for the opposite claim. Scope against build.md §22/M8's full spec recorded as D-043, not silently narrowed |
| F-036 | HIGH DESIGN | Reader acknowledgement must never equal automatic restoration of health | CLOSED | SHIPPED | `LiveReader.acknowledge()` only clears the sticky latch to `DISCONNECTED`; the next `poll_once()` performs a full fresh reconnect + revalidation (account guard, margin mode, symbol resolution, instrument spec) before status can become `HEALTHY` again. A real gap found while verifying this: `SymbolNotFoundError` from a missing/unresolvable symbol was not caught in `_reconnect()` and would have crashed the reader rather than failing closed — fixed, now `UNHEALTHY` like an account mismatch. Three new tests assert acknowledging a still-broken account/symbol does not restore `HEALTHY`, and that a genuinely fixed one does. Review 1.11 §2 closes this "based on documented test evidence" and asks that the invariant be kept when Dashboard v0 is built |
| F-037 | HIGH | MT5 timestamp semantics must be verified against the real feed before M1 is qualified | CLOSED | SHIPPED | Settled by observation, not assumption, per review 1.11 §7: the fourth real Phase A attempt (30 min, real Pepperstone demo, 19,437 ticks) showed a stable ~2:59:39-2:59:40 gap between the terminal's `event_time_utc` and this platform's own `received_time_utc` — a genuine, constant broker-clock offset, not UTC as `readonly.py` had assumed. `ReadOnlyMt5Gateway._clock_offset()` now measures this once per gateway instance (i.e. every `LiveReader` reconnect) via `symbol_info_tick`, rounds to the nearest 30 minutes, and corrects every timestamp the gateway converts (`ticks`, `bars`, `positions`) plus the `since` parameter sent to `copy_ticks_from`. Not hard-coded to +3, per the review's own instruction not to invent a correction without observation — this discovers it fresh every session, the same way the symbol and account are discovered rather than assumed. `tests/unit/test_mt5_readonly_gateway.py::TestClockOffset` (4 tests). D-039 updated with the full measurement and fix |
| F-038 | HIGH DATA INTEGRITY | D-041's chunked insert must have a proven, not assumed, failure/recovery contract | CLOSED | SHIPPED | Contract A (batch atomic) confirmed correct and now proven: `tests/integration/test_market_data_store.py::TestChunkedInsertFailureSemantics::test_a_failure_partway_through_a_multi_chunk_batch_rolls_back_the_whole_batch` injects a failure into the second of two chunks against a real PostgreSQL connection and asserts zero rows from the logical batch survive. `MarketDataStore._record_ticks` docstring now states the contract explicitly; `review/DEVIATIONS.md` D-041 updated with the proof |
| F-039 | HIGH BEFORE M5/RECONCILIATION | Semantic instrument identity must not change merely because it was re-observed | CLOSED | SHIPPED | The reviewer's guessed mechanism (a fresh `captured_at_utc` changing the fingerprint) was already wrong — that field was excluded from `spec_version` before this review. The real cause, traced to first-contact evidence already on record: `tick_value` is recomputed live by MT5 from the current EUR/USD cross-currency rate whenever the account currency differs from the quote currency, so it drifts with the market, not with broker policy — hashing it produced `spec_changed` on every Phase B reconnect. Excluded from `InstrumentSpec.spec_version`'s hash alongside `captured_at_utc`; still recorded on every spec, just not part of what "changed" means. `domain/models.py::InstrumentSpec.spec_version`; `tests/unit/test_control_plane_contracts.py::TestInstrumentSpecVersioning::test_a_tick_value_fluctuation_alone_does_not_change_the_version` |
| F-040 | HIGH BEFORE UNATTENDED/PAPER OPERATION | Broker-clock detection must fail closed when its reference tick is stale | CLOSED | SHIPPED | `ReadOnlyMt5Gateway._clock_offset()` now rejects a measurement whose residual from a clean half-hour multiple exceeds 3 minutes (real GMT offsets round cleanly — Phase A/B measured ~2:59:39-2:59:40 for 180 minutes, seconds of latency, not minutes) or whose magnitude exceeds ±15h, raising `ClockOffsetUnavailableError` rather than caching a bad value — the next call re-measures fresh rather than staying poisoned. `LiveReader` maps this to `DISCONNECTED` (not sticky, clears on a fresh tick), the same treatment as a stale feed rather than a wrong-account mismatch. `tests/unit/test_mt5_readonly_gateway.py::TestClockOffset` (+3), `tests/unit/test_live_reader.py::TestClockOffsetStaleReference` (+2) |
| F-041 | MEDIUM DATA GOVERNANCE | Operational soak/database reset must remain on the migration path | CLOSED | SHIPPED | The third Phase A attempt's real recovery mixed `bootstrap_schema()`/`create_all` with Alembic's `alembic_version` table — exactly the two-paths-disagreeing failure `persistence/migrations.py` already warns against. `scripts/reset_soak_database.py` provides the coherent alternative: `alembic downgrade base` -> `upgrade head` only, the same round trip `tests/integration/test_migrations.py::test_the_baseline_can_be_unwound` already proves is clean, run deliberately — refuses a URL without "soak" in it, requires `--yes`. Documented in `.env.example` |

**Most of this table predates the real broker; some of it no longer does.**
Until 2026-08-24 nothing marked `SHIPPED` had met a real broker — every entry
was exercised against simulated data only, and F-018/F-019 (real PostgreSQL,
real child processes) was the strongest claim available, saying nothing about
MT5. First contact, Phase A and Phase B changed that for the M1 read-only
path specifically: F-030, F-031, F-032, F-034, F-037, F-038, F-039, F-040 are
now shipped **and** real-terminal-validated, and M1 itself is PASSED
(`feedback.1.12.md`). Everything downstream of M1 — the Trading Agent, risk
engine, Evaluator, and anything execution-shaped — has still never met a real
broker and remains exactly as provisional as this paragraph always said.

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
| ~~Read-only MT5 gateway~~ | M1 | **Done, M1 PASSED** — `feedback.1.12.md`, 2026-08-24. Written 2026-08-23, first contact 2026-08-24, Phase A (clean 30-minute soak) and Phase B (two real reconnects, owner present) both completed the same day. `mt5_gateway/readonly.py` + `scripts/mt5_probe.py` + `application/live_reader.py`. Recorded in `status.md` §16 Promotion history |
| Broker reconciliation | M5 | M1 |
| Execution-time revalidation *implementation* | M5 | ADR-001 is written; the code lands with the execution engine |
| Intraday cut-off and mandatory flatten times | M5 | An owner decision, per O-003 |
| Supervisor recalibration for the M5 cadence | P2 | O-002 fixes the cadence; D-015 and EV-002 hold the work |

---

## Unreviewed work — `feedback.1.13.md` now triggered

`feedback.1.7.md` through `feedback.1.12.md` have all been processed
(F-026…F-041, O-005, above). **M1 is PASSED** (`feedback.1.12.md` §7),
recorded in `status.md` §16 Promotion history. Review 1.12 §14 names the
trigger for `feedback.1.13.md`: Dashboard v0 available, F-039/F-040/F-041
addressed, CI result and/or M0 contract review available, and initial real
reconciliation evidence. F-039/F-040/F-041 and **Dashboard v0** are now
addressed (below); CI, the M0 contract review and reconciliation are not
yet — see `status.md` §12.

**Nothing below has been seen by a reviewer yet:**

| Artifact | What it claims | Where to check it |
|---|---|---|
| F-031 fix (reopened by 1.11) | Full MT5 login no longer appears in `mt5.connected`/`mt5.account_guard_failed` or in `AccountGuardError`'s message; masked to `account_ref="***nnn"` at both call sites plus a processor-level backstop that redacts any future raw `login=` field | `src/crumblr/mt5_gateway/client.py::mask_login`, `observability/logging.py`; `tests/unit/test_mt5_client.py`, `tests/unit/test_mt5_readonly_gateway.py::TestAccountGuard`, `tests/unit/test_logging.py::TestSecretsAreRedacted` |
| F-038 fix (new in 1.11) | D-041's chunked insert is batch-atomic — proven by injecting a failure into the second of two chunks and asserting zero rows survive, not merely asserted from "same connection" | `tests/integration/test_market_data_store.py::TestChunkedInsertFailureSemantics` |
| F-033, fixed a third time | §12 "Next 10 actions" no longer lists the Pepperstone entity or Q2 hedging/netting as open; both were already resolved elsewhere in the document. Test counts refreshed | `status.md` §12 |
| D-042 (new, found on the third real soak; addendum from the fifth) | `ReadOnlyMt5Gateway.bars()` was persisting MT5's current, still-forming bar as though it were closed. Fixed by excluding any bar whose interval has not yet closed. Fifth attempt found the closedness check alone was not enough either: MT5 revised a just-closed bar's `tick_volume` a few seconds after its raw boundary, so a `_BAR_SETTLE_BUFFER` (30s) now holds a bar back a little further before returning it | `review/DEVIATIONS.md` D-042; `src/crumblr/mt5_gateway/readonly.py::bars`; `tests/unit/test_mt5_readonly_gateway.py::TestBars` (+4) |
| Dedicated soak database | `scripts/mt5_live_reader.py` now refuses to start without `CRUMBLR_DATABASE_URL` explicitly set, rather than silently defaulting to the shared test database that `tests/integration` tears down at teardown — the exact cause of the third attempt's first failure | `status.md` §10 decision log; `.env.example`; `scripts/mt5_live_reader.py` |
| F-037/D-039 closed (new in this pass) | The fourth Phase A attempt proved MT5 tick/bar timestamps are not UTC — a stable ~2:59:39-2:59:40 broker-clock offset, measured across 19,437 real ticks. `ReadOnlyMt5Gateway._clock_offset()` now detects this per gateway instance (via `symbol_info_tick`, rounded to the nearest 30 minutes) and corrects every timestamp the gateway converts, plus the `since` sent to `copy_ticks_from`. Not hard-coded — discovered fresh each session | `review/DEVIATIONS.md` D-039; `src/crumblr/mt5_gateway/readonly.py::_clock_offset`, `_to_utc`; `tests/unit/test_mt5_readonly_gateway.py::TestClockOffset` |
| D-042 addendum (new, found on the fifth real soak) | Even a bar past its raw M5 boundary could still have its `tick_volume` revised by MT5 a few seconds later; `_BAR_SETTLE_BUFFER` (30s) added on top of the existing closedness check | `review/DEVIATIONS.md` D-042 addendum; `src/crumblr/mt5_gateway/readonly.py::_BAR_SETTLE_BUFFER` |
| CI | Still not confirmed running — unchanged from the 1.10/1.11 entries | — |
| **Phase A — satisfied** | Sixth real attempt: 30 minutes, real Pepperstone demo, zero disconnects, zero errors. **2,920 real ticks and 17 real M5 bars persisted**, all `data_quality=GOOD`, zero anomalies, zero gaps between consecutive bars, every bar's open time aligned exactly to a 5-minute UTC boundary — direct confirmation of D-039's timestamp fix, not only the offset-measurement evidence F-037 already closed on. Four real defects found and fixed across all six attempts (D-040, D-041, D-042×2, D-039); one operational fix (dedicated `crumblr_soak` database). Full detail: `status.md` §13 sixteenth entry | `status.md` §13 sixteenth entry; `var/soak_phase_a_health.json` (local, gitignored — no account number) |
| **Phase B — satisfied** | Owner present. Two deliberate MT5 terminal closures, minutes apart, against the same live Pepperstone demo session Phase A had just proven clean. Both times: loss detected (`live_reader.read_failed`, `IPC send failed`), reconnected on its own once the terminal returned (`reconnect_count` 1→2→3), full revalidation each time (symbol re-resolved, account guard re-run with no mismatch, broker clock offset re-measured at a consistent 180 minutes, a changed instrument spec correctly flagged rather than silently accepted), fresh ticks/bars resumed within seconds. F-034 closed | `status.md` §13 seventeenth entry; `var/soak_phase_b_health.json` (local, gitignored) |
| **M1 PASSED** (new, review 1.12) | Reviewer decision recorded: M1 MT5 read-only gateway is MT5-INTEGRATED, PASSED. Does not authorize `order_send` | `feedback.1.12.md` §7; `status.md` §16 Promotion history |
| F-039 fix (new in 1.12) | `InstrumentSpec.spec_version` no longer hashes `tick_value` (drifts live with the account/quote cross-currency rate — confirmed from first-contact evidence, not assumed) alongside the already-excluded `captured_at_utc`. Reconnecting with an unchanged contract no longer produces a false `spec_changed` | `src/crumblr/domain/models.py::InstrumentSpec.spec_version`; `tests/unit/test_control_plane_contracts.py::TestInstrumentSpecVersioning` |
| F-040 fix (new in 1.12) | `ReadOnlyMt5Gateway._clock_offset()` rejects a reference tick whose measured offset does not round cleanly to a half-hour multiple (3-minute tolerance) or is implausibly large (>±15h), raising rather than caching; `LiveReader` treats the failure like a stale feed (`DISCONNECTED`, self-clearing) | `src/crumblr/mt5_gateway/readonly.py::_clock_offset`, `ClockOffsetUnavailableError`; `tests/unit/test_mt5_readonly_gateway.py::TestClockOffset`; `tests/unit/test_live_reader.py::TestClockOffsetStaleReference` |
| F-041 fix (new in 1.12) | `scripts/reset_soak_database.py`: a deliberate, all-Alembic (`downgrade base` -> `upgrade head`) soak-database reset, refusing a URL without "soak" in it and requiring `--yes` — replaces the ad hoc `bootstrap_schema()`-after-manual-drop recovery that caused the original incident | `scripts/reset_soak_database.py`; `.env.example` |
| F-033, fixed a fourth time | §1 "Overall health" and §3's MT5 checklist rows for bars/ticks collection and reconnect behaviour rewritten to state Phase A/B are real-terminal validated, not merely built and unit-tested. Test counts refreshed (718 passed, 3 skipped) | `status.md` §1, §3 |
| **Dashboard v0 (new, review 1.12 §8)** | Read-only status page + `/api/state` JSON (FastAPI + Jinja2, user's chosen stack), reading only PostgreSQL and the `LiveReader` health JSON snapshot — never MT5 directly. Covers review 1.12 §8's minimum screen. Read-only boundary enforced structurally (no route accepts POST/PUT/PATCH/DELETE; package never imports `MetaTrader5`/`crumblr.mt5_gateway`), not only by intent. Smoke-tested against real Phase A/B data in `crumblr_soak`. Deliberate scope gap against build.md §22/M8's full spec (no control surface at all) recorded as **D-043** | `src/crumblr/dashboard/`; `scripts/run_dashboard.py`; `tests/integration/test_dashboard.py::TestReadOnlyBoundary`; `review/DEVIATIONS.md` D-043; `status.md` §13 nineteenth entry |

`feedback.2.0.md` remains mandatory before the first `order_send`, demo
included, and is separate from 1.12.

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
