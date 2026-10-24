"""Matters and their chunks, in Supabase Postgres (docs/16).

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

import datetime
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from policy_advisor.config import (
    LIBRARY_MATTER_IDS,
    MATTERS_DIR,
    PHASE1_DEMO_MATTER_ID,
    get_settings,
)
from policy_advisor.db import (
    DictConnection,
    check_embedding_model,
    connect,
    record_embedding_model,
    transaction,
)

METADATA_COLUMNS = (
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
    "tier",
    "trust_kind",
    "locator_scheme",
    "source_id",
    "effective_date",
    "licence",
    "country",
    "jurisdiction_level",
    "region",
)
TIERS = ("matter", "firm", "authority")
_INSERT_COLUMNS = (*METADATA_COLUMNS, "text", "embedding")

# Stored as NULL, surfaced as "" - the shape Chroma returned and that the rest
# of the pipeline (jurisdiction filters, prompt formatting) was written against.
_EMPTY_STRING_WHEN_NULL = (
    "jurisdiction",
    "heading",
    "translated_text",
    "translated_language",
    "translation_flag_reason",
    "locator_scheme",
    "source_id",
    "licence",
    "country",
    "jurisdiction_level",
    "region",
)


def record_from_row(row: dict) -> dict:
    record = {column: row[column] for column in METADATA_COLUMNS}
    for column in _EMPTY_STRING_WHEN_NULL:
        if record[column] is None:
            record[column] = ""
    date = record.get("effective_date")
    record["effective_date"] = date.isoformat() if isinstance(date, datetime.date) else (date or "")
    record["text"] = row["text"]
    return record


def _ensure_matter(conn: DictConnection, matter_id: str) -> None:
    conn.execute(
        "INSERT INTO matters (matter_id) VALUES (%s) ON CONFLICT (matter_id) DO NOTHING",
        (matter_id,),
    )


def _matter_tier(conn: DictConnection, matter_id: str) -> str:
    row = conn.execute("SELECT tier FROM matters WHERE matter_id = %s", (matter_id,)).fetchone()
    return row["tier"] if row else "matter"


def set_matter_tier(matter_id: str, tier: str) -> None:
    """Make a matter a `firm` or `authority` collection (or back to `matter`).
    Its chunks follow, so tier filters never disagree with the matter."""
    if tier not in TIERS:
        raise ValueError(f"unknown tier {tier!r}; expected one of {TIERS}")
    with connect() as conn, transaction(conn):
        _ensure_matter(conn, matter_id)
        conn.execute("UPDATE matters SET tier = %s WHERE matter_id = %s", (tier, matter_id))
        conn.execute("UPDATE chunks SET tier = %s WHERE matter_id = %s", (tier, matter_id))


def get_matter_tier(matter_id: str) -> str:
    with connect() as conn:
        return _matter_tier(conn, matter_id)


def _bump_version(conn: DictConnection, matter_id: str) -> None:
    conn.execute(
        "UPDATE matters SET content_version = content_version + 1 WHERE matter_id = %s",
        (matter_id,),
    )


def _insert_chunks(
    conn: DictConnection, matter_id: str, records: list[dict], embeddings: np.ndarray
) -> None:
    if len(records) != len(embeddings):
        raise ValueError(
            f"{len(records)} chunks but {len(embeddings)} embeddings - they must pair up one to one."
        )
    placeholders = ", ".join("%s" for _ in _INSERT_COLUMNS)
    # A chunk's tier is its matter's tier, whatever the record says: the
    # matter is the unit of isolation and of trust.
    tier = _matter_tier(conn, matter_id)
    with conn.cursor() as cur:
        # psycopg pipelines executemany, so hundreds of rows cost roughly one
        # round trip to Supabase rather than one each.
        cur.executemany(
            f"INSERT INTO chunks ({', '.join(_INSERT_COLUMNS)}) VALUES ({placeholders})",
            [
                (
                    record["chunk_id"],
                    # From the argument, not the record: the write is scoped to
                    # one matter, and a record claiming another must not leak.
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
                    bool(record.get("translation_flagged")),
                    record.get("translation_flag_reason") or None,
                    tier,
                    record.get("trust_kind")
                    or (
                        "authority"
                        if record.get("doc_type") in ("statute", "judgment")
                        else "evidence"
                    ),
                    record.get("locator_scheme") or None,
                    record.get("source_id") or None,
                    record.get("effective_date") or None,
                    record.get("licence") or None,
                    record.get("country") or None,
                    record.get("jurisdiction_level") or None,
                    record.get("region") or None,
                    record["text"],
                    np.asarray(embedding, dtype=np.float32),
                )
                for record, embedding in zip(records, embeddings)
            ],
        )


def matter_meta_path(matter_id: str) -> Path:
    """Where the Chroma-era meta.json lived; read only by scripts/migrate_to_postgres.py."""
    return MATTERS_DIR / matter_id / "meta.json"


def _iso(value: object) -> str | None:
    return (
        value.isoformat()
        if isinstance(value, datetime.datetime)
        else (str(value) if value else None)
    )


def get_matter_meta(matter_id: str) -> dict:
    """Display fields for the chat client (`title`, `created_at`, `updated_at`)
    plus `owner`, from the matters row. Empty dict when the matter does not exist."""
    with connect() as conn:
        row = conn.execute(
            "SELECT owner, title, created_at, updated_at FROM matters WHERE matter_id = %s",
            (matter_id,),
        ).fetchone()
    if row is None:
        return {}
    meta: dict = {}
    for key in ("owner", "title"):
        if row[key]:
            meta[key] = row[key]
    for key in ("created_at", "updated_at"):
        if row[key] is not None:
            meta[key] = _iso(row[key])
    return meta


def update_matter_meta(matter_id: str, **fields: str | None) -> dict:
    """Set `title`, `created_at` or `updated_at` on the matters row (None clears).
    `owner` is never changed here. Only call for a matter that already exists."""
    allowed = {"title", "created_at", "updated_at"}
    unknown = set(fields) - allowed - {"owner"}
    if unknown:
        raise KeyError(f"unknown matter meta fields {sorted(unknown)}")
    with connect() as conn:
        for key, value in fields.items():
            if key == "owner":
                continue
            conn.execute(
                f"UPDATE matters SET {key} = %s WHERE matter_id = %s", (value or None, matter_id)
            )
    return get_matter_meta(matter_id)


def set_matter_owner(matter_id: str, owner: str) -> None:
    """Called once, when a matter is created - a matter has exactly one owner
    for now (no co-counsel sharing in this pass, see CLAUDE-2.md cross-cutting
    concerns)."""
    with connect() as conn:
        conn.execute(
            "INSERT INTO matters (matter_id, owner) VALUES (%s, %s) "
            "ON CONFLICT (matter_id) DO UPDATE SET owner = excluded.owner",
            (matter_id, owner),
        )


def get_matter_owner(matter_id: str) -> str | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT owner FROM matters WHERE matter_id = %s", (matter_id,)
        ).fetchone()
    return row["owner"] if row else None


def list_matters() -> list[str]:
    with connect() as conn:
        return [
            row["matter_id"]
            for row in conn.execute("SELECT matter_id FROM matters ORDER BY matter_id")
        ]


def list_matters_for_user(username: str) -> list[str]:
    """Every matter this user owns, plus the shared libraries: the Phase 1 demo
    matter always (visible to everyone, read-only - see api/deps.py) and any
    other library collection that has been built. Never the full multi-tenant
    list `list_matters()` returns."""
    with connect() as conn:
        owned = {
            row["matter_id"]
            for row in conn.execute("SELECT matter_id FROM matters WHERE owner = %s", (username,))
        }
        built = {
            row["matter_id"]
            for row in conn.execute(
                "SELECT matter_id FROM matters WHERE matter_id = ANY(%s)",
                (sorted(LIBRARY_MATTER_IDS),),
            )
        }
    owned.add(PHASE1_DEMO_MATTER_ID)
    return sorted(owned | built)


def matter_version(matter_id: str) -> int:
    """Changes whenever this matter's chunks change. 0 for a matter that does
    not exist yet, which is indistinguishable from an empty one - correctly, as
    both have nothing to retrieve."""
    with connect() as conn:
        row = conn.execute(
            "SELECT content_version FROM matters WHERE matter_id = %s", (matter_id,)
        ).fetchone()
    return row["content_version"] if row else 0


@dataclass(frozen=True)
class MatterSummary:
    version: int
    tier: str
    chunk_count: int


def matter_summary(matter_id: str) -> MatterSummary:
    """Version, tier and size in one query - what the retriever needs to decide
    how to search a matter before it loads anything."""
    with connect() as conn:
        row = conn.execute(
            "SELECT m.content_version, m.tier, "
            "(SELECT count(*) FROM chunks c WHERE c.matter_id = m.matter_id) AS chunk_count "
            "FROM matters m WHERE m.matter_id = %s",
            (matter_id,),
        ).fetchone()
    if row is None:
        return MatterSummary(version=0, tier="matter", chunk_count=0)
    return MatterSummary(
        version=row["content_version"], tier=row["tier"], chunk_count=int(row["chunk_count"])
    )


def load_matter_snapshot(matter_id: str) -> tuple[int, list[dict]]:
    """(version, records without embeddings) for one matter, from one snapshot.

    What the in-memory keyword index is built from; embeddings stay in the
    database, where vector search runs. One statement, so the version and the
    rows can't come from two different moments - a cache keyed on the version
    could otherwise label one state's rows with another state's number.
    """
    columns = ", ".join(f"c.{column}" for column in METADATA_COLUMNS)
    with connect() as conn:
        check_embedding_model(conn, get_settings().embedding_model)
        rows = conn.execute(
            f"SELECT m.content_version, {columns}, c.text FROM matters m "
            "LEFT JOIN chunks c ON c.matter_id = m.matter_id "
            "WHERE m.matter_id = %s ORDER BY c.inserted_seq",
            (matter_id,),
        ).fetchall()
    if not rows:
        return 0, []
    return rows[0]["content_version"], [
        record_from_row(row) for row in rows if row["chunk_id"] is not None
    ]


def load_matter_chunks(matter_id: str) -> list[dict]:
    """Chunk records without embeddings, in insertion order."""
    return load_matter_snapshot(matter_id)[1]


def load_matter_vectors(matter_id: str) -> tuple[list[dict], np.ndarray]:
    """Records with their stored embeddings - for checks and tools, not the
    query path, which never pulls embeddings out of the database."""
    with connect() as conn:
        rows = conn.execute(
            f"SELECT {', '.join(METADATA_COLUMNS)}, text, embedding FROM chunks "
            "WHERE matter_id = %s ORDER BY inserted_seq",
            (matter_id,),
        ).fetchall()
    if not rows:
        return [], np.zeros((0, 0), dtype=np.float32)
    # pgvector-python hands back its own Vector type, not a numpy array.
    return [record_from_row(row) for row in rows], np.vstack(
        [np.asarray(row["embedding"].to_numpy(), dtype=np.float32) for row in rows]
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
            "DELETE FROM chunks WHERE matter_id = %s AND source_document = %s",
            (matter_id, source_document),
        )
        _insert_chunks(conn, matter_id, records, embeddings)
        _bump_version(conn, matter_id)


def replace_matter_chunks(matter_id: str, records: list[dict], embeddings: np.ndarray) -> None:
    """Swap every chunk in a matter at once - for re-seeding the demo corpus."""
    with connect() as conn, transaction(conn):
        record_embedding_model(conn, get_settings().embedding_model)
        _ensure_matter(conn, matter_id)
        conn.execute("DELETE FROM chunks WHERE matter_id = %s", (matter_id,))
        _insert_chunks(conn, matter_id, records, embeddings)
        _bump_version(conn, matter_id)


def remove_document(matter_id: str, source_document: str) -> int:
    """Delete one document's chunks. Returns how many were removed."""
    with connect() as conn, transaction(conn):
        removed = conn.execute(
            "DELETE FROM chunks WHERE matter_id = %s AND source_document = %s",
            (matter_id, source_document),
        ).rowcount
        _bump_version(conn, matter_id)
    return removed


def record_document(
    matter_id: str,
    name: str,
    *,
    storage_key: str,
    sha256: str,
    size: int,
    content_type: str | None,
    uploaded_by: str | None,
) -> None:
    """Record (or replace) the retained original for one document."""
    with connect() as conn, transaction(conn):
        _ensure_matter(conn, matter_id)
        conn.execute(
            "INSERT INTO documents (matter_id, name, storage_key, sha256, bytes, content_type, uploaded_by) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (matter_id, name) DO UPDATE SET storage_key = excluded.storage_key, "
            "sha256 = excluded.sha256, bytes = excluded.bytes, content_type = excluded.content_type, "
            "uploaded_by = excluded.uploaded_by, uploaded_at = now()",
            (matter_id, name, storage_key, sha256, size, content_type, uploaded_by),
        )


def get_document_record(matter_id: str, name: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT matter_id, name, storage_key, sha256, bytes, content_type, uploaded_by, uploaded_at "
            "FROM documents WHERE matter_id = %s AND name = %s",
            (matter_id, name),
        ).fetchone()
    return dict(row) if row else None


def delete_document_record(matter_id: str, name: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM documents WHERE matter_id = %s AND name = %s", (matter_id, name))


def list_documents(matter_id: str) -> list[str]:
    with connect() as conn:
        return [
            row["source_document"]
            for row in conn.execute(
                "SELECT DISTINCT source_document FROM chunks WHERE matter_id = %s ORDER BY source_document",
                (matter_id,),
            )
        ]
