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


def search(
    matter_id: str, query_vector: np.ndarray, k: int, jurisdiction: str | None = None
) -> list[tuple[dict, str, float]]:
    """The k nearest chunks in one matter as (metadata, text, cosine distance),
    nearest first.

    The jurisdiction filter sits in the WHERE clause, so it applies before the
    LIMIT, as Chroma's metadata filter did - otherwise a Lagos question could
    spend its whole candidate pool on Federal High Court rules and find no Lagos
    ones. Ties break on insertion order so results are reproducible.
    """
    with connect() as conn:
        rows = conn.execute(
            f"SELECT {', '.join(METADATA_COLUMNS)}, text, embedding <=> %(query)s AS distance "
            "FROM chunks "
            "WHERE matter_id = %(matter)s AND (%(jurisdiction)s::text IS NULL OR jurisdiction = %(jurisdiction)s) "
            "ORDER BY distance, inserted_seq LIMIT %(k)s",
            {
                "query": np.asarray(query_vector, dtype=np.float32),
                "matter": matter_id,
                "jurisdiction": jurisdiction or None,
                "k": k,
            },
        ).fetchall()
    hits = []
    for row in rows:
        record = record_from_row(row)
        text = record.pop("text")
        hits.append((record, text, float(row["distance"])))
    return hits


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
        if jurisdiction:
            candidates = np.array(
                [i for i in candidates if self.metadatas[i].get("jurisdiction") == jurisdiction],
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
