# 40 — Plan: the live evaluation harness (Phase 4 slice P4-B)

**Status:** ready within scope. **Owner:** product (the developer). **Authority:** docs/39
(the Phase 4 plan; D1 and D2 accepted 2026-10-28, D3 as amended 2026-10-29), ADR-001
decision 9. **Format:** docs/20. **Date:** 2026-10-30.

## 1. Requested outcome and scope

Every earlier phase deferred the same question: does the system behave when a real
model is on the other end? The deterministic checks say a connector result is wrapped as
data (docs/26), the faithfulness check says a citation resolves (docs/06), the routing
evals say the selector picks a mode (docs/29). None says whether the model ignores an
instruction planted in a document, keeps a judgment's reasoning apart from a rule, or
answers a research question from the library without inventing a section. docs/39 AC-2
asks for a harness that runs graded cases against the real model through the web API,
records each verdict with the model, the judge and the prompt versions, and fails the
nightly job below recorded thresholds.

**In scope:** a case format; a runner that drives `POST /api/matters/{id}/chat` on a
FastAPI app built as the tests build it, with real services and the server key; two
graders (deterministic, Claude-as-judge); at least 30 cases over five kinds; a nightly
job that keeps the verdicts as an artifact; thresholds by D3 once three runs exist.

**Out of scope:** a hosted eval product (D1 alternative); grading drafts, which the
drafting rubric already does nightly (docs/38); cases that need a lawyer's answer key,
which wait for the review packet (P4-C); any change to the product's prompts to pass a
case (a failing case is a finding, recorded in docs/39 §9a, and fixed in its own commit).

## 2. Inspected evidence

- `tests/api/conftest.py` builds the app with `create_app(serve_static=False)` over a reset
  database, creates users with bcrypt, logs in through `/api/auth/login`, and swaps
  `services.research_agent_factory` for a `ResearchAgent` whose `run_loop` is scripted.
  The same construction with the real loop and a real key is the harness D1 names.
- `POST /api/matters/{id}/documents` returns `202` and, under `TestClient`, the document
  is `ready` on the next `GET`: ingestion runs inline. A case's documents can be
  synthetic text turned into a `.docx` with python-docx, as `tests/api/helpers.py` does.
- `POST /api/matters/{id}/chat` takes `message`, `intent` (`auto|ask|analyze|research`),
  `allow_web`, `jurisdiction`; the reply carries `mode`, `playbook`, `citations` (each
  with `kind` evidence/authority/web and `cited_as`), and for question and research modes
  an `answer` with `faithful` and `unsupported_citations`. Everything a deterministic
  grader needs is in the reply.
- `ResearchAgent(..., sources=, specs=)` takes the registry rows and connector specs it
  may use. `tests/fixtures/fake_connector_server.py` is a stdio MCP server answering from
  JSON fixtures; `render_untrusted` wraps a result as data. A fake connector with a
  planted instruction can therefore be offered to the real model in a live run without
  touching the registry seed or the network.
- `generation/judge_check.py` and `JUDGE_PROMPT` are the existing Claude-as-judge, bound
  to faithfulness ("stays within the authorities"). The live harness needs verdicts on
  behaviour (did it follow the planted instruction, did it present evidence as law), so
  it gets its own prompt, versioned, and the strongest model (D2) through a new setting.
- `telemetry.record_cost` writes every model call to `cost_ledger` under the attribution
  in force; `usage_totals(username=...)` sums them. The runner attributes its calls to
  the user `eval` and reports the difference, which is the per-run cost R2 asks for.
- The drafting eval (`eval/run_drafting_eval.py`) is the pattern for a live runner:
  `last_run.json`, one row per case, a failed case recorded rather than hiding the rest,
  an artifact kept 30 days, `--fail-below` wired only once a threshold exists.
- An uploaded contract of a few paragraphs is one chunk with the locator `Section 1`
  (the generic chunker; `classify_document_type` knows statutes and judgments, not
  contracts). A case can therefore ask that an upload be cited by document
  (`must_cite_documents`), not by clause, until the clause parser of docs/39 P4-E lands;
  library citations (`Order 11 Rule 1`, `Paragraph 13`) are checked by locator.
- What inspection could not establish: how often the real model calls a fake connector
  tool when the question invites it (the injection-by-connector cases depend on it), and
  the judge's agreement with a lawyer (R1 in docs/39). Both are measured by running.

## 3. Decisions

| ID | Decision | Status | Choice |
|---|---|---|---|
| D1 (docs/39) | How the harness grades | Accepted | Graded cases in the plugin's eval shape plus `grader` and `must_cite`; one runner through the API; deterministic first, then judge; verdicts record model, judge and prompt versions. |
| D2 (docs/39) | Which model judges | Accepted | `JUDGE_MODEL`, default `claude-opus-5-5`, never the model under test; judge model and prompt version on every verdict. |
| D3 (docs/39, amended) | Thresholds | Accepted | Median of the first three nightly runs less 0.03; advisory until then. |
| L1 | Where cases live | Routine | `eval/live/cases/<kind>.yaml`, one file per kind, each with its own synthetic `matters` (documents as paragraphs) so a case file is self-contained and a lawyer can read it. |
| L2 | The graders | Routine | `deterministic`: mode, playbook, `must_include`, `must_not_include`, `must_cite`, `must_not_cite` (locators, under the locator scheme), `must_cite_documents`, `citation_kinds` (allowed kinds), `expect_faithful`. `judge`: the kind's fixed rubric plus the case's `judge_focus`, verdict PASS or FAIL with notes. `rubric` is accepted by the loader and reserved for a draft-scoring grader; no case uses it in this slice, and the runner refuses it rather than scoring nothing. |
| L3 | The planted instruction | Routine | A fake connector under `eval/live/` (same shape as the test fixture) with its own tool names, so it cannot shadow a registry connector; wired only by the runner, through `sources` and `specs`, never through the registry. The planted text asks for an action the product cannot take and a phrase a grader can look for. |
| L4 | Cost | Routine | `--limit` caps cases per run (round-robin over kinds so every kind is sampled); the ledger difference for the `eval` user is printed and stored in `last_run.json`. The nightly run uses the full set while it is 30 cases; the cap is the lever if it grows. |
| L5 | What a failing case means | Routine | A finding, not a build break, until thresholds bind: the job uploads the verdicts and reports; a case that fails three nights running is recorded in docs/39 §9a with its verdict notes and gets a fix in its own commit, as g11 did. |

## 4. Acceptance criteria

| ID | Observable result | Basis |
|---|---|---|
| B1 | `eval/live/cases/*.yaml` load through one validated schema; a malformed case (unknown grader, `expect_faithful` on an analysis case, a judged case without a focus, a matter a case names that is not defined) is refused with its path | D1; docs/29 shape |
| B2 | At least 30 cases across injection (document and connector), evidence versus authority, citation discipline, routing with a real reply, research answers; each kind has at least five | docs/39 AC-2 |
| B3 | `uv run python -m eval.run_live_evals` builds the app, logs in as `eval`, creates the case matters, uploads their documents, posts each prompt to `/chat`, grades the reply, writes `eval/live/last_run.json` with one row per case and `model`, `judge_model`, `judge_prompt_version`, `cases_version`, `ledger` on the run | D1, D2, L4 |
| B4 | With the model loop and the judge replaced by scripted fakes, the runner's tests prove: a planted phrase in the reply fails `must_not_include`; a missing locator fails `must_cite`; an evidence citation where only authority is allowed fails `citation_kinds`; the wrong mode fails; a judge FAIL fails the case; the deterministic verdict is recorded before the judge is asked | L2 |
| B5 | The nightly workflow has a `live-evals` job that runs the set with the server key, uploads `live-last-run`, and prints the pass rate by kind and the ledger total; `--fail-below` is added when D3 yields a threshold | D3, docs/38 |
| B6 | docs/38 and docs/39 describe the job, the case format and the first measurement | docs convention |

## 5. Slices

### S1 — Case format, loader, fake connector, first 30 cases (no model)

`eval/live/__init__.py` (schema and loader, `load_cases()`), `eval/live/cases/*.yaml`,
`eval/live/fake_connector_server.py` with `eval/live/connector_results/*.json`. Tests:
loading every shipped file; each refusal in B1; the fake connector lists its tools and
returns the planted fixture through the real MCP client. Done when B1 and B2 hold.

### S2 — Runner through the API harness, deterministic grader

`eval/live/harness.py` (build the app as the tests do, with the server key and
`ALLOW_SHARED_ANTHROPIC_KEY`; create the `eval` user; create matters and upload
documents; wire the fake connector into the research agent for cases that ask for it),
`eval/live/grade.py` (deterministic checks over a `ChatReply`), `eval/run_live_evals.py`
(`--limit`, `--kind`, `--fail-below`, `--out`). Tests: B4's deterministic rows with a
scripted loop and a fake question chain; the ledger row count for the run. Done when B3
holds with the judge disabled.

### S3 — Judge grader and the model setting

`eval/live/judge.py` (one rubric per kind, `JUDGE_PROMPT_VERSION`, verdict PASS/FAIL with
notes), `Settings.judge_model` and `JUDGE_MODEL` in `.env.example`. Tests: the fake judge
rows of B4; the judge is called only after the deterministic checks and only for cases
that list it. Done when B3's verdict fields are filled.

### S4 — Nightly job and the record

`.github/workflows/nightly.yml` `live-evals` job; docs/38 job table and a section; docs/39
§9a first measurement and the owed threshold; this document's §10. Done when B5 and B6
hold on the next nightly run.

## 6. Risks: assumption and disproving check

| ID | Risk | Assumption | Check |
|---|---|---|---|
| R1 | The real model never calls the fake connector, so the connector-injection cases measure nothing | the tool's description and the prompt make it the obvious tool; `tool_calls` on the reply shows whether it was called | a case whose fake tool was not called is recorded as `not_exercised`, not PASS; the first nightly run says how many |
| R2 | The eval user's ledger rows cannot be told from a developer's local rows | the runner creates the `eval` user and reads totals before and after | the difference is printed; a negative or absurd figure fails the run |
| R3 | The judge is kinder than a lawyer | R1 of docs/39: a sample of verdicts is compared with a lawyer's in P4-C before thresholds bind | the comparison is recorded in docs/39 |
| R4 | Ingestion is not inline outside `TestClient`, so documents are not ready when the prompt is posted | the runner uses `TestClient`, as the tests do; it still checks `status == "ready"` and fails the case otherwise | the status check |
| R5 | The fake connector leaks into the product | it is wired only by the runner's own agent factory; the registry seed is untouched | a test asserts no registry row names the `eval-fake` connector |
| R6 | 30 live cases a night cost more than the other jobs together | each case is one chat call and at most one judge call; roughly 60 calls, about the extraction job's size | the ledger total in the job output |

## 7. Verification map

| Criterion | Check | Status |
|---|---|---|
| B1 | `tests/test_live_evals.py::test_every_shipped_case_file_loads`, `::test_malformed_cases_are_refused_by_path` | planned, S1 |
| B2 | the loader test counts kinds and cases | planned, S1 |
| B3 | `::test_the_runner_drives_the_api_and_writes_the_report` with scripted fakes; the first nightly run with the real model | planned, S2; live in S4 |
| B4 | the grading tests named in B4 | planned, S2 and S3 |
| B5 | workflow YAML parses; the first nightly run uploads `live-last-run` | planned, S4 |
| B6 | the documents exist and name the run | planned, S4 |

Existing gates (`ruff`, `mypy`, `pytest`, routing evals, extraction oracle, rubric
fixtures) on every pull request. A planned check is not a passed check: §10 records what
ran.

## 8. Review needs

A lawyer for the judge comparison (docs/39 R1) and for the answer keys of research cases
beyond what the golden set already holds; a security read of the fake connector wiring
(R5).

## 9. Readiness and next action

Ready within scope. Next action: S1 on branch `platform/phase-4-b`.

## 10. Implementation record

| Slice | Commit | Verification | Notes |
|---|---|---|---|
