"""Exact vector search over one matter's chunk embeddings, run in Postgres.

Every query is already scoped to a single matter (CLAUDE-2.md capability 1), so
search only ever compares against that matter's rows - 855 for the demo corpus.
At that size an exact scan costs well under a millisecond, which makes an
approximate index all downside: it can return different neighbours from run to
run, and an HNSW index combined with a matter filter can quietly return fewer
than `k` results. The schema deliberately has no vector index.

Distance is pgvector's cosine distance, `<=>`, which is 1 - cos(q, e): the same
quantity Chroma's cosine space returned, so the relevance thresholds calibrated
against it carry over.
"""

from dataclasses import dataclass, field

import numpy as np

from policy_advisor.db import connect
from policy_advisor.ingestion.chunk import Chunk
from policy_advisor.ingestion.matter_store import METADATA_COLUMNS, record_from_row
from policy_advisor.jurisdictions import storage_values


def search(
    matter_id: str,
    query_vector: np.ndarray,
    k: int,
    jurisdiction: str | None = None,
    tier: str | None = None,
) -> list[tuple[dict, str, float]]:
    """The k nearest chunks in one matter as (metadata, text, cosine distance),
    nearest first.

    The jurisdiction filter sits in the WHERE clause, so it applies before the
    LIMIT, as Chroma's metadata filter did - otherwise a Lagos question could
    spend its whole candidate pool on Federal High Court rules and find no Lagos
    ones. `jurisdiction` is a taxonomy id or legacy alias; rows may hold either,
    so the filter matches every storage value of that jurisdiction. Ties break
    on insertion order so results are reproducible.
    """
    values = list(storage_values(jurisdiction))
    # Naming the tier lets the planner use the authority tier's HNSW index
    # (migration 0004); for a lawyer's matter it changes nothing.
    tier_clause = "AND tier = %(tier)s " if tier else ""
    with connect() as conn:
        rows = conn.execute(
            f"SELECT {', '.join(METADATA_COLUMNS)}, text, embedding <=> %(query)s AS distance "
            "FROM chunks "
            f"WHERE matter_id = %(matter)s {tier_clause}AND (%(all)s OR jurisdiction = ANY(%(values)s)) "
            "ORDER BY distance, inserted_seq LIMIT %(k)s",
            {
                "query": np.asarray(query_vector, dtype=np.float32),
                "matter": matter_id,
                "tier": tier,
                "all": not values,
                "values": values,
                "k": k,
            },
        ).fetchall()
    hits = []
    for row in rows:
        record = record_from_row(row)
        text = record.pop("text")
        hits.append((record, text, float(row["distance"])))
    return hits


def keyword_search(
    matter_id: str, query: str, k: int, jurisdiction: str | None = None
) -> list[tuple[dict, str, float]]:
    """Full-text keyword hits in one matter as (metadata, text, rank), best
    first. `websearch_to_tsquery` parses free text safely (quotes, minus,
    "or"), and the generated search_vector covers the translation too, so a
    query in either language matches. Rank is relative within the query, like
    BM25's score; the retriever normalises it the same way."""
    values = list(storage_values(jurisdiction))
    with connect() as conn:
        rows = conn.execute(
            f"SELECT {', '.join(METADATA_COLUMNS)}, text, ts_rank_cd(search_vector, q) AS rank "
            "FROM chunks, websearch_to_tsquery('english', %(query)s) q "
            "WHERE matter_id = %(matter)s AND search_vector @@ q "
            "AND (%(all)s OR jurisdiction = ANY(%(values)s)) "
            "ORDER BY rank DESC, inserted_seq LIMIT %(k)s",
            {"query": query, "matter": matter_id, "all": not values, "values": values, "k": k},
        ).fetchall()
    hits = []
    for row in rows:
        record = record_from_row(row)
        text = record.pop("text")
        hits.append((record, text, float(row["rank"])))
    return hits


def locator_matches(matter_id: str, locator: str) -> list[tuple[str, dict]]:
    """Every chunk in a matter whose locator is exactly `locator`, as
    (text, metadata), in insertion order - the exact-match short-circuit for
    matters searched through the database rather than an in-memory index."""
    with connect() as conn:
        rows = conn.execute(
            f"SELECT {', '.join(METADATA_COLUMNS)}, text FROM chunks "
            "WHERE matter_id = %s AND locator = %s ORDER BY inserted_seq",
            (matter_id, locator),
        ).fetchall()
    out = []
    for row in rows:
        record = record_from_row(row)
        text = record.pop("text")
        out.append((text, record))
    return out


def chunk_to_record(chunk: Chunk) -> dict:
    return {
        "chunk_id": chunk.chunk_id,
        "source_document": chunk.source_document,
        "doc_type": chunk.doc_type,
        "matter_id": chunk.matter_id,
        "jurisdiction": chunk.jurisdiction or "",
        "locator": chunk.locator,
        "page": chunk.page,
        "heading": chunk.heading or "",
        "text": chunk.text,
        "language": chunk.language,
        "translated_text": chunk.translated_text or "",
        "translated_language": chunk.translated_language or "",
        "translation_flagged": chunk.translation_flagged,
        "translation_flag_reason": chunk.translation_flag_reason or "",
        "tier": chunk.tier,
        "trust_kind": chunk.trust_kind,
        "locator_scheme": chunk.locator_scheme or "",
        "source_id": chunk.source_id or "",
        "effective_date": chunk.effective_date or "",
        "licence": chunk.licence or "",
        "country": chunk.country or "",
        "jurisdiction_level": chunk.jurisdiction_level or "",
        "region": chunk.region or "",
    }


@dataclass
class VectorIndex:
    """Embeddings held in memory for querying, with no database.

    Only for eval/evaluate_embedding_candidate.py, which compares candidate
    models without writing their vectors into the real database. The query
    path uses `search` above. Same ordering rules - jurisdiction before the
    limit, ties by insertion order - so the two agree on the same vectors.
    """

    metadatas: list[dict] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)
    matrix: np.ndarray = field(default_factory=lambda: np.zeros((0, 0), dtype=np.float32))

    @classmethod
    def from_records(cls, records: list[dict], embeddings: np.ndarray) -> "VectorIndex":
        return cls(
            metadatas=[{k: v for k, v in record.items() if k != "text"} for record in records],
            texts=[record["text"] for record in records],
            matrix=np.asarray(embeddings, dtype=np.float32),
        )

    def __len__(self) -> int:
        return len(self.metadatas)

    def search(
        self, query_vector: np.ndarray, k: int, jurisdiction: str | None = None
    ) -> list[tuple[dict, str, float]]:
        """The k nearest chunks as (metadata, text, cosine distance), nearest first.

        The jurisdiction filter applies before choosing the k, as Chroma's
        metadata filter did - otherwise a Lagos question could spend its whole
        candidate pool on Federal High Court rules and find no Lagos ones.
        """
        if not self.metadatas:
            return []
        candidates = np.arange(len(self.metadatas))
        values = storage_values(jurisdiction)
        if values:
            candidates = np.array(
                [i for i in candidates if self.metadatas[i].get("jurisdiction") in values],
                dtype=int,
            )
            if candidates.size == 0:
                return []
        distances = 1.0 - self.matrix[candidates] @ np.asarray(query_vector, dtype=np.float32)
        # Stable, so equal distances keep insertion order and results are
        # reproducible run to run.
        order = np.argsort(distances, kind="stable")[:k]
        return [
            (self.metadatas[candidates[i]], self.texts[candidates[i]], float(distances[i]))
            for i in order
        ]
