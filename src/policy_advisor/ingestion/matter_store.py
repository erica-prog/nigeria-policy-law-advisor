"""Matters and their chunks, in the SQLite database (docs/16).

Each chunk's text, metadata and embedding live in one row, and every write
replaces a whole document or matter inside a single transaction. That is the
guarantee the old layout could not give: chunks.json (read by BM25) and Chroma
(vector search) were separate stores written one after the other, so a failure
between the two - or a deletion that reached only one - left the keyword and
vector indexes disagreeing about what a matter contained.

Matters stay isolated the same way they always were (CLAUDE-2.md capability 1):
every read and write is scoped by matter_id, and the shared demo matter is
read-only at the ingestion layer.
"""

import sqlite3

import numpy as np

from policy_advisor.config import PHASE1_DEMO_MATTER_ID, get_settings
from policy_advisor.db import (
    blob_to_embedding,
    check_embedding_model,
    connect,
    embedding_to_blob,
    read_snapshot,
    record_embedding_model,
    transaction,
)

_METADATA_COLUMNS = (
    "chunk_id",
    "matter_id",
    "source_document",
    "doc_type",
    "jurisdiction",
    "locator",
    "page",
    "heading",
    "language",
    "translated_text",
    "translated_language",
    "translation_flagged",
    "translation_flag_reason",
)
_INSERT_COLUMNS = (*_METADATA_COLUMNS, "text", "embedding")

# Stored as NULL, surfaced as "" - the shape Chroma returned and that the rest
# of the pipeline (jurisdiction filters, prompt formatting) was written against.
_EMPTY_STRING_WHEN_NULL = (
    "jurisdiction",
    "heading",
    "translated_text",
    "translated_language",
    "translation_flag_reason",
)


def _record_from_row(row: sqlite3.Row) -> dict:
    record = {column: row[column] for column in _METADATA_COLUMNS}
    for column in _EMPTY_STRING_WHEN_NULL:
        if record[column] is None:
            record[column] = ""
    record["translation_flagged"] = bool(record["translation_flagged"])
    record["text"] = row["text"]
    return record


def _ensure_matter(conn: sqlite3.Connection, matter_id: str) -> None:
    conn.execute("INSERT INTO matters (matter_id) VALUES (?) ON CONFLICT (matter_id) DO NOTHING", (matter_id,))


def _bump_version(conn: sqlite3.Connection, matter_id: str) -> None:
    conn.execute(
        "UPDATE matters SET content_version = content_version + 1 WHERE matter_id = ?", (matter_id,)
    )


def _insert_chunks(
    conn: sqlite3.Connection, matter_id: str, records: list[dict], embeddings: np.ndarray
) -> None:
    if len(records) != len(embeddings):
        raise ValueError(f"{len(records)} chunks but {len(embeddings)} embeddings - they must pair up one to one.")
    placeholders = ", ".join("?" for _ in _INSERT_COLUMNS)
    conn.executemany(
        f"INSERT INTO chunks ({', '.join(_INSERT_COLUMNS)}) VALUES ({placeholders})",
        [
            (
                record["chunk_id"],
                # From the argument, not the record: the write is scoped to one
                # matter, and a record claiming another must not leak across.
                matter_id,
                record["source_document"],
                record.get("doc_type"),
                record.get("jurisdiction") or None,
                record.get("locator"),
                record.get("page"),
                record.get("heading") or None,
                record.get("language"),
                record.get("translated_text") or None,
                record.get("translated_language") or None,
                int(bool(record.get("translation_flagged"))),
                record.get("translation_flag_reason") or None,
                record["text"],
                embedding_to_blob(embedding),
            )
            for record, embedding in zip(records, embeddings)
        ],
    )


def set_matter_owner(matter_id: str, owner: str) -> None:
    """Called once, when a matter is created - a matter has exactly one owner
    for now (no co-counsel sharing in this pass, see CLAUDE-2.md cross-cutting
    concerns)."""
    with connect() as conn, transaction(conn):
        conn.execute(
            "INSERT INTO matters (matter_id, owner) VALUES (?, ?) "
            "ON CONFLICT (matter_id) DO UPDATE SET owner = excluded.owner",
            (matter_id, owner),
        )


def get_matter_owner(matter_id: str) -> str | None:
    with connect() as conn:
        row = conn.execute("SELECT owner FROM matters WHERE matter_id = ?", (matter_id,)).fetchone()
    return row["owner"] if row else None


def list_matters() -> list[str]:
    with connect() as conn:
        return [row["matter_id"] for row in conn.execute("SELECT matter_id FROM matters ORDER BY matter_id")]


def list_matters_for_user(username: str) -> list[str]:
    """Every matter this user owns, plus the shared Phase 1 demo matter
    (visible to everyone, read-only - see app.py) - never the full multi-tenant
    list `list_matters()` returns."""
    with connect() as conn:
        owned = {
            row["matter_id"]
            for row in conn.execute("SELECT matter_id FROM matters WHERE owner = ?", (username,))
        }
    owned.add(PHASE1_DEMO_MATTER_ID)
    return sorted(owned)


def matter_version(matter_id: str) -> int:
    """Changes whenever this matter's chunks change. 0 for a matter that does
    not exist yet, which is indistinguishable from an empty one - correctly, as
    both have nothing to retrieve."""
    with connect() as conn:
        row = conn.execute("SELECT content_version FROM matters WHERE matter_id = ?", (matter_id,)).fetchone()
    return row["content_version"] if row else 0


def load_matter_chunks(matter_id: str) -> list[dict]:
    """Chunk records without embeddings, in insertion order."""
    with connect() as conn:
        rows = conn.execute(
            f"SELECT {', '.join(_METADATA_COLUMNS)}, text FROM chunks WHERE matter_id = ? ORDER BY rowid",
            (matter_id,),
        ).fetchall()
    return [_record_from_row(row) for row in rows]


def load_matter_vectors(matter_id: str) -> tuple[int, list[dict], np.ndarray]:
    """(version, records, embeddings) for one matter, all from one snapshot.

    The version is read in the same transaction as the rows, so a cache keyed on
    it can never label one state's rows with another state's version.
    """
    with connect() as conn:
        check_embedding_model(conn, get_settings().embedding_model)
        with read_snapshot(conn):
            version_row = conn.execute(
                "SELECT content_version FROM matters WHERE matter_id = ?", (matter_id,)
            ).fetchone()
            rows = conn.execute(
                f"SELECT {', '.join(_METADATA_COLUMNS)}, text, embedding FROM chunks "
                "WHERE matter_id = ? ORDER BY rowid",
                (matter_id,),
            ).fetchall()
    version = version_row["content_version"] if version_row else 0
    if not rows:
        return version, [], np.zeros((0, 0), dtype=np.float32)
    return version, [_record_from_row(row) for row in rows], np.vstack(
        [blob_to_embedding(row["embedding"]) for row in rows]
    )


def replace_document_chunks(
    matter_id: str, source_document: str, records: list[dict], embeddings: np.ndarray
) -> None:
    """Swap one document's chunks for a new set, atomically.

    Re-uploading a document that now splits into fewer chunks, or different
    ones, can't leave its old chunks behind under stale ids - they are deleted
    in the same transaction that inserts the new ones.
    """
    with connect() as conn, transaction(conn):
        record_embedding_model(conn, get_settings().embedding_model)
        _ensure_matter(conn, matter_id)
        conn.execute(
            "DELETE FROM chunks WHERE matter_id = ? AND source_document = ?", (matter_id, source_document)
        )
        _insert_chunks(conn, matter_id, records, embeddings)
        _bump_version(conn, matter_id)


def replace_matter_chunks(matter_id: str, records: list[dict], embeddings: np.ndarray) -> None:
    """Swap every chunk in a matter at once - for re-seeding the demo corpus."""
    with connect() as conn, transaction(conn):
        record_embedding_model(conn, get_settings().embedding_model)
        _ensure_matter(conn, matter_id)
        conn.execute("DELETE FROM chunks WHERE matter_id = ?", (matter_id,))
        _insert_chunks(conn, matter_id, records, embeddings)
        _bump_version(conn, matter_id)


def remove_document(matter_id: str, source_document: str) -> int:
    """Delete one document's chunks. Returns how many were removed."""
    with connect() as conn, transaction(conn):
        removed = conn.execute(
            "DELETE FROM chunks WHERE matter_id = ? AND source_document = ?", (matter_id, source_document)
        ).rowcount
        _bump_version(conn, matter_id)
    return removed


def list_documents(matter_id: str) -> list[str]:
    with connect() as conn:
        return [
            row["source_document"]
            for row in conn.execute(
                "SELECT DISTINCT source_document FROM chunks WHERE matter_id = ? ORDER BY source_document",
                (matter_id,),
            )
        ]
