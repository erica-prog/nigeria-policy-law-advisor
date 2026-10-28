# 38 — CI gates, the drafting rubric and connector canaries

**Status:** implemented (docs/30 slice P3-H). **Decisions:** ADR-001 decision 9 and action
18; docs/30 D10.

## Two workflows

| Workflow | Runs on | Needs | Jobs |
|---|---|---|---|
| `ci.yml` | every pull request, push to main | nothing beyond package installs and a local Postgres | lint, mypy, the unit tests, the Node connector tests, routing evals, the extraction oracle, the drafting rubric on recorded fixtures |
| `nightly.yml` | 03:00 UTC daily, push to main, by hand | the repository's Anthropic key, connector credentials, the network | retrieval golden sets (NG and US), case-reasoning invariants, extraction F1 (advisory), live drafting scored by the rubric (advisory), connector canaries |

A pull request is gated only by what a contributor can run locally without a key. The
keyed runs still happen on every push to main, so a regression reaches the log the
same day; they stop being a reason a pull request cannot merge because of a rate limit
or a provider outage.

## The drafting rubric

`eval/drafting/rubric.py` scores a draft from its outline and provenance manifest alone,
seven criteria, each 0 to 1: grounded (authority paragraphs whose every source is
verified), no evidence cited as authority, instruction paragraphs carrying no sources,
the playbook's sections present, limitations stated, no section flagged by the judge,
sensible bounds. The headline is the mean.

Two ways to run it. `--fixtures` scores recorded artefacts under
`eval/drafting/fixtures/` without a model; each fixture states what it should score, so
a good memo must reach 1.0 and each defective one must stay under its maximum. That
is the pull-request gate: a scorer that stops noticing a defect fails the build. The
live run drafts the instructions in `eval/drafting/instructions.yaml` with the real
models against the shared libraries and scores the artefacts; it is nightly and
advisory until a threshold is set from what the models achieve.

## Connector canaries

`eval/run_connector_canaries.py` takes every connector the registry seed describes
that is available in the environment (script present, credential set), opens a session,
lists its tools, and makes one small read through the same policy the agent uses. A
row per connector says reachable, tool count, the probe's outcome and which envelope
fields came back. A test keeps the probe table in step with the seed: a new connector
without a probe fails the unit tests.

Canaries run nightly, never on a pull request. A failure does not fail the workflow: the
job opens an issue titled "Connector canary failed" with the `canary` label, or
comments on the open one, with the last lines of the run. A source being down is
something to know, not a defect in this repository.

## What was decided and what is still open

- The extraction F1 threshold and the drafting threshold are set from the first nightly
  measurements and recorded in docs/30, as planned. Both runs are advisory until then.
- The canary issue needs the three connector secrets in the repository to probe the
  hosted servers; without them those rows are skipped, not failed.
- Locally: `uv run python -m eval.run_drafting_eval --fixtures`,
  `uv run python -m eval.run_connector_canaries`.
