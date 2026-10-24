# 18 — Architecture review: from Nigerian single-matter RAG to a USA + Nigeria legal AI platform

Revision 2, reviewed on 2026-10-24 against `main` at `580d393` merged into
branch `2026-10-19-pozq`. Revision 1 was written against a stale branch and
overstated two gaps (storage and the relevance floor); those are corrected
below. This is the "what is actually there" half of the review. The decision
about where to take it is in
[19-adr-001-multi-jurisdiction-legal-platform.md](19-adr-001-multi-jurisdiction-legal-platform.md).

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
| Ingestion | PDF (pypdf, OCR fallback via tesseract) and DOCX. Heuristic type detection: `statute` (Order/Rule), `judgment` (numbered paragraphs), else `generic` (recursive splitter, 1200/200). Optional per-chunk Claude translation with a fidelity check. Uploaded originals are deleted after ingestion. | `src/policy_advisor/ingestion/` |
| Storage | Supabase Postgres with pgvector. One `chunks` table holds text, metadata and the 384-dim embedding in the same row; `matters` carries an owner and a `content_version`; `chat_exchanges` keeps a lawyer's last five exchanges; `index_meta` pins the embedding model. Row-level security is on and the Supabase API roles are revoked. Exact cosine scan per matter, no vector index by design. Still on disk: per-matter `conversation.json` and `meta.json`, `credentials.yaml`, encrypted `user_keys.json`. | `db.py`, `migrations/0001_init.sql`, `ingestion/matter_store.py`, docs 16 |
| Retrieval | Hybrid: bge-small dense search in Postgres + rank-bm25 held in memory per matter and rebuilt when `content_version` moves; min-max fusion; optional `jurisdiction` filter; exact "Order N Rule M" short-circuit. A two-band relevance gate (cosine distance 0.21 / 0.50) with a Claude adjudicator for the middle band. No reranker. | `retrieval/`, `generation/relevance_check.py` |
| Generation | `RAGChain` (one retrieval, relevance gate, one Claude call, deterministic citation check, restricted web fallback). `CaseReasoningChain` (issues → arguments per side → synthesis) with tiered faithfulness: regex locator check, one corrective retry, Claude-as-judge at step boundaries. `AdvisoryChain` (opt-in recommendation with computed, not generated, confidence). One retry/backoff policy for every Claude call. | `generation/`, `llm_retry.py` |
| Web research | Anthropic `web_search` server tool, `allowed_domains` = `nass.gov.ng`, `ndpc.gov.ng`. | `generation/web_search.py` |
| API / UI | FastAPI with cookie auth, per-matter ownership check, in-process job tables for uploads and chat, a chat router that picks analysis / research / question. Vanilla-JS browser client with the avatar. Streamlit prototype still the default container command. | `api/`, `web/`, `app.py` |
| Eval / CI | Golden set over the 3-document demo matter gated at recall 0.9; a relevance-floor calibration report (advisory); a case-reasoning invariants job; both eval jobs start a pgvector container. 232 unit tests. `mypy` is advisory: 43 errors on `main`, 18 after the fix branch. | `eval/`, `.github/workflows/ci.yml` |
| Agents / docs | Six Cursor role agents, a product brief, docs 15 to 17, web-MVP architecture and API contract. | `.cursor/agents/`, `docs/` |
| MCP / TypeScript | Designed in docs 04 and 05; **nothing implemented**. No MCP server, no TypeScript beyond the browser client. | — |

About 6,300 lines of Python. It is a well-disciplined **single-matter
Nigerian civil-procedure assistant** with a real database behind it, not yet
a platform.

## 2. Strengths to keep (do not rewrite these)

- **Grounding discipline is the best part of the codebase.** Citation
  locators are resolved against chunks the model actually saw; web answers
  are labelled as a different trust tier; case reasoning and advisory output
  are structured so every step is auditable, and confidence is computed from
  the state of the analysis rather than asked of the model. Keep
  `faithfulness.py`, `judge_check.py`, `advisory_models.py` and the
  `kind: authority | evidence | web` distinction as the contract every new
  source type must satisfy.
- **The relevance gate is honest about its own limits.** Two bands plus an
  adjudicator, calibrated by a script that is re-run in CI, with the
  reasoning written down in `config.py`. This is the pattern to extend per
  tier and per jurisdiction, not replace.
- **Storage is one transaction.** Text and embedding share a row; a document
  is replaced wholesale; `content_version` lets every process see an upload.
  The schema comment explains each choice. Build on this.
- **Per-matter isolation is enforced at the function level**, and the
  Supabase tables are unreachable through the public API roles.
- **Bring-your-own-key, per-user chains, secrets never logged.**
- **The eval gate exists, runs in CI, and now has a structural-invariants
  job** for the reasoning chains.

## 3. Findings, ranked by how much they block the target

### F0. `main` does not install and the upload path raises (blocking, fix exists)
Three independent breakages are on `main` right now:

| Breakage | Symptom | Evidence |
|---|---|---|
| `uv.lock` does not parse ("Dependency `flatbuffers` has missing `source` field") | `uv sync --frozen`, `uv run`, the Dockerfile and every CI job fail before running anything | `uv export --frozen` on main's lock |
| `ingestion/ingest_document.py` calls `load_vector_store`, `chunk_to_document` and `upsert_chunks` without importing them, while importing `replace_document_chunks` and `chunk_to_record` unused | `NameError` on every upload, document removal and case deletion | ruff F821, mypy |
| `ingestion/matter_store.py` calls `connect`, `METADATA_COLUMNS`, `check_embedding_model`, `get_settings` and `record_from_row` without importing them | `NameError` on every matter listing and every retrieval | ruff F821 |

Cause: the merges that brought the web MVP and Cursor-agent branches over the
pgvector store (`3be941d`, `0b565f2`) resolved the same files in different
directions. Branch `fix-uv-lock-dangling` (8 commits, 2026-10-19, contains
all of `main`) repairs all three, moves matter ownership from `meta.json`
into the `matters` table, keeps live Anthropic calls out of the unit-test
job, and adds the workspace-id header. Its lock exports cleanly and ruff is
clean on the repaired files. **Merge it before anything else.** Until then,
no CI result on `main` means anything. Branch `2026-10-19-pozq` already carries
this merge, so the pull request from it brings the repair with it.

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

### F3. Storage is half-migrated and originals are thrown away
Revision 1 said storage could not scale; `main` has since moved chunks,
vectors and chat history into Postgres, which removes the worst of it. What
remains:

- **Uploaded originals are deleted after ingestion** (`api/jobs.py`,
  `routers/documents.py`; the migration script confirms "the original
  uploaded files were never kept"). Nothing can be re-embedded under a new
  model, re-chunked under a new parser, shown to the lawyer as the source
  page, or bulk-analysed later. For capability 3 this is the first thing to
  fix. Object storage is required, not optional.
- **BM25 is still an in-memory index per matter**, rebuilt from a database
  snapshot when the matter changes. Fine for one lawyer's matter; a
  library tier or a 20,000-document matter needs Postgres full-text search
  or an external index.
- **Files beside the database.** `conversation.json` and `meta.json` per
  matter, `credentials.yaml` and `user_keys.json` live on a mounted volume.
  On `main` ownership is read from `meta.json` while matter listing reads the
  `matters` table; the fix branch resolves that, the rest stays on disk.
- **In-process job tables and a process-wide ingest lock.** One document
  ingests at a time; a restart forgets in-flight work. The connection pool
  is capped at four to respect the Supabase session pooler, which also caps
  concurrency for workers.
- **No vector index by design.** Correct for an exact scan over one matter;
  not viable for a shared authority tier of hundreds of thousands of chunks.

### F4. Relevance floor: resolved on `main`, with limits worth knowing
The gate exists and the web fallback can fire. Limits: thresholds are
calibrated on 21 golden-set questions over one matter; about half of
queries land in the adjudication band and spend a Claude call; BM25-only
candidates are never rescued (by design, they have no absolute score); there
is still no reranker, so federated results from several tiers have no common
scale to be merged on.

### F5. The "agent" is a fixed chain; there is no tool use and no MCP
`RAGChain`, `CaseReasoningChain` and `AdvisoryChain` are hand-wired
sequences. The model never chooses a tool, never issues a follow-up search,
never asks for a different jurisdiction. LangChain is used only as a thin
wrapper around prompts and structured output. Capabilities 1 and 2 are
**agentic by definition** (plan → search several sources → read → draft →
verify → revise). Doc 04's MCP tool design is still the right direction; it
has simply not been built.

### F6. No generation of artefacts at all
Nothing produces a DOCX, PPTX, memo, timeline or chart. There is no template
system, no provenance manifest for an output, no review/sign-off state.

### F7. Evaluation is better but still narrow
The gate is tighter (0.9), calibration is visible in CI, and the invariants
job checks the reasoning chains' structural promises. The corpus under test
is still 3 Nigerian documents in one matter. There is no eval for
generic/contract chunking, for US locators, for extraction accuracy, for
generated documents, for routing between playbooks, or for connector
freshness.

### F8. Documentation disagrees with the code
- `AGENTS.md` on `main` is the Phase 1 memory and the web brief concatenated
  (both halves, no conflict markers). It says "Codex API" and "React Native".
  This branch replaces it.
- `docs/product-brief.md` and the six Cursor agents describe a React Native
  app with a managed auth provider; the repository has a browser client and
  cookie auth.
- `docs/architecture/web-mvp.md` still says storage is Chroma.
- The document map in `docs/00-overview.md` omits docs 07 to 10 and 15 to 17.

For a project built partly by AI agents reading these files, this is a
correctness risk, not a tidiness issue.

### F9. Security and operations gaps that matter more at platform scale
- Auth is a YAML file plus a signed cookie; no password reset, no MFA, no
  firm/tenant concept, no roles (partner vs. associate vs. paralegal).
- Rate limiting and key-attempt limiting are in-memory per process.
- `mypy` runs with `continue-on-error`: 43 errors on `main` (including the
  F0 `NameError`s it would have caught), 18 after the fix branch. `ruff
  format --check` would reformat 51 files; it is a documented convention
  but not a CI step. On `main`, unit tests receive
  the real `ANTHROPIC_API_KEY`; the fix branch stops that.
- Supabase has no African region (doc 16). Hosting client documents in
  London or Frankfurt is a decision under Nigeria's NDPA that a lawyer, not
  the code, must take; US state-bar guidance on AI confidentiality bears on
  the US side.
- Prompt-injection surface grows sharply once external sources, premium
  feeds and tens of thousands of untrusted contracts enter context. The
  current defence is "wrap retrieved text in a template".

### F10. Smaller items
- `bge-small-en-v1.5` is English-only and 384-dim; the schema pins the
  dimension, so a model change is a migration plus a full re-embed, which
  F3 makes impossible today because originals are gone.
- `max_tokens=1024` on `RAGChain` truncates long synthesised answers.
- OCR noise regex (`#sabilaw`) is specific to the demo PDFs.
- `chunk_id` embeds the filename; two different documents with the same
  name in one matter overwrite each other.
- The Streamlit app is still the default `CMD` in the Dockerfile.
- `invalidate_matter` is now redundant (content_version does the job) but
  still threaded through every chain.

## 4. External input: the `legal-ai-skills` plugin

[rohasnagpal/legal-ai-skills](https://github.com/rohasnagpal/legal-ai-skills)
(MIT, v4.1.1) is a Claude Code / Codex plugin, not a product backend. It
bundles 21 agent prompts (a managing partner, 17 specialists, India/US/UK
jurisdiction counsel), 226 skill prompts, 20 workflows, per-jurisdiction
source guides, five local MCP servers in plain Node.js (legal research,
company registries, document production, calculators, sanctions screening),
two hosted MCP connections (CourtListener, GovInfo), a tool-blocking hook,
a generated skill registry and behavioural eval YAMLs. It has **no Nigeria
and no immigration content**, and its servers are JavaScript, not
TypeScript. What is worth adopting, adapting or rejecting is decided in the
ADR.

## 5. Capability gap matrix

| Target capability | Exists | Missing | Hardest part |
|---|---|---|---|
| Read US and Nigerian documents | NG rules + judgments; generic fallback | US locator grammars (USC §, CFR, reporter cites, USCIS Policy Manual Vol/Part/Ch; NG Laws of the Federation sections, NIS circulars), country/level taxonomy, jurisdiction-aware prompts | Parsers and citation grammar per jurisdiction |
| Investigations (US + NG) | Upload + Q&A + evidence gaps + advisory | Entity and timeline extraction, Bates/exhibit numbering, privilege flags, cross-document contradiction detection, review queue; retained originals | Scale (F3) and structured extraction |
| Immigration (US + NG) | — | Policy-manual and form-instruction ingestion, eligibility checklists, deadline logic, agency taxonomy (USCIS/EOIR/BIA; NIS/Ministry of Interior) | Keeping authority current |
| 1. Global + premium sources | Two allow-listed NG domains; relevance gate | Source registry, connectors (open + licensed), federation, rerank, citation validity, licence-aware storage | Licensing and "is this still good law" |
| 2. Grounded document/presentation generation | Structured case reasoning and advisory | Firm-knowledge tier, drafting agent with tool use, DOCX/PPTX rendering, provenance manifest, human sign-off | Faithfulness for long generated text |
| 3. Analyse tens of thousands of contracts | Postgres store; one-at-a-time ingest; generic chunker; originals deleted | Object storage, queue + workers, per-doc extraction schema, aggregation and inconsistency checks, cost controls, sampling QA | Throughput and cost |

## 6. What this review recommends (summary; details in the ADR)

1. Merge `fix-uv-lock-dangling` and get `main` green (F0).
2. Stop deleting uploads: put originals in object storage (F3).
3. Fix the documentation conflicts (F8): this branch's `AGENTS.md`, then
   the product brief and Cursor agents.
4. Adopt a **three-tier corpus model** and a **jurisdiction taxonomy** as
   the data contract before adding any source or parser (F1, F2).
5. Finish the storage move (files into Postgres, FTS for keyword search,
   a worker queue) on top of the schema `main` already has (F3).
6. Build the **MCP tool layer** doc 04 designed, adopting the plugin's US
   connectors and writing the Nigeria connector in TypeScript, with a thin
   **agent loop** and a tool-policy layer on top (F5).
7. Add a **reranker** so tiers can be merged, and extend the relevance gate
   per tier (F4).
8. Add **artefact generation** as a separate pipeline with templates,
   provenance and review states (F6).
9. Grow the **eval suite per capability and per jurisdiction**, including
   behavioural routing evals (F7).
