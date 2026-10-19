# 01 — Architecture

## End-to-end flow

```
                ┌─────────────────────┐
                │   Source documents   │  (PDFs: rules, judgments)
                └──────────┬───────────┘
                           │ ingestion (offline, batch)
                           ▼
                ┌─────────────────────┐
                │  Parse → chunk →     │
                │  enrich metadata     │
                └──────────┬───────────┘
                           ▼
                ┌─────────────────────┐
                │  Embed (HF model)    │
                └──────────┬───────────┘
                           ▼
                ┌─────────────────────┐
                │   Vector store        │  (+ optional BM25 index)
                └──────────┬───────────┘
                           │
   ── runtime (per user query) ──────────────────────────────
                           │
   User query → MCP tool call → Retriever → top-k chunks
                           │
                           ▼
                ┌─────────────────────┐
                │  Context assembly     │  (LangChain: rerank, dedupe,
                │                       │   format with citations)
                └──────────┬───────────┘
                           ▼
                ┌─────────────────────┐
                │   Claude API           │  generates answer, forced to
                │                       │  cite only what's in context
                └──────────┬───────────┘
                           ▼
                ┌─────────────────────┐
                │  Answer + citations   │ → lawyer
                └─────────────────────┘
```

## Components

### 1. Ingestion pipeline (Python, offline)
Runs whenever the corpus changes (new PDF added, rules amended). Output is a
versioned set of chunks + embeddings, not something computed at query time.
Treat this like a build artifact — log what version of the corpus produced
which index, so you can roll back if a re-ingestion degrades quality.

### 2. Vector store
Holds chunk embeddings + metadata (source document, section/order/rule
number, document type: *statute* vs *judgment*, page number). Metadata is
what lets you filter ("only Lagos rules," "only judgments after 2020") before
or after vector search — critical for legal precision, since a Federal High
Court rule and a Magistrate's Court rule can both mention "service of
process" but are not interchangeable authorities.

### 3. Retriever
Likely **hybrid**: dense vector search (semantic) + keyword/BM25 (exact
term/rule-number matches). Legal text has a lot of exact-match value — a
lawyer searching "Order 5 Rule 3" wants that rule, not a semantically similar
one. Pure embedding search under-performs here; pure keyword search misses
paraphrased questions. Combine both, then optionally rerank.

### 4. MCP layer
MCP exposes retrieval as a **tool** Claude can call mid-conversation, rather
than you stuffing all context in up front. This matters for a multi-document
legal corpus: the agent can issue a narrower follow-up search ("now find the
Lagos equivalent of this FHC rule") instead of relying on a single retrieval
pass. Details in [04-mcp-integration.md](04-mcp-integration.md).

### 5. LangChain orchestration
Glue code: receives the query, calls the MCP retrieval tool(s), assembles
the returned chunks into a prompt with citation markers, calls Claude, and
post-processes the response (e.g., verifying every citation in the answer
actually appeared in retrieved context — a basic faithfulness check you can
do without an LLM judge).

### 6. Claude API (generation)
Given retrieved context, generates the answer. The prompt should make three
things explicit:
- Answer **only** from provided context; if insufficient, say so.
- Cite the specific rule/order/section/paragraph for every claim.
- Distinguish statutory text from case commentary (judgments interpret
  rules; they aren't the rules themselves).

## Why this shape, specifically for a legal advisor
- **Separation of ingestion and query time** means embedding cost is paid
  once, not per question, and you can evaluate retrieval quality against a
  fixed index.
- **Hybrid retrieval** is non-negotiable once you have numbered
  rules/sections — pure semantic search will, with non-trivial probability,
  return the *wrong* rule number on an exact-match query.
- **Tool-based retrieval (MCP) over static context-stuffing** lets the agent
  do multi-hop legal reasoning (rule → related case → cross-reference)
  without you anticipating every retrieval pattern in advance.

Next: [02-environment-setup.md](02-environment-setup.md)
