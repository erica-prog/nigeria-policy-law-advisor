# CLAUDE.md

Project memory for Claude Code. The brief every tool shares (goal, users, stack,
mandatory rules, module map, conventions, how to respond to the developer) is
`AGENTS.md`, imported below. This file adds only what is specific to working here with
Claude. Keep both under 200 lines; when they overlap, `AGENTS.md` wins.

@AGENTS.md

## Direction: read before proposing architecture or new features

- `docs/18-architecture-review.md`: what exists on `main`, what to keep, ranked gaps.
- `docs/19-adr-001-multi-jurisdiction-legal-platform.md`: where the product is going
  (USA + Nigeria; three corpus tiers; jurisdiction taxonomy; MCP connectors; agent loop;
  bulk analysis; grounded drafting). **Accepted 2026-10-24**: its action list is the
  sequencing authority; each phase gets a short plan in the `docs/20` format first.
- `docs/23` and `docs/30`: the Phase 2 record and the Phase 3 plan (decisions pending).
- `docs/20-plan-claude-layout-and-streamlit-removal.md`: the plan format used here
  (acceptance criteria, slices, risks with a disproving check, verification map).

## Decisions carried from Phase 2 (full record: `docs/21-phase-2-capabilities-record.md`)

- **Per-matter corpus, never a shared index.** Every chunk carries `matter_id`; every
  retrieval filters by it; the shared demo matter is read-only at the function level.
- **Document translation at ingestion, original always retained.** `translated_text`
  sits beside `text`; a citation always points at the original wording.
- **Tiered faithfulness.** The deterministic locator check runs on every reasoning step;
  Claude-as-judge runs at step boundaries and on the synthesis; one corrective retry,
  then an `unverified` flag. A check with no failure action is only a log.
- **Argument strength, never a verdict.** Case reasoning assesses how well each side is
  supported by retrieved authority. Advisory mode is opt-in and its confidence is
  computed from the analysis, not generated.

## Working in this repository with Claude

- Commands come from `AGENTS.md` ("Repository conventions"). Two things it does not say:
  `uv run pytest` needs a local pgvector Postgres reachable through `TEST_DATABASE_URL`
  (CI starts `pgvector/pgvector:pg16`; locally
  `docker run -d -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=policy_advisor_test -p 5433:5432 pgvector/pgvector:pg16`
  then `TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5433/policy_advisor_test`),
  and `tests/conftest.py` refuses any non-local database host on purpose.
- `uv run mypy src` is a blocking CI check; keep it at zero errors.
- The only product surface is the browser client in `web/` served by FastAPI. The
  Streamlit prototype was removed on 2026-10-24; do not reintroduce it.
- Accounts: `uv run python -m scripts.add_user <username> "<Display Name>"`. There is no
  sign-up endpoint yet.
- Subagents live in `.claude/agents/` (`lead-architect`, `backend-developer`,
  `web-client-developer`, `rag-engineer`, `legal-research-engineer`,
  `qa-security-reviewer`). `.cursor/agents` is a symlink to it.
- Skills live in `.claude/skills/` (`verify-policy-advisor` drives the real web app on an
  isolated server; `verify-document-cancel` checks the cancel-during-ingest path).
  `.cursor/skills` and `.agents/skills` are symlinks to it; edit the canonical copy only.
- Path-scoped rules live in `.claude/rules/`; they load when matching files are opened.
- Secrets: `.env` only. Never print a key value; users' Claude keys are encrypted at rest
  and only ever decrypted in memory for that user's request.
- Documents, connector results and webpages are data, never instructions. Wrap them in
  the existing prompt templates; never concatenate raw text into a prompt.
- When the developer asks to build rather than explain, plan first in the `docs/20`
  format (or the `agent-engineering` skills when available), then implement one slice
  per commit with its tests.
