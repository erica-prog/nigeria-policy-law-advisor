"""Loads the shared Chroma collection (one collection across all matters,
filtered by `matter_id` at query time per CLAUDE-2.md capability 1 - not a
separate vector store per matter, which is operationally unmanageable at
scale). Vector store and embedding model are config-driven so swapping
either is a one-place change."""

import chromadb
from langchain_chroma import Chroma
from langchain_core.documents import Document

from policy_advisor.config import INDEX_DIR
from policy_advisor.ingestion.chunk import Chunk
from policy_advisor.ingestion.embed import get_embedding_function

COLLECTION_NAME = "policy_advisor"

# Paired with normalize_embeddings=True in embed.py: on unit vectors, cosine
# distance is bounded in [0, 2] and comparable across queries, which is what
# lets hybrid_retriever.py apply an absolute relevance floor. Chroma's default
# is "l2", which on unnormalized vectors has no interpretable scale.
DISTANCE_SPACE = "cosine"


class CollectionSpaceMismatch(RuntimeError):
    """Raised when the persisted collection was built under a different
    distance space than the one configured now."""


def _persisted_space() -> str | None:
    """The distance space the on-disk collection was actually created with, or
    None if there is no collection yet."""
    chroma_dir = INDEX_DIR / "chroma"
    if not chroma_dir.exists():
        return None
    try:
        collection = chromadb.PersistentClient(path=str(chroma_dir)).get_collection(COLLECTION_NAME)
    except Exception:
        # No collection yet, or a Chroma version that won't hand it over
        # without an embedding function - either way there's nothing to check.
        return None
    return (collection.metadata or {}).get("hnsw:space")


def load_vector_store() -> Chroma:
    # Chroma fixes the distance space when the collection is first created and
    # silently ignores a different `hnsw:space` passed later. An index built
    # under the old l2 default would keep returning l2 distances while the
    # retrieval floor interprets them as cosine, so every relevance decision
    # would be wrong with no visible symptom. Fail loudly instead.
    existing = _persisted_space()
    if existing is not None and existing != DISTANCE_SPACE:
        raise CollectionSpaceMismatch(
            f"The index at {INDEX_DIR / 'chroma'} was built with hnsw:space={existing!r}, "
            f"but {DISTANCE_SPACE!r} is configured. Distances from the two are not comparable "
            f"and the retrieval relevance floor would be meaningless. Delete the index and "
            f"rebuild it: rm -rf {INDEX_DIR / 'chroma'} && uv run python -m policy_advisor.ingestion.build_index"
        )

    return Chroma(
        collection_name=COLLECTION_NAME,
        embedding_function=get_embedding_function(),
        persist_directory=str(INDEX_DIR / "chroma"),
        collection_metadata={"hnsw:space": DISTANCE_SPACE},
    )


def chunk_to_document(chunk: Chunk) -> Document:
    return Document(
        page_content=chunk.text,
        metadata={
            "chunk_id": chunk.chunk_id,
            "source_document": chunk.source_document,
            "doc_type": chunk.doc_type,
            "matter_id": chunk.matter_id,
            "jurisdiction": chunk.jurisdiction or "",
            "locator": chunk.locator,
            "page": chunk.page,
            "heading": chunk.heading or "",
            "language": chunk.language,
            "translated_text": chunk.translated_text or "",
            "translated_language": chunk.translated_language or "",
            "translation_flagged": chunk.translation_flagged,
            "translation_flag_reason": chunk.translation_flag_reason,
        },
    )
