import json
import sqlite3

import numpy as np
import pytest

from policy_advisor.chat_history import ChatExchange, save_exchange
from policy_advisor.config import PHASE1_DEMO_MATTER_ID
from policy_advisor.ingestion import matter_store
from scripts.backup_db import backup
from scripts.migrate_to_sqlite import legacy_matters, migrate


def _write_legacy_matter(root, matter_id, owner=None, chunks=None):
    directory = root / matter_id
    directory.mkdir(parents=True)
    if owner is not None:
        (directory / "meta.json").write_text(json.dumps({"owner": owner}))
    if chunks is not None:
        (directory / "chunks.json").write_text(json.dumps(chunks))


def test_migration_moves_owners_and_chunks(fresh_db, tmp_path, monkeypatch):
    legacy = tmp_path / "matters"
    _write_legacy_matter(
        legacy,
        "smith-v-acme",
        owner="jdoe",
        chunks=[{"chunk_id": "c1", "source_document": "brief.pdf", "text": "the claim", "locator": "Para 1"}],
    )
    monkeypatch.setattr(
        "policy_advisor.ingestion.embed.embed_texts",
        lambda texts: np.ones((len(texts), 3), dtype=np.float32) / np.sqrt(3),
    )

    migrate(legacy)

    assert matter_store.get_matter_owner("smith-v-acme") == "jdoe"
    assert [c["text"] for c in matter_store.load_matter_chunks("smith-v-acme")] == ["the claim"]
    _, _, embeddings = matter_store.load_matter_vectors("smith-v-acme")
    assert embeddings.shape == (1, 3)


def test_migration_skips_the_demo_matter_and_test_leftovers(tmp_path):
    legacy = tmp_path / "matters"
    _write_legacy_matter(legacy, PHASE1_DEMO_MATTER_ID, chunks=[{"chunk_id": "c", "source_document": "d", "text": "t"}])
    _write_legacy_matter(legacy, "test-matter-a-1234", chunks=[])
    _write_legacy_matter(legacy, "owned-but-empty", owner="jdoe", chunks=[])

    assert [matter_id for matter_id, _, _ in legacy_matters(legacy)] == ["owned-but-empty"]


def test_dry_run_changes_nothing(fresh_db, tmp_path):
    legacy = tmp_path / "matters"
    _write_legacy_matter(legacy, "m", owner="jdoe")

    assert migrate(legacy, dry_run=True) == [("m", "jdoe", 0)]
    assert matter_store.get_matter_owner("m") is None


def _populate():
    matter_store.replace_document_chunks(
        "matter-a", "doc.pdf", [{"chunk_id": "c1", "source_document": "doc.pdf", "text": "t"}], np.eye(1, 3)
    )
    save_exchange("jdoe", "matter-a", ChatExchange(question="q", answer="a", source="corpus", faithful=True))


def test_backup_is_a_complete_consistent_copy(fresh_db, tmp_path):
    _populate()

    snapshot = backup(fresh_db, tmp_path / "backups")

    with sqlite3.connect(snapshot) as conn:
        assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 1
        assert conn.execute("SELECT question FROM chat_exchanges").fetchone()[0] == "q"


def test_backup_captures_writes_still_in_the_write_ahead_log(fresh_db, tmp_path):
    # The reason `cp` is unsafe: with a reader holding the database open,
    # SQLite can't checkpoint, so the latest commit lives only in the -wal file.
    _populate()
    reader = sqlite3.connect(fresh_db)
    reader.execute("BEGIN")
    reader.execute("SELECT COUNT(*) FROM chunks").fetchone()
    try:
        save_exchange("jdoe", "matter-a", ChatExchange(question="latest", answer="a", source="corpus", faithful=True))
        assert (fresh_db.parent / f"{fresh_db.name}-wal").stat().st_size > 0

        snapshot = backup(fresh_db, tmp_path / "backups")
    finally:
        reader.close()

    with sqlite3.connect(snapshot) as conn:
        questions = [row[0] for row in conn.execute("SELECT question FROM chat_exchanges ORDER BY id")]
    assert questions == ["q", "latest"]


def test_backup_works_when_the_app_is_not_running(fresh_db, tmp_path):
    # Read-only opens of a WAL database can fail when no connection holds its
    # shared-memory file open. A nightly cron job runs exactly then if the app
    # is down, so check it directly rather than assume.
    _populate()

    snapshot = backup(fresh_db, tmp_path / "backups")

    assert snapshot.exists()


def test_backup_refuses_a_missing_database(tmp_path):
    with pytest.raises(FileNotFoundError):
        backup(tmp_path / "nope.db", tmp_path / "backups")
