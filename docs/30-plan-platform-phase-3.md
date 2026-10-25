# 30 — Plan: platform Phase 3 (ADR-001 actions 14 to 18): bulk analysis, artefacts, tenancy, tracing, CI gates

**Phase:** implementation plan (planning only; nothing below is built). Same format as
[20](20-plan-claude-layout-and-streamlit-removal.md), [22](22-plan-platform-phase-0-1.md)
and [23](23-plan-platform-phase-2.md). **Base:** `platform/phase-2-f` at `d050e0d`
(Phase 2 complete: registry, US library, connectors, Nigeria connector, research agent,
playbooks). **Owner:** product owner. **Date:** 2026-10-24.

## 1. Outcome and scope

Turn the advisor from a research tool into a firm system: analyse tens of thousands of
contracts, filings and exhibits as a batch with typed, evidence-backed records; generate
memos and presentations grounded in the firm and authority tiers with a provenance manifest
and a review state; separate firms from one another in the database; trace every model and
tool call with its cost; and make every quality property a CI gate.

**In scope:** ADR actions 14 to 18. **Out of scope:** sending or filing anything
anywhere; licensed databases beyond their registry rows; multi-region deployment beyond a
per-tenant residency setting; a lawyer's review of playbooks, taxonomy and source rows
(still owed from Phase 2 and tracked there).

## 2. Constraints observed

- The database already carries `tenant_id` on `matters` and `chunks` (migration 0003),
  unused. Every query filters by `matter_id`; none by tenant. Row-level security is on for
  every table, but the policies only revoke a hosted provider's API roles: the app connects as one
  database role, so tenancy today is application code, not the database.
- `document_jobs` (migration 0006) with `claim_next` over `FOR UPDATE SKIP LOCKED` is a
  working queue with one worker process; a bulk pipeline needs the same shape at a larger
  scale and with per-job cost accounting.
- `agent_runs` and `agent_tool_calls` (migration 0008) are the audit of research runs;
  there is no equivalent for the fixed chains, uploads or generated artefacts, and no
  per-user or per-matter cost total anywhere. Token counts exist only where the SDK returns
  `usage`.
- `python-docx` is a dependency (for reading uploads); `docxtpl` and `python-pptx` are
  not. `opentelemetry-api` arrives transitively (through `mcp`); no SDK or exporter is
  installed and nothing emits spans. Logging is structured JSON through
  `logging_utils.log_event`.
- Users bring their own Claude key. A batch over ten thousand documents on a user's key is
  a bill that user did not foresee; cost ceilings are therefore a product requirement
  before the first worker runs (ADR decision 8).
- The Batch API halves cost but returns within hours; the SDK (0.111.0) supports it.
  Prompt caching applies to the shared extraction schema and instructions.
- The eval harness has retrieval golden sets (NG, US), case-reasoning invariants and
  routing evals in CI. There is no labelled extraction set, no drafting rubric in code
  (the case-reasoning rubric is a document), and no connector canary.
- The web client is plain ES modules with no build step; a review queue and an
  artefact viewer are new screens.

## 3. Decisions

| ID | Decision | Status | Alternatives and recommendation |
|---|---|---|---|
| D1 | Tenancy enforcement | **Accepted 2026-10-24** (recommended option) | **Recommended:** real row-level security: the app connects as a role with policies on every tenant-scoped table, and sets `SET LOCAL app.tenant_id` per request (from the session) so a missed filter cannot cross tenants; `tenant_id` becomes NOT NULL with a backfill to a `default` tenant. Alternative: keep tenancy in application code with a `tenant_id` filter in every query; cheaper now, and every new query is a chance to forget it. |
| D2 | Roles | **Accepted 2026-10-24** (recommended option) | **Recommended:** three roles per tenant (`admin`, `lawyer`, `reviewer`); matters have an owner and may be shared with named users in the same tenant; `reviewer` can read and approve artefacts and review-queue items but not upload or run. Alternative: owner-only as today, which blocks the review queue (D7) from being anyone but the uploader. |
| D3 | Bulk extraction model tiering and ceilings | **Accepted 2026-10-24** (recommended option) | **Recommended:** triage every document with the cheaper model (`claude-haiku-5-5`), escalate flagged clauses to the matter's configured model; Batch API by default for jobs over 50 documents; a per-job ceiling in USD-equivalent tokens set at submission, enforced by the worker before each call, with the job pausing (not failing) at the ceiling. Alternative: one model, synchronous; simplest, two to four times the cost. |
| D4 | Where bulk records live | Routine | Table `extractions` (one row per document per schema version) with JSONB record, `evidence_spans` (chunk id + character offsets) and a `confidence`; cross-document checks write to `findings`; both tenant-scoped. Queryable by the research agent as a read-only tool (`query_extractions`) under the policy. |
| D5 | Artefact rendering | **Accepted 2026-10-24** (recommended option) | **Recommended:** `docxtpl` (Jinja over DOCX templates) for memos and `python-pptx` for slides, rendered server-side from one outline model; templates in a `templates` table with the tenant's firm style; outputs in the blob store under the matter. Alternative: Pandoc or LibreOffice in the image (the plugin's route); heavier image, more formats. |
| D6 | Drafting agent | Routine | The research agent's loop with a `draft` playbook: tools restricted to `search_case_documents` over `firm` and `authority` tiers, `query_extractions`, connectors per playbook; the model emits an outline whose paragraphs each name their supporting locators; a deterministic check requires every paragraph's locators to resolve, and a judge runs per section (ADR: span-level faithfulness). Output is a provenance manifest (plugin fields: instrument, issuing body, pinpoint, proposition, identifier, status, version date, access date, verification status, limitations) plus the rendered file. |
| D7 | Review states | Routine | `draft → reviewed → approved`, with `rejected` back to `draft`; transitions by role (D2) recorded in an `artefact_events` table; an `approved` artefact is immutable; nothing is ever sent or filed by the system. The review queue for bulk findings uses the same states per finding. |
| D8 | Tracing and cost ledger | **Accepted 2026-10-24** (recommended option) | **Recommended:** OpenTelemetry SDK with OTLP export, one trace per request or job, spans for retrieval, each model call (with token counts as attributes) and each tool call; a `cost_ledger` table written from the same hook (user, tenant, matter, run or job, model, tokens, estimated cost from a price table in settings). Exporter per deployment (console in dev; an OTLP endpoint in production, which the firm chooses). Alternative: ledger only, no tracing; keeps cost control, loses latency and failure diagnosis. |
| D9 | Residency and retention | **Accepted 2026-10-24** (recommended option) | Per-tenant `residency` (`ng` or `us`) and retention days per tier, stored and enforced: the API refuses to create a tenant whose residency the deployment's database region cannot honour (the Supabase region is one per project; state it to the firm); a retention job deletes expired matter-tier rows and originals and records the deletion in the audit. **Recommended:** ship the settings and the job; defer multi-region to a deployment decision. |
| D10 | CI gates | Routine | Add: extraction F1 on a labelled synthetic set (threshold set after the first measurement), the drafting rubric as a scored eval, connector canaries (list tools and one fixture-shaped call per live connector, run nightly, never on pull requests), and the existing NG and US retrieval and routing evals. Pull requests run the fast gates; nightly runs the ones that need keys or network. |

## 4. Acceptance criteria

| ID | Observable result | Basis |
|---|---|---|
| AC-1 | A tenant, its users and roles exist as rows (migration `0009`); every tenant-scoped query runs under the tenant's row-level security context; a test that forgets the filter still cannot read another tenant's rows | ADR sketch "Security, compliance, observability"; D1, D2 |
| AC-2 | A bulk job over N documents produces one `extractions` row per document with a typed record and evidence spans that resolve to chunks; a job stops at its cost ceiling and resumes; the Batch API path is used for large jobs; a `findings` table holds cross-document checks (deterministic for dates, parties and defined terms; model-based for conflicting obligations) | ADR decision 8; D3, D4 |
| AC-3 | Extraction F1 against a labelled synthetic contract set (at least 40 documents, 8 fields) is measured and recorded, and CI fails when it drops below the recorded threshold | ADR decision 9 |
| AC-4 | The research agent can answer "which of these documents have X" from `extractions` through a policy-gated tool, citing document and clause | ADR decision 8 |
| AC-5 | A drafting run produces a DOCX (and, from the same outline, a PPTX) plus a provenance manifest in which every paragraph names supporting locators that resolve to firm or authority chunks or recorded sources; a paragraph that cites a matter-tier (evidence) chunk is rejected; the artefact enters `draft` | ADR decision 7; D5, D6 |
| AC-6 | Review transitions obey roles and are recorded; an approved artefact cannot be changed; the client shows the review queue and the artefact's manifest | D7 |
| AC-7 | Every request, agent run and batch job emits a trace with model and tool spans carrying token counts; `cost_ledger` totals per user, tenant and matter match the sum of their spans | ADR action 17; D8 |
| AC-8 | Tenant residency and retention settings exist; the retention job deletes expired rows and originals and leaves an audit record; a tenant whose residency the deployment cannot honour cannot be created | D9 |
| AC-9 | CI runs the fast gates on pull requests and the keyed or networked ones nightly; a failing connector canary opens an issue rather than blocking merges | D10 |

## 5. Slices

| ID | Slice | Needs | Result |
|---|---|---|---|
| P3-A | Tenancy: migration `0009` (`tenants`, `tenant_users`, roles; `tenant_id` NOT NULL with backfill), row-level security policies and per-request `SET LOCAL`, session carries tenant and role, matter sharing | D1, D2 | AC-1 |
| P3-B | Tracing and cost ledger: OpenTelemetry SDK, spans in `llm.py`, the chains, the agent, connector calls and jobs; `cost_ledger` table and price settings; `/api/usage` for a user's own totals | D8 | AC-7 |
| P3-C | Bulk extraction: schemas per document type (contract first, seeded from the plugin's obligations ledger), `bulk_jobs` on the queue shape of `document_jobs`, worker with tiering, ceilings and the Batch API, `extractions` and `findings`, cross-document checks, `query_extractions` tool | P3-A, P3-B; D3, D4 | AC-2, AC-4 |
| P3-D | Extraction eval: labelled synthetic set under `eval/extraction/`, F1 runner, CI gate | P3-C | AC-3 |
| P3-E | Artefacts: `docxtpl` and `python-pptx`, templates table, outline model, drafting playbook on the agent, provenance manifest, span-level judge, blob storage of outputs | P3-A, P3-B; D5, D6 | AC-5 |
| P3-F | Review: states and events for artefacts and findings, role checks, client screens (review queue, artefact viewer with manifest) | P3-C, P3-E; D7 | AC-6 |
| P3-G | Residency and retention settings and job | P3-A; D9 | AC-8 |
| P3-H | CI gates: drafting rubric as a scored eval, connector canaries nightly, workflow split into pull-request and nightly | P3-D, P3-E; D10 | AC-9 |

Order: P3-A and P3-B first (every later slice writes tenant-scoped rows and should be
traced from its first commit), then P3-C → P3-D, P3-E in parallel with P3-C after P3-B,
then P3-F, P3-G, P3-H. One commit per slice with tests; each slice updates the contract
documents it touches in the same commit.

## 6. Risks

| ID | Risk | Assumption | Check |
|---|---|---|---|
| R1 | Row-level security by session variable is bypassed by the pool reusing a connection | `SET LOCAL` inside the request transaction resets at commit; the pool's `configure` never sets a tenant | a test that runs two tenants' requests on one pooled connection and reads nothing across |
| R2 | A bulk job spends a user's key beyond what they expected | ceilings are set at submission and enforced per call; pause, not fail, at the ceiling; the ledger shows the spend live | AC-2 ceiling test; a run with a deliberately low ceiling |
| R3 | Extraction looks right but F1 is low on real contracts | the synthetic set is built from varied, realistic clause language and a lawyer reviews a sample | the labelled set is reviewed before the threshold is set; F1 is recorded, not asserted, until then |
| R4 | A drafted memo cites evidence as authority | the drafting playbook's tool allow-list excludes matter-tier retrieval and the manifest check rejects evidence chunks | AC-5 negative test |
| R5 | Tracing adds latency or leaks content into spans | spans carry counts, ids and locators, never prompt or document text; export is asynchronous | a test asserting span attributes contain no text fields; latency measured before and after |
| R6 | Retention deletes something a firm needed | retention is per tier with a minimum, deletions are audited, and matters can be put on hold | the hold path is tested; the job dry-runs first in production |
| R7 | The Batch API's turnaround does not fit a lawyer's expectations | large jobs are explicitly asynchronous with a progress screen; small jobs stay synchronous | the client shows an estimate from the job size |

## 7. Verification

`uv run ruff check .`, `uv run mypy src`, `uv run pytest` against the local database;
the routing and retrieval evals as today; the new extraction F1 and drafting rubric runners;
`npm test` in `connectors/ng-sources`; connector canaries nightly only. Live checks (a
real bulk run, a real draft) are run by hand against synthetic documents and recorded in
this document. Load: a bulk run over a synthetic set of at least 2,000 documents on the
local database, with timings recorded before the slice is called done.

## 8. Review needs

Security review of P3-A (tenancy) and P3-B (what spans and ledgers record); a lawyer's
review of the extraction schema fields, the drafting playbook and templates, the review
states, and retention defaults; licensing review of any template or rubric text adapted
from the plugin.

## 9. Readiness and next action

D1 to D9 were accepted on 2026-10-24 with the recommended options; D10 is routine. All
slices are ready within the authorised scope. Implementation order: P3-A → P3-B → P3-C
and P3-E → P3-D → P3-F → P3-G → P3-H, one commit per slice with tests, each on its own
branch from `main` once the Phase 2 pull request (`platform/phase-2-f` into `main`) has
merged.

## 10. Implementation record

| Slice | Branch | Commit | Verification | Notes |
|---|---|---|---|---|
| P3-A | `platform/phase-3-a` | the slice commit (`Tenancy: ...`) | `ruff`, `mypy`, `pytest` green; `tests/test_tenancy.py` proves isolation on one pooled connection, the policy refusing a cross-tenant write, the worker running as the job's tenant, roles and shares | the worker uses an all-tenants view (`*`) the API never sets; shared-library writes are not yet restricted by the database; residency and retention columns exist without behaviour (P3-G) |
| P3-B | `platform/phase-3-a` | the slice commit (`Tracing and cost ledger: ...`) | `ruff`, `mypy`, `pytest` green; `tests/test_telemetry.py` reads spans back from the memory exporter and the ledger rows from the table | the LangChain callback covers every chain; raw SDK calls record their own usage; `MODEL_PRICES` ships empty so `estimated_usd` is NULL until set; exporters: none, console, otlp, memory |
| P3-C | `platform/phase-3-a` | the slice commit (`Bulk analysis: ...`) | `ruff`, `mypy`, `pytest` green; tests run the extractor with a fake model and a fake Batch API client: evidence verification, tiering, the ceiling pause and resume, the batch path, findings, the agent tool and the API | no live Batch API run; F1 gate is P3-D; review transitions are P3-F |
