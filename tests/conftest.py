"""Every test runs against a disposable local database - never Supabase.

`fresh_db` truncates every table so each test starts clean. Pointed at the
Supabase project, that would delete lawyers' matters, uploaded documents and
chats, so where tests may run is an allowlist rather than a hope:

  - Supabase hosts are refused outright, with no override.
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
    if "supabase" in host:
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


@pytest.fixture
def fresh_db():
    """An empty, migrated database for this test."""
    from policy_advisor.db import connect, database_url

    # Checked again at the moment of truncation, against the URL the app will
    # actually use - the last line of defence if settings were read early.
    _refuse_unsafe(database_url())
    with connect() as conn:
        conn.execute("TRUNCATE chat_exchanges, chunks, matters, index_meta RESTART IDENTITY CASCADE")
    return database_url()
