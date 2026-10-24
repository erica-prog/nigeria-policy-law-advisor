# 20 — Plan: Claude Code project layout and Streamlit removal

**Phase:** planning only (`/agent-engineering:plan-change`). Nothing below is
implemented. A ready slice is not a grant of implementation permission.
**Date:** 2026-10-24. **Branch inspected:** `2026-10-19-pozq` at `df58e48`
(contains `main` and `fix-uv-lock-dangling`).
**Requirements owner:** you (product owner / lead architect). This document is
the plan record; requirements live in `AGENTS.md`, the direction in
[18-architecture-review.md](18-architecture-review.md) and
[19-adr-001-multi-jurisdiction-legal-platform.md](19-adr-001-multi-jurisdiction-legal-platform.md).

## 1. Requested outcome and scope

Three things were asked for:

1. Revise `CLAUDE.md` and `CLAUDE-2.md` so the project uses a `.claude/`
   layout.
2. Remove the Streamlit app (`src/policy_advisor/app.py`); the browser client
   in `web/` is the product surface.
3. Treat docs 18 and 19 as the authorities for agent improvement, so the
   project memory must point agents at them.

**In scope:** project memory files, a `.claude/` directory, the Streamlit
code and everything that only exists for it, the container default command,
and the docs that tell someone to run Streamlit.

**Exclusions (not in this plan):** any item from ADR-001's action list other
than the two it already names here (memory conflict, Streamlit default
command); the React Native text in `docs/product-brief.md` and the Cursor
agents' bodies beyond what the port in slice S1 needs; self-service sign-up
for the web API (it existed only in the Streamlit UI and is lost by this
change, see risk R7); dropping the `chat_exchanges` table.

**Desired behaviour vs observation:** the desired behaviour is stated by you.
Everything in section 2 is observed on the inspected revision. Hypotheses are
marked as such.

## 2. Inspected evidence

### 2.1 Streamlit footprint (observed)

| Item | Finding | Consequence |
|---|---|---|
| `src/policy_advisor/app.py` (341 lines) | The Streamlit UI. Only importer of `policy_advisor.auth.require_login` and the only product code that uses `policy_advisor.chat_history`. | Delete. |
| `src/policy_advisor/auth.py` | Imports `streamlit` and `streamlit_authenticator`. Also holds `load_credentials` and `save_credentials` for `data/auth/credentials.yaml`, which `scripts/add_user.py` uses. | Delete after moving the two file helpers. |
| `src/policy_advisor/api/credentials.py` | Already re-implements `load_credentials` read-only with `bcrypt.checkpw`, deliberately not importing `auth.py` "because auth.py pulls in Streamlit". | Becomes the single owner of the credentials file: add `save_credentials` (with the existing `filelock` pattern). |
| `scripts/add_user.py` | Hashes with `streamlit_authenticator.Hasher.hash`. | Port to `bcrypt.hashpw`. The on-disk format (`usernames: {u: {name, password, email}}`) stays. |
| `src/policy_advisor/chat_history.py` + table `chat_exchanges` | Used by `app.py` and by `tests/test_chat_history.py` (8 tests). The API router's `chat_history` is a route function, not an import; the API persists `conversation.json` instead. | Orphaned by the product but tested and schema-backed. Keep (decision D4). |
| `tests/test_app_history.py` | Drives `app.py` with `streamlit.testing`. | Delete. |
| `tests/test_auth.py` (20 lines, 2 tests) | Tests `auth.load_credentials` only. | Re-point at `api/credentials.py`. |
| `pyproject.toml` | `streamlit>=1.38`, `streamlit-authenticator>=0.4.2`. `pyyaml` is imported by `auth.py` and `api/credentials.py` but is only transitive (via `huggingface-hub`, `langchain-core`). `bcrypt` is already direct. `filelock` is direct and still used by `api/user_keys.py`. | Remove two, add `pyyaml` explicitly, re-lock. |
| `config.py` / `.env.example` | `policy_agent_port` / `POLICY_AGENT_PORT=8501` exist only for Streamlit. | Remove. |
| `Dockerfile` | `EXPOSE 8501 8000`; default `CMD` runs Streamlit. | Default `CMD` becomes uvicorn on 8000. |
| `docker-compose.yml` | `app` service (Streamlit, 8501) and `api` service (uvicorn, 8000). | Remove `app`. |
| `.github/workflows/docker-publish.yml` | Builds and pushes the image on `main`; does not reference the command or port. | No change; the published image's default behaviour changes. |
| CI (`ci.yml`) | No Streamlit step. | No change. |
| Docs that instruct running Streamlit | `docs/16-supabase-setup.md` §5, `docs/17-web-app-mvp.md` (three bullets), `AGENTS.md` (one row, one sentence). | Edit. |
| Docs whose subject is Streamlit | `docs/07-demo-ui.md`, `docs/10-self-service-signup.md`, `docs/09-devops-review.md` §3. | Mark superseded; keep for history. |
| Docs that cite `app.py` line numbers as design rationale | `docs/11`, `12`, `13`, `14`. | Leave; they describe trust-state rendering the web client inherited. |
| Source comments naming Streamlit or `app.py` | `chain.py`, `prompt.py`, `web_search.py`, `services.py`, `progress.py`, `matter_store.py`, `ingest_document.py`, `config.py`, `credentials.py`, `sessions.py`. | Reword in passing; no behaviour. |
| `.cursor/skills/verify-policy-advisor` | Drives the web app, not Streamlit. | Unaffected. |

Hypothesis (to be proven in S2): `streamlit_authenticator.Hasher.hash` produces
a standard bcrypt `$2b$` string, so hashes already in `credentials.yaml` keep
verifying through `bcrypt.checkpw` after the library is gone. The API's own
`verify_credentials` already assumes this and its tests pass today.

### 2.2 Project memory and agent folders (observed)

- `CLAUDE.md` (42 lines): Phase 1 memory. `CLAUDE-2.md` (315 lines): Phase 2
  memory with three resolved decisions and three phases of checklists, most
  of which are now implemented on `main`. `AGENTS.md` (this branch): the
  cross-tool brief, which already carries the Phase 1 response rules and says
  it wins where it overlaps `CLAUDE.md`.
- No `.claude/` directory exists.
- `.cursor/agents/` holds six Cursor subagents (471 lines) with
  `name` / `description` / `model: inherit` frontmatter; one is
  `mobile-developer` for a React Native app that does not exist.
- `.cursor/skills/` and the untracked `.agents/skills/` are byte-identical
  copies of two verification skills (`verify-policy-advisor`,
  `verify-document-cancel`) in `SKILL.md` format.
- From the current Claude Code documentation (code.claude.com, read today):
  project memory is `./CLAUDE.md` or `./.claude/CLAUDE.md`; when a
  `CLAUDE.md` exists Claude reads **only** it and ignores `AGENTS.md` unless
  `CLAUDE.md` imports it with `@AGENTS.md`; `.claude/rules/*.md` load
  alongside, optionally path-scoped with a `paths:` frontmatter;
  `.claude/agents/*.md` need only `name` and `description`;
  `.claude/skills/<name>/SKILL.md` is the skill format; nothing under
  `.agents/` is read by Claude Code (it is the Codex location). Target is
  under 200 lines per memory file.

### 2.3 What inspection could not establish

- Whether anyone runs the published Docker image expecting Streamlit on 8501.
- Whether any contributor works on Windows, where symlinked skill folders
  (decision D2) need extra git configuration.
- Whether Codex is actually used on this repo (the `.agents/` copy suggests it).

## 3. Decisions

| ID | Decision | Status | Alternatives and recommendation |
|---|---|---|---|
| D1 | Memory layout | **Accepted 2026-10-24: option A** | **A (recommended):** keep a short root `CLAUDE.md` (under 120 lines) that holds only Claude-specific working rules, imports the shared brief with a bare `@AGENTS.md` line, and names docs 18 and 19 as the direction authority; put path-scoped guidance in `.claude/rules/`; move `CLAUDE-2.md` to `docs/21-phase-2-capabilities-record.md` with each checklist item marked done or open, and keep only its three resolved decisions as one-liners in `CLAUDE.md`. **B:** same content at `.claude/CLAUDE.md` instead of the root; no functional difference, less discoverable. **C:** delete `CLAUDE.md` and rely on Claude reading `AGENTS.md` directly; one file for every tool, but needs Claude Code v2.1.277+ and some hosts cannot read `AGENTS.md`, so A is the robust form of C. |
| D2 | Canonical skills location | **Accepted 2026-10-24: `.claude/skills/` canonical, symlinks** | **Recommended:** `.claude/skills/` is canonical; `.cursor/skills` and `.agents/skills` become symlinks to it so Cursor and Codex keep working. **Alternative:** three copies plus a CI step that fails when they differ. Symlinks need no sync step; copies need no git symlink support. |
| D3 | Agents to port into `.claude/agents/` | **Accepted 2026-10-24: five ported plus `web-client-developer`** | **Recommended:** port `lead-architect`, `backend-developer`, `rag-engineer`, `legal-research-engineer`, `qa-security-reviewer`; replace `mobile-developer` with `web-client-developer` (plain HTML, CSS, ES modules in `web/`); rewrite every "React Native" and "managed auth provider" reference to the web client and cookie auth; add `tools` only where a role is read-only (reviewer). Keep `.cursor/agents` as a symlink to `.claude/agents` if Cursor tolerates the extra frontmatter fields (to be checked in S1); otherwise two copies. |
| D4 | `chat_history.py` and `chat_exchanges` | Accepted as routine | Keep both. They are tested, schema-backed, and are the natural destination for ADR-001 action 7 (move `conversation.json` into Postgres). Only the Streamlit test goes. |
| D5 | `.claude/settings.json` | **Skipped 2026-10-24** | Add a project settings file pre-allowing routine read-only and test commands (`uv run pytest`, `uv run ruff check .`, `uv run mypy src`, `git status`, `git diff`) so sessions prompt less. Skip if you prefer per-session prompts. |
| D6 | Self-service sign-up | **Acknowledged 2026-10-24** | It existed only in the Streamlit UI. After removal, accounts are created by `scripts.add_user` until the tenancy work in ADR-001 (action 16). `AGENTS.md` already states sign-up is not in the web API. |

All material decisions are settled; no slice is blocked on a decision.

## 4. Acceptance criteria

| ID | Observable result | Basis for the expectation |
|---|---|---|
| AC-1 | No file under `src/`, `scripts/`, `tests/`, `eval/` imports `streamlit` or `streamlit_authenticator`; neither package appears in `pyproject.toml` or `uv.lock`. | The request. Checked by `grep` and `uv tree`. |
| AC-2 | An account created with `uv run python -m scripts.add_user` can log in through `POST /api/auth/login` and receives the session cookie. | Existing API login tests in `tests/api/test_auth_api.py` define the login contract. |
| AC-3 | A password hash written by the old `streamlit_authenticator.Hasher` (fixture captured before removal) still verifies through `api/credentials.verify_credentials`. | Existing lawyers must not be locked out; both libraries use bcrypt. |
| AC-4 | `docker build .` succeeds and a container started with no command answers `GET /api/health` with 200 on port 8000. | `docs/17-web-app-mvp.md` names uvicorn on 8000 as the way to run the app. |
| AC-5 | `uv sync --frozen`, `uv run ruff check .`, `uv run pytest` (against the test Postgres) and both CI eval jobs pass on the pull request. | Existing CI gates (`.github/workflows/ci.yml`). |
| AC-6 | In a Claude Code session opened at the repo root, `/context` lists `CLAUDE.md` with `AGENTS.md` imported, the `.claude/agents` entries appear in the agent list, and `/verify-policy-advisor` appears in the `/` menu. | Claude Code memory, subagent and skill documentation read today. |
| AC-7 | `/doctor prompt-audit` reports no contradiction between `CLAUDE.md`, `AGENTS.md` and `.claude/` files, and `CLAUDE.md` is under 200 lines. | Documentation guidance on size and consistency. |
| AC-8 | `ls -L .cursor/skills .agents/skills` resolves to the same two skills as `.claude/skills`. | D2. |
| AC-9 | No document under `docs/` and no comment under `src/` instructs running Streamlit as the way to start the app; docs 07, 09 §3 and 10 carry a superseded note pointing at doc 17. | The request; AGENTS.md's "where things are today" must stay true. |
| AC-10 | `CLAUDE-2.md` no longer exists at the root; every section of it is present in its new location with each checklist item marked done (with the file that implements it) or open. | The request to revise, with no loss of recorded decisions. |

## 5. Slices

### S1 — Claude Code layout (ready; D1, D2, D3 accepted, D5 skipped)

Areas: `CLAUDE.md`, `CLAUDE-2.md` → `docs/21-…`, new `.claude/{agents,skills,rules}`,
symlinks for `.cursor/skills`, `.agents/skills` and possibly `.cursor/agents`,
`docs/00-overview.md` map, `AGENTS.md` module map rows for agents and skills.

Expected result: AC-6, AC-7, AC-8, AC-10.

Tests: no automated tests exist for memory files. Verification is the manual
checks in AC-6 and AC-7 plus a shell check for AC-8. Add a short section to
`AGENTS.md` "Repository conventions" naming `.claude/` as canonical so the
rule is recorded.

Documentation: `docs/00-overview.md` gains the new doc; `AGENTS.md` table rows
updated.

### S2 — Remove Streamlit from code and dependencies (ready within authorized scope)

Prerequisite: capture a fixture hash with the current library **before**
removing it (AC-3), for example in `tests/fixtures/legacy_credentials.yaml`.

Areas: delete `src/policy_advisor/app.py`, `src/policy_advisor/auth.py`,
`tests/test_app_history.py`; add `save_credentials` to
`api/credentials.py`; port `scripts/add_user.py` to `bcrypt`; remove
`policy_agent_port` from `config.py`; edit `pyproject.toml` (remove two,
add `pyyaml`); run `uv lock`; re-point `tests/test_auth.py` at
`api/credentials.py` and add the AC-2 and AC-3 tests under `tests/api/`.

Expected result: AC-1, AC-2, AC-3, AC-5.

Tests: existing `tests/api/test_auth_api.py` (login contract),
`tests/test_chat_history.py` (unchanged), new `test_add_user_account_logs_in`
and `test_legacy_streamlit_hash_still_verifies`. Defect-detection evidence:
run the new legacy-hash test once with the fixture's hash altered by one
character and confirm it fails.

Documentation: none beyond S3.

### S3 — Container, config and docs cleanup (depends on S2)

Areas: `Dockerfile` (`CMD`, `EXPOSE`, comments), `docker-compose.yml`
(remove `app`), `.env.example` (remove `POLICY_AGENT_PORT`, reword two
comments), `README.md` (point at doc 17), `docs/16` §5, `docs/17` bullets,
`AGENTS.md` row and sentence, superseded notes on docs 07, 09 §3, 10, source
comments listed in 2.1.

Expected result: AC-4, AC-9.

Tests: `docker build .` and the health check in AC-4 (manual, local Docker is
available on this machine); `grep -ri streamlit` for AC-9.

Documentation: this slice is mostly documentation.

Ordering: S2 then S3 in one pull request with one commit each; S1 can land
in the same PR or a separate one once D1 to D3 are decided.

## 6. Risks: assumption and disproving check

| ID | Risk | Assumption that makes it safe | Check |
|---|---|---|---|
| R1 | Existing lawyers cannot log in after the hasher swap | `Hasher.hash` output is plain bcrypt | AC-3 fixture test, captured before removal; verified: unverified until run |
| R2 | `uv lock` drifts unrelated pins | Removing dependencies only removes lock entries | Diff `uv.lock`; only removals plus `pyyaml` promotion expected; otherwise pin with `uv lock --upgrade-package` restraint |
| R3 | PyYAML vanishes from the environment | It stays via `huggingface-hub` and becomes explicit | `uv tree --invert --package pyyaml` after S2 |
| R4 | A consumer of the published image expects Streamlit on 8501 | None known (2.3) | Announce in the PR description; keep `EXPOSE 8000` only |
| R5 | Claude ignores `AGENTS.md` once `CLAUDE.md` exists | The `@AGENTS.md` import line is present and not inside backticks | AC-6 `/context` |
| R6 | Symlinked skill folders break for a Windows contributor | None on Windows (2.3, unverified) | Ask; fall back to copies plus a CI equality check |
| R7 | Sign-up disappears with no replacement | Admin provisioning is acceptable until ADR-001 tenancy work | D6 acknowledged by you |
| R8 | Deleting `auth.py` breaks a caller inspection missed | Only `app.py` and `scripts/add_user.py` import it (grep, 2.1) | `uv run ruff check .` (F401/F821) and the full test suite |

## 7. Verification map

| Criterion | Check | Existing or new | Status |
|---|---|---|---|
| AC-1 | `grep -rn "streamlit" src scripts tests eval`; `uv tree \| grep -i streamlit` | new, shell | planned |
| AC-2 | `tests/api/test_auth_api.py` + new `test_add_user_account_logs_in` | existing + new | planned |
| AC-3 | new `test_legacy_streamlit_hash_still_verifies` with pre-captured fixture | new | planned |
| AC-4 | `docker build .`; `docker run -p 8000:8000 --env-file .env <image>`; `curl :8000/api/health` | manual | planned |
| AC-5 | CI on the PR (`lint-and-test`, `eval-gate`, `case-reasoning-invariants`) | existing | planned |
| AC-6, AC-7 | `/context`, agent list, `/` menu, `/doctor prompt-audit` in Claude Code | manual | planned |
| AC-8 | `ls -L` | shell | planned |
| AC-9 | `grep -rin "streamlit run\|uv run streamlit" docs src` | shell | planned |
| AC-10 | `test ! -e CLAUDE-2.md`; manual read of the relocated file | manual | planned |

Test-ID mechanism: the project identifies tests by pytest function name only
and has no stable behavioural IDs. No new mechanism is proposed for this
change; the two new tests are named after their criteria.

Unavailable here: the local Postgres 14 has no password-free test database,
so `uv run pytest` was not run during planning; CI provides a pgvector
container. Claude Code manual checks need a session you open.

## 8. Review needs

One fresh reviewer for the whole change. Specialist attention on S2 for the
credential helper and hashing (security), and on the lock-file diff (R2).
Docs-only edits in S3 need no specialist.

## 9. Readiness and next action

- **S1, S2 and S3:** ready within authorized scope. Decisions D1 to D3 and
  D6 were accepted on 2026-10-24 (D5 skipped). Nothing is blocked.
- This plan does not itself authorise implementation.

Next action: authorise implementation of S2 → S3 → S1 (one pull request,
one commit per slice), starting with the legacy-hash fixture in S2.

## 10. Implementation record (2026-10-24)

Implemented on branch `2026-10-19-pozq` as three commits (S2, S3, S1), in that order.

| Criterion | Result | Evidence or gap |
|---|---|---|
| AC-1 | pass | `grep` finds no Streamlit import under `src/`, `scripts/`, `tests/`, `eval/`; `uv tree` has no streamlit entry |
| AC-2 | pending CI | `test_account_created_by_add_user_script_can_log_in` written; needs the test Postgres |
| AC-3 | pass | `tests/test_credentials.py::test_legacy_streamlit_authenticator_hash_still_verifies` passes; fails when the fixture hash is altered by one character |
| AC-4 | pending CI | Docker daemon unavailable on the development machine; `docker-publish.yml` builds the image |
| AC-5 | partial | `uv sync --frozen` and `uv run ruff check .` pass; mypy unchanged at 18 advisory errors; 246 tests collect; the database-backed suite runs in CI |
| AC-6, AC-7 | pending (owner) | run `/context` and `/doctor prompt-audit` in a Claude Code session at the repo root |
| AC-8 | pass | `ls -L` on the three symlinks lists the same skills and agents |
| AC-9 | pass | no doc or comment instructs running Streamlit, except the superseded doc 09 section under its banner |
| AC-10 | pass | `CLAUDE-2.md` removed; `docs/21` holds every section, 21 items done, 4 open |

Risk R1 is retired by AC-3. R2: the lock diff was removals only plus the `pyyaml`
promotion. R4 stands: announce the default-command change in the pull request.
