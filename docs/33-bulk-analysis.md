# 33 — Bulk analysis

**Status:** implemented (docs/30 slice P3-C). **Decisions:** ADR-001 decision 8 and action
14; docs/30 D3 (tiering and ceilings) and D4 (where records live).

## What it is

A batch pipeline, not a chat feature: every document of a matter becomes a typed record
with evidence spans, the records are checked against each other, and the research agent
can query them. The lawyer starts a job, sets how much it may spend, and reads the
records and findings when it is done. Nothing here decides anything; it extracts and
compares, and says what it could not verify.

## The record

The first schema is the contract ledger, seeded from the plugin's obligations extractor
(MIT, attribution in `bulk/schemas.py`): parties with their defined terms, governing law,
key dates, every obligation (obligor, obligee, performance, trigger, deadline as written,
conditions, consequence, recurrence, survival), the defined terms a deadline depends on,
risk clauses summarised without assessment, and gaps. Every item carries evidence: the
clause locator and the exact words.

**Evidence is verified, not trusted.** `bulk/evidence.py` finds each quote inside the
chunk its locator names, tolerant of whitespace and curly quotes. A quote that is there
becomes a span with character offsets; one that is not is kept but marked unverified.
Confidence is the share of verified evidence, and a record with no evidence at all is
zero. Schemas are versioned and a record is stored with the version that produced it.

## Tiering and the ceiling (D3)

Every document is first read by the triage model (`BULK_TRIAGE_MODEL`, the cheaper
one). A record below the confidence floor, or one that reports risk clauses, is read again
by the escalation model, and the better record is kept. Before every model call the
worker estimates the call (document characters over four plus an output allowance) and
pauses the job when the estimate would cross the ceiling: status `paused_ceiling`, no
failure, no spend. A lawyer resumes it with a higher ceiling (`POST .../resume`). Spend is
counted from the provider's token numbers, and the same callback writes them to the
cost ledger (docs/32), attributed to the job.

Jobs over `BULK_BATCH_THRESHOLD` documents use the Batch API: one request per document,
the record forced out as a tool call whose input schema is the extraction schema. The
worker records the batch id, polls on later passes, stores the results when the batch has
ended, then runs escalations synchronously. Small jobs call the model per document.

## When one document fails

A document that cannot be read, or whose record does not validate, costs that document
and not the job. The worker logs `bulk_document_failed`, moves to the next document, and
finishes the job as `done` with `error` naming what was skipped
(`"1 of 12 documents not extracted: lease.docx (reason)"`). The job is `failed` only when
no document could be extracted. A failed escalation keeps the triage record. A call whose
record did not validate still spent tokens, and they count toward the ceiling.

Tool use sometimes sends a nested array as a JSON-encoded string. Every extraction model
inherits `ExtractionRecord`, which reads a string that decodes to a list as that list and
refuses anything else. The first nightly run (2026-10-28) lost all twelve documents of the
extraction eval to one such field before these two changes.

## Cross-document checks

Deterministic where strings can be compared: the same party under different defined
terms or roles, the same key date written differently, the same defined term with two
definitions, governing law that differs across the set. Model-based for obligations that
pull against each other, asked of the escalation model over a compact list under the
ceiling, and only when the job covers more than one document. Findings name their
documents, carry evidence locators, and start `open` for the review queue (P3-F).

## Where it lives

| Table | Holds |
|---|---|
| `bulk_jobs` | the queue: schema, documents, backend, batch id, models, ceiling and spend, progress, status |
| `extractions` | one row per document per schema version: record, evidence spans, confidence, model, flagged |
| `findings` | one row per cross-document finding with its status |

All tenant-scoped under the forced policy (docs/31). The worker claims from every firm's
queue and runs each job as its own tenant.

## The agent's tool

`query_extractions(field, contains, document)` lets the research agent answer "which of
these documents have a non-mutual indemnity governed by Lagos law" from the records.
It returns items with their documents and locators and loads the cited chunks, so the
agent's citations resolve like any other. The investigations playbooks allow it.

## The API (contract: docs/contracts/web-api.md)

`POST /api/matters/{id}/bulk` starts a job; `GET` lists or fetches one; `POST
.../{job}/resume` raises the ceiling; `POST .../{job}/cancel`; `GET
.../records/{schema}` returns the records; `GET .../findings/all` the findings. Starting
and resuming need a writable matter and the caller's own key, which pays.

## Not done here

- The labelled extraction set and the F1 gate (P3-D). Until then confidence measures
  evidence verification, not field accuracy.
- The review queue screens and finding transitions (P3-F).
- A per-tenant ceiling across jobs; today the ceiling is per job.
- A live run against the Batch API; the backend is exercised with a fake client in tests.
