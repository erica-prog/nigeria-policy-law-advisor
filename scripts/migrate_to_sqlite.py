"""One-off: move matters from the pre-SQLite layout into the database.

Reads data/index/matters/<id>/chunks.json (chunk text, used by BM25) and
meta.json (the owner), re-embeds each chunk's text with the configured model,
and writes both into the database. The original uploaded files were never
kept, so the stored chunk text is the only source there is - and re-embedding
it with the same model reproduces the vectors Chroma held.

Safe to re-run: each matter's chunks are replaced wholesale.

Skipped:
  - the shared demo matter, which build_index rebuilds from the source PDFs,
    so it cannot inherit a stale chunks.json;
  - directories with neither chunks nor an owner, which are what test runs
    left behind.

Usage:
  uv run python -m scripts.migrate_to_sqlite --dry-run
  uv run python -m scripts.migrate_to_sqlite
"""

import argparse
import json
from pathlib import Path

from policy_advisor.config import MATTERS_DIR, PHASE1_DEMO_MATTER_ID


def _read_json(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def legacy_matters(matters_dir: Path) -> list[tuple[str, str | None, list[dict]]]:
    """(matter_id, owner, chunks) for every matter worth migrating."""
    found = []
    if not matters_dir.exists():
        return found
    for matter_dir in sorted(p for p in matters_dir.iterdir() if p.is_dir()):
        matter_id = matter_dir.name
        if matter_id == PHASE1_DEMO_MATTER_ID:
            continue
        owner = _read_json(matter_dir / "meta.json", {}).get("owner")
        chunks = _read_json(matter_dir / "chunks.json", [])
        if owner is None and not chunks:
            continue
        found.append((matter_id, owner, chunks))
    return found


def migrate(matters_dir: Path, dry_run: bool = False) -> list[tuple[str, str | None, int]]:
    from policy_advisor.ingestion.embed import embed_texts
    from policy_advisor.ingestion.matter_store import replace_matter_chunks, set_matter_owner

    migrated = []
    for matter_id, owner, chunks in legacy_matters(matters_dir):
        migrated.append((matter_id, owner, len(chunks)))
        if dry_run:
            continue
        if owner is not None:
            set_matter_owner(matter_id, owner)
        if chunks:
            replace_matter_chunks(matter_id, chunks, embed_texts([chunk["text"] for chunk in chunks]))
    return migrated


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matters-dir", type=Path, default=MATTERS_DIR)
    parser.add_argument("--dry-run", action="store_true", help="list what would move, change nothing")
    args = parser.parse_args()

    migrated = migrate(args.matters_dir, dry_run=args.dry_run)
    verb = "Would migrate" if args.dry_run else "Migrated"
    for matter_id, owner, chunk_count in migrated:
        print(f"{verb} {matter_id}: owner={owner or '-'} chunks={chunk_count}")
    print(f"\n{verb} {len(migrated)} matter(s) from {args.matters_dir}.")
    if not args.dry_run and migrated:
        print(
            "Check them in the app, then delete the old data/index/chroma and "
            "data/index/matters directories - nothing reads them any more."
        )


if __name__ == "__main__":
    main()
