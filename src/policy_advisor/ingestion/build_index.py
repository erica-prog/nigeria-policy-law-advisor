"""CLI: rebuild the Phase 1 demo corpus into its own matter
(`PHASE1_DEMO_MATTER_ID`) in the database, and write a manifest recording the
corpus hash and embedding model version, so a re-ingestion that degrades
quality can be traced and rolled back (docs/01, docs/06).

Only this matter's chunks are replaced - the database holds every matter
(CLAUDE-2.md capability 1). For adding/removing an arbitrary lawyer-uploaded
document in any matter, use `policy_advisor.ingestion.ingest_document` instead;
this script is only for re-seeding the fixed demo corpus. Creates the tables on
first run.

Run with: uv run python -m policy_advisor.ingestion.build_index
"""

import hashlib
import json
import time

from policy_advisor.config import DATA_DIR, INDEX_DIR, PHASE1_DEMO_MATTER_ID, get_settings
from policy_advisor.ingestion.chunk import CORPUS, chunk_corpus
from policy_advisor.ingestion.embed import embed_texts
from policy_advisor.ingestion.matter_store import replace_matter_chunks
from policy_advisor.logging_utils import get_logger, log_event
from policy_advisor.retrieval.vector_store import chunk_to_record

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

    embeddings = embed_texts([chunk.text for chunk in chunks])
    replace_matter_chunks(PHASE1_DEMO_MATTER_ID, [chunk_to_record(c) for c in chunks], embeddings)

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
    }
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    (INDEX_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    log_event(logger, "index_build_complete", **manifest)


if __name__ == "__main__":
    main()
