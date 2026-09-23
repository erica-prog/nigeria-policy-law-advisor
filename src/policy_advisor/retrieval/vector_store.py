"""Loads the shared Chroma collection (one collection across all matters,
filtered by `matter_id` at query time per CLAUDE-2.md capability 1 - not a
separate vector store per matter, which is operationally unmanageable at
scale). Vector store and embedding model are config-driven so swapping
either is a one-place change."""

from langchain_chroma import Chroma
from langchain_core.documents import Document

from policy_advisor.config import INDEX_DIR
from policy_advisor.ingestion.chunk import Chunk
from policy_advisor.ingestion.embed import get_embedding_function

COLLECTION_NAME = "policy_advisor"


def load_vector_store() -> Chroma:
    return Chroma(
        collection_name=COLLECTION_NAME,
        embedding_function=get_embedding_function(),
        persist_directory=str(INDEX_DIR / "chroma"),
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
