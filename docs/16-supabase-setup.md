# 16 — Supabase Setup: Matters, Documents and Chats in One Database

The advisor keeps everything it stores - matters, the text and embeddings of
every uploaded document, and each lawyer's recent chats - in one Supabase
Postgres database, using the pgvector extension for vector search.

## What lives where

| Table | Holds |
|---|---|
| `matters` | Each matter and its owner |
| `chunks` | Each piece of each document: its text, metadata and 384-number embedding, in one row |
| `chat_exchanges` | Each lawyer's last five question-and-answer exchanges per matter |
| `index_meta` | Which embedding model produced the stored vectors |
| `schema_migrations` | Which schema changes have been applied |

Because a chunk's text and its embedding share a row, and every upload or
deletion replaces a whole document in one transaction, keyword search and
vector search can never disagree about what a matter contains. That was a real
risk before, when the two lived in separate stores written one after the
other.

## Before you start: where things run

**Pick the region for the lawyers, then put the app next to the database.**
Supabase has no African region. For users in Nigeria and West Africa, London
(`eu-west-2`) is usually the closest; many West African routes reach Europe
faster than they reach South Africa. Measure from a few users' locations if
you can.

Then host the app in the **same AWS region as the database**. Each question
makes several database round trips - a version check and a vector search per
retrieval - and a case analysis makes around thirty. Within one region that
costs tens of milliseconds. Across continents it adds several seconds to every
analysis. The single round trip from the lawyer's browser to the app matters
far less, because it is already waiting seconds for Claude.

**Data residency is a legal decision.** Hosting outside Nigeria is an
international transfer under the Nigeria Data Protection Act 2023, and the
same is true for lawyers elsewhere in Africa under their own laws. The app
keeps as little as it can - five exchanges per lawyer per matter, pruned
automatically, storing citation references rather than the passages quoted -
but a qualified lawyer should confirm the legal basis before real client
matters go in. The public demo corpus raises no such question.

## Setting it up

You have already created the project. From there:

### 1. Copy the Session pooler connection string

Dashboard > **Connect** > **Session pooler**. It looks like:

```
postgresql://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres
```

Use this one, not the direct connection or the transaction pooler:

- The **direct connection** only works over IPv6 unless you buy the IPv4
  add-on, and many hosts - GitHub Actions among them - are IPv4-only.
- The **transaction pooler** (port 6543) does not support the prepared
  statements the app's database driver creates for repeated queries.
- The **Session pooler** works over IPv4 and supports them.

### 2. Put it in `.env`

```bash
DATABASE_URL=postgresql://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres
```

This string contains your database password. Keep it in `.env` (already
ignored by git) or your host's secret store. Never paste it into code, an
issue, or a chat.

### 3. Create the tables and load the demo corpus

```bash
uv run python -m policy_advisor.ingestion.build_index
```

The first connection creates everything: it enables pgvector in Supabase's
`extensions` schema, creates the five tables, and records the schema version.
Then it loads the three demo documents. It should report **855 chunks**. You
never need to paste SQL into the dashboard; the schema lives in
[`src/policy_advisor/migrations/`](../src/policy_advisor/migrations/) and the
app applies anything new when it starts.

If you're upgrading an installation that still has
`data/index/matters/*/chunks.json` files from the Chroma era, move those
matters across once:

```bash
uv run python -m scripts.migrate_to_postgres --dry-run   # see what would move
uv run python -m scripts.migrate_to_postgres
```

### 4. Check nothing is exposed through Supabase's public API

Supabase gives every project an automatic REST API, reachable with the
project's public "anon" key. This app doesn't use it - it talks to Postgres
directly - so every table is locked off from it: row-level security is on
with no policies, and the `anon` and `authenticated` roles have no access at
all. Confirm it:

```bash
uv run python -m scripts.check_database
```

It is read-only and safe against the live project. It checks that pgvector
works, that row-level security is on for every table, and that neither API
role holds any privilege. It also reports what's stored, which embedding model
produced it, and the round-trip time to the database. It exits with an error
if anything puts client data at risk, and never prints your connection string.

In the dashboard, the **Table Editor** should show **RLS enabled** on each
table, and **Advisors > Security Advisor** should show no "RLS disabled"
warnings for them.

This matters more than it looks. Without it, the anon key, which is designed
to be public, would be enough to read lawyers' chats.

### 5. Run the app

```bash
uv run streamlit run src/policy_advisor/app.py
```

or with Docker, `docker compose up -d`: the container reads `DATABASE_URL` from
`.env`.

## Backups

The Pro plan takes a **daily backup, kept for 7 days**, and never pauses the
project. Restore from Dashboard > Database > Backups. Point-in-time recovery,
which can restore to any moment rather than to last night, is a paid add-on
for when a day's lost work would matter.

Seven days is short for legal records. For a copy that lives outside Supabase,
take an occasional dump:

```bash
pg_dump "$DATABASE_URL" --format=custom --file=data/backups/advisor-$(date +%F).dump
```

Your `pg_dump` must be at least as new as the project's Postgres version
(Dashboard > Settings > Infrastructure), or it refuses to run. The dump holds
client chats and document text, so encrypt it before it leaves your machine,
and delete old ones rather than keeping them forever. `data/backups/` is
ignored by git.

## If you change the embedding model

`EMBEDDING_MODEL` and the database have to agree. The `chunks.embedding`
column holds exactly 384 numbers, the size bge-small-en-v1.5 produces, and
`index_meta` records the model's name. If `EMBEDDING_MODEL` changes, the app
refuses to search or add documents rather than compare vectors from two
different models, which would give plausible-looking and meaningless results.
Switching models means a schema change for the new size and re-embedding every
matter.

## Running the tests

The test suite empties every table between tests, so it must never run
against Supabase. `tests/conftest.py` refuses any Supabase host outright, and
any other non-local host unless you explicitly allow it. Run the tests
against a local Postgres instead:

```bash
sudo apt-get install postgresql-16 postgresql-16-pgvector
sudo -u postgres createdb policy_advisor_test
sudo -u postgres psql -c "ALTER USER postgres PASSWORD 'postgres';"
uv run pytest
```

CI starts its own disposable Postgres with pgvector for every run and never
touches your project.
