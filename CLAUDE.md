# Working agreements for Crumblr

Read this before doing anything else in this repository.

## 1. Session start protocol — non-negotiable

**Before picking up any new request, read [review/FEEDBACK.md](review/FEEDBACK.md).**

An independent reviewing agent writes findings into that file. Any item under
`## Open` must be resolved, or explicitly answered with a reason for not acting,
*before* new work begins. Starting new work on top of unaddressed review
findings compounds whatever the reviewer caught.

The sequence at the start of every session:

1. Read `review/FEEDBACK.md`. If `## Open` is non-empty, handle those items
   first and tell the user you are doing so.
2. Move each handled item to `## Processed` with the date, what was done, and
   the commit or file touched. Never delete a finding.
3. Skim `status.md` §1-2 to reload where the project actually stands.
4. Only then start on the new request.

If the user's request directly contradicts an open finding, say so rather than
silently choosing one.

## 2. Where things are recorded

| Document | Holds | Who writes it |
|---|---|---|
| `build.md` | Architecture and risk specification. The contract. | Human, rarely |
| `status.md` | Gates, milestones, decision log, update log, incidents | Claude, every session with meaningful progress |
| `review/DEVIATIONS.md` | Every place the code departs from `build.md`, and why | Claude, when a deviation is introduced |
| `review/FEEDBACK.md` | Reviewer findings and their resolution | Reviewer writes; Claude resolves |
| `README.md` | How to run it, current capability, what is not built | Claude |

`build.md` is the specification and is not edited to match the implementation.
When the implementation needs to depart from it, that goes in
`review/DEVIATIONS.md` with a rationale — the gap stays visible instead of
being papered over.

## 3. After every meaningful change

1. Add an entry to `status.md` §13 using the template in §14. Include the
   evidence (test counts, coverage, what was actually run) and the problems
   found, not only the wins.
2. If the change departs from `build.md`, add or update the entry in
   `review/DEVIATIONS.md`.
3. If it is an architectural or risk decision, add a row to `status.md` §10.
4. Update the milestone tracker and health lines honestly. "AMBER because CI
   has never run" is more useful than a green light nobody verified.

## 4. Project rules

- **No live trading.** `config/live.yaml` does not exist and must not be
  created without an explicit, recorded human promotion decision.
- **Do not commit** unless the user asks. They are holding commits until a
  working prototype satisfies them.
- **No secrets** in config, code, logs, notebooks or `status.md`.
- **Do not tune the strategy against synthetic data.** `baseline_v1` exists to
  exercise infrastructure. Optimising it against a random walk is overfitting
  with extra steps.
- **Report failures plainly.** If tests fail or a step was skipped, say so with
  the output. A status document that flatters the project is worse than none.

## 5. Quality gate

Everything below must pass before a change is considered done:

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

Determinism is part of the gate — two identical replays must produce identical
output:

```bash
uv run python scripts/run_replay.py --bars 600 | md5
```
