# 08 — Deployment, Docker, and CI/CD (Data-Engineering Showcase)

CLAUDE.md scopes deployment infra out *unless asked* — you're asking, so
here's how to do it in a way that actually demonstrates data-engineering
judgment, not just "ships a Dockerfile." For a RAG project specifically, the
differentiator a data engineer should showcase is **data-quality gating in
CI**, not just code tests — anyone can lint Python; fewer people gate a
deploy on retrieval recall.

## What you're containerizing — list the services first
This project has more than one runtime unit; don't put everything in one
image:

| Service | Language | What it does | Runs as |
|---|---|---|---|
| Ingestion job | Python | Parse PDFs → chunk → embed → write index | Batch job (run on schedule / on corpus change, not always-on) |
| Retrieval/orchestration API | Python (or TS) | LangChain + MCP client, calls Claude | Long-running service |
| MCP server | TypeScript | Exposes search/metadata tools | Long-running service |
| Vector store | (Chroma/Qdrant/etc., if self-hosted) | Stores embeddings | Long-running service |
| UI | Streamlit or TS web | Demo/product frontend | Long-running service |

Separating these into distinct images is itself the data-engineering point:
the **batch ingestion job** has a fundamentally different lifecycle (runs,
finishes, produces an artifact) than the **always-on API** — conflating them
into one container/process is the kind of thing that looks fine in a demo
and causes confusion in production (e.g., re-running ingestion accidentally
on every API restart).

## Docker

### Why multi-stage builds matter here specifically
Python dependencies (`langchain`, HF model libs, PDF parsers) and the Node
toolchain for the MCP server/TS API are both large and have different build
requirements. Multi-stage builds let you compile/install in a fat "builder"
stage and copy only the runtime artifacts into a slim final image:

```dockerfile
# illustrative shape, not a finished Dockerfile
FROM python:3.12-slim AS builder
WORKDIR /app
RUN pip install uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

FROM python:3.12-slim
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY . .
ENV PATH="/app/.venv/bin:$PATH"
CMD ["python", "-m", "app.api"]
```

Key things to actually get right (these are the parts a reviewer checks):
- **`--frozen`** (or `uv sync` against the committed lockfile) so the image
  build is reproducible — same dependency versions every build, not
  whatever resolves at build time.
- **Don't bake secrets into the image.** No `.env` copied in, no API key
  `ARG`s baked into layers (build args persist in image history). Secrets
  are injected at *run* time via environment variables from the
  orchestrator (Compose, Kubernetes, GitHub Actions secrets).
- **Non-root user** in the final stage — a small but real signal of having
  thought about container security, not just "it runs."
- **`.dockerignore`** excluding `.venv/`, `data/` raw PDFs (if large/not
  needed in the runtime image), `.git/`, `.env`.

### docker-compose for local orchestration
This is what makes the project demoable with one command — a strong
portfolio signal ("clone, `docker compose up`, it works").

```yaml
# illustrative
services:
  vector-store:
    image: chromadb/chroma
    ports: ["8000:8000"]
  api:
    build: ./services/api
    env_file: .env
    depends_on: [vector-store]
    ports: ["8080:8080"]
  mcp-server:
    build: ./services/mcp
    env_file: .env
  ui:
    build: ./services/ui
    ports: ["8501:8501"]
    depends_on: [api]
```
Note `env_file: .env` — Compose injects the env vars at container start;
the `.env` itself is still gitignored, per
[02-environment-setup.md](02-environment-setup.md).

## CI (GitHub Actions)

### The standard gates
- Lint + type-check (`ruff`/`mypy` for Python, `tsc --noEmit` + eslint for
  TS) on every PR.
- Unit tests for chunking logic, MCP tool input validation, etc.
- Build the Docker images (catches Dockerfile/dependency breakage before
  merge, even if you don't push them yet).

### The gate that actually shows data-engineering maturity: retrieval/faithfulness eval in CI
Re-run the golden-set evaluation from
[03-data-and-retrieval.md](03-data-and-retrieval.md#retrieval-evaluation--do-this-before-trusting-any-answer)
and [06-production.md](06-production.md#evaluation) as a CI job, and **fail
the build** if recall@k or citation-faithfulness drops below a threshold
compared to the last known-good run. This is the data equivalent of a
regression test suite — it's what proves a chunking/embedding/prompt change
didn't quietly degrade answer quality, which is a much more meaningful claim
for this project than "tests pass."

```yaml
# illustrative — not a complete workflow
name: ci
on: [pull_request]
jobs:
  eval:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: uv sync --frozen
      - run: uv run python -m eval.run_golden_set --fail-below 0.85
```

### CD: build → push → (optionally) deploy
- Tag images with both a **git SHA** and a **corpus/index version** (the
  versioning concern flagged in
  [01-architecture.md](01-architecture.md#vector-store) and
  [06-production.md](06-production.md#reliability-extras-worth-planning-for))
  — so you can answer "which code *and* which index was running" for any
  deployed instance, not just which code.
- Push to GitHub Container Registry (`ghcr.io`) on merge to `main` — free,
  no extra account setup, and ties image provenance directly to the repo.
- Actual deploy target (Fly.io/Render/a VM/k8s) is a separate decision and
  genuinely is "infra beyond what's needed to explain the architecture" —
  worth deciding only once you know where this needs to actually run. The
  CI/CD pipeline up to "produces a versioned, tested image" is valuable and
  demoable on its own, even without a live deployment target picked yet.

## What to put in the README to make this legible to a reviewer
- An architecture diagram (reuse/adapt the one in
  [01-architecture.md](01-architecture.md)).
- A CI badge showing the eval gate is real and passing.
- "Run it locally" = `docker compose up`, nothing else.
- A short note on what the eval gate measures and why (most reviewers won't
  know to ask; stating it explicitly is what makes it register as deliberate
  engineering rather than boilerplate CI).
