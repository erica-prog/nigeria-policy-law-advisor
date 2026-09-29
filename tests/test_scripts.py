import json

import numpy as np

from policy_advisor.config import PHASE1_DEMO_MATTER_ID
from policy_advisor.db import connect
from policy_advisor.ingestion import matter_store
from scripts.check_database import _connection_warnings, run_checks
from scripts.migrate_to_postgres import legacy_matters, migrate

DIMENSIONS = 384


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
        lambda texts: np.ones((len(texts), DIMENSIONS), dtype=np.float32) / np.sqrt(DIMENSIONS),
    )

    migrate(legacy)

    assert matter_store.get_matter_owner("smith-v-acme") == "jdoe"
    assert [c["text"] for c in matter_store.load_matter_chunks("smith-v-acme")] == ["the claim"]
    _, embeddings = matter_store.load_matter_vectors("smith-v-acme")
    assert embeddings.shape == (1, DIMENSIONS)


def test_migration_skips_the_demo_matter_and_test_leftovers(tmp_path):
    legacy = tmp_path / "matters"
    _write_legacy_matter(legacy, PHASE1_DEMO_MATTER_ID, chunks=[{"chunk_id": "c", "source_document": "d", "text": "t"}])
    _write_legacy_matter(legacy, "test-matter-a-1234", chunks=[])
    _write_legacy_matter(legacy, "owned-but-empty", owner="jdoe", chunks=[])

    assert [matter_id for matter_id, _, _ in legacy_matters(legacy)] == ["owned-but-empty"]


def test_database_check_passes_a_correctly_set_up_database(fresh_db):
    _, _, failures = run_checks(fresh_db)
    assert failures == []


def test_database_check_fails_when_row_level_security_is_off(fresh_db):
    # The check only earns its place if it catches the dangerous state, not
    # just approves the safe one.
    with connect() as conn:
        conn.execute("ALTER TABLE chat_exchanges DISABLE ROW LEVEL SECURITY")
    try:
        _, _, failures = run_checks(fresh_db)
    finally:
        with connect() as conn:
            conn.execute("ALTER TABLE chat_exchanges ENABLE ROW LEVEL SECURITY")

    assert any("chat_exchanges" in f and "Row-level security is OFF" in f for f in failures)


def test_database_check_fails_when_an_api_role_can_read_a_table(fresh_db):
    # Supabase's anon key is public by design; any privilege it holds on these
    # tables is a way to read client data without logging in.
    with connect() as conn:
        conn.execute(
            "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'anon') "
            "THEN CREATE ROLE anon NOLOGIN; END IF; END $$"
        )
        conn.execute("GRANT SELECT ON chunks TO anon")
    try:
        _, _, failures = run_checks(fresh_db)
    finally:
        with connect() as conn:
            conn.execute("REVOKE ALL ON chunks FROM anon")

    assert any("anon" in f and "chunks" in f for f in failures)


def test_database_check_warns_about_the_wrong_connection_type():
    transaction_pooler = "postgresql://postgres.ref:pw@aws-0-eu-west-2.pooler.supabase.com:6543/postgres"
    direct = "postgresql://postgres:pw@db.ref.supabase.co:5432/postgres"
    session_pooler = "postgresql://postgres.ref:pw@aws-0-eu-west-2.pooler.supabase.com:5432/postgres"

    assert any("transaction pooler" in w for w in _connection_warnings(transaction_pooler))
    assert any("direct connection" in w for w in _connection_warnings(direct))
    assert _connection_warnings(session_pooler) == []


def test_database_check_never_prints_the_password(fresh_db):
    facts, warnings, failures = run_checks(fresh_db)
    assert "postgres:postgres@" not in " ".join(facts + warnings + failures)


def test_dry_run_changes_nothing(fresh_db, tmp_path):
    legacy = tmp_path / "matters"
    _write_legacy_matter(legacy, "m", owner="jdoe")

    assert migrate(legacy, dry_run=True) == [("m", "jdoe", 0)]
    assert matter_store.get_matter_owner("m") is None
