"""Incremental ingestion for lawyer-uploaded documents (CLAUDE-2.md capability
1): add or remove a single document within one matter without touching any
other matter's vectors, BM25 index, or persisted chunk list. This is the
on-demand counterpart to build_index.py's full-corpus rebuild, which is only
for the fixed Phase 1 demo seed."""

from pathlib import Path

from policy_advisor.config import PHASE1_DEMO_MATTER_ID
from policy_advisor.ingestion.chunk import SUPPORTED_SUFFIXES, chunk_uploaded_document
from policy_advisor.ingestion.chunk_translation import translate_chunks
from policy_advisor.ingestion.matter_store import remove_document as remove_document_chunks
from policy_advisor.ingestion.matter_store import upsert_chunks
from policy_advisor.logging_utils import get_logger, log_event
from policy_advisor.retrieval.vector_store import chunk_to_document, load_vector_store

logger = get_logger(__name__)


class UnsupportedDocumentError(ValueError):
    pass


class SharedMatterReadOnlyError(ValueError):
    pass


def _reject_shared_demo_matter(matter_id: str) -> None:
    # The shared reference matter is visible to every lawyer (CLAUDE-2.md
    # cross-cutting concerns) - it must stay read-only at the function
    # level, not just have its UI controls hidden, or any caller bypassing
    # app.py could still mutate a matter every other user relies on.
    if matter_id == PHASE1_DEMO_MATTER_ID:
        raise SharedMatterReadOnlyError(
            f"'{PHASE1_DEMO_MATTER_ID}' is a shared reference matter and is read-only."
        )


def add_document(matter_id: str, file_path: Path, jurisdiction: str | None = None) -> int:
    """Ingests one document into one matter. Returns the number of chunks
    written. Re-adding the same filename replaces its previous chunks
    (upsert by chunk_id) rather than duplicating them."""
    _reject_shared_demo_matter(matter_id)
    if file_path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise UnsupportedDocumentError(
            f"Unsupported document format: {file_path.suffix} (supported: {sorted(SUPPORTED_SUFFIXES)})"
        )

    chunks = chunk_uploaded_document(matter_id, file_path, jurisdiction=jurisdiction)
    if not chunks:
        raise ValueError(f"Ingestion produced zero chunks for {file_path.name} - the file may be empty or unreadable.")
    chunks = translate_chunks(chunks)

    vector_store = load_vector_store()
    # Clear any prior chunks for this exact document before re-adding, so a
    # re-upload that produces fewer/renumbered chunks doesn't leave stale
    # vectors behind under the old chunk_ids.
    vector_store.delete(where={"$and": [{"matter_id": matter_id}, {"source_document": file_path.name}]})
    vector_store.add_documents(
        documents=[chunk_to_document(chunk) for chunk in chunks],
        ids=[chunk.chunk_id for chunk in chunks],
    )

    upsert_chunks(
        matter_id,
        [
            {
                "chunk_id": c.chunk_id,
                "source_document": c.source_document,
                "doc_type": c.doc_type,
                "matter_id": c.matter_id,
                "jurisdiction": c.jurisdiction,
                "locator": c.locator,
                "page": c.page,
                "heading": c.heading or "",
                "text": c.text,
                "language": c.language,
                "translated_text": c.translated_text or "",
                "translated_language": c.translated_language or "",
                "translation_flagged": c.translation_flagged,
                "translation_flag_reason": c.translation_flag_reason,
            }
            for c in chunks
        ],
    )

    log_event(logger, "document_ingested", matter_id=matter_id, document=file_path.name, chunk_count=len(chunks))
    return len(chunks)


def remove_document(matter_id: str, source_document: str) -> None:
    """Removes one document's chunks from both the vector store and this
    matter's BM25 source file - a deletion must clear both, not just the
    vector store, or BM25 search would keep surfacing "deleted" text."""
    _reject_shared_demo_matter(matter_id)
    vector_store = load_vector_store()
    vector_store.delete(where={"$and": [{"matter_id": matter_id}, {"source_document": source_document}]})
    remove_document_chunks(matter_id, source_document)
    log_event(logger, "document_removed", matter_id=matter_id, document=source_document)
