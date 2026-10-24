# 09 — DevOps Review: Workflows, SSH, Streamlit Currency, CI/CD

Written from a senior-DevOps-engineer pass over this repo as it stands
today. Each section states what was actually found, then the steps to fix
it. Per [CLAUDE.md](../CLAUDE.md), this is guidance for *you* to apply —
no workflow files, keys, or remotes are being changed by writing this doc.

## 1. Review the GitHub workflows

### What's actually there
- `.github/workflows/issues.yml`, `sync-workflow.yml`, `validate.yml` are
  **all 0 bytes**. GitHub Actions will either ignore these or fail to parse
  them — either way, nothing in `.github/workflows/` currently does
  anything.
- The real workflow content lives in `ci/` (`docker-image.yml`,
  `docker-publish.yml`, `makefile.yml`, `python-package.yml`) — **GitHub
  Actions only ever reads `.github/workflows/`**, so none of these four
  files have ever run as CI on this repo, regardless of how correct their
  contents are.
- Even once moved, three of the four aren't usable as-is:
  - `docker-image.yml` / `docker-publish.yml` still contain literal,
    unresolved template placeholders — `$default-branch` and
    `$cron-daily` — left over from GitHub's "new workflow" scaffold. These
    are not valid branch refs or cron syntax; the workflow will fail to
    trigger.
  - `makefile.yml` runs `./configure`, `make`, `make check`,
    `make distcheck` — that's Autotools/C boilerplate, not applicable to a
    `uv`-managed Python project. There's no `Makefile`/`configure` script
    in this repo, so this job would fail immediately.
  - `python-package.yml` installs from `requirements.txt` (doesn't exist —
    this project uses `pyproject.toml` + `uv.lock`) and targets Python
    3.9–3.11 with `pip`/`flake8`. None of that matches the project's actual
    tooling.
  - `docker-publish.yml` is the one file that's substantively sound (GHCR
    push + cosign signing) once the cron placeholder is fixed and it's
    relocated.
- There is currently no workflow that lints, type-checks, runs `pytest`, or
  runs the retrieval/faithfulness eval described in
  [08-deployment-cicd.md](08-deployment-cicd.md) — i.e., **no CI gate
  exists today**, despite four workflow-shaped files sitting in the repo.

### What `pyproject.toml` actually says (so the CI rewrite isn't a guess)
- `requires-python = ">=3.12"` — so CI should run on 3.12, not the
  3.9–3.11 matrix `python-package.yml` currently has.
- `[dependency-groups] dev = ["pytest>=8.0"]` — `pytest` is already a dev
  dependency. **`ruff` and `mypy` are not** — there's no `[tool.ruff]` /
  `[tool.mypy]` config anywhere in the repo either, so lint/type-check
  isn't just "wired up wrong," it doesn't exist yet at all. Decide whether
  to add them before wiring CI to call them (step 0 below), or cut lint
  from the CI rewrite for now and add it later — don't write a CI step
  that calls a tool the project hasn't adopted.

### Steps to fix, in order

**Step 0 — (optional but recommended) adopt lint/type-check tooling first**
```sh
uv add --dev ruff mypy
```
Add minimal config to `pyproject.toml` (illustrative starting point, tune
as needed):
```toml
[tool.ruff]
line-length = 100

[tool.mypy]
python_version = "3.12"
ignore_missing_imports = true
```
Run `uv run ruff check .` and `uv run mypy src` locally once and fix
whatever surfaces *before* it's a required CI check — landing a brand-new
lint gate on a repo that's never been linted will otherwise fail on the
first commit for reasons unrelated to that commit.

**Step 1 — clear out the dead files**
```sh
git rm ci/makefile.yml ci/docker-image.yml
git rm .github/workflows/issues.yml .github/workflows/sync-workflow.yml .github/workflows/validate.yml
```
- `makefile.yml` is Autotools boilerplate for a project with no
  `Makefile`/`configure` script — not adaptable, just wrong stack.
- `docker-image.yml` is fully superseded by `docker-publish.yml`'s build
  step (same `docker build`, plus push/sign/tag) — keeping both means two
  workflows building the same image for no reason.
- The three empty `.github/workflows/*.yml` files have no recoverable
  content — there's nothing to "fix," they're 0 bytes. If you specifically
  wanted an issue-triage or label-sync automation, that's a new workflow to
  design later, not a repair of these.

**Step 2 — create `.github/workflows/ci.yml`** (replaces
`ci/python-package.yml`, matched to the real toolchain):
```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  lint-and-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - name: Set up Python
        run: uv python install 3.12
      - name: Install dependencies
        run: uv sync --frozen
      - name: Lint
        run: uv run ruff check .
      - name: Type check
        run: uv run mypy src
      - name: Unit tests
        run: uv run pytest

  eval-gate:
    runs-on: ubuntu-latest
    needs: lint-and-test
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: uv python install 3.12
      - run: uv sync --frozen
      - name: Retrieval/faithfulness eval
        run: uv run python -m eval.run_golden_set --fail-below 0.85
```
Notes on choices here, not just syntax:
- `lint-and-test` and `eval-gate` are split into two jobs (not steps in
  one job) so the Actions UI shows eval-gate failures distinctly from
  lint/test failures — when this fails at 2am, you want the tab title to
  say which one broke without opening logs.
- `needs: lint-and-test` means the (slower, model-calling) eval doesn't
  run at all if basic lint/tests already failed — saves CI minutes and
  Claude API spend on a PR that was going to be blocked anyway.
- `--fail-below` is real, confirmed against
  [eval/run_golden_set.py](../eval/run_golden_set.py) (`argparse` flag,
  exits nonzero if mean recall@k drops below the threshold) — this
  invocation is wired correctly, not illustrative. `--top-k` defaults to
  8 and `--skip-generation` exists if you want a retrieval-only run that
  skips Claude calls (faster/cheaper, but doesn't catch generation-side
  faithfulness regressions) — decide which mode `eval-gate` should use.

**Step 3 — relocate and fix `docker-publish.yml`**
```sh
git mv ci/docker-publish.yml .github/workflows/docker-publish.yml
```
Then in that file:
- Replace `cron: $cron-daily` with a real schedule, e.g. `cron: '0 6 * * *'`
  for a daily 6am UTC run — or delete the `schedule:` trigger entirely if
  you don't actually want a daily rebuild (a stale-base-image rebuild
  policy is a deliberate choice, not a default you should leave on
  unexamined).
- Replace `branches: [ $default-branch ]` (both occurrences) with
  `branches: [main]`.
- Everything else in that file (Buildx, GHCR push, cosign signing,
  pinned action SHAs) is solid as-is — leave it.

**Step 4 — verify, don't assume**
1. Commit the changes on a throwaway branch and open a PR against `main`.
2. Open the repo's **Actions** tab on GitHub and confirm `CI` and `Docker`
   both appear as running workflows with the steps above — a workflow file
   that's never been observed running in that tab is not "working CI,"
   regardless of how correct it reads.
3. Confirm `ci.yml`'s `lint-and-test` job fails when you deliberately break
   something (e.g. an unused import for ruff, a type error for mypy) —
   tests passing isn't a substitute for confirming the *failure* path
   actually blocks the PR.
4. Add a **branch protection rule** on `main` (Settings → Branches) marking
   `lint-and-test` and `eval-gate` as required status checks — without
   this, a red CI run is informational only and doesn't actually stop a
   merge.

## 2. GitHub SSH key — current state and setup

### What was actually found on this machine
- `git remote -v` shows `origin` as `https://github.com/erica-prog/...` —
  this repo is currently authenticated over **HTTPS, not SSH**.
- `~/.ssh/config` has `Host` entries for EC2 boxes (`ec2`, `ea973-dev`,
  `ea973-prod`, `ea973-last`) but **no `Host github.com` entry**, and there
  is no GitHub-specific key file (`id_ed25519`, `id_rsa`, etc.) in
  `~/.ssh`.
- A live `ssh -T git@github.com` from this machine failed immediately with
  "Host key verification failed" — `github.com` isn't even in
  `known_hosts` yet, confirming SSH to GitHub has never been set up here.
- Conclusion: **there is no GitHub SSH key saved or configured on this
  machine right now.** If the intent is to use SSH for this repo (e.g. for
  a deploy key, or just personal push access), it needs to be created from
  scratch — there's nothing to "fix," only to set up.

### Steps to set it up correctly
1. Generate a dedicated key (don't reuse the AWS `.pem` files for this —
   those are EC2 instance keys, unrelated to GitHub auth):
   ```sh
   ssh-keygen -t ed25519 -C "atomamaro@gmail.com" -f ~/.ssh/github_ed25519
   ```
   Set a passphrase when prompted — an unprotected private key on disk is
   a standing credential-leak risk if this machine is ever compromised.
2. Add it to the agent and keychain so you're not retyping the passphrase
   every push:
   ```sh
   eval "$(ssh-agent -s)"
   ssh-add --apple-use-keychain ~/.ssh/github_ed25519
   ```
3. Add an explicit `Host github.com` block to `~/.ssh/config` (it's
   currently missing one), pointing at the new key:
   ```
   Host github.com
       HostName github.com
       User git
       IdentityFile ~/.ssh/github_ed25519
       IdentitiesOnly yes
   ```
4. Copy the **public** key (`~/.ssh/github_ed25519.pub`) into GitHub:
   Settings → SSH and GPG keys → New SSH key. Never paste the private key
   anywhere, including into chat, scripts, or `.env`.
5. Verify before touching the repo:
   ```sh
   ssh -T git@github.com
   ```
   Expect `Hi <username>! You've successfully authenticated...`. This also
   populates `known_hosts` correctly, which is why the earlier test failed.
6. Only after that succeeds, switch the existing remote from HTTPS to SSH
   if that's actually what you want:
   ```sh
   git remote set-url origin git@github.com:erica-prog/data-engineering-AI-trends-public.git
   ```
   This is optional — HTTPS with a credential helper or a PAT is an
   equally valid choice; only switch if you have a specific reason (e.g. a
   deploy key for CI, or a personal preference for SSH).
7. If the goal is a **deploy key for CI/CD** specifically (machine-to-repo,
   not personal-to-repo), generate a separate key for that purpose, add it
   under the repo's Settings → Deploy keys (read-only unless a push-back
   workflow genuinely needs write access), and store the private half as
   a GitHub Actions secret rather than on any laptop.

## 3. Is the Streamlit application up to date?

> **Superseded (2026-10-24).** The Streamlit app was removed with its dependencies; this
> section is kept for the dependency-tracking advice, which applies to the web app too.

### What was checked
- `pyproject.toml` pins `streamlit>=1.38` and `streamlit-authenticator>=0.4.2`.
- The environment actually has `streamlit 1.58.0` installed, which is also
  the **latest version published on PyPI** as of this check — so the
  dependency itself is current, not stale.
- `src/policy_advisor/app.py` is a real, maintained app, not a stub: it
  wires up login (`require_login`), a matter-scoped `RAGChain` /
  `CaseReasoningChain`, document add/remove via `ingest_document`, and
  jurisdiction/language pickers — consistent with the design in
  [07-demo-ui.md](07-demo-ui.md).
- What's *not* covered anywhere: there is no CI job that imports or
  smoke-runs `app.py`, so a breaking change (e.g. a renamed function in
  `policy_advisor.generation.chain`) would only be caught by manually
  running the app, not by a pipeline.

### Steps to keep it current going forward
1. Re-run the version check periodically (or in CI) rather than trusting a
   point-in-time read:
   ```sh
   uv run pip index versions streamlit
   ```
2. Pin a tested upper bound once you've verified a version against this
   app, e.g. `streamlit>=1.38,<1.60`, so an untested major/minor bump
   doesn't silently land via `uv sync` — then bump deliberately and re-test.
3. Add a minimal CI smoke check that the app module imports cleanly and,
   ideally, that `streamlit run` boots without throwing on startup (e.g.
   via `streamlit run app.py --server.headless true` with a short timeout)
   — this is the gap noted above and the cheapest possible regression
   catch for a UI that currently has zero automated coverage.
4. Track `streamlit-authenticator` the same way — auth libraries are a
   higher-stakes dependency than most UI libraries, so don't let it drift
   unpinned at an upper bound either.

## 4. CI/CD pipeline — high-level description

This describes the pipeline as it *should* exist once section 1's fixes
land — not a finished implementation. Full rationale is in
[08-deployment-cicd.md](08-deployment-cicd.md); this is the condensed,
top-level shape.

```
PR opened/updated
   │
   ▼
┌───────────────────────────┐
│ CI (on every PR)            │
│  1. uv sync --frozen         │  ← reproducible deps, no surprise resolves
│  2. lint + type-check        │  ← ruff / mypy
│  3. unit tests (pytest)      │  ← chunking, ingestion, auth, chain logic
│  4. retrieval/faithfulness   │  ← fails the build if answer quality
│     eval gate                  regresses vs. last known-good run
│  5. docker build (no push)   │  ← catches Dockerfile breakage pre-merge
└───────────────────────────┘
   │ merge to main
   ▼
┌───────────────────────────┐
│ CD (on merge / tag)         │
│  1. build + tag image        │  ← tag with git SHA + corpus/index version
│  2. push to ghcr.io           │  ← provenance tied directly to the repo
│  3. sign image (cosign)      │  ← supply-chain integrity
│  4. (optional) deploy step   │  ← target platform is a separate decision,
│                                  not required for the pipeline to be valid
└───────────────────────────┘
```

Key properties this pipeline is meant to guarantee, in order of importance
for a production legal-advice tool:
1. **No merge can silently degrade answer quality** — the eval gate is the
   one check that's specific to a RAG system rather than generic software
   CI, and it's the one currently missing entirely.
2. **No image is pushed without first being checkable** — `docker build`
   in CI before merge, real push only after merge.
3. **Every deployed image is traceable** to both a commit and a corpus/index
   version, so "what changed" is always answerable for an incident.
4. **Secrets never enter the image or the workflow logs** — injected at
   run time from GitHub Actions secrets / the runtime environment, never
   baked into a layer or printed in a step.
