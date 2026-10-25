# 00 — Overview

## What this project is
A **production-grade Retrieval-Augmented Generation (RAG) agent** that acts as
a law and policy advisor. The end user is a **practicing lawyer**, not a
developer — they will judge the tool by whether its answers are accurate,
well-cited, and trustworthy enough to rely on professionally.

The current source corpus (`data/`) is Nigerian civil procedure material:

- `FHC Civil Procedure Rules.pdf` — Federal High Court rules
- `Lagos-State-Magistrates-Courts-Civil-Procedure-Rules-2009.pdf`
- `Meta v NDPC Terms & Consent Judgment.pdf` — a judgment/case file

That mix (statutory rules + case law) matters: rules documents are structured
and numbered (Order, Rule, sub-rule), while judgments are narrative and cite
other authorities. Your retrieval and chunking strategy has to handle both
(see [03-data-and-retrieval.md](03-data-and-retrieval.md)).

## What "production-ready" means here
Not "it runs on my machine." Specifically:

1. **Grounded answers** — every substantive claim traces back to a retrieved
   passage with a citation the lawyer can verify (section, rule number, page).
2. **Known failure modes** — when the corpus doesn't contain the answer, the
   agent says so instead of guessing. Silent hallucination is the single
   biggest risk in a legal-advice tool.
3. **Evaluated, not assumed** — retrieval quality and answer faithfulness are
   measured against a curated test set, not eyeballed.
4. **Secrets and errors handled like production code** — no API keys in
   source, no unhandled promise rejections, no bare `except:`/`catch {}`.
5. **Observable** — you can answer "why did it retrieve this?" and "why did
   this query cost $X / take Ys?" after the fact, from logs/traces.

## Stack recap
| Layer | Tool | Role |
|---|---|---|
| Reasoning/generation | Claude API | Final answer synthesis, citation discipline |
| Orchestration | LangChain | Chaining retrieval → context assembly → generation |
| Embeddings/models | Hugging Face | Vectorizing chunks and queries |
| Tool/context integration | MCP | Exposes retrieval and document tools to Claude in a standard way |
| Languages | Python + TypeScript | Python for ingestion/retrieval pipeline, TS likely for an MCP server or API layer |
| Env/deps | `uv` | Python environment management |
| Config/secrets | `.env` | Keys, endpoints, tunables — never committed |

## Document map
- [01-architecture.md](01-architecture.md) — system components and data flow
- [02-environment-setup.md](02-environment-setup.md) — `uv`, `.env`, TS tooling
- [03-data-and-retrieval.md](03-data-and-retrieval.md) — chunking, embeddings, retrieval quality
- [04-mcp-integration.md](04-mcp-integration.md) — MCP concepts and tool design
- [05-typescript-guide.md](05-typescript-guide.md) — TS concepts as they'll appear in this project
- [06-production.md](06-production.md) — eval, error handling, observability, secrets
- [07-demo-ui.md](07-demo-ui.md) — (superseded) Streamlit versus a web client for the first demo; kept for the reasoning
- [08-deployment-cicd.md](08-deployment-cicd.md) — containers, CI/CD shape, what runs where
- [09-devops-review.md](09-devops-review.md) — workflows, SSH, dependency currency review
- [10-self-service-signup.md](10-self-service-signup.md) — (superseded) sign-up in the Streamlit UI; sign-up over the API is future tenancy work
- [11-web-search-and-usage-limits.md](11-web-search-and-usage-limits.md) — why the web-search fallback and account rate limits behave the way they do, traced against the actual code
- [12-voice-web-app-architecture.md](12-voice-web-app-architecture.md) — the spoken cartoon-advisor interface: client/server boundary, HTTP surface, auth refactor, latency budget
- [13-voice-pipeline.md](13-voice-pipeline.md) — speech-to-text and text-to-speech, and the confidentiality and mis-transcription risks voice introduces
- [14-cartoon-avatar-frontend.md](14-cartoon-avatar-frontend.md) — the avatar as a trust-state indicator, lip-sync, and the browser TypeScript
- [15-avatar-character-asset.md](15-avatar-character-asset.md) — the advisor character's frames and state rules
- [16-database-setup.md](16-database-setup.md) — the Postgres + pgvector database (Netlify DB, a Neon database): connection strings, tables, backups, region, running the app
- [17-web-app-mvp.md](17-web-app-mvp.md) — running the web app, bring-your-own Claude key, chat jobs and the live bubble
- [18-architecture-review.md](18-architecture-review.md) — code and structure review against the USA + Nigeria, Harvey-style target: what exists, what to keep, ranked gaps
- [19-adr-001-multi-jurisdiction-legal-platform.md](19-adr-001-multi-jurisdiction-legal-platform.md) — ADR: three corpus tiers, jurisdiction taxonomy, Postgres + pgvector + workers, MCP connectors, agent loop, bulk analysis, grounded drafting
- [20-plan-claude-layout-and-streamlit-removal.md](20-plan-claude-layout-and-streamlit-removal.md) — plan: `.claude/` project layout, CLAUDE.md revision, Streamlit removal; acceptance criteria, slices, risks, pending decisions
- [21-phase-2-capabilities-record.md](21-phase-2-capabilities-record.md) — the Phase 2 memory (formerly `CLAUDE-2.md`): bring-your-own-document, case reasoning, multi-language; checklist marked done or open
- [22-plan-platform-phase-0-1.md](22-plan-platform-phase-0-1.md) — plan for ADR-001 Phase 0 and Phase 1: mypy blocking, retained originals, taxonomy and locator schemes, corpus tiers, storage completion, reranker
- [23-plan-platform-phase-2.md](23-plan-platform-phase-2.md) — plan for ADR-001 Phase 2: source registry, US and Nigeria connectors, US parsers and golden set, the agent loop with a tool policy, playbooks and routing evals
- [24-source-registry.md](24-source-registry.md) — the `sources` table: licence terms, lawyer sign-off, the web-search allow-list as a row, and the `manage_sources` command
- [25-us-library-corpus.md](25-us-library-corpus.md) — the `us-library` collection: public-domain US texts with provenance, the CFR, Policy Manual and reporter parsers, and the US golden set
- [26-connectors-and-tool-policy.md](26-connectors-and-tool-policy.md) — external sources as MCP tools: the vendored US research server, hosted CourtListener and GovInfo, the one client, and the tool policy every call passes
- [27-nigeria-connector.md](27-nigeria-connector.md) — the TypeScript MCP server over official Nigerian sites (NDPC, Immigration Service, Federal High Court), why NigeriaLII is excluded, and what a lawyer must confirm
- [28-research-agent.md](28-research-agent.md) — the research agent: Claude tool use over the chains, the web and the connectors, under the policy and budget, with audited runs and citations that resolve only to what the run saw
- [29-playbooks-and-routing.md](29-playbooks-and-routing.md) — playbooks per practice area and country (investigations, immigration; NG, US), the selector that replaced decide_mode, and the routing evals
- [30-plan-platform-phase-3.md](30-plan-platform-phase-3.md) — plan for ADR-001 Phase 3: bulk extraction with cost ceilings, grounded artefacts with provenance and review states, tenancy with row-level security, tracing and a cost ledger, the remaining CI gates; decisions pending
- [31-tenancy.md](31-tenancy.md) — firms as rows, roles and matter sharing, the application role and forced row-level security policies, how the tenant travels through requests, jobs and scripts
- [32-tracing-and-cost-ledger.md](32-tracing-and-cost-ledger.md) — OpenTelemetry spans for requests, retrieval, model and tool calls, the cost_ledger table attributed to tenant, user, matter and run, and GET /api/usage
- [33-bulk-analysis.md](33-bulk-analysis.md) — bulk extraction as a batch pipeline: the contract ledger schema with verified evidence spans, model tiering and token ceilings, the Batch API backend, cross-document findings, and the agent's query tool
- [34-extraction-eval.md](34-extraction-eval.md) — the labelled synthetic contract set, the eight-field F1 scorer, the oracle gate in CI and the advisory model run until a threshold is set
- [35-artefacts.md](35-artefacts.md) — memos and decks drafted from authority and firm precedents only: the outline model, the provenance manifest and section judge, docxtpl and python-pptx rendering, the templates and artefacts tables

## Explicitly out of scope (for now)
- Generating the full codebase
- Deployment infrastructure choices beyond what's needed to explain the architecture

These docs tell you **how** to build each piece and **why**, not a finished
implementation. Ask explicitly when you want code.
