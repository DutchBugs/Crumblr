# ADR-018 — One-shot DEMO canary permit (Phase B item B8)

**Status:** ACCEPTED — implemented and tested; not wired into
`ExecutionOrchestrator`; no issuance tool built
**Date:** 2026-09-03
**Drivers:** Owner/reviewer coordination order
`review/OWNER_WORK_ORDERS_DEMO_CANARY_2026-09-03.md`, Phase B item B8;
`build.md` §"Gate P4 — guarded live canary" (one symbol, one strategy,
tight risk budget, manual approval — the exact shape this item
implements for the DEMO canary)
**Supersedes:** nothing.
**Implementation:** `src/crumblr/domain/models.py` (`CanaryPermit`,
`CanaryPermitConsumption`), `src/crumblr/persistence/schema.py`,
`src/crumblr/persistence/canary_permit.py`,
`migrations/versions/20260903_e91f4a7c2b53_canary_permit_tables.py`

---

## 1. The decision being recorded

B8's own wording: a durable, operator-issued, one-shot permit binding
account reference + server, EUR/USD, MARKET-only, an optional
agent/assignment/strategy-artifact identity, an owner-chosen risk cap,
one submission attempt, a short validity window, and operator
provenance. *"A consumed/expired permit can never be reused or
auto-reset."* The reviewer's 0.25%-of-equity cap recommendation is
explicitly not owner-approved policy — never hardcoded here.

Same scope discipline as B1+B2/B7: this item builds the durable
mechanism, real and tested, but does not wire a call to `consume()`
from `ExecutionOrchestrator` — B2's own required chain still needs
Phase C/AG-012's shared execution/Risk authority first.

## 2. A structural constraint that shaped the design

`persistence/schema.py::APPEND_ONLY_TABLES`/`append_only_grants()` is
not a convention — it is a real PostgreSQL grant restriction:
`GRANT SELECT, INSERT` only, for every current table, application-role-
wide. **No table may ever be `UPDATE`d.** "Consuming" a permit therefore
cannot be an in-place update of the issued row — it must be a second,
append-only fact. `canary_permit_consumptions` is that fact, with
`permit_id` as its own primary key: at most one row can ever exist per
permit, the identical idiom `execution_requests.order_request_id`
already uses to enforce its own idempotency-key invariant
(`persistence/execution.py::ExecutionRequestStore._claim`).
`CanaryPermitStore.consume()` mirrors `_claim()`'s exact shape: attempt
`INSERT ... ON CONFLICT (permit_id) DO NOTHING RETURNING permit_id`; a
loss reads back the existing row and reports who actually holds it,
never silently reporting success for the loser of the race.

## 3. Domain-model validation, not trust

`CanaryPermit`'s own `model_validator` refuses, at construction time,
before any persistence: a non-EUR/USD `canonical_symbol`, a non-MARKET
`entry_type`, a partial agent/assignment/strategy-artifact identity
binding (all three or none — a partial binding scopes nothing), a
`valid_until_utc` not strictly after `issued_at_utc`, and a validity
window longer than 24 hours. The 24h bound is a named, reversible
engineering choice — "a short validity window" per the owner's own
wording — not owner policy, mirroring D-053's own
engineering-chosen-not-owner-policy framing for `max_open_positions`.
`max_requested_risk_fraction` reuses `RiskFraction` (`domain/money.py`)
directly — no new numeric type, same `(0, 1]` bound and binary-float
rejection every other risk fraction in this codebase already has.
`approved_account_ref` reuses the exact `login_hash`-style fingerprint
`ExecutionConfig.approved_canary_account_ref` (Phase B item B7) already
established — never the raw account number.

## 4. `expired` is checked before any write is attempted

`consume()` reads the permit first; if `now > valid_until_utc`, it
returns `EXPIRED` without attempting the consumption insert at all —
proven directly by a test asserting zero rows exist in
`canary_permit_consumptions` afterward. An expired permit cannot be
consumed even by the very first caller to try, matching "a
consumed/expired permit can never be reused" literally for both
conditions, not just the consumed one.

## 5. What this does not do

- Does not wire `consume()` into `application/execution.py` — no new
  `ReasonCode`, no orchestrator changes at all. That belongs with the
  eventual chain-wiring slice, once Phase C/AG-012 exists and knows the
  exact refusal shape it needs.
- Does not build an operator-facing CLI/script to call `issue()` for a
  real canary — the durable mechanism is this item's contract (matching
  Dev-1 Phase-B's own "definition of done": "one-shot permit is proven
  atomic/idempotent"); a real issuance tool is Phase E/F's own concern.
- Does not hardcode the reviewer's 0.25% recommendation anywhere —
  `max_requested_risk_fraction` is supplied per-issuance, always.
- Does not touch B3/B5, or Phase C/AG-012.

## 6. Consequences

- No new `review/DEVIATIONS.md` entry — `build.md`'s own "Gate P4"
  section already calls for exactly this shape (one symbol, one
  strategy, tight risk budget, manual approval); this item implements
  the spec, it does not depart from it.
- `status.md` records this as a Dev-1 Phase-B deliverable — no new
  O-number needed, same reasoning as every prior Phase-B ADR.
- No `review/INTEGRATION_NOTICES.md` entry — no cross-track call-site
  changes; `application/execution.py` is untouched.
