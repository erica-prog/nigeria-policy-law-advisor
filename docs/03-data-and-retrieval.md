# 03 — Data and Retrieval

This is the highest-leverage part of the system. Generation quality is
capped by retrieval quality: Claude can't cite a rule it was never given.

## Document types in your corpus need different handling
- **Statutory rules** (`FHC Civil Procedure Rules.pdf`,
  `Lagos-State-Magistrates-Courts-Civil-Procedure-Rules-2009.pdf`) — highly
  structured: Order → Rule → sub-rule. Numbering is the primary key a lawyer
  will search by.
- **Judgments** (`Meta v NDPC Terms & Consent Judgment.pdf`) — narrative,
  cites other cases/rules, has a different internal structure (facts →
  issues → holding).

Don't run one generic chunker over both. Build (or configure) two ingestion
paths.

## Chunking
- **Rules documents:** chunk on structural boundaries (per Rule or per
  sub-rule), not fixed token windows. A fixed-size splitter will cut a rule
  mid-sentence and separate a rule from its own sub-rules, which is exactly
  the kind of error a lawyer will notice immediately. If the PDF has
  extractable structure (headings, numbering), parse it explicitly rather
  than relying on a generic text splitter to infer it.
- **Judgments:** chunk by section (facts, issues, ratio/holding, orders) if
  identifiable; otherwise a semantic/recursive splitter with meaningful
  overlap (so a holding isn't separated from the reasoning right before it).
- **Always attach metadata per chunk:** source document, document type
  (statute/judgment), jurisdiction (Federal/Lagos State), structural locator
  (Order/Rule number or paragraph number), and page number. This is what
  makes citations verifiable and filtering possible.

## Embeddings (Hugging Face)
- General-purpose multilingual/legal-domain sentence embedding models work
  reasonably for this size of corpus; you don't need to fine-tune initially.
  Evaluate 2–3 candidate models against your golden set (below) rather than
  picking one on reputation alone — legal phrasing is dense and
  domain-specific enough that general web-text embeddings sometimes
  underperform.
- Keep the embedding model **versioned** alongside the index. If you swap
  models, you must re-embed the entire corpus — old and new vectors aren't
  comparable.

## Retrieval strategy: hybrid, not pure-vector
- **Dense (vector) search** for semantic/paraphrased questions ("what's the
  deadline to respond to a suit?").
- **Sparse/keyword (BM25)** search for exact-match queries ("Order 5 Rule
  3"), which dense embeddings handle unreliably.
- Combine via score fusion or run both and merge top-k, then optionally
  **rerank** (a cross-encoder reranker, or even a cheap Claude call) on the
  merged candidate set before final selection.
- Apply **metadata filters** before or after retrieval when the query
  context implies jurisdiction (e.g., user mentions "Lagos") — don't make the
  vector search disambiguate jurisdiction purely semantically.

## Retrieval evaluation — do this before trusting any answer
Build a small **golden set**: 20–50 real questions a lawyer would ask, each
with the *correct* source passage(s) identified by hand (you, or ideally
someone with legal judgment). For each question, measure:

- **Recall@k** — is the correct passage anywhere in the top-k retrieved?
- **Precision@k** — how much retrieved noise accompanies it?
- **MRR (mean reciprocal rank)** — how high does the correct passage rank?

Re-run this whenever you change chunking, embedding model, or retrieval
logic. A retrieval change that looks like an improvement in one example can
regress badly on others — only the golden set tells you which.

## Faithfulness evaluation (downstream of retrieval)
Separately from retrieval, evaluate whether Claude's **generated answer**
stays grounded in what was retrieved (no claims beyond the provided context,
every citation traceable). Covered in
[06-production.md](06-production.md#evaluation).

Next: [04-mcp-integration.md](04-mcp-integration.md)
