"""Every test runs against a disposable local database - never a hosted one.

`fresh_db` truncates every table so each test starts clean. Pointed at the
production database, that would delete lawyers' matters, uploaded documents and
chats, so where tests may run is an allowlist rather than a hope:

  - Known hosted providers (Neon, Netlify DB, Supabase) are refused outright, with no override.
  - Other non-local hosts are refused unless ALLOW_REMOTE_TEST_DATABASE=1, for
    someone with a dedicated remote database they are happy to wipe.

The app's own DATABASE_URL is overwritten here, before anything reads the
settings, so a real connection string in .env can't leak into a test run.

Locally: `sudo apt-get install postgresql-16 postgresql-16-pgvector`, then
create the database named below. CI starts a pgvector container instead.
"""

import os
from urllib.parse import urlparse

import pytest

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/policy_advisor_test"
)
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "postgres"}


def _refuse_unsafe(url: str) -> None:
    host = (urlparse(url).hostname or "").lower()
    if any(provider in host for provider in ("neon.tech", "netlify", "supabase")):
        raise pytest.UsageError(
            f"Refusing to run tests against {host}: the tests truncate every table, which would "
            "delete real client data. Point TEST_DATABASE_URL at a local Postgres."
        )
    if host not in _LOCAL_HOSTS and os.environ.get("ALLOW_REMOTE_TEST_DATABASE") != "1":
        raise pytest.UsageError(
            f"Refusing to run tests against non-local host {host!r}: the tests truncate every "
            "table. Use a local Postgres, or set ALLOW_REMOTE_TEST_DATABASE=1 for a database "
            "that exists only to be wiped."
        )


_refuse_unsafe(TEST_DATABASE_URL)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
# No cross-encoder in unit tests: deterministic ordering, no model download.
# Tests that exercise reranking inject their own scorer.
os.environ["RERANK_ENABLED"] = "false"
# Spans go to an in-memory exporter the telemetry tests read; nothing is printed.
os.environ["OTEL_EXPORTER"] = "memory"


TENANT_TABLES = (
    "chat_exchanges, chunks, documents, document_jobs, conversations, user_keys, users, matters, "
    "index_meta, agent_tool_calls, agent_runs, matter_shares, tenant_users, tenants, cost_ledger, "
    "findings, extractions, bulk_jobs"
)


def owner_sql(url: str, *statements: str) -> None:
    """Run DDL as the connecting (owner) role: the pool switches to the
    application role, which cannot TRUNCATE, ALTER or GRANT (docs/31)."""
    import psycopg

    with psycopg.connect(url) as conn:
        conn.execute("SET search_path TO public, extensions")
        for statement in statements:
            conn.execute(statement)


def reset_database(url: str) -> None:
    """Empty every table as the connecting (owner) role: the app role the
    pool switches to may not TRUNCATE. Migrations run first through the pool.
    The two tenants every test relies on are put back (migration 0009)."""
    import psycopg

    from policy_advisor.db import connect

    with connect():
        pass  # applies migrations if this is the first connection
    with psycopg.connect(url) as conn:
        conn.execute("SET search_path TO public, extensions")
        conn.execute(f"TRUNCATE {TENANT_TABLES} RESTART IDENTITY CASCADE")
        conn.execute(
            "INSERT INTO tenants (tenant_id, name) VALUES ('shared', 'Shared reference libraries'), "
            "('default', 'Default tenant')"
        )


@pytest.fixture(autouse=True)
def default_tenant():
    """Every test runs as the default tenant unless it says otherwise; the
    database policies make an unset tenant see only the shared libraries."""
    from policy_advisor.tenancy import DEFAULT_TENANT, tenant_context

    with tenant_context(DEFAULT_TENANT):
        yield


@pytest.fixture
def fresh_db():
    """An empty, migrated database for this test."""
    from policy_advisor.db import database_url

    # Checked again at the moment of truncation, against the URL the app will
    # actually use - the last line of defence if settings were read early.
    _refuse_unsafe(database_url())
    reset_database(database_url())
    return database_url()
    # `sources` is deliberately not truncated: migration 0007 seeds the Phase 1
    # web allow-list and the real web-search test relies on it. Tests that
    # edit the registry use the `registry` fixture in tests/test_sources.py,
    # which empties it and restores the packaged seed afterwards.
