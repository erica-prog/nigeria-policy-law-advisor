# ADR-001: Evolve the Nigerian single-matter RAG prototype into a USA + Nigeria legal AI platform

**Status:** Accepted 2026-10-24 (revision 2, re-based on `main` at `580d393`; accepted by the
product owner with the instruction to deliver the platform in full, phase by phase)
**Date:** 2026-10-24
**Deciders:** product owner / lead architect (you). A practising lawyer should
sign off the jurisdiction taxonomy and the source allow-list, since both are
legal judgement calls, not engineering ones.

Companion document: [18-architecture-review.md](18-architecture-review.md)
holds the code-level findings (F0 to F10) this ADR refers to.

Decided since revision 1: **TypeScript is in.** The Nigeria connector will be
written as a TypeScript MCP server (see "Where TypeScript appears").

## Context

The repository is a disciplined single-matter assistant over Nigerian civil
procedure: hybrid retrieval over hosted Postgres + pgvector, a calibrated
relevance gate, locator-level citations, tiered faithfulness checks, an
opt-in advisory chain with computed confidence, per-matter isolation,
bring-your-own Claude key, and CI eval gates. `main` is currently broken by a
bad merge (review F0) and the repair sits on an unmerged branch. The target is
a Harvey-style platform that:

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
| Solo developer, Python-strong, learning TypeScript | One deployable and one language for the core. TypeScript where it earns its place: the MCP connector boundary. |
| Bring-your-own-key billing | Cost per user must be attributable; batch analysis must have hard budgets. |
| Client confidentiality, NDPA (Nigeria) and US bar guidance on AI | Tenancy, audit trails, retention and data residency are requirements, not later polish. the database provider has no African region. |
| Existing storage, grounding and gating code on `main` | Build on the Postgres schema, the relevance gate and the faithfulness checks. Do not rewrite them. |
| An MIT-licensed plugin (`legal-ai-skills`) already ships US connectors, research protocol, skill prompts and eval formats | Adopt what fits, write what is missing (Nigeria, immigration), reject what assumes a Claude Code session. |

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
   citations generalises into `trust_kind`. This lands as migration `0002`
   on the schema `main` already has.
2. **A jurisdiction taxonomy, not a string.** `country` (NG, US),
   `level` (federal, state), `region` (Lagos, NY, ...), `body` (FHC, Lagos
   Magistrates, USCIS, EOIR/BIA, NIS, Federal Register, ...), and
   `practice_area` (immigration, investigations, commercial, ...). Filters
   and prompts read from this object; the prompt no longer names a country.
   The per-jurisdiction material lives in `jurisdictions/<country>/` using the
   plugin's file layout (`authoritative-sources.md`, `operating-rules.md`,
   `integrations.md`, `skill-map.yaml`), which we author for `ng` and adapt
   for `us`.
3. **Storage and runtime: finish the move `main` started.** Keep hosted
   Postgres + pgvector. Add: object storage for uploaded originals (stop
   deleting them); Postgres full-text search (`tsvector`) for keyword
   retrieval over the `firm` and `authority` tiers and for large matters,
   with the in-memory BM25 kept only for small matters; a Postgres-backed
   worker queue (no new infrastructure) with N worker processes; the
   remaining files (`conversation.json`, `meta.json`, credentials, user
   keys) moved into tables; an HNSW index on the `authority` tier only. One
   API process, N workers, same codebase.
4. **Retrieval becomes a federated planner.** Scope resolution (matter →
   firm → authority → connectors) → parallel retrievers → cross-encoder
   rerank (Hugging Face) → the existing two-band relevance gate, calibrated
   per tier → context assembly with per-tier trust labels. The gate's
   "nothing relevant here" signal is what lets the planner widen scope
   honestly.
5. **External sources are MCP servers behind a source registry.** Each
   connector exposes `search`, `fetch`, `cite` tools with one result schema
   that includes `source`, `source_url`, `retrieved_at`, `coverage_warning`,
   `licence` and `access_status` (fields taken from the plugin's connector
   results). For the US, adopt the plugin's connectors as-is: the hosted
   CourtListener MCP, the hosted GovInfo MCP, and `legal-research-server.mjs`
   for eCFR, Federal Register and Regulations.gov. For Nigeria, build the
   connector in TypeScript (NigeriaLII, nass.gov.ng, NDPC, NIS, court
   websites; expect scraping, OCR and a curated mirror). Licensed sources
   (LawPavilion, vLex, Westlaw/Lexis where an API licence exists) sit behind
   feature flags. The registry records licence terms, caching rules,
   freshness, and a lawyer-approved allow status; the `web_search`
   allow-list becomes a registry row with `trust_kind: web`.
6. **An agent loop using Claude tool use over those MCP tools, with a
   tool-policy layer.** The loop replaces the fixed chains for research and
   drafting; `RAGChain`, `CaseReasoningChain` and `AdvisoryChain` remain as
   callable tools so their grounding checks are reused. The policy layer is
   the product equivalent of the plugin's `PreToolUse` hook and
   `disabled_tools` list: an allow-list of tools per playbook, a deny-list
   of anything that creates alerts, subscriptions, filings or writes to an
   external account, and per-run caps on tool calls and tokens. LangChain
   stays for prompt templates and structured output where it is already
   used.
7. **Artefact generation is its own pipeline**: template registry (DOCX via
   `python-docx`/`docxtpl`, PPTX via `python-pptx`, rendered server-side),
   a drafting agent that must cite from the firm and authority tiers, a
   provenance manifest per output using the plugin's source-provenance
   fields (instrument, issuing body, pinpoint, proposition, identifier,
   status, version date, access date, verification status, limitations),
   and a review state machine (`draft → reviewed → approved`). The system
   never sends or files anything.
8. **Bulk analysis is a batch pipeline, not a chat feature**: queue → extract
   per document with a typed schema seeded from the plugin's
   obligations-extractor ledger (who, what, when, trigger, consequence,
   clause pinpoint, plus parties, dates, governing law, risk clauses and
   evidence spans) → store rows → aggregate and cross-check → review queue
   with sampling QA. Model tiering and the Batch API keep cost bounded.
9. **Evaluation grows per capability and per jurisdiction** and stays a CI
   gate: retrieval golden sets for NG and US, extraction F1 on a labelled
   contract set, generated-document rubric, connector freshness canaries,
   and **behavioural routing evals** in the plugin's YAML shape (`prompt`,
   `expected_skill`, `expected_behavior`, `must_include`,
   `must_not_include`) for playbook and jurisdiction routing.
10. **Playbooks, not modes, for investigations and immigration.** A playbook
    declares its tools, extraction schemas, checklists and output templates
    per `practice_area` and `country`. Investigations playbooks are adapted
    from the plugin's investigations skills (evidence organiser, chronology
    builder, chain-of-custody documenter, legal-hold planner, privilege-log
    builder, production-set checker, redaction reviewer, investigation
    report drafter). Immigration playbooks are written from scratch for
    USCIS/EOIR and NIS, since the plugin has none.

## Options Considered

### Option A: Extend `main` in place

Keep the half-migrated storage (Postgres for chunks, files for the rest,
originals deleted), in-process jobs and fixed chains; add US parsers, more
allow-listed domains, and a document generator.

| Dimension | Assessment |
|---|---|
| Complexity | Low to start, rising fast |
| Cost | Lowest initial, highest rework |
| Scalability | Fails at thousands of documents: no originals, in-memory BM25, one ingest at a time |
| Team familiarity | High; it is the code you have |

**Pros:** fastest path to a US demo; no migration; eval gate keeps working.
**Cons:** cannot meet capability 3; capability 1 degenerates into a longer
allow-list; no tenancy; every later step is a rewrite under load.

### Option B: Modular monolith + data platform + MCP tool layer (chosen)

One Python codebase with clear modules (`sources`, `retrieval`, `agents`,
`playbooks`, `artefacts`, `batch`, `api`), the existing Postgres + pgvector
database extended with FTS and a queue, object storage, MCP servers for
connectors (adopted and built), Claude tool use for the agent loop.

| Dimension | Assessment |
|---|---|
| Complexity | Medium; several new moving parts but one language for the core and one deployable plus workers |
| Cost | Managed Postgres + object storage + workers; model cost dominated by batch analysis and controllable |
| Scalability | Tens of thousands of documents per tenant is routine for pgvector + FTS with partitioning; connectors scale independently |
| Team familiarity | High for Python/FastAPI/Postgres; new: queues, MCP server authoring, agent loops, one TypeScript service |

**Pros:** meets all three capabilities; reuses the storage, gating and
grounding code on `main`; tenancy and audit become database features; the
US connector problem is already solved under MIT.
**Cons:** real migration work; more operational surface (queue, workers,
backups, one Node runtime); you must design the metadata contract carefully
once.

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

### Option D: Adopt `legal-ai-skills` as the product

Give lawyers Claude Code with the plugin installed; drop the web app.

| Dimension | Assessment |
|---|---|
| Complexity | Lowest |
| Cost | Zero build; each lawyer needs a Claude subscription and Node |
| Scalability | None of the platform properties: no shared corpus, no tenancy, no retained matters, no bulk pipeline |
| Team familiarity | n/a |

**Pros:** 226 skills and US connectors on day one.
**Cons:** the users are non-technical lawyers on a web app with
bring-your-own key; the plugin assumes a developer tool session on a local
machine; no Nigeria, no immigration, no persistence, no evaluation of
retrieval. Rejected as the product; adopted as a parts bin.

### Sub-decision: what to take from `legal-ai-skills` (MIT)

| Component | Decision | How |
|---|---|---|
| US connectors: hosted CourtListener MCP, hosted GovInfo MCP, `legal-research-server.mjs` (eCFR, Federal Register, Regulations.gov) | **Adopt** | Run them as MCP servers our agent loop calls; register each in the source registry; keep their result fields as our connector schema; disable CourtListener alert tools in the policy layer |
| Research protocol (`integrations/legal-research.md`): source hierarchy, provenance record, "UNVERIFIED — LIVE LEGAL RESEARCH UNAVAILABLE", quality gate | **Adopt as specification** | Becomes the system prompt for the research agent and the schema for the provenance manifest |
| `jurisdictions/us/` guides and `skill-map.yaml` | **Adapt** | Template for `jurisdictions/ng/`; US guide reused with our taxonomy ids |
| Skill prompts for contracts, investigations and research (obligations-extractor, contract-reviewer, clause-comparator, evidence-organizer, chronology-builder, chain-of-custody-documenter, legal-hold-planner, privilege-log-builder, authority-validator, citation-integrity-checker, adversarial-reviewer, assumption-flagger) | **Adapt** | Seed our playbooks and extraction schemas; strip the plugin's self-identification lines; have a lawyer check NG substance |
| Behavioural eval YAML format and the `skill-registry.yaml` schema | **Adopt the format** | Routing evals and the playbook registry |
| Hook / `disabled_tools` pattern | **Adopt the idea** | Tool-policy layer in the agent loop |
| Document-production server (local Pandoc/LibreOffice/OCRmyPDF) | **Reference only** | Our rendering runs server-side in Python; its "never signs, files, emails" rule is kept |
| Company registries, sanctions screening, calculators | **Later** | Useful for investigations playbooks; not on the critical path |
| Managing-partner / specialist agent prompts and the install flow | **Reject** | Assume a Claude Code session and a developer user |
| India and UK content | **Out of scope** | |

Attribution: keep the MIT notice with every adapted file.

## Trade-off Analysis

- **Speed now vs. rework later.** Option A reaches a US demo in days but
  cannot host capability 3, and every capability-1 feature would be rebuilt
  on migration. Option B extends the schema `main` already has rather than
  replacing it, so the migration cost is lower than revision 1 assumed.
- **One deployable vs. independent scaling.** Workers in the same codebase
  (Option B) give batch throughput without service boundaries. Option C's
  independent scaling is not needed until a single Postgres cannot hold a
  tenant's corpus, which is far beyond tens of thousands of documents.
- **Agent flexibility vs. grounding control.** A free agent loop can wander.
  Keeping the chains as tools, requiring every final answer to pass the
  same citation resolution, and a tool-policy layer with per-run caps
  preserves the current grounding guarantees inside an agentic design.
- **Open vs. licensed sources.** Open connectors give coverage fast but no
  citator. Licensed APIs give validity signals and polished metadata but
  constrain storage. The registry makes this per-source, so the product can
  ship with open sources and add licences without re-architecture.
- **Adopting someone else's connectors vs. owning them.** The plugin's US
  server is dependency-free, read-only, tested and MIT-licensed; adopting it
  saves weeks. The cost is a Node runtime in the deployment and tracking an
  upstream that changes. Pinning a version and keeping the result schema
  ours limits the exposure.
- **Python-only vs. adding TypeScript.** Decided: TypeScript for the Nigeria
  connector, Python for everything else. One TS service is enough to learn
  the language on something real and small, and it keeps one test suite for
  the core.

## Consequences

**Easier**
- Adding a jurisdiction becomes: a taxonomy entry, a locator grammar, a
  parser, a `jurisdictions/<cc>/` folder, a golden set. No prompt or
  retriever changes.
- Adding a source becomes: an MCP server plus a registry row.
- Tenancy, audit and retention are Postgres row-level security and tables,
  not file-system conventions.
- Bulk analysis and drafting share the same storage and the same citation
  resolution, so one eval harness covers both.
- US research coverage arrives with the plugin's connectors.

**Harder**
- Operating a queue and workers, backups, migrations, and one Node service.
- Keeping licence terms encoded correctly per source; a mistake is a legal
  problem, not a bug.
- Calibrating the relevance gate and reranker per tier and jurisdiction.
- Long generated documents are harder to verify than short answers;
  faithfulness checking must move to span level.
- Nigeria sources are patchier than US ones; the curated mirror is a
  maintenance commitment.

**To revisit**
- Embedding model: `bge-small-en-v1.5` is English-only; the schema pins
  384 dimensions, so a change is a migration plus a full re-embed, which
  retained originals make possible.
- Whether firm-knowledge retrieval needs a separate index per tenant for
  isolation guarantees stronger than row-level security.
- Whether to move the agent loop onto the Claude Agent SDK once MCP tools
  exist, and whether Managed Agents simplify hosting.
- Whether to contribute the Nigeria jurisdiction folder upstream.
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

### Source registry
One table, one row per source: `source_id`, `country`, `tier`,
`trust_kind`, `connector` (MCP server name and tool prefix), `licence`
(terms URL, may-store-text, may-store-snippets, attribution), `freshness`
(how checked, last checked), `allowed` (lawyer sign-off), `notes`. The
agent loop can only call connectors with `allowed = true`, and every
retrieved passage carries its `source_id` into the provenance manifest.

### Investigations and immigration as playbooks
A playbook is a YAML file: `practice_area`, `country`, allowed tools,
extraction schemas, checklist, output templates, eval file. The chat
router's `decide_mode` generalises into a playbook selector, which is what
the behavioural routing evals test.

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
produce a provenance manifest (every paragraph → supporting chunk ids and
source rows), and hands the document to a review state. Span-level
faithfulness (judge per section, not per document) keeps the check
tractable. Presentations are generated from the same outline model so
slides and memo share provenance.

### Security, compliance, observability
Tenant → users → matters with roles; row-level security in Postgres;
immutable audit log of every tool call and generated artefact; encryption
at rest for originals and embeddings; a per-tenant residency setting (NG or
US region, with the database provider's region constraint stated to the firm);
retention policies per tier; a prompt-injection boundary that treats every
external and uploaded text as data, with connector output schema-validated
before it reaches the model; OpenTelemetry traces per agent run with a cost
ledger per user and matter. `mypy` moves from advisory to blocking before
the storage work starts.

### Where TypeScript appears
One service: the **Nigeria connector**, an MCP server written in TypeScript
with the official `@modelcontextprotocol/sdk` and Zod. Concepts you will
meet, in the order they matter:

- **Interfaces and type aliases** describe the result shape the Python side
  expects (`source`, `source_url`, `retrieved_at`, `coverage_warning`,
  `access_status`). Think of them as Pydantic models that exist only at
  compile time.
- **Zod schemas** validate tool inputs at runtime, the way Pydantic
  validates request bodies. The SDK takes a Zod schema per tool and
  generates the JSON schema the model sees.
- **async/await and Promises** wrap every HTTP call to a source. A function
  marked `async` returns a Promise; `await` pauses until it resolves. The
  same idea as Python's `asyncio`, with `fetch` instead of `httpx`.
- **Discriminated unions** let the compiler check `trust_kind` and
  `access_status`: a value is one of a fixed set of string literals, and a
  `switch` over it must handle every case.
- **Timeouts and rate limits**: `AbortController` for a per-request
  timeout, and a minimum interval between calls to sites that rate-limit.

The plugin's servers are plain JavaScript (`.mjs`) that implement JSON-RPC
over stdio by hand. They are a readable reference for what the wire protocol
looks like; use the SDK rather than copying that approach.

## Action Items

Phase 0 — make `main` true again (days)
1. [x] Merge `fix-uv-lock-dangling` (lock, ingestion and matter-store `NameError`s, ownership into the `matters` table, no live key in unit tests). Merged as pull request 11 on 2026-10-24. CI on that push: lint-and-test and Docker green; the two eval jobs failed only for the missing `ANTHROPIC_WORKSPACE_ID` secret, since added. Confirm green on the next pull request.
2. [x] `AGENTS.md`, the `.claude/agents` port, `docs/product-brief.md`, the architecture storage section and the overview map are done (pull request 12 and `ea2073c`).
3. [x] `mypy` is blocking with zero errors (`80f61f2`). `ruff format` stays a convention, not a CI step.
4. [x] Originals retained in a blob store with a `documents` table and owner-only download (`e2ad389`).

Phase 1 — data contract and storage (weeks)
5. [x] Taxonomy, locator schemes and `jurisdictions/ng/` (`0e92df3`). Lawyer review still pending: `reviewed_by_lawyer` is false.
6. [x] Tiers and the chunk metadata contract, migration `0003` (`648a2b1`); source references carry `trust_kind` and `tier`.
7. [x] Full-text search and the authority HNSW index (`79f8588`); conversations, titles, accounts and keys in tables (`380e2f3`); the document queue and worker (`6cf2d0a`).
8. [x] Reranker, per-tier gate overrides and the scope ladder with golden-set case `g_widen_01` (`519c957`); measured in docs/22.

Phase 2 — sources, agent loop, playbooks (weeks)
9. [x] Source registry table and admin view; register the plugin's US connectors (pinned version) and the `web_search` allow-list. *(done: docs/24, commit 38c6a8c)*
10. [x] Nigeria connector as a TypeScript MCP server; NigeriaLII and nass.gov.ng first; curated mirror job. *(done: docs/27, commit c662cfe; NigeriaLII excluded, WordPress sites instead)*
11. [x] Add US locator parsers (`us_usc`, `us_cfr`, `us_reporter`, `uscis_pm`) and a US golden set; make the faithfulness check scheme-aware. *(done: docs/25, commit bc41394)*
12. [x] Agent loop with Claude tool use over MCP tools, the three chains exposed as tools, the tool-policy layer, per-run caps, audit log. *(done: docs/26 and docs/28, commits 0a7eeb1 and 13af231)*
13. [x] Playbook registry; investigations playbooks for NG and US adapted from the plugin's skills; immigration playbooks written new; behavioural routing evals. *(done: docs/29, slice P2-F)*

Phase 3 — bulk analysis (weeks)
14. [ ] Extraction schemas per document type with evidence spans; batch workers; cross-document checks; review queue; cost ceilings; extraction F1 eval on a labelled set.

Phase 4 — artefacts (weeks)
15. [ ] Template registry, drafting agent restricted to firm and authority tiers, DOCX/PPTX rendering, provenance manifest, review states, generated-document rubric in eval.

Cross-cutting
16. [ ] Tenancy, roles, row-level security per tenant, audit, residency and retention settings.
17. [ ] OpenTelemetry tracing and a cost ledger per user and matter.
18. [ ] Expand CI gates: NG and US retrieval, extraction F1, drafting rubric, connector canaries, routing evals.
