from policy_advisor.config import PHASE1_DEMO_MATTER_ID
from policy_advisor.ingestion import matter_store


def test_upsert_then_load_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(matter_store, "MATTERS_DIR", tmp_path)

    matter_store.upsert_chunks(
        "matter-a",
        [{"chunk_id": "matter-a::doc.pdf::1", "source_document": "doc.pdf", "text": "hello"}],
    )

    assert matter_store.load_matter_chunks("matter-a")[0]["text"] == "hello"


def test_upsert_replaces_by_chunk_id_not_duplicates(tmp_path, monkeypatch):
    monkeypatch.setattr(matter_store, "MATTERS_DIR", tmp_path)

    matter_store.upsert_chunks(
        "matter-a", [{"chunk_id": "c1", "source_document": "doc.pdf", "text": "v1"}]
    )
    matter_store.upsert_chunks(
        "matter-a", [{"chunk_id": "c1", "source_document": "doc.pdf", "text": "v2"}]
    )

    chunks = matter_store.load_matter_chunks("matter-a")
    assert len(chunks) == 1
    assert chunks[0]["text"] == "v2"


def test_remove_document_only_removes_its_own_chunks(tmp_path, monkeypatch):
    monkeypatch.setattr(matter_store, "MATTERS_DIR", tmp_path)

    matter_store.upsert_chunks(
        "matter-a",
        [
            {"chunk_id": "c1", "source_document": "doc-a.pdf", "text": "a"},
            {"chunk_id": "c2", "source_document": "doc-b.pdf", "text": "b"},
        ],
    )
    matter_store.remove_document("matter-a", "doc-a.pdf")

    remaining = matter_store.load_matter_chunks("matter-a")
    assert [c["chunk_id"] for c in remaining] == ["c2"]


def test_matters_are_isolated_from_each_other(tmp_path, monkeypatch):
    monkeypatch.setattr(matter_store, "MATTERS_DIR", tmp_path)

    matter_store.upsert_chunks("matter-a", [{"chunk_id": "c1", "source_document": "x.pdf", "text": "a"}])
    matter_store.upsert_chunks("matter-b", [{"chunk_id": "c1", "source_document": "x.pdf", "text": "b"}])

    assert matter_store.load_matter_chunks("matter-a")[0]["text"] == "a"
    assert matter_store.load_matter_chunks("matter-b")[0]["text"] == "b"
    assert sorted(matter_store.list_matters()) == ["matter-a", "matter-b"]


def test_list_documents_returns_unique_source_documents(tmp_path, monkeypatch):
    monkeypatch.setattr(matter_store, "MATTERS_DIR", tmp_path)

    matter_store.upsert_chunks(
        "matter-a",
        [
            {"chunk_id": "c1", "source_document": "doc.pdf", "text": "a"},
            {"chunk_id": "c2", "source_document": "doc.pdf", "text": "b"},
        ],
    )
    assert matter_store.list_documents("matter-a") == ["doc.pdf"]


def test_load_matter_chunks_for_unknown_matter_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(matter_store, "MATTERS_DIR", tmp_path)
    assert matter_store.load_matter_chunks("does-not-exist") == []


def test_set_and_get_matter_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(matter_store, "MATTERS_DIR", tmp_path)

    matter_store.set_matter_owner("matter-a", "jdoe")

    assert matter_store.get_matter_owner("matter-a") == "jdoe"


def test_get_matter_owner_for_unowned_matter_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(matter_store, "MATTERS_DIR", tmp_path)
    assert matter_store.get_matter_owner("never-created") is None


def test_list_matters_for_user_only_returns_their_own_matters(tmp_path, monkeypatch):
    monkeypatch.setattr(matter_store, "MATTERS_DIR", tmp_path)

    matter_store.upsert_chunks("matter-a", [{"chunk_id": "c1", "source_document": "x.pdf", "text": "a"}])
    matter_store.set_matter_owner("matter-a", "jdoe")
    matter_store.upsert_chunks("matter-b", [{"chunk_id": "c1", "source_document": "y.pdf", "text": "b"}])
    matter_store.set_matter_owner("matter-b", "someone-else")

    assert "matter-a" in matter_store.list_matters_for_user("jdoe")
    assert "matter-b" not in matter_store.list_matters_for_user("jdoe")


def test_list_matters_for_user_always_includes_the_shared_demo_matter(tmp_path, monkeypatch):
    monkeypatch.setattr(matter_store, "MATTERS_DIR", tmp_path)
    # Not created on disk at all in this test - still must be present, since
    # it's a shared reference matter visible to every lawyer regardless of
    # whether they personally own anything yet.
    assert PHASE1_DEMO_MATTER_ID in matter_store.list_matters_for_user("brand-new-user")
