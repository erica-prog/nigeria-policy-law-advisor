# 06 — Production Concerns

This is the document to revisit before calling anything "done." A legal
advisory tool used by a non-technical lawyer has a higher bar than a typical
internal RAG demo: wrong-but-confident answers carry real professional risk.

## Evaluation

### Two separate things to evaluate
1. **Retrieval quality** — covered in
   [03-data-and-retrieval.md](03-data-and-retrieval.md#retrieval-evaluation--do-this-before-trusting-any-answer)
   (recall@k, precision@k, MRR against a golden set).
2. **Answer faithfulness** — does the *generated* answer stay within what
   was retrieved?

### Faithfulness checks, cheapest to most expensive
- **Citation existence check (no LLM needed):** parse the citations Claude's
  answer references (e.g., "Order 5 Rule 3") and verify each one appeared in
  the chunks actually passed into context. Cheap, deterministic, catches the
  most dangerous failure mode (citing something that wasn't retrieved). One
  bracket may name several locators separated by `;`; each is checked on its
  own (the golden set's g11 failed five nights running before that was so).
- **Claude-as-judge:** a second Claude call, given the answer and the
  retrieved context, asked to flag any claim *not* supported by the context.
  More expensive, catches subtler unsupported inference, not just bad
  citations.
- **Human spot-check:** before any update to prompts/retrieval ships, have a
  person (ideally with legal judgment) review a sample of golden-set answers.
  Automated checks catch regressions; they don't replace this entirely for a
  legal-advice product.

### Build the golden set early, keep it versioned
Store it as JSON/CSV in the repo (e.g., `eval/golden_set.json`): question,
expected source passage(s), and ideally an example acceptable answer. Re-run
both evaluations whenever you touch chunking, embeddings, retrieval logic, or
prompts — treat it like a regression test suite, not a one-time check.

## Secrets handling
Setup is in [02-environment-setup.md](02-environment-setup.md); the
production-specific parts:
- **Rotation plan:** know how to rotate the Anthropic key and HF token
  without downtime (e.g., support reading a new key from `.env` via a
  reload, rather than requiring a code change).
- **Principle of least privilege:** if the HF token only needs read access to
  models, don't issue one with write/org-admin scope.
- **If a secret leaks** (committed by accident, logged, pasted somewhere):
  rotate it immediately — don't just remove it from the latest commit; it's
  still in git history. Treat the old key as permanently compromised.
- **No secrets in error messages** that might surface to the lawyer-facing
  UI or to logs that aren't access-controlled.

## Error handling
- **Fail loudly internally, gracefully externally.** Internal logs get full
  stack traces; what reaches the lawyer is a clear, non-technical message
  ("I couldn't find this in the available rules" beats a stack trace or a
  silently wrong answer).
- **Distinguish error types** so the response can differ:
  - Retrieval found nothing relevant → say so explicitly, don't let Claude
    guess.
  - Upstream API failure (Claude/HF down or rate-limited) → retry with
    backoff for transient errors; surface a "try again" message for the
    user rather than a generic crash.
  - Validation failure (bad MCP tool input) → reject early, as covered in
    [05-typescript-guide.md](05-typescript-guide.md#zod-runtime-validation--your-pydantic-equivalent).
- **Timeouts everywhere an external call happens** (embedding calls, Claude
  calls, vector store queries) — an unbounded hang on one call shouldn't hang
  the whole interaction.

## Observability

> **Since Phase 3 (docs/32):** OpenTelemetry spans and a per-tenant cost ledger are implemented in `telemetry.py`; the rest of this section is the original guidance.
At minimum, log per query (structured, not free text):
- Query text (consider whether this needs redaction/retention limits —
  legal queries may contain client-sensitive detail)
- Retrieved chunk IDs + scores
- Which retrieval path fired (vector / keyword / both)
- Final answer's citations
- Latency breakdown (embedding time, retrieval time, generation time)
- Token usage / cost for the Claude call

This is what lets you debug "why did it answer that?" after the fact instead
of only being able to reproduce issues live. A simple structured JSON log
line per stage, correlated by a request ID, is enough to start — you don't
need a full tracing platform on day one, but design the log shape so it
could feed one later (e.g., OpenTelemetry-style spans) without a rewrite.

## Reliability extras worth planning for
- **Index versioning:** tag each vector index build with corpus version +
  embedding model version, so you can roll back a bad re-ingestion.
- **Rate limiting / cost ceilings:** a legal advisor tool with no usage cap
  is a surprise bill waiting to happen — set sane per-user or per-day limits
  on Claude/embedding calls.
- **Disclaimers in the product, not just the docs:** the agent should make
  clear it's a research aid, not a substitute for the lawyer's own
  judgment — this is a liability/scope concern as much as a UX one.
