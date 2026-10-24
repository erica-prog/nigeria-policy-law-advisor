# 23 — Plan: platform Phase 2 (ADR-001 actions 9 to 13): sources, agent loop, playbooks

**Phase:** implementation plan (planning only; nothing below is built). Same format as
[20](20-plan-claude-layout-and-streamlit-removal.md) and
[22](22-plan-platform-phase-0-1.md). **Base:** `platform/phase-0-1` at `4aa4312`
(Phase 0 and 1 complete). **Owner:** product owner. **Date:** 2026-10-24.

## 1. Outcome and scope

Give the advisor external legal sources and the ability to use them: a registry of
sources with licence terms, the United States connectors adopted from the MIT
`legal-ai-skills` plugin, a Nigeria connector written in TypeScript, parsers for US
citation locators with a US golden set, an agent loop that calls these as tools under a
policy, and playbooks for investigations and immigration with routing evals.

**In scope:** ADR actions 9 to 13. **Out of scope:** bulk analysis (14), artefacts
(15), tenancy, tracing and the remaining CI gates (16 to 18), licensed databases beyond
registry rows and feature flags.

## 2. Constraints observed

- The Anthropic SDK in the lock is `anthropic 0.111.0`, which carries the beta tool
  runner (`client.beta.messages.tool_runner`, `@beta_tool`) and MCP conversion helpers
  (`anthropic.lib.tools.mcp`); the Python `mcp` client package is not a dependency yet.
- The Messages API MCP connector (`mcp_servers` + `mcp_toolset`, beta
  `mcp-client-2025-11-20`) reaches **URL** servers only. The plugin's US research
  server is a stdio Node process, so it cannot be used through the connector; the hosted
  CourtListener and GovInfo servers could be.
- `legal-ai-skills` main is at commit `ad5df1934e981f9aa1c2c163da02590d8ae0504c`
  (2026-10-10). Its `legal-research-server.mjs` is dependency-free Node (no npm install),
  hand-rolled JSON-RPC over stdio, read-only, with tests. Node 24 and npm 11 are on the
  development machine; the runtime image is `python:3.12-slim` with no Node.
- The chains run on `ANTHROPIC_MODEL` (`claude-sonnet-4-6` today) through LangChain;
  web search uses the raw SDK with `web_search_20250305`. Users bring their own key;
  every agent run spends that user's key, so budgets are a product requirement.
- Chat routing is `decide_mode` with three modes (analysis, research, question); the
  "research" mode exists only when a matter has no documents and forces the web.
- Locator schemes for US citations already exist in `jurisdictions/locators.py`; no
  parser produces them yet, and the demo corpus is Nigerian only.
- `data/` holds three Nigerian PDFs; the Dockerfile copies `src`, `web`, `assets`.

## 3. Decisions

| ID | Decision | Status | Alternatives and recommendation |
|---|---|---|---|
| D1 | Agent harness | **Accepted 2026-10-24** (recommended option) | **Recommended:** the SDK tool runner on the raw Anthropic client, not LangChain; per-turn hooks give approval gates, caps and audit without a hand-written loop. The three chains stay as they are and are exposed as tools. Alternative: a manual loop (only if a transport the runner cannot do is needed). |
| D2 | How MCP tools reach the model | **Accepted 2026-10-24** (recommended option) | **Recommended:** one in-process MCP client (Python `mcp` package): stdio for the vendored Node server and the TypeScript Nigeria server, streamable HTTP for hosted CourtListener and GovInfo; tools converted for the runner with `anthropic.lib.tools.mcp`. One policy and audit layer covers every tool. Alternative: the server-side MCP connector for the two hosted servers only; it splits policy enforcement across two mechanisms and is beta. |
| D3 | Vendoring the plugin's US server | **Accepted 2026-10-24** (recommended option) | **Recommended:** copy `legal-research-server.mjs`, its test file and the MIT `LICENSE` into `connectors/legal-ai-skills/` with an `UPSTREAM.md` naming commit `ad5df19`; refresh by diff, never edit in place. Alternative: a git submodule (heavier; pulls 226 skills we do not run). |
| D4 | Node in the runtime | **Accepted 2026-10-24** (recommended option) | **Recommended:** install Node 20 LTS into the Python image (`apt` from NodeSource or a multi-stage copy from `node:20-slim`) and build the TypeScript connector in the image. Alternative: run connectors as a separate container; more moving parts for one developer. |
| D5 | Agent model and budgets | **Accepted 2026-10-24** (recommended option) | Default `AGENT_MODEL` to the existing `ANTHROPIC_MODEL` so one key and one bill; caps per run: 12 tool calls, 45 s per tool call, one retry per failing tool, and `max_tokens` 16000 per turn. Newer models (the skill's default is `claude-opus-5-5` with adaptive thinking and effort tuning) can be chosen per deployment; the loop must not depend on model-specific features such as task budgets. |
| D6 | Where research surfaces in the product | **Accepted 2026-10-24** (recommended option) | **Recommended:** a `research` chat job (`intent: "research"` or the existing no-documents path) that runs the agent and returns `source: "research"` with citations carrying `trust_kind` and `source_id`; the client labels it like the library source. Case analysis keeps its fixed chain this phase. |
| D7 | Connector credentials | Routine | Server settings (`COURTLISTENER_API_TOKEN`, `GOVINFO_API_KEY`, `REGULATIONS_GOV_API_KEY`), never per user, never persisted elsewhere; absent keys mark the source `unavailable` in the registry, and the agent says so. |
| D8 | US corpus for the golden set | Routine | Public-domain federal material fetched through the adopted connectors and committed as text under `data/us/` with provenance (eCFR 8 CFR 245, USCIS Policy Manual Vol. 7 Pt. B, one Supreme Court opinion); seeded as an `authority` collection `us-library`. |
| D9 | Playbook and eval formats | Routine | Playbooks as YAML under `playbooks/<practice_area>/<country>.yaml`; routing evals in the plugin's YAML shape (`prompt`, `expected_skill`, `expected_behavior`, `must_include`, `must_not_include`), MIT attribution kept on adapted text. |

## 4. Acceptance criteria

| ID | Observable result | Basis |
|---|---|---|
| AC-1 | A `sources` table (migration `0007`) lists every source with `source_id`, country, tier, trust kind, connector, licence terms, freshness and `allowed`; a CLI seeds the plugin's US sources and the `web_search` allow-list; the agent refuses to call any tool whose source is not `allowed` | ADR decision 5 |
| AC-2 | `ingestion/parse_us.py` turns 8 CFR, USCIS Policy Manual and a reporter-cited opinion into chunks whose locators parse under `us_cfr`, `uscis_pm` and `us_reporter`; a US golden set of at least ten questions reaches recall@8 ≥ 0.9 against `us-library` | ADR action 11 |
| AC-3 | The vendored US server starts under the MCP client, its tools appear to the runner with their result fields (`source`, `source_url`, `retrieved_at`, `coverage_warning`, `access_status`), and a recorded-fixture test runs without network | ADR decision 5; plugin schema |
| AC-4 | The Nigeria connector (TypeScript, `@modelcontextprotocol/sdk`, Zod) exposes `search`, `fetch`, `cite` for NigeriaLII and nass.gov.ng, passes `tsc` and its vitest suite on recorded fixtures, and is called from the Python side through the same client | ADR action 10 |
| AC-5 | A research run on a question the matter and library cannot answer calls at most the capped number of tools, never calls a tool outside its playbook's allow-list or on the deny-list (alerts, subscriptions, filings, writes), records every call in `agent_tool_calls`, and returns an answer whose citations resolve to retrieved passages or recorded source results only | ADR decision 6; AGENTS.md rules 3 and 6 |
| AC-6 | Connector text reaches the model only inside the labelled, schema-validated tool-result shape; an injected instruction in a fixture result is not followed (test) | AGENTS.md rule 6 |
| AC-7 | The playbook registry lists investigations (NG, US) and immigration (NG, US); `decide_mode` becomes a playbook selector; the routing eval file passes with the selector's choices for every case | ADR action 13 |
| AC-8 | The runtime image contains Node, builds the TypeScript connector, and starts both servers; `docker compose up api` serves a research run end to end | D3, D4 |

## 5. Slices

| ID | Slice | Needs | Result |
|---|---|---|---|
| P2-A | Source registry: migration `0007`, `sources` module, `scripts/seed_sources.py`, `web_search` row, admin listing in `check_database` | DB | AC-1 |
| P2-B | US parsers and corpus: `parse_us.py`, `document_type` cues for US material, `data/us/` with provenance, `us-library` seeding in `build_index`, US golden set and CI run | DB, network once | AC-2 |
| P2-C | MCP client layer: `mcp` dependency, `policy_advisor/tools/` (client, result schema, policy), vendored plugin server with `UPSTREAM.md`, hosted servers over HTTP, Node in the image | Node | AC-3, AC-8 (part) |
| P2-D | Nigeria connector in TypeScript under `connectors/ng-sources/` with fixtures, CI job, image build | Node | AC-4, AC-8 (part) |
| P2-E | Agent loop: `policy_advisor/agents/research.py` on the tool runner; the three chains as tools; caps, deny-list, audit tables (`agent_runs`, `agent_tool_calls`, migration `0008`); research chat job and `source: "research"` through API, bubble and client | P2-A, P2-C | AC-5, AC-6 |
| P2-F | Playbooks and routing: `playbooks/`, selector replacing `decide_mode`, adapted investigations prompts, new immigration prompts, `eval/run_routing_evals.py` and the YAML cases | P2-E | AC-7 |

Order: P2-A and P2-B first (no agent), then P2-C, P2-D in parallel, then P2-E, then
P2-F. One commit per slice; P2-D is the first TypeScript in the repository, so its
commit also explains the toolchain in `docs/05-typescript-guide.md`.

## 6. Risks

| ID | Risk | Assumption | Check |
|---|---|---|---|
| R1 | Nigerian sites block or rate-limit scraping | NigeriaLII and nass.gov.ng tolerate polite, cached, rate-limited reads | robots and terms read first; fixtures recorded; a curated mirror is the fallback the ADR already names |
| R2 | Connector results carry instructions | all connector text is wrapped as untrusted data and schema-validated | AC-6 injection test |
| R3 | The agent spends a user's key without bound | hard caps on calls and tokens per run; cost logged per run | AC-5 and the audit table |
| R4 | Upstream plugin changes break the vendored server | pinned commit; refresh by diff | `UPSTREAM.md` and the fixture test |
| R5 | Hosted MCP servers need per-user authorisation | a server-held token suffices for research use | D7; verify CourtListener's terms during P2-C |
| R6 | Node in the image grows it and its attack surface | one LTS runtime, no npm at runtime for the vendored server | image size recorded; `npm ci --omit=dev` for the TS build |
| R7 | Tool runner is beta in the SDK | behaviour is pinned by the lock | integration test with a fake model client |

## 7. Verification

`uv run ruff check .`, `uv run mypy src`, `uv run pytest` against the local database;
`npm test` and `tsc --noEmit` in `connectors/ng-sources/`; `node --test` for the
vendored server; `eval/run_golden_set.py` for the NG and US sets; the routing eval
runner; CI gains a `connectors` job. Live-network checks (connector smoke tests) run
manually and record evidence in this document, never in CI.

## 8. Review needs

Security review of the tool-policy layer and the injection boundary (P2-C, P2-E);
licensing review of vendored and adapted plugin text (P2-C, P2-F); a lawyer's review of
the Nigeria source list stays open from Phase 1.

## 9. Readiness and next action

D1 to D6 were accepted on 2026-10-24 with the recommended options; D7 to D9 are routine.
All slices are ready within the authorised scope. Implementation order: P2-A → P2-B →
P2-C and P2-D → P2-E → P2-F, one commit per slice, each on its own branch from `main`
after the Phase 0 and 1 pull request merges.

## 10. Implementation record

| Slice | Branch | Commit | Verification | Notes |
|---|---|---|---|---|
| P2-A | `platform/phase-2-a` | the slice commit on that branch (`Source registry: ...`) | `ruff`, `mypy`, `pytest` green locally against Postgres 17 + pgvector; 11 new tests in `tests/test_sources.py`; 323 passed, 3 skipped | started 2026-10-24 from `platform/phase-0-1` at `f1bd2f3` because the Phase 0 and 1 pull request was not yet merged; the branch rebases onto `main` cleanly once it is |
