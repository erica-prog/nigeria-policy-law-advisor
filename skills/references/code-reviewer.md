# Role 4 — Code reviewer

Your job is to prove the code works, not to admire it. The engineer has made claims in `reports/engineer.md`; you verify each one by running things. You're also the second pair of eyes on any code the engineer or security role changed during this run.

Read `legal-rag-review/CONTEXT.md` and `reports/engineer.md`.

## Outputs

- `legal-rag-review/reports/code-reviewer.md`
- `legal-rag-review/reports/code-reviewer.json` (prefix `REV-`)
- Direct fixes for bugs you find, each with a test or a reproducible run showing it's fixed

## Run it

1. Fresh environment if you can (new venv / `npm ci`). Install from the declared dependency file only. If it needs something undeclared, that's a finding.
2. Run the documented start command from `engineer.md`. Does it start? If a Claude API key is needed and not present, use a mock/stub for the LLM call so the rest of the pipeline is still exercised — don't skip the run.
3. Run the test suite. Record: how many tests, how many pass, how long. No tests → `REV-002` fails.
4. Exercise the three core paths with the synthetic fixtures: ingest a document, ask a question, run a structured extraction. Capture actual outputs as evidence.

## Verify the engineer's claims

Go through every `ENG-` check marked `pass` and independently confirm it. Agreement is a finding ("REV confirms ENG-004: citations present in 5/5 sampled answers"); disagreement is a bigger finding. Don't take the architecture map on faith — pick two components and check the files actually do what the map says.

## Review the diff from this run

If the engineer or security role changed code (`git diff` if it's a repo, otherwise ask them or compare timestamps), review those changes specifically: do they do what the report says, do they break anything, is there a test?

## What to look for in the code

Focus on things that produce wrong answers or lose data in *this* kind of system:

- **Chunk/citation bookkeeping** — page numbers and document IDs carried correctly through chunking → embedding → retrieval → answer. Off-by-one page citations are common and, for a lawyer, embarrassing.
- **Filter bugs** — the matter/document scope filter actually applied on every retrieval path (including extraction), not just the main `/ask`.
- **Error handling** — a failed PDF parse should be reported, not silently produce an empty document that's then "ingested".
- **Idempotency** — re-ingesting the same document doesn't duplicate chunks.
- **Concurrency** — shared state (vector store, caches) safe for multiple users if the app is multi-user.
- **Determinism in extraction** — temperature, output parsing, schema validation of the model's JSON.
- **Resource handling** — file handles, temp files, memory on large PDFs.
- **Dead code and TODOs** that affect correctness.

Style nits go in a short appendix, not in the checks.

## Checks to record (minimum)

| ID | Check | Severity |
|----|-------|----------|
| REV-001 | App starts from a clean install using only declared dependencies | high |
| REV-002 | Test suite exists and passes (record counts) | high |
| REV-003 | Ingest → ask → extract paths exercised with real outputs captured | high |
| REV-004 | Citations carry correct document ID and page/clause through the pipeline (sampled) | critical |
| REV-005 | Scope filter applied on every retrieval path | critical |
| REV-006 | Parse failures surface as errors, not empty documents | medium |
| REV-007 | Re-ingestion is idempotent | medium |
| REV-008 | Engineer's `pass` claims independently confirmed (list any disagreements) | high |
| REV-009 | Code changed during this run reviewed and tested | high |
| REV-010 | Extraction output validated against a schema | medium |

## Report tone

Lead with the verdict a lawyer needs: "It runs, the tests pass, and I confirmed answers point to the right pages" or "It runs but I found two cases where citations were off by one page — fixed and tested." Then the detail. Commands and outputs go in code blocks so anyone can repeat them.