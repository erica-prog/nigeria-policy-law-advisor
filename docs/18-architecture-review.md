# 18 — Architecture review: from Nigerian single-matter RAG to a USA + Nigeria legal AI platform

Reviewed on 2026-10-24 against branch `2026-10-19-pozq`. This is the
"what is actually there" half of the review. The decision about where to
take it is in [19-adr-001-multi-jurisdiction-legal-platform.md](19-adr-001-multi-jurisdiction-legal-platform.md).

Target you described: a Harvey-style assistant that reads **USA and
Nigerian** material, supports **investigations and immigration** work in
both countries, and adds three platform capabilities:

1. Access many global legal data sources and premium primary law to surface
   authorities and synthesise complex questions.
2. Agents that generate polished documents and presentations grounded in
   the firm's precedents and history.
3. Analysis of tens of thousands of contracts, filings and exhibits for
   risks, obligations and inconsistencies.

## 1. What exists today (one page)

| Layer | What is there | Where |
|---|---|---|
| Ingestion | PDF (pypdf, OCR fallback via tesseract) and DOCX. Heuristic type detection: `statute` (Order/Rule), `judgment` (numbered paragraphs), else `generic` (recursive splitter, 1200/200). Optional per-chunk Claude translation with a fidelity check. | `src/policy_advisor/ingestion/` |
| Storage | One local Chroma collection filtered by `matter_id`; a `chunks.json` + `meta.json` sidecar per matter; `credentials.yaml` and Fernet-encrypted user keys under `data/auth/`. | `retrieval/vector_store.py`, `ingestion/matter_store.py` |
| Retrieval | Hybrid: bge-small dense + rank-bm25 (in-memory, rebuilt per matter from the JSON sidecar), min-max fusion, optional `jurisdiction` post-filter, exact "Order N Rule M" short-circuit. No relevance floor, no reranker. | `retrieval/hybrid_retriever.py` |
| Generation | `RAGChain` (one retrieval, one Claude call, deterministic citation check, restricted web fallback). `CaseReasoningChain` (issues → arguments per side → synthesis) with tiered faithfulness: regex locator check, one corrective retry, Claude-as-judge at step boundaries. | `generation/` |
| Web research | Anthropic `web_search` server tool, `allowed_domains` = `nass.gov.ng`, `ndpc.gov.ng`. | `generation/web_search.py` |
| API / UI | FastAPI with cookie auth, per-matter ownership check, in-process job tables for uploads and chat, a chat router that picks analysis / research / question. Vanilla-JS browser client with the avatar. Streamlit prototype still shipped as the default container command. | `api/`, `web/`, `app.py` |
| Eval / CI | Golden set over the 3-document demo matter (recall@k, MRR, faithfulness, refusal), gated at recall 0.85 in CI. Human rubric for case reasoning. 157 unit tests. mypy is advisory only. | `eval/`, `.github/workflows/ci.yml` |
| MCP / TypeScript | Designed in docs 04 and 05; **nothing implemented**. No MCP server, no TS beyond the browser client. | — |

Roughly 5,300 lines of Python. It is a well-disciplined **single-matter
Nigerian civil-procedure assistant**, not yet a platform.

## 2. Strengths to keep (do not rewrite these)

- **Grounding discipline is the best part of the codebase.** Citation
  locators are resolved against chunks the model actually saw; web answers
  are labelled as a different trust tier; the case-reasoning output is
  structured so every step is auditable. Harvey-class products live or die
  on exactly this. Keep `faithfulness.py`, `judge_check.py` and the
  `kind: authority | evidence | web` distinction as the contract that every
  new source type must satisfy.
- **Per-matter isolation is enforced at the function level**, not just in
  the UI (`SharedMatterReadOnlyError`, `get_matter` dependency before any
  retrieval). That is the right instinct for client confidentiality.
- **Hybrid retrieval with a locator short-circuit** is the correct shape for
  legal text. It just needs a reranker, a relevance floor and more locator
  grammars.
- **Bring-your-own-key, per-user chains, secrets never logged.** Good
  production hygiene for a solo-maintained service.
- **The eval gate exists and runs in CI.** Most RAG projects never get there.

## 3. Findings, ranked by how much they block the target

### F1. Jurisdiction is a flat string with Nigerian values baked in
`jurisdiction` is `"federal" | "lagos" | None` on every chunk, and the system
prompt says "do not fall back on outside knowledge of **Nigerian** law". The
parsers understand Nigerian rules (Order/Rule) and judgment paragraphs only.
A US document hits the `generic` chunker and gets `Section N` locators that
mean nothing to a lawyer. There is no notion of country, court level,
agency, practice area, or document currency. Everything in the target list
needs a real taxonomy first.

### F2. There is one corpus tier ("matter") and the shared library is a hack
The read-only `phase1-demo` matter is doing the job of a library. Harvey-style
work needs three distinct tiers with different trust, licensing and retention
rules:

| Tier | Contents | Who can see it | Trust label today |
|---|---|---|---|
| Matter evidence | uploads, exhibits, filings, correspondence | that matter's team | `evidence` |
| Firm knowledge | precedents, templates, clause bank, prior memos, playbooks | the firm | **does not exist** |
| External authority | statutes, regulations, cases, policy manuals, premium databases | everyone, subject to licence | `authority` (only for uploaded statutes) / `web` |

Capability 2 (grounded drafting) is impossible without the middle tier.
Capability 1 is impossible without the third being a first-class, federated
source rather than two allow-listed domains.

### F3. Storage and runtime cannot carry "tens of thousands of documents"
- BM25 is rebuilt **in memory from a JSON file per matter** on first query.
  A 20,000-document matter means loading and tokenising the whole sidecar
  into one process.
- Chroma is a local embedded store on a mounted `data/` directory. No
  concurrent writers, no row-level security, no backup story.
- Upload jobs and chat jobs are **in-process tables** behind a
  **process-wide ingest lock**: one document ingests at a time and a restart
  forgets everything in flight.
- One container runs API, ingestion, embedding and the browser client.

Two branches (`cursor/supabase-pgvector-store-e1d0`, `cursor/sqlite-single-store-e1d0`)
already explore replacing this layer but are unmerged. This is the
single biggest structural decision and the ADR is mostly about it.

### F4. No relevance floor, so the web fallback never fires
Documented in doc 11 and still true: `retrieve()` always returns the top-k
least-bad chunks, so `if not retrieved` is never reached once a matter has
any document. The same gap will silently break federated search: without a
calibrated "nothing relevant here" signal, the planner cannot decide to
widen scope from matter → firm → external.

### F5. The "agent" is a fixed chain; there is no tool use and no MCP
`RAGChain` and `CaseReasoningChain` are hand-wired sequences. The model never
chooses a tool, never issues a follow-up search, never asks for a different
jurisdiction. LangChain is used only as a thin wrapper around prompts and
structured output. Capabilities 1 and 2 are **agentic by definition**
(plan → search several sources → read → draft → verify → revise). Doc 04's
MCP tool design is still the right direction; it has simply not been built.

### F6. No generation of artefacts at all
Nothing produces a DOCX, PPTX, memo, timeline or chart. There is no template
system, no provenance manifest for an output, no review/sign-off state.

### F7. Evaluation is narrow
The golden set is 3 Nigerian documents in one matter. There is no eval for
generic/contract chunking, for US locators, for extraction accuracy, for
generated documents, or for connector freshness. The CI gate would stay
green while every new capability regresses.

### F8. Documentation is in conflict with itself
- `AGENTS.md` contains an **unresolved git merge conflict** (both
  `<<<<<<<` halves are checked in). One half says "Codex API"; the other
  describes a React Native app with a `docs/product-brief.md` that does not
  exist and six Cursor sub-agents that are not in the repo.
- `CLAUDE.md` (Phase 1) and `CLAUDE-2.md` (Phase 2) are both authoritative.
- Docs 15 and 16 are referenced but missing. Several `cursor/*` branches
  carry work the docs assume is on `main`.

For a project built partly by AI agents reading these files, this is a
correctness risk, not a tidiness issue: an agent will build against
whichever half it reads first.

### F9. Security and operations gaps that matter more at platform scale
- Auth is a YAML file plus a signed cookie; no password reset, no MFA, no
  firm/tenant concept, no roles (partner vs. associate vs. paralegal).
- Rate limiting and key-attempt limiting are in-memory per process.
- `mypy` runs with `continue-on-error`; unit tests receive the real
  `ANTHROPIC_API_KEY` in CI.
- Prompt-injection surface grows sharply once external sources, premium
  feeds and tens of thousands of untrusted contracts enter context. The
  current defence is "wrap retrieved text in a template".
- No tenancy or data-residency story. Nigeria's NDPA and US state-bar
  guidance on confidentiality (for example ABA Formal Opinion 512) both bear
  on where client documents and embeddings may live.

### F10. Smaller items
- `bge-small-en-v1.5` is English-only and 384-dim; fine for en, weak for
  French uploads you already translate, and will need an upgrade or a
  legal-domain model once the library grows.
- `max_tokens=1024` on `RAGChain` truncates long synthesised answers.
- OCR noise regex (`#sabilaw`) is specific to the demo PDFs.
- `chunk_id` embeds the filename; two different documents with the same
  name in one matter overwrite each other.
- The Streamlit app is still the default `CMD` in the Dockerfile.

## 4. Capability gap matrix

| Target capability | Exists | Missing | Hardest part |
|---|---|---|---|
| Read US and Nigerian documents | NG rules + judgments; generic fallback | US locator grammars (USC §, CFR, reporter cites, USCIS Policy Manual Vol/Part/Ch; NG Laws of the Federation sections, NIS circulars), country/level taxonomy, jurisdiction-aware prompts | Parsers and citation grammar per jurisdiction |
| Investigations (US + NG) | Upload + Q&A + evidence gaps | Entity and timeline extraction, Bates/exhibit numbering, privilege flags, cross-document contradiction detection, review queue | Scale (F3) and structured extraction |
| Immigration (US + NG) | — | Policy-manual and form-instruction ingestion, eligibility checklists, deadline logic, agency taxonomy (USCIS/EOIR/BIA; NIS/Ministry of Interior) | Keeping authority current |
| 1. Global + premium sources | Two allow-listed NG domains | Source registry, connectors (open + licensed), federation, rerank, relevance floor, citation validity, licence-aware storage | Licensing and "is this still good law" |
| 2. Grounded document/presentation generation | Structured case reasoning | Firm-knowledge tier, drafting agent with tool use, DOCX/PPTX rendering, provenance manifest, human sign-off | Faithfulness for long generated text |
| 3. Analyse tens of thousands of contracts | One-at-a-time ingest, generic chunker | Queue + workers, object storage, Postgres + pgvector, per-doc extraction schema, aggregation and inconsistency checks, cost controls, sampling QA | Throughput and cost |

## 5. What this review recommends (summary; details in the ADR)

1. Fix the documentation conflict first (F8). Decide which brief is true.
2. Adopt a **three-tier corpus model** and a **jurisdiction taxonomy** as the
   data contract before adding any source or parser (F1, F2).
3. Replace the storage/runtime layer with **Postgres + pgvector + full-text
   search, object storage, and a worker queue** (F3). Merge or re-do the
   pgvector branch rather than starting from Chroma.
4. Build the **MCP tool layer** doc 04 already designed, and a thin
   **agent loop** using Claude tool use on top of it (F5). Connectors to
   external sources become MCP servers behind a source registry.
5. Add a **reranker and relevance floor** so scope-widening works (F4).
6. Add **artefact generation** as a separate pipeline with templates,
   provenance and review states (F6).
7. Grow the **eval suite per capability and per jurisdiction** so the CI
   gate means something again (F7).
