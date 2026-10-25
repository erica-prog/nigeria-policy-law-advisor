# 16 — Database Setup: Matters, Documents and Chats in One Postgres

**Revision 2 (2026-10-24):** the database target moved from Supabase to **Netlify DB**,
Netlify's managed Postgres, which is a Neon database provisioned and wired to a Netlify
site. Everything the application needs is plain Postgres plus the `vector` extension, so
the code did not change; this guide and the connection advice did.

## What lives where

| Data | Where | Why |
|---|---|---|
| Matters, chunks with embeddings, documents, jobs, conversations, chats, agent audit, tenants | Postgres (`db.py`, `migrations/`) | One store, transactional, with row-level security per tenant (docs/31) |
| Accounts and encrypted Claude keys | Postgres (`users`, `user_keys`) | Moved off disk in Phase 1 (docs/22) |
| Uploaded originals | The blob store (`ORIGINALS_DIR`, a mounted volume) | Large binaries do not belong in rows |
| The reference libraries' source texts | The repository (`data/`, `data/us/`) | Rebuilt into the database by `build_index` |

## Where things run

Netlify hosts the static browser client (`web/`) and provisions the database. It does
not run long-lived Python processes or the Node connector servers the API starts, so
the API image (`Dockerfile`) runs on a container host of your choosing (any service
that runs an OCI image with a persistent volume for `data/`). The client calls the API
by URL; the API connects to the database by the connection string Netlify provides.
Deployment choices beyond that are outside this project's scope (AGENTS.md).

## Setting it up

### 1. Provision the database

In the Netlify site, add Netlify DB (the Neon extension) or run `netlify db init` with
the Netlify CLI. Netlify exposes two environment variables to the site:
`NETLIFY_DATABASE_URL` (pooled) and `NETLIFY_DATABASE_URL_UNPOOLED` (direct). Read
them from the Netlify UI; they contain the database password.

### 2. Put the unpooled string in `.env` as `DATABASE_URL`

Use the **unpooled** connection string. The pooled one goes through PgBouncer in
transaction mode, which rejects the prepared statements psycopg creates for repeated
queries and cannot carry `SET ROLE` across statements, which the tenant isolation in
docs/31 relies on. The application keeps its own pool small (`POOL_MAX_SIZE = 4`), so
the direct connection is the right one. `scripts.check_database` warns when the host
looks pooled (`-pooler` in the host name).

```
DATABASE_URL=postgresql://<role>:<password>@<host>.neon.tech/<db>?sslmode=require
```

### 3. Create the tables and load the libraries

```bash
uv run python -m policy_advisor.ingestion.build_index
```

The first connection applies every migration under `src/policy_advisor/migrations/`,
including the `vector` extension, the application role `policy_advisor_app` and the
tenant policies (docs/31). The default Neon role owns the database and can create roles,
which migration 0009 needs.

### 4. Check the database

```bash
uv run python -m scripts.check_database
```

Read-only. Fails on missing pgvector, a table without row-level security, a tenant table
without its forced policy, or an application role that could bypass policies; warns on a
pooled host. Prints the host, never the connection string.

### 5. Run the app

```bash
uv run uvicorn policy_advisor.api.main:app --reload
```

or `docker compose up api`. The API reads `DATABASE_URL` from `.env` (`env_file` in
`docker-compose.yml`).

## Backups and region

Netlify DB inherits Neon's point-in-time restore window and branching; use a branch to
rehearse a migration against real data before applying it to the main branch. Regions
are the provider's: there is no African region today, so a Nigerian firm's documents
sit outside Nigeria. That is a data-residency fact to state to the firm (docs/30 D9,
docs/31), not something this guide can change.

## If you change the embedding model

Vectors from two models are not comparable; the app refuses to search until the
libraries are rebuilt (`build_index`) and every matter is re-uploaded. `index_meta`
records the model in use.

## Running the tests

Tests never touch a hosted database: `tests/conftest.py` refuses any host that is not
local (and refuses known hosted providers outright) because the suite truncates every
table. Run a local Postgres with pgvector and point `TEST_DATABASE_URL` at it (CLAUDE.md
has the commands). The connecting role must be able to create roles: migration 0009
creates `policy_advisor_app`.

## Tenancy (docs/31)

Migration 0009 creates the role `policy_advisor_app` and grants it to the connecting
role. The application switches to it on every connection, so the tenant policies apply
even though the owner role can bypass them. `scripts.check_database` verifies the role
and the forced policies.
