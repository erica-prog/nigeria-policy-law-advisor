# 25 — The United States federal library

**Status:** implemented (docs/23 slice P2-B). **Decisions:** ADR-001 action 11, docs/23 D8.

## What it is

A second shared, read-only authority collection beside the Phase 1 Nigerian demo
corpus: matter `us-library`, tier `authority`, jurisdiction `us-federal`. Its texts
live in `data/us/` with their provenance ([PROVENANCE.md](../data/us/PROVENANCE.md))
and are built into the database by the same command as the demo corpus:

```bash
uv run python -m policy_advisor.ingestion.build_index
```

A lawyer's question widens into it through the scope ladder (ADR-001 decision 4)
when their own matter has nothing, the same way it widens into the Nigerian
library. The jurisdiction filter keeps the two apart: a question tagged
`us-federal` never returns Nigerian rules, and the reverse.

## Why committed text and not a connector

The connectors arrive in slices P2-C and P2-D. Until then the parsers, the
locator schemes and the golden set need something real to run against, and the
eval gate in CI needs it without network. Public-domain federal material is the
only kind that can be committed outright; the three families chosen are the ones
the immigration playbook (P2-F) will lean on: a regulation, agency guidance, and
a Supreme Court opinion. The set is deliberately small. More US law comes through
connectors, not more files.

## The parsers and their locators

`policy_advisor/ingestion/parse_us.py`, one parser per family, each producing
locators that `jurisdictions/locators.py` already knows how to compare:

| Family | Structure the parser follows | Locator | Scheme |
|---|---|---|---|
| Code of Federal Regulations | `§ 245.1 Heading` lines, then lettered paragraphs `(a)`, `(b)`; a long paragraph splits at its numbered sub-paragraphs | `8 C.F.R. § 245.1(b)(4)` | `us_cfr` |
| USCIS Policy Manual | `## A. Section` and `### 1. Subsection` headings; one chunk per deepest heading; footnotes not chunked | `USCIS-PM Vol. 7, Pt. B, Ch. 2.A.7` | `uscis_pm` |
| Reported opinion | one chunk per reporter page, running head as the heading | `533 U.S. 678, 690` | `us_reporter` |

Two production details worth knowing:

- **Pinpoints are real pages.** The bound-volume PDF carries no page numbers in
  its text layer, so the fetch script derives them from position and the
  provenance note records the check against the syllabus cross-references. A
  citation to `533 U.S. 678, 690` is a page the lawyer can open in the volume.
- **The CFR paragraph is the unit, not a token window.** A lettered paragraph is
  never cut mid-sentence. Some remain long (the longest is about 7,500
  characters), which the embedding model truncates; keyword search still sees
  all of it, and the golden set shows recall holding.

Uploads benefit too. `classify_document_type` now recognises a PDF of a CFR part
(`us_cfr`) and a scan of a bound US Reports volume (`us_opinion`), and routes
them to the same parsers, so a lawyer's own copy gets the same locators as the
library's. The Policy Manual parser needs the header lines the fetch script
writes, so it serves the library only.

## Evaluation

`eval/golden_set_us.json`: 15 questions (14 scored, one expecting a refusal)
across the three families. Run it with:

```bash
uv run python -m eval.run_golden_set --golden-set eval/golden_set_us.json --skip-generation
```

CI runs that retrieval-only form as a gate at recall@8 ≥ 0.9. The generation and
faithfulness run spends Claude calls and is done by hand from a machine with a
server key; the docs/23 implementation record says whether it has been done yet.

## Provenance and licence

All three sources are US government works in the public domain. Their registry
rows (`us-ecfr`, `us-uscis-policy-manual`, `us-reports-loc`; docs/24) say
`may_store_text: true`, which is what allows the text to be committed at all.
The rows are not yet signed off by a lawyer; retrieval over committed library text
is not gated by the registry in this slice (connectors and web search are), which
the agent loop in P2-E should close by checking `source_id` before citing.

## Refreshing

```bash
uv run python -m scripts.fetch_us_corpus
```

Network only; never in CI. Commit the changed texts with the new
`data/us/manifest.json`, re-run the golden set, and update the chunk counts in
the docs/23 record.
