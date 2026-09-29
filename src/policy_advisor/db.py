"""The Supabase Postgres database behind matters, chunks and chat history.

One place owns the connection pool, the per-connection setup, and the schema
migrations, so the rest of the code just asks for a connection.

Connect with the Session pooler string (port 5432) from the Supabase dashboard.
It speaks IPv4, which the direct connection does not without a paid add-on, and
unlike the transaction pooler it supports the prepared statements psycopg
creates for frequently repeated queries. The pool is kept small because the
Session pooler caps how many clients a project may hold open at once.
"""

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import DictRow, dict_row
from psycopg_pool import ConnectionPool

from policy_advisor.config import get_settings

# Rows come back as dicts keyed by column name. Declared on the pool itself,
# not set after connecting, so type checkers see it too.
DictConnection = psycopg.Connection[DictRow]

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

# Supabase installs extensions into `extensions`, which a plain Postgres lacks.
# Postgres skips schemas that don't exist, so one setting fits both.
SEARCH_PATH = "public, extensions"

POOL_MAX_SIZE = 4

# Arbitrary, fixed: every process that migrates takes the same lock, so two
# app instances starting at once can't both apply a migration.
_MIGRATION_LOCK_KEY = 7_214_023_991

_pools: dict[str, ConnectionPool[DictConnection]] = {}
_pools_lock = threading.Lock()


class DatabaseNotConfigured(RuntimeError):
    pass


class EmbeddingModelMismatch(RuntimeError):
    """The stored vectors came from a different embedding model than the one
    configured now."""


def database_url() -> str:
    url = get_settings().database_url
    if url is None:
        raise DatabaseNotConfigured(
            "DATABASE_URL is not set. Copy the Session pooler connection string from the "
            "Supabase dashboard (Connect > Session pooler) into .env - see docs/16-supabase-setup.md."
        )
    return url.get_secret_value()


def _configure(conn: DictConnection) -> None:
    conn.execute(f"SET search_path TO {SEARCH_PATH}")
    # Needs search_path first: it finds the vector type by name.
    register_vector(conn)
    conn.commit()  # SET is transactional; commit so it outlives this setup step


def _pool() -> ConnectionPool[DictConnection]:
    url = database_url()
    pool = _pools.get(url)
    if pool is not None:
        return pool
    with _pools_lock:
        pool = _pools.get(url)
        if pool is None:
            # Before the pool exists: register_vector fails on a database
            # where the migration hasn't created the extension yet.
            _migrate(url)
            pool = ConnectionPool(
                url,
                connection_class=DictConnection,
                kwargs={"row_factory": dict_row},
                min_size=1,
                max_size=POOL_MAX_SIZE,
                configure=_configure,
                open=True,
                name="policy_advisor",
            )
            _pools[url] = pool
    return pool


@contextmanager
def connect() -> Iterator[DictConnection]:
    """A pooled connection. Commits on a clean exit, rolls back on an error."""
    with _pool().connection() as conn:
        yield conn


@contextmanager
def transaction(conn: DictConnection) -> Iterator[DictConnection]:
    """A unit of work that either happens completely or not at all.

    Every multi-step write goes through here - replace a document's chunks,
    save a chat and prune the old ones - so no reader ever sees one half done.
    """
    with conn.transaction():
        yield conn


def close_pools() -> None:
    with _pools_lock:
        for pool in _pools.values():
            pool.close()
        _pools.clear()


def _migrations() -> list[tuple[int, Path]]:
    found = []
    for path in MIGRATIONS_DIR.glob("*.sql"):
        number, _, _ = path.name.partition("_")
        found.append((int(number), path))
    return sorted(found)


_BOOTSTRAP = """
CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE schema_migrations ENABLE ROW LEVEL SECURITY;
DO $$
DECLARE
    api_role TEXT;
BEGIN
    FOREACH api_role IN ARRAY ARRAY['anon', 'authenticated'] LOOP
        IF EXISTS (SELECT FROM pg_roles WHERE rolname = api_role) THEN
            EXECUTE format('REVOKE ALL ON schema_migrations FROM %I', api_role);
        END IF;
    END LOOP;
END
$$;
"""


def _migrate(url: str) -> None:
    """Apply any migration this database hasn't run yet, atomically."""
    with psycopg.connect(url) as conn:
        conn.execute(f"SET search_path TO {SEARCH_PATH}")
        with conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (_MIGRATION_LOCK_KEY,))
            exists = conn.execute("SELECT to_regclass('public.schema_migrations') IS NOT NULL").fetchone()
            if not (exists and exists[0]):
                conn.execute(_BOOTSTRAP)
            applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
            for number, path in _migrations():
                if number in applied:
                    continue
                # No parameters, so psycopg sends this through the simple
                # query protocol, which accepts a whole file of statements.
                conn.execute(path.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (number,))


def check_embedding_model(conn: DictConnection, model: str) -> None:
    """Refuse to mix vectors from two embedding models.

    Two models of the same dimension produce vectors that compare without
    complaint and rank chunks by noise, so the mismatch has to be caught by
    name - there is nothing in the numbers to catch it with.
    """
    row = conn.execute("SELECT value FROM index_meta WHERE key = 'embedding_model'").fetchone()
    if row is not None and row["value"] != model:
        raise EmbeddingModelMismatch(
            f"The stored vectors were produced by {row['value']!r}, but EMBEDDING_MODEL is "
            f"{model!r}. Their similarity scores are not comparable. Either restore "
            "EMBEDDING_MODEL, or rebuild every matter's embeddings under the new model "
            "(build_index for the demo corpus, re-upload for the rest)."
        )


def record_embedding_model(conn: DictConnection, model: str) -> None:
    """Call inside the write transaction that stores vectors."""
    check_embedding_model(conn, model)
    conn.execute(
        "INSERT INTO index_meta (key, value) VALUES ('embedding_model', %s) ON CONFLICT (key) DO NOTHING",
        (model,),
    )
