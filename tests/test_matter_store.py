import numpy as np
import pytest

from policy_advisor.config import PHASE1_DEMO_MATTER_ID
from policy_advisor.db import EmbeddingModelMismatch, connect, transaction
from policy_advisor.ingestion import matter_store


def _chunk(chunk_id: str, source_document: str, text: str, **extra) -> dict:
    return {"chunk_id": chunk_id, "source_document": source_document, "text": text, **extra}


def _vectors(n: int) -> np.ndarray:
    return np.eye(n, 3, dtype=np.float32)


def test_replace_then_load_round_trips(fresh_db):
    matter_store.replace_document_chunks("matter-a", "doc.pdf", [_chunk("c1", "doc.pdf", "hello")], _vectors(1))

    assert matter_store.load_matter_chunks("matter-a")[0]["text"] == "hello"


def test_embeddings_round_trip_with_their_rows(fresh_db):
    vectors = np.array([[0.6, 0.8, 0.0], [0.0, 0.0, 1.0]], dtype=np.float32)
    matter_store.replace_document_chunks(
        "matter-a", "doc.pdf", [_chunk("c1", "doc.pdf", "a"), _chunk("c2", "doc.pdf", "b")], vectors
    )

    _version, records, embeddings = matter_store.load_matter_vectors("matter-a")

    assert [r["chunk_id"] for r in records] == ["c1", "c2"]
    np.testing.assert_array_equal(embeddings, vectors)


def test_reuploading_a_document_replaces_it_rather_than_merging(fresh_db):
    # The old chunks.json upsert merged by id, so a re-upload that split into
    # fewer chunks kept the stale extras alive in BM25 while Chroma dropped
    # them. Replacing the whole document in one transaction can't do that.
    matter_store.replace_document_chunks(
        "matter-a", "doc.pdf", [_chunk("c1", "doc.pdf", "v1"), _chunk("c2", "doc.pdf", "v1")], _vectors(2)
    )
    matter_store.replace_document_chunks("matter-a", "doc.pdf", [_chunk("c1", "doc.pdf", "v2")], _vectors(1))

    chunks = matter_store.load_matter_chunks("matter-a")
    assert [(c["chunk_id"], c["text"]) for c in chunks] == [("c1", "v2")]


def test_replacing_one_document_leaves_the_others_alone(fresh_db):
    matter_store.replace_document_chunks("matter-a", "a.pdf", [_chunk("c1", "a.pdf", "a")], _vectors(1))
    matter_store.replace_document_chunks("matter-a", "b.pdf", [_chunk("c2", "b.pdf", "b")], _vectors(1))

    assert matter_store.list_documents("matter-a") == ["a.pdf", "b.pdf"]


def test_remove_document_only_removes_its_own_chunks(fresh_db):
    matter_store.replace_document_chunks("matter-a", "doc-a.pdf", [_chunk("c1", "doc-a.pdf", "a")], _vectors(1))
    matter_store.replace_document_chunks("matter-a", "doc-b.pdf", [_chunk("c2", "doc-b.pdf", "b")], _vectors(1))

    removed = matter_store.remove_document("matter-a", "doc-a.pdf")

    assert removed == 1
    assert [c["chunk_id"] for c in matter_store.load_matter_chunks("matter-a")] == ["c2"]


def test_matters_are_isolated_even_when_chunk_ids_collide(fresh_db):
    # Same chunk id in two matters. Isolation must come from the schema's
    # (matter_id, chunk_id) key, not from the chunker happening to prefix ids.
    matter_store.replace_document_chunks("matter-a", "x.pdf", [_chunk("c1", "x.pdf", "a")], _vectors(1))
    matter_store.replace_document_chunks("matter-b", "x.pdf", [_chunk("c1", "x.pdf", "b")], _vectors(1))

    assert matter_store.load_matter_chunks("matter-a")[0]["text"] == "a"
    assert matter_store.load_matter_chunks("matter-b")[0]["text"] == "b"
    assert matter_store.list_matters() == ["matter-a", "matter-b"]


def test_a_record_claiming_another_matter_is_written_to_the_one_requested(fresh_db):
    matter_store.replace_document_chunks(
        "matter-a", "x.pdf", [_chunk("c1", "x.pdf", "a", matter_id="matter-b")], _vectors(1)
    )

    assert matter_store.load_matter_chunks("matter-a")[0]["matter_id"] == "matter-a"
    assert matter_store.load_matter_chunks("matter-b") == []


def test_every_write_moves_the_content_version(fresh_db):
    # Retrievers rebuild their cached indexes when this changes, so a write
    # that failed to move it would leave every cache serving stale chunks.
    start = matter_store.matter_version("matter-a")
    matter_store.replace_document_chunks("matter-a", "x.pdf", [_chunk("c1", "x.pdf", "a")], _vectors(1))
    after_add = matter_store.matter_version("matter-a")
    matter_store.remove_document("matter-a", "x.pdf")
    after_remove = matter_store.matter_version("matter-a")

    assert start < after_add < after_remove


def test_a_failed_write_changes_nothing(fresh_db):
    matter_store.replace_document_chunks("matter-a", "x.pdf", [_chunk("c1", "x.pdf", "old")], _vectors(1))
    version = matter_store.matter_version("matter-a")

    with pytest.raises(ValueError):
        # Two chunks, one embedding: rejected after the delete has run, so
        # only the transaction rolling back keeps the old chunk.
        matter_store.replace_document_chunks(
            "matter-a", "x.pdf", [_chunk("c1", "x.pdf", "new"), _chunk("c2", "x.pdf", "new")], _vectors(1)
        )

    assert [c["text"] for c in matter_store.load_matter_chunks("matter-a")] == ["old"]
    assert matter_store.matter_version("matter-a") == version


def test_vectors_from_a_different_embedding_model_are_refused(fresh_db):
    matter_store.replace_document_chunks("matter-a", "x.pdf", [_chunk("c1", "x.pdf", "a")], _vectors(1))
    with connect() as conn, transaction(conn):
        conn.execute("UPDATE index_meta SET value = 'some-other-model' WHERE key = 'embedding_model'")

    with pytest.raises(EmbeddingModelMismatch):
        matter_store.load_matter_vectors("matter-a")
    with pytest.raises(EmbeddingModelMismatch):
        matter_store.replace_document_chunks("matter-a", "y.pdf", [_chunk("c2", "y.pdf", "b")], _vectors(1))


def test_list_documents_returns_unique_source_documents(fresh_db):
    matter_store.replace_document_chunks(
        "matter-a", "doc.pdf", [_chunk("c1", "doc.pdf", "a"), _chunk("c2", "doc.pdf", "b")], _vectors(2)
    )
    assert matter_store.list_documents("matter-a") == ["doc.pdf"]


def test_load_matter_chunks_for_unknown_matter_returns_empty(fresh_db):
    assert matter_store.load_matter_chunks("does-not-exist") == []
    assert matter_store.matter_version("does-not-exist") == 0


def test_set_and_get_matter_owner(fresh_db):
    matter_store.set_matter_owner("matter-a", "jdoe")

    assert matter_store.get_matter_owner("matter-a") == "jdoe"


def test_get_matter_owner_for_unowned_matter_returns_none(fresh_db):
    assert matter_store.get_matter_owner("never-created") is None


def test_list_matters_for_user_only_returns_their_own_matters(fresh_db):
    matter_store.set_matter_owner("matter-a", "jdoe")
    matter_store.set_matter_owner("matter-b", "someone-else")

    assert "matter-a" in matter_store.list_matters_for_user("jdoe")
    assert "matter-b" not in matter_store.list_matters_for_user("jdoe")


def test_list_matters_for_user_always_includes_the_shared_demo_matter(fresh_db):
    # Not in the database at all in this test - still must be present, since
    # it's a shared reference matter visible to every lawyer regardless of
    # whether they personally own anything yet.
    assert PHASE1_DEMO_MATTER_ID in matter_store.list_matters_for_user("brand-new-user")
