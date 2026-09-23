"""Per-matter chunk persistence (CLAUDE-2.md capability 1: per-matter/session
corpus, not a shared index). Each matter's chunk list lives in its own file
so an upload or deletion in one matter never touches another's data, and
BM25 - which has no native metadata filter - can be built from a correctly
scoped corpus per matter rather than the whole multi-tenant store."""

import json
from pathlib import Path

from policy_advisor.config import MATTERS_DIR, PHASE1_DEMO_MATTER_ID


def matter_chunks_path(matter_id: str) -> Path:
    return MATTERS_DIR / matter_id / "chunks.json"


def matter_meta_path(matter_id: str) -> Path:
    return MATTERS_DIR / matter_id / "meta.json"


def set_matter_owner(matter_id: str, owner: str) -> None:
    """Called once, when a matter is created - a matter has exactly one
    owner for now (no co-counsel sharing in this pass, see CLAUDE-2.md
    cross-cutting concerns). A meta.json sidecar, separate from chunks.json,
    since a freshly-created matter has no chunks yet to attach an owner to."""
    path = matter_meta_path(matter_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"owner": owner}), encoding="utf-8")


def get_matter_owner(matter_id: str) -> str | None:
    path = matter_meta_path(matter_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8")).get("owner")


def list_matters_for_user(username: str) -> list[str]:
    """Every matter this user owns, plus the shared Phase 1 demo matter
    (visible to everyone, read-only - see app.py) - never the full
    multi-tenant list `list_matters()` returns."""
    owned = [m for m in list_matters() if get_matter_owner(m) == username]
    if PHASE1_DEMO_MATTER_ID not in owned:
        owned.append(PHASE1_DEMO_MATTER_ID)
    return sorted(set(owned))


def load_matter_chunks(matter_id: str) -> list[dict]:
    path = matter_chunks_path(matter_id)
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def save_matter_chunks(matter_id: str, chunks: list[dict]) -> None:
    path = matter_chunks_path(matter_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(chunks, indent=2), encoding="utf-8")


def upsert_chunks(matter_id: str, new_chunks: list[dict]) -> list[dict]:
    """Merge by chunk_id - re-ingesting the same document replaces its old
    chunks instead of duplicating them, without touching other documents
    already in this matter."""
    existing = {chunk["chunk_id"]: chunk for chunk in load_matter_chunks(matter_id)}
    for chunk in new_chunks:
        existing[chunk["chunk_id"]] = chunk
    merged = list(existing.values())
    save_matter_chunks(matter_id, merged)
    return merged


def remove_document(matter_id: str, source_document: str) -> list[dict]:
    remaining = [c for c in load_matter_chunks(matter_id) if c["source_document"] != source_document]
    save_matter_chunks(matter_id, remaining)
    return remaining


def list_matters() -> list[str]:
    if not MATTERS_DIR.exists():
        return []
    return sorted(p.name for p in MATTERS_DIR.iterdir() if p.is_dir())


def list_documents(matter_id: str) -> list[str]:
    return sorted({c["source_document"] for c in load_matter_chunks(matter_id)})
