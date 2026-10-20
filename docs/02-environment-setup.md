# 02 — Environment Setup

This project mixes Python (ingestion/retrieval) and TypeScript (likely the
MCP server or API layer). Each has its own dependency manager; keep them
separate and don't let either reach into the other's config.

## Python: `uv`
`uv` replaces the `pip` + `venv` + `pip-tools` combo with one tool.

- `uv init` — creates `pyproject.toml` (your dependency manifest, replacing
  `requirements.txt`).
- `uv add langchain anthropic ...` — adds a dependency and updates a lockfile
  (`uv.lock`), so installs are reproducible across machines.
- `uv run python script.py` — runs inside the project's virtual environment
  without you manually activating it.
- `uv sync` — installs exactly what's in the lockfile (use this in CI/prod,
  not `uv add`, so prod never silently picks up a newer version).

You already have a `.venv/` — that's `uv`'s managed virtual environment.
Don't edit it by hand; it's regenerated from `pyproject.toml` / `uv.lock`.

**Production concern:** pin versions via the lockfile and commit `uv.lock`.
An unpinned `langchain` or `anthropic` SDK bump between dev and prod is a
common source of "works on my machine" RAG breakage (prompt formats and
tool-call schemas do change between versions).

## TypeScript side: npm/pnpm + `tsconfig.json`
TypeScript doesn't have a direct `uv` equivalent, but the shape is similar:

- `package.json` — your `pyproject.toml` equivalent (dependencies + scripts).
- `package-lock.json` (npm) or `pnpm-lock.yaml` — your `uv.lock` equivalent.
- `tsconfig.json` — compiler config. For a new-to-TS developer, the settings
  that matter most:
  - `"strict": true` — turns on all of TS's null-safety and type-checking
    rules. Turn this on from day one; retrofitting strictness onto a growing
    codebase is painful.
  - `"target"` / `"module"` — which JS version/module system TS compiles
    down to. For a Node-based MCP server, `"module": "NodeNext"` is the
    current recommended setting.
- Run TS directly during development with `tsx` (a fast TS runner), and
  compile with `tsc` for anything you actually deploy.

## `.env` and secrets
Both Python and TS sides read from the **same** `.env` conventions, but each
needs its own loader:

- Python: `python-dotenv` (or `uv add python-dotenv`), loaded once at process
  start.
- TypeScript/Node: Node 20+ has built-in `.env` support via
  `node --env-file=.env`; otherwise the `dotenv` npm package.

**What goes in `.env`:**
```
ANTHROPIC_API_KEY=...
HF_API_TOKEN=...
VECTOR_STORE_URL=...        # if not purely local
LOG_LEVEL=info
```

**Non-negotiables:**
- `.env` is in `.gitignore` from the very first commit — verify this *before*
  the first `git add`, not after a secret is already in history.
- Provide an `.env.example` (committed) listing required keys with placeholder
  values, so the project is runnable by someone else without you sharing real
  secrets out-of-band.
- Separate keys per environment (dev/staging/prod) if you ever deploy this
  beyond your machine — one leaked dev key shouldn't mean rotating prod.
- Never log full env values; if you log config at startup, mask secrets
  (e.g., show last 4 chars only).

More on secrets-as-a-production-concern (rotation, what to do on a leak) in
[06-production.md](06-production.md).

Next: [03-data-and-retrieval.md](03-data-and-retrieval.md)
