"""CLI: rebuild the Phase 1 demo corpus into its own matter
(`PHASE1_DEMO_MATTER_ID`) inside the shared Chroma collection, and write a
manifest recording the corpus hash and embedding model version, so a
re-ingestion that degrades quality can be traced and rolled back (docs/01,
docs/06).

Only this matter's vectors are touched - the vector store is shared across
matters (CLAUDE-2.md capability 1), so a full `Chroma.from_documents` rebuild
would otherwise wipe every other matter's data along with this one. For
adding/removing an arbitrary lawyer-uploaded document in any matter, use
`policy_advisor.ingestion.ingest_document` instead - this script is only for
re-seeding the fixed demo corpus.

Run with: uv run python -m policy_advisor.ingestion.build_index
"""

import hashlib
import json
import time

from policy_advisor.config import DATA_DIR, INDEX_DIR, PHASE1_DEMO_MATTER_ID, get_settings
from policy_advisor.ingestion.chunk import CORPUS, chunk_corpus
from policy_advisor.ingestion.matter_store import save_matter_chunks
from policy_advisor.logging_utils import get_logger, log_event
from policy_advisor.retrieval.vector_store import chunk_to_document, load_vector_store

logger = get_logger(__name__)


def _corpus_hash() -> str:
    hasher = hashlib.sha256()
    for spec in CORPUS:
        hasher.update((DATA_DIR / spec.filename).read_bytes())
    return hasher.hexdigest()[:16]


def main() -> None:
    settings = get_settings()

    chunks = chunk_corpus(DATA_DIR)
    log_event(logger, "chunking_complete", chunk_count=len(chunks))
    if not chunks:
        raise RuntimeError("Chunking produced zero chunks - check parser regexes against the source PDFs.")

    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    vector_store = load_vector_store()
    # Replace only this matter's vectors, not the whole shared collection.
    vector_store.delete(where={"matter_id": PHASE1_DEMO_MATTER_ID})
    vector_store.add_documents(
        documents=[chunk_to_document(chunk) for chunk in chunks],
        ids=[chunk.chunk_id for chunk in chunks],
    )

    save_matter_chunks(
        PHASE1_DEMO_MATTER_ID,
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

    manifest = {
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "corpus_hash": _corpus_hash(),
        "embedding_model": settings.embedding_model,
        "chunk_count": len(chunks),
        "documents": [spec.filename for spec in CORPUS],
        "matter_id": PHASE1_DEMO_MATTER_ID,
    }
    (INDEX_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    log_event(logger, "index_build_complete", **manifest)


if __name__ == "__main__":
    main()
