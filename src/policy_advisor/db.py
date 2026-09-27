"""The single SQLite database behind matters, chunks and chat history.

Embedded rather than a hosted service (docs/16): the whole corpus is a few MB,
so what a database service would buy is mostly a bill, a network hop on every
read, and a public endpoint to secure. The file lives beside the app, and the
app reads it in-process.

Connections are opened per operation rather than shared. Streamlit serves each
session on its own thread and a sqlite3 connection may not cross threads, while
opening one is cheap enough that pooling would add complexity and nothing else.
WAL mode lets those threads read while one of them writes.
"""

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import numpy as np

from policy_advisor.config import PROJECT_ROOT, get_settings

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

# Paths already brought up to date by this process, so checking the schema
# costs one read per process rather than one per connection.
_migrated: set[Path] = set()
_migration_lock = threading.Lock()


class EmbeddingModelMismatch(RuntimeError):
    """The stored vectors came from a different embedding model than the one
    configured now."""


def database_path() -> Path:
    path = Path(get_settings().database_path)
    return path if path.is_absolute() else PROJECT_ROOT / path


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # isolation_level=None hands transaction control to `transaction()` below.
    # Python's implicit BEGIN is DEFERRED, and a deferred transaction that reads
    # before it writes can deadlock against another writer in a way that
    # busy_timeout cannot wait out.
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")  # off by default, per connection
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")  # durable under WAL, and faster than FULL
        _ensure_migrated(conn, path)
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """A write transaction that takes the lock up front.

    Every multi-step write goes through here - replace a document's chunks,
    save a chat and prune the old ones - so it either happens completely or not
    at all, and no reader ever sees it half done.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


@contextmanager
def read_snapshot(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Several reads that must agree with each other.

    In autocommit mode each SELECT sees the database as of its own moment, so a
    write landing between two of them could pair one version number with
    another version's rows. Inside one read transaction they all see the same
    committed state.
    """
    conn.execute("BEGIN")
    try:
        yield conn
    finally:
        conn.execute("COMMIT")


def _migrations() -> list[tuple[int, Path]]:
    found = []
    for path in MIGRATIONS_DIR.glob("*.sql"):
        number, _, _ = path.name.partition("_")
        found.append((int(number), path))
    return sorted(found)


def _statements(sql: str) -> Iterator[str]:
    """Split a migration into statements without tripping on semicolons inside
    strings or comments, using SQLite's own notion of a complete statement.

    `executescript` would be simpler, but it commits any open transaction before
    running, so it cannot run inside the locked transaction that makes applying
    a migration atomic and safe against another process doing the same.
    """
    buffer = ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            yield buffer
            buffer = ""
    leftover = "\n".join(
        line for line in buffer.splitlines() if line.strip() and not line.strip().startswith("--")
    )
    if leftover:
        raise ValueError(f"Incomplete SQL statement at end of migration: {leftover[:80]!r}")


def _ensure_migrated(conn: sqlite3.Connection, path: Path) -> None:
    resolved = path.resolve()
    if resolved in _migrated:
        return
    with _migration_lock:
        if resolved in _migrated:
            return
        pending = _migrations()
        latest = pending[-1][0] if pending else 0
        if conn.execute("PRAGMA user_version").fetchone()[0] < latest:
            with transaction(conn):
                # Re-read under the write lock: another process may have
                # applied these between the check above and acquiring it.
                current = conn.execute("PRAGMA user_version").fetchone()[0]
                for number, migration in pending:
                    if number <= current:
                        continue
                    for statement in _statements(migration.read_text(encoding="utf-8")):
                        conn.execute(statement)
                    conn.execute(f"PRAGMA user_version = {number}")
        _migrated.add(resolved)


def embedding_to_blob(vector: np.ndarray | list[float]) -> bytes:
    return np.asarray(vector, dtype="<f4").tobytes()


def blob_to_embedding(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype="<f4")


def check_embedding_model(conn: sqlite3.Connection, model: str) -> None:
    """Refuse to mix vectors from two embedding models.

    Two models of the same dimension produce vectors that multiply together
    without complaint and rank chunks by noise, so the mismatch has to be caught
    by name - there is nothing in the numbers to catch it with.
    """
    row = conn.execute("SELECT value FROM index_meta WHERE key = 'embedding_model'").fetchone()
    if row is not None and row["value"] != model:
        raise EmbeddingModelMismatch(
            f"The vectors in {database_path()} were produced by {row['value']!r}, but "
            f"EMBEDDING_MODEL is {model!r}. Their similarity scores are not comparable. "
            "Either restore EMBEDDING_MODEL, or rebuild every matter's embeddings under the "
            "new model (build_index for the demo corpus, re-upload for the rest) after "
            "deleting the old database."
        )


def record_embedding_model(conn: sqlite3.Connection, model: str) -> None:
    """Call inside the write transaction that stores vectors."""
    check_embedding_model(conn, model)
    conn.execute(
        "INSERT OR IGNORE INTO index_meta (key, value) VALUES ('embedding_model', ?)", (model,)
    )
