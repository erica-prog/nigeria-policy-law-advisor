"""CLI: rebuild the shared library collections in the database and write a
manifest recording each corpus hash and the embedding model version, so a
re-ingestion that degrades quality can be traced and rolled back (docs/01,
docs/06).

Two collections: the Phase 1 Nigerian demo corpus (`PHASE1_DEMO_MATTER_ID`,
three PDFs in data/) and the United States federal library
(`US_LIBRARY_MATTER_ID`, the texts scripts/fetch_us_corpus.py writes to
data/us/; docs/23 slice P2-B). Only these matters' chunks are replaced - the
database holds every matter (CLAUDE-2.md capability 1). For adding/removing a
lawyer-uploaded document in any matter, use
`policy_advisor.ingestion.ingest_document` instead. Creates the tables on
first run.

Run with: uv run python -m policy_advisor.ingestion.build_index
"""

import hashlib
import json
import time

from pathlib import Path

from policy_advisor.config import (
    DATA_DIR,
    INDEX_DIR,
    PHASE1_DEMO_MATTER_ID,
    US_DATA_DIR,
    US_LIBRARY_MATTER_ID,
    get_settings,
)
from policy_advisor.ingestion.chunk import CORPUS, US_LIBRARY, Chunk, chunk_corpus, chunk_us_library
from policy_advisor.ingestion.embed import embed_texts
from policy_advisor.ingestion.matter_store import (
    replace_matter_chunks,
    set_matter_tier,
    update_matter_meta,
)
from policy_advisor.logging_utils import get_logger, log_event
from policy_advisor.retrieval.vector_store import chunk_to_record

logger = get_logger(__name__)


def _hash_files(paths: list[Path]) -> str:
    hasher = hashlib.sha256()
    for path in paths:
        hasher.update(path.read_bytes())
    return hasher.hexdigest()[:16]


def _corpus_hash() -> str:
    return _hash_files([DATA_DIR / spec.filename for spec in CORPUS])


def build_collection(matter_id: str, chunks: list[Chunk], title: str | None = None) -> int:
    """Replace one library collection's chunks. The collections are the shared
    reference libraries, so the matter is the authority tier; set before the
    insert so every chunk inherits it."""
    if not chunks:
        raise RuntimeError(
            f"Chunking produced zero chunks for {matter_id} - check the parsers against the sources."
        )
    embeddings = embed_texts([chunk.text for chunk in chunks])
    set_matter_tier(matter_id, "authority")
    replace_matter_chunks(matter_id, [chunk_to_record(c) for c in chunks], embeddings)
    if title:
        update_matter_meta(matter_id, title=title)
    return len(chunks)


def main() -> None:
    # The libraries belong to the shared tenant (docs/31): every firm reads them.
    from policy_advisor.tenancy import SHARED_TENANT, tenant_context

    with tenant_context(SHARED_TENANT):
        _build_all()


def _build_all() -> None:
    settings = get_settings()

    chunks = chunk_corpus(DATA_DIR)
    log_event(logger, "chunking_complete", matter_id=PHASE1_DEMO_MATTER_ID, chunk_count=len(chunks))
    build_collection(PHASE1_DEMO_MATTER_ID, chunks)

    us_files = [US_DATA_DIR / spec.filename for spec in US_LIBRARY]
    us_present = all(path.exists() for path in us_files)
    us_count = 0
    if us_present:
        us_chunks = chunk_us_library(US_DATA_DIR)
        log_event(
            logger, "chunking_complete", matter_id=US_LIBRARY_MATTER_ID, chunk_count=len(us_chunks)
        )
        us_count = build_collection(
            US_LIBRARY_MATTER_ID, us_chunks, title="United States federal library"
        )
    else:
        log_event(
            logger,
            "us_library_skipped",
            reason="data/us is incomplete; run scripts.fetch_us_corpus",
        )

    manifest = {
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "corpus_hash": _corpus_hash(),
        "embedding_model": settings.embedding_model,
        # Recorded because the retrieval relevance floor is calibrated against
        # this specific combination - a threshold picked under cosine on
        # normalized vectors means nothing under any other pairing.
        "distance": "cosine",
        "embeddings_normalized": True,
        # Deliberately not the connection string: it carries the database
        # password, and this manifest is written to disk and logged.
        "store": "postgres+pgvector",
        "chunk_count": len(chunks),
        "documents": [spec.filename for spec in CORPUS],
        "matter_id": PHASE1_DEMO_MATTER_ID,
        "collections": {
            PHASE1_DEMO_MATTER_ID: {
                "chunk_count": len(chunks),
                "corpus_hash": _corpus_hash(),
                "documents": [spec.filename for spec in CORPUS],
            },
            US_LIBRARY_MATTER_ID: {
                "chunk_count": us_count,
                "corpus_hash": _hash_files(us_files) if us_present else None,
                "documents": [spec.filename for spec in US_LIBRARY],
            },
        },
    }
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    (INDEX_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    log_event(logger, "index_build_complete", **manifest)


if __name__ == "__main__":
    main()
