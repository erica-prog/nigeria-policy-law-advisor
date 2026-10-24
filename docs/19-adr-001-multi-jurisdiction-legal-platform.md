# ADR-001: Evolve the Nigerian single-matter RAG prototype into a USA + Nigeria legal AI platform

**Status:** Proposed
**Date:** 2026-10-24
**Deciders:** product owner / lead architect (you). A practising lawyer should
sign off the jurisdiction taxonomy and the source allow-list, since both are
legal judgement calls, not engineering ones.

Companion document: [18-architecture-review.md](18-architecture-review.md)
holds the code-level findings (F1 to F10) this ADR refers to.

## Context

The repository is a disciplined single-matter assistant over Nigerian civil
procedure: hybrid retrieval, locator-level citations, tiered faithfulness
checks, per-matter isolation, bring-your-own Claude key, and a CI eval gate.
The target is a Harvey-style platform that:

- reads **USA and Nigerian** law and documents;
- supports **investigations** and **immigration** matters in both countries;
- federates **many external and premium primary-law sources**;
- lets **agents draft documents and presentations** grounded in firm
  precedent;
- analyses **tens of thousands of contracts, filings and exhibits** at once.

Forces at play:

| Force | Effect on the decision |
|---|---|
| Non-technical lawyer users | Every output must carry verifiable citations and a trust label. Silent hallucination is the primary risk; scale makes it worse, not better. |
| Two countries, several agencies and courts | Jurisdiction must become a structured taxonomy, and citation grammars must be pluggable. |
| Premium sources are licensed | Storage of licensed text is usually forbidden; the design must retrieve on demand and persist only citations and short snippets where the licence permits. |
| Solo developer, Python-strong, learning TypeScript | Prefer one deployable and one language for the core. TypeScript appears only where it earns its place. |
| Bring-your-own-key billing | Cost per user must be attributable; batch analysis must have hard budgets. |
| Client confidentiality, NDPA (Nigeria) and US bar guidance on AI | Tenancy, audit trails, retention and data residency are requirements, not later polish. |
| Existing eval gate and grounding code | Reuse, do not rewrite, the faithfulness and citation-resolution logic. |

## Decision

Adopt **Option B: a modular monolith with a shared data platform and a
tool-using agent layer**, delivered in phases. Concretely:

1. **Three corpus tiers with one source contract.** `matter` (evidence),
   `firm` (precedents, templates, clause bank, memos) and `authority`
   (public and premium primary law). Every chunk, regardless of tier, carries
   the same metadata contract: `tenant_id`, `tier`, `matter_id?`,
   `jurisdiction` (structured, see below), `doc_type`, `locator`,
   `locator_scheme`, `source_id`, `effective_date?`, `licence`, `trust_kind`
   (`evidence | firm | authority | web`). The existing `kind` field on
   citations generalises into `trust_kind`.
2. **A jurisdiction taxonomy, not a string.** `country` (NG, US),
   `level` (federal, state), `region` (Lagos, NY, ...), `body` (FHC, Lagos
   Magistrates, USCIS, EOIR/BIA, NIS, Federal Register, ...), and
   `practice_area` (immigration, investigations, commercial, ...). Filters
   and prompts read from this object; the prompt no longer names a country.
3. **Storage and runtime: PostgreSQL + pgvector + Postgres full-text search,
   object storage for originals, and a worker queue.** One API process, N
   worker processes, same codebase. Chroma, the JSON sidecars and the
   in-process job tables are retired. The unmerged pgvector branch is the
   starting point, re-based onto the new metadata contract.
4. **Retrieval becomes a federated planner.** Scope resolution (matter →
   firm → authority → connectors) → parallel retrievers → cross-encoder
   rerank (Hugging Face) → calibrated relevance floor → context assembly with
   per-tier trust labels. The relevance floor fixes F4 and is what lets the
   planner widen scope honestly.
5. **External sources are MCP servers behind a source registry.** Each
   connector exposes `search`, `fetch`, `cite` tools with the same result
   schema. Open sources first (CourtListener/Free Law Project, GovInfo,
   eCFR, Federal Register, USCIS Policy Manual; NigeriaLII, nass.gov.ng,
   NDPC, NIS), licensed ones (vLex, LawPavilion, Westlaw/Lexis where an API
   licence exists) behind feature flags. The registry records licence terms,
   caching rules, freshness and a lawyer-approved allow status.
6. **An agent loop using Claude tool use over those MCP tools** replaces the
   fixed chains for research and drafting. The existing `RAGChain` and
   `CaseReasoningChain` remain as callable tools, so their grounding checks
   are reused rather than reimplemented. LangChain stays for prompt
   templates and structured output where it is already used.
7. **Artefact generation is its own pipeline**: template registry (DOCX via
   `python-docx`/`docxtpl`, PPTX via `python-pptx`), a drafting agent that
   must cite from the firm and authority tiers, a provenance manifest per
   output, and a review state machine (`draft → reviewed → approved`). The
   system never sends or files anything.
8. **Bulk analysis is a batch pipeline, not a chat feature**: queue → extract
   per document with a typed schema (parties, dates, governing law,
   obligations, risk clauses, evidence spans) → store rows → aggregate and
   cross-check → review queue with sampling QA. Model tiering and the
   Batch API keep cost bounded.
9. **Evaluation grows per capability and per jurisdiction** and stays a CI
   gate: retrieval golden sets for NG and US, extraction F1 on a labelled
   contract set, generated-document rubric, connector freshness canaries.

## Options Considered

### Option A: Extend the current monolith in place

Keep Chroma, JSON sidecars, in-process jobs and fixed chains; add US
parsers, more allow-listed domains, and a document generator.

| Dimension | Assessment |
|---|---|
| Complexity | Low to start, rising fast |
| Cost | Lowest initial, highest rework |
| Scalability | Fails at thousands of documents (F3); no concurrent ingestion |
| Team familiarity | High; it is the code you have |

**Pros:** fastest path to a US demo; no migration; eval gate keeps working.
**Cons:** cannot meet capability 3 at all; capability 1 degenerates into a
longer allow-list; no tenancy; every later step is a rewrite under load.

### Option B: Modular monolith + data platform + MCP tool layer (chosen)

One Python codebase with clear modules (`sources`, `retrieval`, `agents`,
`artefacts`, `batch`, `api`), one database (Postgres + pgvector), object
storage, a worker queue, MCP servers for connectors, Claude tool use for the
agent loop.

| Dimension | Assessment |
|---|---|
| Complexity | Medium; several new moving parts but one language and one deployable plus workers |
| Cost | Managed Postgres + object storage + workers; model cost dominated by batch analysis and controllable |
| Scalability | Tens of thousands of documents per tenant is routine for pgvector + FTS with partitioning; connectors scale independently |
| Team familiarity | High for Python/FastAPI/Postgres; new: queues, pgvector, MCP server authoring, agent loops |

**Pros:** meets all three capabilities; reuses grounding code; migration is a
single storage boundary (the API already never touches Chroma directly);
tenancy and audit become database features; TypeScript is optional.
**Cons:** real migration work; more operational surface (queue, workers,
backups); you must design the metadata contract carefully once.

### Option C: Microservices and managed search from day one

Separate services for ingestion, retrieval, agents, connectors and
generation; a managed vector/search engine (Vespa, Elastic, Pinecone); an
event bus.

| Dimension | Assessment |
|---|---|
| Complexity | High |
| Cost | Highest fixed cost; several managed services |
| Scalability | Best ceiling |
| Team familiarity | Low; multi-service operations for one developer |

**Pros:** Harvey-like ceiling; independent scaling.
**Cons:** premature for current scale; coordination and observability
overhead would consume the only developer; no capability is unlocked that
Option B does not.

### Sub-decision: build vs. license for primary law

| Source class | Decision | Why |
|---|---|---|
| Open US (CourtListener, GovInfo, eCFR, Federal Register, USCIS Policy Manual) | Build connectors, cache text where terms allow | Free, API-accessible, stable citation schemes |
| Open NG (NigeriaLII, nass.gov.ng, NDPC, NIS, court websites) | Build connectors; expect scraping and OCR; keep a curated mirror | Coverage is patchy; a maintained mirror is the real asset |
| Licensed (LawPavilion, vLex, Westlaw, Lexis) | Integrate only via licensed APIs, retrieve on demand, store citations and permitted snippets | Storing full text usually breaches terms; citation validity ("still good law") is a licensed feature worth paying for |
| Open web | Keep the allow-listed Anthropic `web_search` tool as the last-resort tier, labelled `web` | Already works; never promote it to `authority` |

## Trade-off Analysis

- **Speed now vs. rework later.** Option A reaches a US demo in days but
  cannot host capability 3, and every capability-1 feature would be rebuilt
  on migration. Option B pays the storage migration once, early, while the
  codebase is 5k lines and the API already isolates storage.
- **One deployable vs. independent scaling.** Workers in the same codebase
  (Option B) give batch throughput without service boundaries. Option C's
  independent scaling is not needed until a single Postgres cannot hold a
  tenant's corpus, which is far beyond tens of thousands of documents.
- **Agent flexibility vs. grounding control.** A free agent loop can wander.
  Keeping `RAGChain` and `CaseReasoningChain` as tools, requiring every
  final answer to pass the same citation resolution, and capping tool calls
  and tokens per run preserves the current grounding guarantees inside an
  agentic design.
- **Open vs. licensed sources.** Open connectors give coverage fast but no
  citator. Licensed APIs give validity signals and polished metadata but
  constrain storage. The registry makes this per-source, so the product can
  ship with open sources and add licences without re-architecture.
- **Python-only vs. adding TypeScript.** MCP servers can be written in
  Python (FastMCP) or TypeScript. Core connectors should be Python so one
  test suite covers them. A TypeScript MCP server is a good *first* TS
  project if you want one: it is small, typed at the boundary, and the
  `05-typescript-guide.md` concepts (interfaces, Zod validation,
  async/await, Promises) all appear in it. See the teaching note below.

## Consequences

**Easier**
- Adding a jurisdiction becomes: a taxonomy entry, a locator grammar, a
  parser, a golden set. No prompt or retriever changes.
- Adding a source becomes: an MCP server plus a registry row.
- Tenancy, audit and retention are Postgres row-level security and tables,
  not file-system conventions.
- Bulk analysis and drafting share the same storage and the same citation
  resolution, so one eval harness covers both.

**Harder**
- Operating a queue and workers, backups and migrations.
- Keeping licence terms encoded correctly per source; a mistake is a legal
  problem, not a bug.
- Calibrating the relevance floor and reranker per tier and jurisdiction.
- Long generated documents are harder to verify than short answers;
  faithfulness checking must move to span level.

**To revisit**
- Embedding model: `bge-small-en-v1.5` is English-only; evaluate a
  multilingual or legal-domain model once French and NG corpora grow.
- Whether firm-knowledge retrieval needs a separate index per tenant for
  isolation guarantees stronger than row-level security.
- Whether to move the agent loop onto the Claude Agent SDK once MCP tools
  exist, and whether Managed Agents simplify hosting.
- Option C becomes relevant if one tenant exceeds what a partitioned
  Postgres can hold, or if connectors need a different runtime.

## Key design notes (how, not code)

### Jurisdiction taxonomy and locator schemes
Treat the taxonomy as data in the repository (JSON), reviewed by a lawyer.
Each `doc_type` maps to a `locator_scheme` with a grammar used both by the
parser and by the faithfulness check, so `is_supported_citation` stops
being a prefix match and becomes scheme-aware. Minimum schemes:

| Scheme | Example locator | Used by |
|---|---|---|
| `ng_rules` | Order 5 Rule 3 | NG court rules (exists) |
| `ng_section` | Section 37(1) | NG Acts, Laws of the Federation |
| `judgment_para` | Paragraph 11 | NG and US judgments (exists) |
| `us_usc` | 8 U.S.C. § 1255(a) | US statutes |
| `us_cfr` | 8 C.F.R. § 245.1 | US regulations |
| `us_reporter` | 123 F.3d 456, 460 | US cases with pinpoint |
| `uscis_pm` | USCIS-PM Vol. 7, Pt. B, Ch. 2 | Immigration policy |
| `contract_clause` | Article 12.3 | Contracts (replaces `Section N`) |
| `exhibit_bates` | ACME-0001234 | Investigations |

### Investigations and immigration as workflows, not modes
Both are **playbooks** on top of the platform: a declared set of tools,
extraction schemas, checklists and output templates per `practice_area` and
`country`. Investigations add entity and timeline extraction, privilege
flags and contradiction detection across a matter. Immigration adds
eligibility checklists against the policy tier, form-instruction retrieval
(USCIS forms; NIS visa categories) and deadline computation. The chat
router's `decide_mode` generalises into a playbook selector.

### Bulk contract analysis pipeline
Queue a job per document; a worker extracts a typed record with evidence
spans (chunk id + character offsets) using a cheaper model for triage and a
stronger one for flagged clauses; store records in a table; run
cross-document checks (deterministic for dates, parties and defined terms;
model-based for conflicting obligations); surface a review queue with
sampling-based QA and per-job cost ceilings. Prompt caching on the shared
schema and the Batch API cut cost substantially. The result is queryable by
the agent as a tool, so "which of these 8,000 NDAs have a non-mutual
indemnity governed by Lagos law" is a SQL-backed answer with citations.

### Grounded drafting
The drafting agent may only cite from `firm` and `authority` tiers, must
produce a provenance manifest (every paragraph → supporting chunk ids), and
hands the document to a review state. Span-level faithfulness (judge per
section, not per document) keeps the check tractable. Presentations are
generated from the same outline model so slides and memo share provenance.

### Security, compliance, observability
Tenant → users → matters with roles; row-level security in Postgres;
immutable audit log of every tool call and generated artefact; encryption
at rest for originals and embeddings; a per-tenant residency setting (NG or
US region); retention policies per tier; a prompt-injection boundary that
treats every external and uploaded text as data, with connector output
schema-validated before it reaches the model; OpenTelemetry traces per agent
run with a cost ledger per user and matter. `mypy` moves from advisory to
blocking before the storage migration.

### TypeScript teaching note (where TS would appear)
If you write one connector as a TypeScript MCP server, these concepts show
up and are worth learning in that order: **interfaces** (describing the
search-result shape the Python side expects), **Zod** (runtime validation of
tool inputs, the TS equivalent of Pydantic), **async/await and Promises**
(every HTTP call to a source), and **discriminated unions** (`trust_kind`
as a type the compiler checks). Everything else in this ADR can stay in
Python.

## Action Items

Phase 0 — hygiene (days)
1. [ ] Resolve the merge conflict in `AGENTS.md`; pick one brief (web client vs. React Native) and delete or archive the other; create or remove the `docs/product-brief.md` reference.
2. [ ] Merge `CLAUDE.md` and `CLAUDE-2.md` guidance into one source of truth; note the stack is the Claude API, not Codex.
3. [ ] Make `mypy` blocking; stop passing the real API key to unit tests.

Phase 1 — data contract and storage (weeks)
4. [ ] Write the jurisdiction taxonomy and locator-scheme registry as JSON; have a lawyer review it.
5. [ ] Define the unified chunk metadata contract and the three tiers; update `docs/contracts/`.
6. [ ] Re-base the pgvector branch onto the contract; add Postgres FTS for BM25-equivalent search; move originals to object storage; add a worker queue; retire the in-process job tables.
7. [ ] Add a cross-encoder reranker and a calibrated relevance floor; add a golden-set case that expects scope widening.

Phase 2 — sources and agent loop (weeks)
8. [ ] Build the source registry and the first open connectors as MCP servers (one US, one NG), with licence, caching and freshness fields.
9. [ ] Add US locator parsers (`us_usc`, `us_cfr`, `us_reporter`, `uscis_pm`) and a US golden set; make the faithfulness check scheme-aware.
10. [ ] Implement the agent loop with Claude tool use over MCP tools, with `RAGChain` and `CaseReasoningChain` exposed as tools, per-run tool and token caps, and an audit log.
11. [ ] Define the investigations and immigration playbooks for NG and US.

Phase 3 — bulk analysis (weeks)
12. [ ] Extraction schemas per document type with evidence spans; batch workers; cross-document checks; review queue; cost ceilings; extraction F1 eval on a labelled set.

Phase 4 — artefacts (weeks)
13. [ ] Template registry, drafting agent restricted to firm and authority tiers, DOCX/PPTX rendering, provenance manifest, review states, generated-document rubric in eval.

Cross-cutting
14. [ ] Tenancy, roles, row-level security, audit, residency and retention settings.
15. [ ] OpenTelemetry tracing and a cost ledger per user and matter.
16. [ ] Expand CI gates: NG and US retrieval, extraction F1, drafting rubric, connector canaries.
