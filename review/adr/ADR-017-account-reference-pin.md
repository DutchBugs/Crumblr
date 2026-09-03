# ADR-017 — Exact account-reference pin on SubmissionGate (Phase B item B7)

**Status:** ACCEPTED — implemented and tested; `SubmissionGate` remains
called by nobody real (no shipped config approves any of its ten
conditions)
**Date:** 2026-09-03
**Drivers:** Owner/reviewer coordination order
`review/OWNER_WORK_ORDERS_DEMO_CANARY_2026-09-03.md`, Phase B item B7
**Supersedes:** nothing. Adds condition 10 to ADR-006's `SubmissionGate`.
**Implementation:** `src/crumblr/config.py`,
`src/crumblr/risk/submission_gate.py`,
`src/crumblr/application/execution.py`

---

## 1. The decision being recorded

B7's own wording: *"Add an owner-approved exact account-reference pin
for real DEMO submission... Submission must require simultaneously:
exact approved account reference; exact expected Pepperstone DEMO
server; demo account flag; expected currency/leverage; current
reconciliation MATCHED. A different demo account is a refusal even if
all other fields look plausible."* §1.7 adds: prefer the existing
`AccountState.login_hash` style over the raw account number; the gate
must fail closed when the observed account reference is not the exact
approved canary account.

## 2. A landmine found during research, and how this design avoids it

`review/DEVIATIONS.md` D-046's own "Watch for" already warns: setting a
real `AccountGuardConfig.expected_login` would *"close itself... silently
BLOCK every live intent, safely but confusingly"* — because
`application/live_decision.py` reconstructs `AccountState` from a
durable `BrokerAccountSnapshot` that never carries the raw MT5 login
(build.md §21), so its `AccountState.login` is a placeholder `0`.

Confirmed directly by grepping every `RiskContext(` construction site
for `expected_login=`: `live_decision.py`, `execution.py`,
`paper_lite.py` and `agent_gateway/decision_path.py` **all** hardcode
`expected_login=None` — only `orchestration.py` (the synthetic-data
replay path) reads `config.account_guard.expected_login` for real. The
existing general-purpose `AccountGuardConfig.expected_login` field is
therefore inert everywhere today except replay and the raw MT5
gateway's own `_verify_account()` guard — which is used by
`capture_broker_state`/`order_check`/the new `order_send` **and by the
currently-running real `LiveReader`/`mt5_live_reader.py` process**.

Setting a real value into `AccountGuardConfig.expected_login` would
therefore have been the wrong mechanism: it would immediately affect the
already-running real reader, and would still do nothing for the
execution-time `RiskContext` leg (which hardcodes `None` independent of
config). **This item deliberately does not touch
`AccountGuardConfig.expected_login`, any of the four hardcoded
`expected_login=None` call sites, or `_account_state_from_snapshot`** —
that is exactly the D-046-flagged revisit, left exactly as deferred as
before.

Instead: `SubmissionGateContext.account` already comes from a fresh,
real, per-pass `capture_broker_state()` read
(`observation.account_state`) in `execution.py`'s own
`_evaluate_submission_readiness()` — genuinely trustworthy, unlike
`live_decision.py`'s reconstructed snapshot. A new, narrowly-scoped
`SubmissionGate` leg is sufficient and has zero blast radius onto the
live reader or the other three orchestrators.

## 3. The mechanism

New `ExecutionConfig.approved_canary_account_ref: str | None = None` —
an owner-approval-gated field in the same style as
`submission_enabled`/`feedback_2_0_approved`: defaults unset, no
shipped config sets it, flipping it is a deliberate, git-reviewed,
owner-made act (Phase E). Holds a `login_hash`-style fingerprint
(`fingerprint({"login": ..., "server": ...})[:16]`, the exact technique
`AccountState.login_hash`/`ExpectedState.expected_account_ref` already
use) — never the raw account number.

`SubmissionGateContext` gains `approved_account_ref: str | None`.
`evaluate_submission_gate()` gains a tenth condition:

```python
if context.approved_account_ref != context.account.login_hash:
    reasons.append(ReasonCode.WRONG_ACCOUNT)
```

A plain inequality, mirroring condition 6
(`approved_risk_config_version != risk_config_version`) exactly — fails
closed automatically when `approved_account_ref` is `None` (every
shipped config today), no separate null-check needed, opens once an
owner sets the exact matching fingerprint. `ReasonCode.WRONG_ACCOUNT`
(not a new code) is reused rather than growing the vocabulary with a
near-duplicate meaning — the same precedent `ACCOUNT_NOT_CONNECTED`
already sets by being shared across `risk/policies.py` and
`submission_gate.py` for the identical underlying concern, checked at a
different layer.

`_evaluate_submission_readiness()` passes the config value through and
logs both `approved_account_ref` and the freshly observed
`observed_account_ref` (`context.account.login_hash`) in the
`SUBMISSION_GATE_PASSED`/`SUBMISSION_GATE_BLOCKED` payload — both
fingerprints, safe to log per `login_hash`'s own docstring, giving an
operator direct visibility into exactly what didn't match.

`B7`'s other four requirements (server, demo flag, currency, leverage,
reconciliation MATCHED) were already satisfied before this item:
server/demo/currency/leverage by `ReadOnlyMt5Gateway._verify_account()`
(raises `AccountGuardError`, an exception-based fail-closed — if any of
those four mismatch, `capture_broker_state()` itself refuses before
`_process()` ever reaches gate construction), and reconciliation MATCHED
by condition 3, unchanged. This item's own gap was specifically the
*exact reference* — which one demo account among possibly-many sharing
the same server/currency/leverage.

## 4. A second circularity, caught before shipping (mirrors F-062)

The first version of this change made
`test_a_fully_approved_config_reaches_submission_started` fail closed on
`RISK_POLICY_NOT_APPROVED` — not `WRONG_ACCOUNT` as expected. Root
cause: `PlatformConfig.config_version` (ADR-006 §5/F-062) excludes
`risk.approved_config_version`/`execution.submission_enabled`/
`execution.feedback_2_0_approved`/`execution.flatten_submission_enabled`
from its content hash, but the new `approved_canary_account_ref` field
was not yet in that exclusion set — so setting it changed
`config_version` itself, invalidating the `approved_config_version`
pin computed moments earlier from the pre-change hash. The exact
self-referential trap F-062 already named and fixed for the first three
fields, reproduced for a fourth. Fixed by adding
`approved_canary_account_ref` to the same exclusion set — see ADR-006
§7 for the amendment.

## 5. What this does not do

- Does not touch `AccountGuardConfig.expected_login`, any
  `RiskContext(expected_login=...)` call site, or
  `_account_state_from_snapshot` (§2).
- Does not touch `ReadOnlyMt5Gateway._verify_account()` — already
  adequate for the other four B7 requirements.
- Does not build Phase B items B3/B5/B8, or Phase C/AG-012.
- No shipped config sets `approved_canary_account_ref` — this item
  changes no reachable behaviour anywhere until an owner acts (Phase E).

## 6. Consequences

- No new `review/DEVIATIONS.md` entry — directly implements B7 rather
  than departing from `build.md`.
- ADR-006 amended (§7) rather than superseded — the gate is one
  document, ten conditions now, not two competing descriptions of it.
- `status.md` records this as a Dev-1 Phase-B deliverable — no new
  O-number needed, same reasoning as every prior Phase-B/item-9 ADR.
- No `review/INTEGRATION_NOTICES.md` entry — no cross-track call-site
  signature changes; Dev 2 notified informationally about the D-046
  finding since `agent_gateway/decision_path.py` also hardcodes
  `expected_login=None` and would hit the identical trap if ever
  revisited on their side.
