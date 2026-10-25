# 34 — The extraction eval

**Status:** implemented (docs/30 slice P3-D). **Decisions:** ADR-001 decision 9; docs/30
AC-3 and D10.

## Why a labelled set

Bulk analysis (docs/33) measures confidence as the share of evidence quotes it could
find in the document. That says whether the record points at real words, not whether
it extracted the right things. For that there is no substitute for documents whose
correct records are known in advance.

## The set

`eval/extraction/contracts.json`: 48 synthetic contracts across seven kinds (supply,
services, licence, distribution, lease, loan, confidentiality), each a list of clauses
with `Clause N` locators and a label record in the contract schema's shape. The
generator (`eval/extraction/generate_set.py`) writes text and label from the same
random choices, so the labels are exact by construction; the seed is fixed and the file
is committed, so the score is reproducible. Every party, term and clause is invented
(AGENTS.md rule 7). About a third of the contracts omit a payment deadline on purpose,
so the extractor's gap reporting is exercised.

## The score

Eight fields, each precision, recall and F1 over item sets, micro-averaged across
documents (`eval/extraction/score.py`): parties, governing law, key dates, obligations
(matched on obligor, deadline and half the obligation's words), obligation evidence (the
labelled locator among the predicted ones for each matched obligation), defined terms,
risk clauses, classification. The headline number is the mean of the eight F1 values.
Normalisation is deliberately forgiving of articles and company suffixes and
unforgiving of substance.

## Running it

```bash
uv run python -m eval.run_extraction_eval --oracle --fail-below 0.99
```

The oracle feeds each document's label back as the model's answer. It proves the
pipeline (chunks, job, evidence verification, storage, scoring) end to end without a
key and must score 1.0; CI runs it in the lint-and-test job as a hard gate.

```bash
uv run python -m eval.run_extraction_eval --limit 12
```

The real run uses the configured triage and escalation models on the lawyer's or the
server's key, scores the records, and writes `eval/extraction/last_run.json`. CI runs it
in the eval-gate job on 12 documents and reports; it does not yet fail the build.

## The threshold

docs/30 says the threshold is set after the first measurement. No measurement has been
made yet: this machine holds no server key. The first CI run of the eval-gate job will
print the table; the person who reads it sets `--fail-below` in the workflow from what
the models actually achieve, and records the number in the docs/30 implementation
record. Until then the oracle gate stands and the real run is advisory.

## Limits

- The set is synthetic and regular. A model that scores well here can still miss a
  real contract's side letter, schedule or badly scanned page. The set is the floor the
  pipeline must clear, not a claim about real documents.
- Obligation matching is lenient on wording and strict on obligor and deadline, which
  is where a wrong extraction does harm.
- `gaps` is not scored: free text with one correct reading, which a rubric, not a
  set comparison, should judge (Phase 3 drafting rubric work).
