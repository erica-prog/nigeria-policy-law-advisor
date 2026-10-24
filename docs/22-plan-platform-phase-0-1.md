# 22 — Plan: platform Phase 0 and Phase 1 (ADR-001 actions 2 to 8)

**Phase:** implementation plan for the first two phases of
[19-adr-001-multi-jurisdiction-legal-platform.md](19-adr-001-multi-jurisdiction-legal-platform.md),
accepted 2026-10-24. Implementation is authorised ("deliver the platform in full"); this
document sequences it into slices with observable acceptance criteria, in the format of
[20-plan-claude-layout-and-streamlit-removal.md](20-plan-claude-layout-and-streamlit-removal.md).
**Branch:** `platform/phase-0-1` from `main` at `ef5449f`. **Owner:** product owner.

## 1. Outcome and scope

Finish Phase 0 (make the repository true and safe to build on) and Phase 1 (the data
contract and storage the later phases stand on). Phases 2 to 4 get their own plans.

**In scope:** ADR actions 2 to 8. **Out of scope here:** connectors, the agent loop,
playbooks, bulk analysis, artefacts, tenancy (actions 9 to 18).

## 2. Constraints observed

- Local Docker is unavailable; a Homebrew Postgres with pgvector on port 5433 is the
  local test database (`TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5433/policy_advisor_test`).
  CI runs the same suite in a `pgvector/pgvector:pg16` container.
- Every stored chunk carries `jurisdiction` as `"federal"`, `"lagos"` or `NULL`; the
  API accepts the same two literals; the golden set and seed cases use them. The
  taxonomy must accept these as aliases so nothing breaks on day one.
- `is_supported_citation` is a prefix match: `Order 5 Rule 3` currently "supports"
  `Order 5 Rule 30`. The scheme-aware check fixes this; the fix is a behaviour change
  the faithfulness tests must pin.
- Uploaded originals are deleted after ingestion (`api/jobs.py`); nothing can be
  re-embedded or re-chunked today.
- mypy reports 17 errors, all in two families: `ChatAnthropic` constructor aliases and
  `with_structured_output` returning `dict | BaseModel`; plus one Optional match and one
  `content` that may be a list.

## 3. Slices

| ID | Slice | ADR action | Needs DB |
|---|---|---|---|
| P0-A | mypy blocking: one `llm.py` factory for Claude clients and structured output; fix the two stragglers; remove `continue-on-error` | 3 | no |
| P0-B | Retain originals: `BlobStore` (local disk now, S3-compatible later), `documents` table (migration `0002`), download route, delete on removal | 4 | yes |
| P0-C | Docs consistency: product brief for the web client, architecture storage section, complete overview map | 2 | no |
| P1-A | Jurisdiction taxonomy and locator schemes as package data; aliases for the two legacy values; scheme-aware `is_supported_citation`; `jurisdictions/ng/` guides | 5 | no |
| P1-B | Metadata contract and corpus tiers: migration `0003` adds `tier`, `trust_kind`, `locator_scheme`, `source_id`, `effective_date`, `licence`, structured jurisdiction columns; `Chunk` and `chunk_to_record` carry them; API `SourceReference` gains `trust_kind` | 6 | yes |
| P1-C | Postgres FTS (`tsvector` column + GIN) as the keyword retriever for large matters and the authority tier; HNSW index on the authority tier; conversation log, matter meta, credentials and user keys into tables; Postgres-backed job queue | 7 | yes |
| P1-D | Cross-encoder reranker; relevance gate calibrated per tier; golden-set case that expects scope widening | 8 | yes (eval) |

Order: P0-A, P1-A and P0-C need no database and go first; P0-B, P1-B, P1-C, P1-D follow
once the local database is up. One commit per slice.

## 4. Acceptance criteria

| ID | Observable result | Basis |
|---|---|---|
| AC-1 | `uv run mypy src` exits 0 and the CI step has no `continue-on-error` | ADR action 3; review F9 |
| AC-2 | After an upload reaches `ready`, the original bytes are retrievable through the API by the matter's owner only, with the stored sha256 matching the upload; removing the document or deleting the matter removes the blob and the row | ADR action 4; AGENTS.md rules 1 and 2 |
| AC-3 | `docs/product-brief.md` describes the web client and cookie auth; `docs/architecture/web-mvp.md` describes Postgres storage; `docs/00-overview.md` lists every numbered doc | review F8 |
| AC-4 | `normalize_jurisdiction("federal") == "ng-federal"` and `"lagos" -> "ng-lagos"`; every taxonomy id resolves to a country, level, region and bodies; the API still accepts the two legacy literals and the new ids | ADR action 5 |
| AC-5 | `is_supported_citation("Order 5 Rule 30", {"Order 5 Rule 3"})` is false; `("Order 5 Rule 3(6)", {"Order 5 Rule 3"})` is true; `("8 U.S.C. § 1255(a)", {"8 U.S.C. § 1255"})` is true; an unknown scheme falls back to exact match; the golden set and invariants stay green | review F1; the current prefix bug |
| AC-6 | Migration `0003` applies on an empty database and on one holding `0001`-era rows, backfilling `tier='matter'`, `trust_kind` from `doc_type`, `locator_scheme` from the locator text and structured jurisdiction from the alias map; every existing test passes unchanged | ADR action 6 |
| AC-7 | Keyword retrieval over a matter above a size threshold uses Postgres FTS and returns the same top result as BM25 for the golden set's exact-locator and keyword questions; the recall gate stays at or above 0.9 | ADR action 7 |
| AC-8 | Conversation log, matter meta, credentials and user keys are read from tables; a fresh clone with an empty `data/` directory serves login, matters and chat | ADR action 7 |
| AC-9 | Upload jobs survive an API restart: a job queued before restart is picked up by a worker after it | ADR action 7 |
| AC-10 | With the reranker on, golden-set recall@8 and MRR are not lower than without it, and a cross-tier query returns results with a common score scale | ADR action 8 |

## 5. Risks

| ID | Risk | Assumption | Check |
|---|---|---|---|
| R1 | Scheme-aware citation check rejects legitimate pinpoints | each scheme's pinpoint grammar is wider than what Claude emits | faithfulness tests per scheme; invariants job green |
| R2 | Alias normalisation changes golden-set retrieval | filters compare normalised ids on both sides | golden set green |
| R3 | Migration `0003` on real Supabase data | backfill is deterministic from existing columns | run on a copy first; `scripts/check_database.py` extended |
| R4 | Retained originals change the storage footprint and retention posture | blobs are per matter and deleted with it; residency note in doc 16 | AC-2 delete path tests |
| R5 | FTS ranks differently from BM25 | both are only candidates for fusion and the gate decides relevance | AC-7 comparison |

## 6. Verification

`uv run ruff check .`, `uv run mypy src`, `uv run pytest` against the local database,
`eval/run_golden_set.py --fail-below 0.9` and `eval/run_case_reasoning_invariants.py`
in CI on the pull request. New tests are named after their criterion.

## 7. Implementation record

| Slice | Commit | Result |
|---|---|---|
| P0-A mypy blocking | `80f61f2` | `policy_advisor/llm.py` factory; mypy 0 errors; CI step blocking; full suite 246 passed (AC-1) |
| P1-A taxonomy and locator schemes | `0e92df3` | package data + loader; aliases `federal`/`lagos`; scheme-aware support; API accepts ids; prompt country-neutral; `jurisdictions/ng/` guides; suite 274 passed (AC-4, AC-5) |
| P0-C docs consistency | `ea2073c` | product brief rewritten for the web client; architecture storage section; overview map complete (AC-3) |
| P0-B retained originals | `e2ad389` | `storage.py` blob store, migration `0002_documents`, download route, deletion with document and matter; tests for owner-only download, hash, failed upload leaves nothing (AC-2) |
| P1-B corpus tiers | `648a2b1` | migration `0003`: matters.tier, chunk trust_kind, locator_scheme, provenance and structured jurisdiction; idempotent backfill tested on legacy rows; demo library is the first authority collection (AC-6) |
| P1-C part 1: full-text search | `79f8588` | migration `0004`: tsvector + GIN, locator index, HNSW on the authority tier; size-based keyword backend; both backends agree on top results (AC-7) |
| P1-C part 2: state into Postgres | see git log | migration `0005`: conversations, users, user_keys tables and matter title/updated_at columns; import script for credentials.yaml and user_keys.json; nothing but originals left on disk (AC-8) |
