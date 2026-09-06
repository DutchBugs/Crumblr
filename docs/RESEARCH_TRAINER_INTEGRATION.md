# Crumblr research-plane and Trainer integration

Status: implemented as a research-only Step-D boundary on 2026-09-06.

## Ownership

- A Strategy Agent or an existing governed source produces a
  `StrategyArtifact`.
- Crumblr freezes identity, hashes, dataset and `BacktestRequest`.
- The external Trainer runs bounded research and returns evidence plus an
  unapproved candidate.
- Crumblr stores `BacktestReport`, `EvaluationRecord`, `TrainingFinding` and
  `StrategyChangeProposal` as immutable content-addressed records.
- Human governance remains the only path from candidate to an approved
  artifact or changed `TradingAssignment`.

The research package has no imports into execution, Risk, Policy, MT5 or the
Agent Gateway assignment writer.

## Implemented contracts

`src/crumblr/research/contracts.py` contains:

1. `StrategyArtifact`
2. `BacktestRequest`
3. `BacktestReport`
4. `EvaluationRecord`
5. `TrainingFinding`
6. `StrategyChangeProposal`

Every contract is immutable and rejects unknown fields through Crumblr's
existing `Contract` base. `StrategyChangeProposal` can only describe an
unapproved research result: human approval is mandatory and execution and
automatic-promotion authority are structurally false.

## Durable storage

`JsonlResearchArtifactRegistry` is an append-only, fsync-backed adapter for
the first external Trainer rollout. Repeating identical content is idempotent;
reusing an identity with different content fails closed. Its interface is
storage-neutral so a PostgreSQL implementation can replace it without changing
the Trainer client or wire contracts.

## Trainer workflow

`CrumblrTrainerClient` performs the bounded sequence:

1. verify Trainer capabilities;
2. register immutable strategy and request locally;
3. upload the strategy ZIP;
4. create the campaign with Crumblr provenance;
5. upload the frozen candle CSV;
6. start or resume the autonomous loop;
7. read campaign and live agent progress;
8. fetch and validate an unapproved candidate;
9. translate and store all research artifacts.

Remote Trainer URLs require HTTPS. Responses are size-bounded and malformed,
cross-campaign, wrong-dataset, wrong-parent or authority-claiming results fail
closed. The Trainer API credential is used only for its research API and is
never written into an artifact.

## Deliberate boundary

This completes the Crumblr-to-Trainer research connection. It does not approve
or assign the produced candidate. A later governance operation must review the
`StrategyChangeProposal`, register an approved immutable `StrategyArtifact`
and explicitly update a `TradingAssignment`.
