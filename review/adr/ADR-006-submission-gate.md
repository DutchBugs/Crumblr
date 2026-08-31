# ADR-006 — SubmissionGate (F-049)

**Status:** ACCEPTED — implemented and tested; called by nobody yet
**Date:** 2026-08-28
**Drivers:** review 1.15 §14 (F-049, `review/FEEDBACK.md`), review 1.24 §12.B,
review 1.25 §4/§12.B, `CRUMBLR_DEV1_CORE_EXECUTION_INSTRUCTIONS.md` §2
**Supersedes:** nothing. Replaces the always-refusing stub
`risk/submission_gate.py` shipped as part of Phase 4.
**Implementation:** `src/crumblr/risk/submission_gate.py`,
`src/crumblr/config.py` (`RiskConfig.approved_config_version`,
`ExecutionConfig.submission_enabled`, `ExecutionConfig.feedback_2_0_approved`),
`tests/unit/test_execution_gates.py::TestSubmissionGate`

---

## 1. The decision being recorded

`review/PHASE4_PLAN_REVIEW_GO_WITH_TWEAKS.md` point 2 required
`SubmissionGate` to stay a separate, later question from
`ExecutionPreflightGate` — the different question being *whether a real
`order_send` may run*, not whether the non-sending preflight chain may.
Phase 4 shipped it as a stub that always refused, deliberately, because
building the real multi-gate before the conditions it checks even had a
place to be recorded would have meant inventing a placeholder for those
conditions too.

This ADR records that `evaluate_submission_gate()` is now the real
function review 1.15 §14 specified, and names exactly how each of its nine
required conditions is satisfied.

**This does not make `order_send` reachable.**
`OrderCheckMt5Gateway.order_send` (`mt5_gateway/execution.py`) still
unconditionally raises `ExecutionDisabledError` — nothing about this
function's existence changes that. Nothing in `src/` calls
`evaluate_submission_gate()` — there is no `SubmissionOrchestrator`. The
gate is real and tested; it remains called by nobody, the same
inert-by-construction discipline the rest of this codebase already uses
(`ApprovedOrder`/`ExecutionResult` were built the same way, ahead of the
engine that would use them).

---

## 2. The nine conditions

Review 1.15 §14, verbatim requirement: submission requires — **all
simultaneously; any one false or unknown closes it**:

| # | Condition | Signal | Reason code if failed |
|---|---|---|---|
| 1 | `environment=DEMO` | `environment in DEMO_ONLY_ENVIRONMENTS` (`config.py`, already `{PAPER, SHADOW}`) | `LIVE_EXECUTION_NOT_PERMITTED` |
| 2 | verified account | `AccountState.is_demo` / `.connected` (a fresh, already-observed read — the gate does not touch MT5 itself) | `LIVE_ACCOUNT_IN_PAPER_MODE` / `ACCOUNT_NOT_CONNECTED` |
| 3 | reconciliation=MATCHED | `ReconciliationStatus` | `RECONCILIATION_MISMATCH` / `RECONCILIATION_UNKNOWN` |
| 4 | market data=HEALTHY | fresh tick age vs. `ExecutionConfig.max_market_data_age_ms`, plus `DataQuality` | `STALE_MARKET_DATA` / `INVALID_QUOTE` |
| 5 | safety=RUNNING | `KillSwitch.is_halted` | `SYSTEM_HALTED` |
| 6 | owner-approved risk policy | `RiskConfig.approved_config_version` (new) checked against `PlatformConfig.config_version` | `RISK_POLICY_NOT_APPROVED` (new) |
| 7 | execution adapter explicitly enabled | `ExecutionConfig.submission_enabled` (new) | `EXECUTION_NOT_EXPLICITLY_ENABLED` (new) |
| 8 | terminal AlgoTrading enabled | `terminal_health()['trade_allowed']` (a fresh, already-observed read) | `ALGOTRADING_DISABLED` (new) |
| 9 | `feedback.2.0` GO | `ExecutionConfig.feedback_2_0_approved` (new) | `FEEDBACK_2_0_NOT_APPROVED` (new) |

`evaluate_submission_gate()` is a pure function taking a
`SubmissionGateContext` — every signal pre-observed, nothing fetched by
the gate itself, exactly mirroring `evaluate_preflight_gate`'s style
(`risk/execution_preflight_gate.py`). It collects every failing reason
rather than short-circuiting on the first, the same philosophy
`risk/policies.py::evaluate()` already established: an operator looking
at a closed gate should see every reason it is closed, not just the first
one encountered.

---

## 3. Why conditions 6, 7 and 9 needed new config surface

No durable source of truth existed anywhere in this codebase for "the
owner approved this risk policy," "the execution adapter is explicitly
enabled," or "`feedback.2.0` gave its GO." Three fields were added,
deliberately shaped like the one precedent this project already has for
exactly this kind of durable, human-set approval —
`MarketConfig.expected_spec_version` (F-055, review 1.19 §4):

- `RiskConfig.approved_config_version: str | None = None`, compared
  against `PlatformConfig.config_version` (a content hash of the whole
  configuration). `None` means unapproved. Changing *any* config value
  changes `config_version`, which automatically invalidates a prior
  approval — an approval is for one exact, git-reviewable configuration,
  not a standing blanket permission that survives edits.
- `ExecutionConfig.submission_enabled: bool = False`.
- `ExecutionConfig.feedback_2_0_approved: bool = False`.

**No shipped config file sets any of the three.** `config/base.yaml` and
`config/paper.yaml` were not touched by this change —
`tests/unit/test_execution_gates.py::TestSubmissionGate
::test_the_gate_is_closed_against_the_actual_shipped_config` builds a
`SubmissionGateContext` from `load_config()`'s real, current values and
asserts the gate is closed with all three of `RISK_POLICY_NOT_APPROVED`,
`EXECUTION_NOT_EXPLICITLY_ENABLED`, and `FEEDBACK_2_0_NOT_APPROVED`
present. This is the concrete proof, not merely design intent, that the
gate does not quietly open in production today.

---

## 4. Consequences

- `review/FEEDBACK.md`'s F-049 finding moves to `SHIPPED` — the gate
  itself is real, tested, and closed against production config. The
  finding's own text is explicit that this alone is not M5/submission
  readiness; `order_send` remains a separate, still-impossible act.
- A future `SubmissionOrchestrator` (not built here) is what will
  eventually call `evaluate_submission_gate()` before whatever future,
  gated `order_send` call exists. Building that orchestrator, wiring
  `SUBMISSION_STARTED` emission at the correct pre-side-effect point,
  `order_send` idempotence, ambiguous-outcome recovery, automatic flatten
  submission, post-fill reconciliation, and broker-side SL verification
  are each their own separate, later item (review 1.24 §12.B / review
  1.25 §4).
- Setting `submission_enabled`/`feedback_2_0_approved`/
  `approved_config_version` to anything but their closed defaults is an
  owner decision, never an engineering one — this ADR does not authorize
  or recommend when that should happen.

---

## 5. Addendum 2026-08-28 — condition 6 was unsatisfiable as originally shipped (F-062)

While wiring the orchestrator caller this ADR anticipated
(`application/execution.py::_evaluate_submission_readiness`, "durable
execution-activation wiring," Dev-1 core critical path item 2),
condition 6 turned out to be impossible to satisfy by construction, not
merely unapproved: `RiskConfig.approved_config_version` is compared
against `PlatformConfig.config_version`, and `config_version` was a
content hash of the *entire* config — including
`approved_config_version` itself. Writing the approved hash into the
file changed the file, which changed the hash the write was supposed to
match. Confirmed empirically before any fix: setting
`approved_config_version` to the config's own current `config_version`
and recomputing produced a *different* `config_version`, every time.

This is not the `MarketConfig.expected_spec_version` precedent §3 cites
— that field pins a hash of a genuinely separate artifact (the observed
`InstrumentSpec`), never itself part of the hash it's compared against.
`approved_config_version` lives inside the very object it was compared
to, which the precedent does not.

**Fix**: `PlatformConfig.config_version` now excludes the three
governance/approval fields (`risk.approved_config_version`,
`execution.submission_enabled`, `execution.feedback_2_0_approved`) from
what it hashes — it represents the substantive, risk-bearing content an
owner reviews and approves, not whether that review already happened.
Every other field still changes the version on any edit, unchanged from
before (`tests/unit/test_config.py::TestConfigVersioning`). New test:
`test_approving_this_exact_version_does_not_change_it` proves the fixed
point now holds. No shipped config sets any of the three fields, so this
changes no config file and no other reachable behaviour —
`DecisionCapsule.risk_config_version` binding, `test_the_gate_is_closed
_against_the_actual_shipped_config`, and everything else that reads
`config_version` still gets the value it always got before.

Not a live-trading risk either way: `order_send` stays structurally
unreachable regardless of this leg. But a CRITICAL-severity gate whose
"owner approves" leg could never actually be satisfied is a real defect
in what F-049 delivered, logged as `review/FEEDBACK.md` F-062.
