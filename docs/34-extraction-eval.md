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

The labels follow the schema's own descriptions, not a narrower reading of them. They
cover every risk-clause kind the schema lists that the text contains, including
termination and governing law, and every duty a "shall" clause imposes, including those
in risk clauses. They also cover every defined term a deadline depends on: the Effective
Date and the Term as well as Business Day. This is version 2 of the set. Version 1
labelled only some of these, so a model that followed the schema was scored wrong for
it (docs/39 §9a). Version 2 changed the labels only; the contract text is the same.

## The score

Eight fields, each precision, recall and F1 over item sets, micro-averaged across
documents (`eval/extraction/score.py`): parties, governing law (agrees when either
text contains the other as whole words, because the schema asks for it "as written" and a
model may return the whole clause), key dates
matched on the date text alone (a label such as "Notice period" is a description; the
date as written is what a lawyer relies on), obligations (matched on obligor, deadline
and half the obligation's words), obligation evidence (the
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
server's key, scores the records, and writes `eval/extraction/last_run.json`. The file
holds the scores and every predicted record, so a scorer or label question can be settled
by rescoring rather than by another paid run. The nightly `extraction-f1` job runs it on
12 documents, uploads that file as the `extraction-last-run` artifact (kept 30 days) and
reports; it does not yet fail the build. `--no-escalation` sets the escalation model to
the triage model so that no record is read twice, and `--out` names the report file; the
pair is how docs/39 D11 was measured, and how it would be measured again (for example after
the escalation model changes). Each run scores only its own job's records, so two runs in
one database cannot mix.

## The threshold

The threshold is 0.908, set on 2026-10-29 by docs/39 D3 as amended: the median of the
first three nightly measurements on version 2 of the labels (0.949, 0.915, 0.938; median
0.938) less 0.03. The nightly `extraction-f1` job runs with `--fail-below 0.908`; a drop
opens or comments on an "Extraction F1 below threshold" issue (label `eval-gate`) and fails
the job. A job that does not finish fails it too. The measurement on version 1 of the
labels (0.699) does not count; docs/39 §9a says why. The oracle gate on pull requests is
unchanged.

## Limits

- The set is synthetic and regular. A model that scores well here can still miss a
  real contract's side letter, schedule or badly scanned page. The set is the floor the
  pipeline must clear, not a claim about real documents.
- Obligation matching is lenient on wording and strict on obligor and deadline, which
  is where a wrong extraction does harm. A deadline agrees when one text contains the
  other as whole words ("within thirty Business Days of receipt" and "thirty Business
  Days"); "thirty days" never agrees with "thirty Business Days".
- `gaps` is not scored: free text with one correct reading, which a rubric, not a
  set comparison, should judge (Phase 3 drafting rubric work).
