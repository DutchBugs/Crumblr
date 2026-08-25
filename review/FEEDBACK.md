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
| [feedback.1.13.md](feedback.1.13.md) | 2026-08-24 | **GO — M1 REMAINS PASSED; DASHBOARD FUNCTIONAL BOUNDARY ACCEPTED; VISUAL ITERATION REQUIRED** · M5/P2 **NO-GO** | F-039/F-040/F-041 reconfirmed CLOSED, F-042/F-043/F-044 (new, all closed same day) |
| [feedback.1.14.md](feedback.1.14.md) | 2026-08-25 | **GO — DASHBOARD VISUAL DIRECTION ACCEPTED; PRIMARY FOCUS RETURNS TO CI/M0/RECONCILIATION** · M1 **PASSED/MT5-INTEGRATED** · M2 **PASSED** · M0 **GO WITH CONDITIONS** · M5/P2 **NO-GO** | F-042/F-043/F-044 reconfirmed CLOSED, F-033 reconfirmed OPEN then CLOSED, F-045/F-046 (new, both closed same day) |
| [feedback.1.15.md](feedback.1.15.md) | 2026-08-25 | **GO — SHIFT FROM UI/FOUNDATION WORK TO THE M5 CRITICAL PATH** · M1 **PASSED** · M2 **PASSED** · Dashboard **VISUAL SCOPE FROZEN** · Live shadow decision pipeline **GO NOW** (execution stays disabled) · M5/first DEMO order **NO-GO YET — explicit critical path given** · Live money **out of scope** | F-045/F-046/F-044/F-033 reconfirmed CLOSED, F-047/F-048/F-049 (new, all OPEN), O-006 (new) |

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
| F-033 | MEDIUM | `status.md` current-state sections still lagged behind the first-contact update | **REOPENED 2026-08-24 by review 1.10, CLOSED, PARTLY REOPENED again 2026-08-24 by review 1.11, CLOSED, REOPENED a third time by review 1.12, CLOSED, REOPENED a fifth time by review 1.14, then CLOSED again the same day** | N/A — documentation | 1.9's fix was itself incomplete — the MT5 checklist, the M1 milestone row, "Overall health" and the repo/build checklist (stale `(no remote)`, stale test/file counts) all still described the reader as unbuilt after it had shipped. Rewritten to distinguish impl+unit-tested from real-terminal-validated per capability. Review 1.11 found a third instance: §12 "Next 10 actions" still listed the Pepperstone entity and Q2 hedging/netting as open after both were resolved elsewhere (O-005, `RETAIL_HEDGING`). Review 1.12 found a fourth instance: §1 "Overall health" (Data health, MT5 connectivity) and §3's MT5 checklist rows for bars/ticks collection and reconnect behaviour still read as unvalidated against the real terminal after Phase A/B had both succeeded. Review 1.14 §11 found a fifth instance: the platform checklist still said "no test in the repository has run against the real terminal, only the manual first-contact probe has" — silently ignoring Phase A/B, which are also manual-not-automated evidence but far beyond a single probe; the Risk section said "none can be MT5-integrated until M1" after M1 had already passed; and `APP-014` still read "PARTLY CLOSED... still open: continuous bar/tick read and observed reconnect behaviour" after both had been proven. All three rewritten in place, `status.md` §3/§13 (twenty-first entry). **Review 1.15 §2: "SUBSTANTIALLY CLOSED" — do not spend another engineering cycle on documentation cleanup unless a contradiction affects a gate or operator decision** |
| F-034 | HIGH | Reconnect must revalidate broker/account truth before data flow resumes | CLOSED | SHIPPED, real-terminal-validated | `src/crumblr/application/live_reader.py::LiveReader`. All five required scenarios pass against a scripted fake terminal (`tests/unit/test_live_reader.py`, 16 tests). **Phase B run 2026-08-24, owner present:** two deliberate MT5 terminal closures against the live Pepperstone demo, minutes apart. Both times: `live_reader.read_failed` (`IPC send failed`) detected the loss, `mt5.disconnected` followed, the terminal was restored, the reader reconnected on its own (`reconnect_count` 1→2→3), re-resolved the symbol, re-ran the account guard (no `AccountGuardError` — the account was still correct, and a wrong one would have gone `UNHEALTHY` instead of recovering, per the unit-tested scenario), re-detected the broker clock offset fresh both times (180 min, consistent with Phase A's measurement), and correctly flagged the instrument spec as changed on each reconnect rather than silently continuing (`live_reader.spec_changed`). Fresh ticks/bars resumed immediately after each reconnect — the last tick read was 1.6 seconds old at the moment of verification, and both tick and bar counts kept growing through both interruptions (2,920→3,578 ticks, 17→19 bars). `status.md` §13 seventeenth entry |
| F-035 | HIGH DESIGN | UI v0 must remain read-only and outside the broker execution boundary | CLOSED | SHIPPED | Dashboard v0 built 2026-08-24 (review 1.12 §8 trigger): reads PostgreSQL and the `LiveReader` health JSON snapshot only, no `MetaTrader5` import, no credentials, no `order_send`, no HALT reset, no risk-config write, no `TradeIntent` creation, no buttons — checked structurally, not only by intent: `tests/integration/test_dashboard.py::TestReadOnlyBoundary` walks every registered route for a mutating HTTP method and the AST of every file in `src/crumblr/dashboard/` for a forbidden import. Kept as its own package rather than folded into `api/`, which build.md §21 already earmarks for the opposite claim. Scope against build.md §22/M8's full spec recorded as D-043, not silently narrowed |
| F-036 | HIGH DESIGN | Reader acknowledgement must never equal automatic restoration of health | CLOSED | SHIPPED | `LiveReader.acknowledge()` only clears the sticky latch to `DISCONNECTED`; the next `poll_once()` performs a full fresh reconnect + revalidation (account guard, margin mode, symbol resolution, instrument spec) before status can become `HEALTHY` again. A real gap found while verifying this: `SymbolNotFoundError` from a missing/unresolvable symbol was not caught in `_reconnect()` and would have crashed the reader rather than failing closed — fixed, now `UNHEALTHY` like an account mismatch. Three new tests assert acknowledging a still-broken account/symbol does not restore `HEALTHY`, and that a genuinely fixed one does. Review 1.11 §2 closes this "based on documented test evidence" and asks that the invariant be kept when Dashboard v0 is built |
| F-037 | HIGH | MT5 timestamp semantics must be verified against the real feed before M1 is qualified | CLOSED | SHIPPED | Settled by observation, not assumption, per review 1.11 §7: the fourth real Phase A attempt (30 min, real Pepperstone demo, 19,437 ticks) showed a stable ~2:59:39-2:59:40 gap between the terminal's `event_time_utc` and this platform's own `received_time_utc` — a genuine, constant broker-clock offset, not UTC as `readonly.py` had assumed. `ReadOnlyMt5Gateway._clock_offset()` now measures this once per gateway instance (i.e. every `LiveReader` reconnect) via `symbol_info_tick`, rounds to the nearest 30 minutes, and corrects every timestamp the gateway converts (`ticks`, `bars`, `positions`) plus the `since` parameter sent to `copy_ticks_from`. Not hard-coded to +3, per the review's own instruction not to invent a correction without observation — this discovers it fresh every session, the same way the symbol and account are discovered rather than assumed. `tests/unit/test_mt5_readonly_gateway.py::TestClockOffset` (4 tests). D-039 updated with the full measurement and fix |
| F-038 | HIGH DATA INTEGRITY | D-041's chunked insert must have a proven, not assumed, failure/recovery contract | CLOSED | SHIPPED | Contract A (batch atomic) confirmed correct and now proven: `tests/integration/test_market_data_store.py::TestChunkedInsertFailureSemantics::test_a_failure_partway_through_a_multi_chunk_batch_rolls_back_the_whole_batch` injects a failure into the second of two chunks against a real PostgreSQL connection and asserts zero rows from the logical batch survive. `MarketDataStore._record_ticks` docstring now states the contract explicitly; `review/DEVIATIONS.md` D-041 updated with the proof |
| F-039 | HIGH BEFORE M5/RECONCILIATION | Semantic instrument identity must not change merely because it was re-observed | CLOSED | SHIPPED | The reviewer's guessed mechanism (a fresh `captured_at_utc` changing the fingerprint) was already wrong — that field was excluded from `spec_version` before this review. The real cause, traced to first-contact evidence already on record: `tick_value` is recomputed live by MT5 from the current EUR/USD cross-currency rate whenever the account currency differs from the quote currency, so it drifts with the market, not with broker policy — hashing it produced `spec_changed` on every Phase B reconnect. Excluded from `InstrumentSpec.spec_version`'s hash alongside `captured_at_utc`; still recorded on every spec, just not part of what "changed" means. `domain/models.py::InstrumentSpec.spec_version`; `tests/unit/test_control_plane_contracts.py::TestInstrumentSpecVersioning::test_a_tick_value_fluctuation_alone_does_not_change_the_version` |
| F-040 | HIGH BEFORE UNATTENDED/PAPER OPERATION | Broker-clock detection must fail closed when its reference tick is stale | CLOSED | SHIPPED | `ReadOnlyMt5Gateway._clock_offset()` now rejects a measurement whose residual from a clean half-hour multiple exceeds 3 minutes (real GMT offsets round cleanly — Phase A/B measured ~2:59:39-2:59:40 for 180 minutes, seconds of latency, not minutes) or whose magnitude exceeds ±15h, raising `ClockOffsetUnavailableError` rather than caching a bad value — the next call re-measures fresh rather than staying poisoned. `LiveReader` maps this to `DISCONNECTED` (not sticky, clears on a fresh tick), the same treatment as a stale feed rather than a wrong-account mismatch. `tests/unit/test_mt5_readonly_gateway.py::TestClockOffset` (+3), `tests/unit/test_live_reader.py::TestClockOffsetStaleReference` (+2) |
| F-041 | MEDIUM DATA GOVERNANCE | Operational soak/database reset must remain on the migration path | CLOSED | SHIPPED | The third Phase A attempt's real recovery mixed `bootstrap_schema()`/`create_all` with Alembic's `alembic_version` table — exactly the two-paths-disagreeing failure `persistence/migrations.py` already warns against. `scripts/reset_soak_database.py` provides the coherent alternative: `alembic downgrade base` -> `upgrade head` only, the same round trip `tests/integration/test_migrations.py::test_the_baseline_can_be_unwound` already proves is clean, run deliberately — refuses a URL without "soak" in it, requires `--yes`. Documented in `.env.example` |
| F-042 | MEDIUM / OWNER EXPERIENCE | Dashboard needs an explicit modern visual design baseline | CLOSED | SHIPPED | `src/crumblr/dashboard/templates/dashboard.html` rebuilt per review 1.13 §§4-10: dark charcoal/navy palette, card system, small status badges, the five-row layout (top bar with always-visible EXECUTION DISABLED; four status cards; EUR/USD hero + vanilla-JS/SVG candlestick chart of the last 60 M5 bars; connection/data-integrity/account-context panels; decision pipeline; activity timeline). Visual-state semantics (§9) implemented as one lookup (`app.py::state_class`) rather than repeated conditionals, with `UNKNOWN` in the unsafe/red bucket per the review's own "most conservative state dominates" rule. No new dependency beyond the FastAPI/Jinja2 stack already chosen — hand-rolled chart, no charting library, no JS framework |
| F-043 | HIGH UX/SAFETY | Dashboard stale-data presentation must be explicit | CLOSED | SHIPPED | `DashboardState` gained `mt5_connectivity`/`data_feed_state` (`CONNECTED/DISCONNECTED/UNKNOWN`, `HEALTHY/STALE/DOWN/UNKNOWN`), derived from `LiveReader`'s own status rather than a re-derived threshold; a missing reader-health snapshot reads as `UNKNOWN` (red), never `HEALTHY`. A database outage is caught at the route level and renders a distinct `DATABASE UNAVAILABLE` page at HTTP 503, never as an empty-but-normal-looking screen. All five required presentation cases are directly tested: fresh, stale, disconnected, missing snapshot, database unavailable (`tests/integration/test_dashboard.py::TestF043PresentationStates`, 5 tests) |
| F-044 | HIGH SEMANTIC INTEGRITY | UI must not invent "live platform decisions" from replay-only events | CLOSED | SHIPPED | Confirmed by reading the code, not assuming: `application/live_reader.py` has no reference to `TradingAgent`/`RiskEngine`/`Supervisor` — nothing in this codebase feeds a live MT5 tick into a live decision today. Every journalled decision is therefore a replay/backtest artifact; `DashboardState.decision_pipeline_label` says so unconditionally (`"LATEST REPLAY DECISION"` or `"NO LIVE DECISION PIPELINE ACTIVE"`), and every decision carries `environment`/`source`/`occurred_at_utc`/`correlation_id`/a version label alongside its verdict, both in the API and the rendered pipeline banner. `tests/integration/test_dashboard.py::TestF044DecisionContextIsNeverAmbiguous` (2 tests). **Visual refinement, review 1.14 §5:** the visible section heading still read "Decision pipeline — latest window", easier to misread as live than the banner beneath it already prevented — changed to "Decision pipeline — latest replay window". **Reconfirmed by review 1.15 §2**: "semantically honest while no live decision pipeline exists" |
| F-045 | MEDIUM / SEMANTIC UX | Environment badge must describe the actual operating state, not the config namespace | CLOSED — **reconfirmed by review 1.15 §2** | SHIPPED | The top-bar badge showed the raw `Environment.PAPER` value, which reads as an active paper-execution campaign to an owner glancing at the screen — none has started (`status.md` "Paper campaign: NOT STARTED") and this build has no order path at all (F-035). `DashboardState.environment_badge_label` (`state.py::_environment_badge_label`) now renders `PAPER` as `"DEMO DATA"`; every other `Environment` value (`BACKTEST`/`REPLAY`/`SHADOW`/`LIVE`) is unambiguous already and passes through unchanged. `tests/integration/test_dashboard.py::TestF045EnvironmentBadgeIsNotMisreadAsACampaign` (3 tests) |
| F-046 | MEDIUM/HIGH UX-SAFETY | Historical market data needs a stronger visual "not live" treatment | CLOSED — **reconfirmed by review 1.15 §2** | SHIPPED | Whenever `data_feed_state != "HEALTHY"` (stale, disconnected, or no reader session at all), the EUR/USD hero now shows a dedicated "Historical data — no active live data session" banner (with the last-live-tick age alongside it when a tick exists) and the candlestick chart gets a subdued "No active live data session" overlay — the chart itself stays visible, only its live-ness claim is withdrawn, per the review's explicit "do not hide the chart" instruction. Both the initial server render and the 5-second JS poll refresh keep this in sync as `data_feed_state` changes. Age formatting in the JS poller (`formatAge()`) was also brought in line with the server-side `format_age()` — "16h 48m", not a raw minute count past an hour, matching review §9.B's request that the runtime build actually use the already-improved formatter. `tests/integration/test_dashboard.py::TestF046HistoricalDataIsNeverMistakenForLive` (3 tests) |
| F-047 | HIGH BEFORE M5 / FIRST ORDER | Durable broker account/position/pending-order state is missing — nothing persists MT5's own balance, equity, open positions or pending orders | OPEN | PENDING | Review 1.15 §5: the platform journals decisions and replay `PositionChanged` events but has no durable producer for the *observed real broker* account/position/pending-order truth. Blocks reconciliation, live risk state and the first autonomous demo order. Required: append-only `broker_account_snapshots`/`broker_position_snapshots`/`broker_pending_order_snapshots` (or one snapshot table with child rows), `balance` **and** `equity` both persisted as `Decimal`/`NUMERIC`, explicit `COMPLETE`/`UNKNOWN`/`FAILED` set-completeness state (the existing `positions_get(None)` fail-vs-empty distinction must survive into persistence), captured at connect/reconnect/each live decision window/around order submission/after reconciliation mismatches/after fill or position change. No MT5 login persisted or displayed |
| F-048 | HIGH PATH-TO-M5 | Trading Agent must be attached to a real live-data decision pipeline, execution disabled | OPEN | PENDING | Review 1.15 §7: `LiveReader` (real MT5 ticks/bars → PostgreSQL) and `ReplayOrchestrator` (Trading Agent → Risk → Supervisor) remain two unconnected systems — correct for M1, now the main blocker to the agent operating on real observations. Required: a live/shadow decision orchestrator — real closed M5 bar → feature pipeline → Trading Agent → `TradeIntent`/`NO_TRADE` → intent-time Risk Engine → Supervisor → **stop, no order submission**. Full decision chain persisted. Agent boundary stays non-negotiable regardless of implementation (deterministic or AI-assisted): no MT5 credentials, no `order_send` access, no HALT-reset authority, no risk-policy mutation, no unrestricted lot-size or promotion authority (review §8) |
| F-049 | CRITICAL BEFORE M5 | First demo order must be multi-gated, not merely "AlgoTrading is on" | OPEN | PENDING | Review 1.15 §14: order submission must require environment=DEMO, verified account/server, reconciliation=MATCHED, market data=HEALTHY, safety state=RUNNING, owner-approved risk policy, execution adapter explicitly enabled, terminal AlgoTrading enabled, and `feedback.2.0` GO — simultaneously. Any one false or unknown → submission unavailable. Formalizes/extends ADR-001's execution-time revalidation; not yet implemented (correctly, since M5 is NO-GO) |

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

## Unreviewed work — `feedback.1.16.md` now triggered

`feedback.1.7.md` through `feedback.1.15.md` have all been processed
(F-026…F-049, O-006, above). **M1 remains PASSED, M2 remains PASSED**
(`status.md` §16 Promotion history); Dashboard v0's visual scope is now
**frozen** (review 1.15 §15 — only broker-state/reconciliation/real-decision
data panels may still be added). Review 1.15 §17 sets the trigger for the
next regular review: a meaningful package such as CI+M0 closure, and/or
broker-state persistence + reconciliation, and/or live shadow Agent
decisions on real Pepperstone M5 data — explicitly **not** "after one small
implementation change."

Pruned per the standing instruction (review 1.13 §13) not to let this table
grow by accretion — everything from reviews 1.7 through 1.14 has now been
seen by a reviewer (1.14 and/or 1.15 itself) and is retired from this list;
see `status.md` §13's chronological log for the full history.

**Nothing below has been seen by a reviewer yet:**

| Artifact | What it claims | Where to check it |
|---|---|---|
| F-045/F-046/F-044/F-033 (reconfirmed by 1.15 §2, first shipped between 1.14 and 1.15) | Environment badge, historical/offline treatment, decision-pipeline heading copy, current-state documentation — see the finding register above | `src/crumblr/dashboard/` |
| CI | Still not confirmed running — unchanged since review 1.10; review 1.15 §11 says M0 "has been open long enough" | — |
| Domain-contract package for reviewer approval | Not yet assembled — review 1.14 §13 names the twelve contracts to cover; review 1.15 §11 repeats the requirement | — |
| **F-047 — durable broker account/position/pending-order snapshots (new, review 1.15)** | Not yet built — no producer persists MT5's observed balance/equity/margin, open positions or pending orders; blocks reconciliation and the first demo order | — |
| **F-048 — live/shadow decision orchestrator (new, review 1.15)** | Not yet built — `LiveReader` and `ReplayOrchestrator`/Trading Agent remain unconnected; real M5 bars do not yet reach the Agent/Risk/Supervisor chain even in shadow (execution-disabled) mode | — |
| **F-049 — multi-gated execution enablement (new, review 1.15)** | Not yet built, correctly — M5 is NO-GO. Formalizes the simultaneous-gate requirement (environment/account/reconciliation/data/safety/risk-policy/adapter/AlgoTrading/`feedback.2.0`) for the eventual first order | — |
| Read-only reconciliation | Not yet built — review 1.14 §14, restated as a direct F-047 dependent by review 1.15 §10 | — |

`feedback.2.0.md` remains mandatory before the first `order_send`, demo
included, and is separate from 1.15.

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
| O-006 | Review 1.15 §3: the owner's direction toward "daadwerkelijk getrade kan worden" is interpreted, for the next promotion, as **MT5 DEMO account, autonomous decisioning, real order submission, real demo fills, zero live-money exposure** — not a live account, not real money, not a higher autonomy level, not strategy promotion from a handful of demo trades | Sets the concrete near-term target: reach one controlled, `feedback.2.0`-gated autonomous DEMO canary order (review §13), not live trading. Reprioritizes engineering from dashboard/foundation polish to the M5 critical path: CI/M0 closure → F-047 broker-state persistence → reconciliation → F-048 live shadow decision pipeline (execution disabled) → execution safety work → `feedback.2.0` → the canary order (review §12) |

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
