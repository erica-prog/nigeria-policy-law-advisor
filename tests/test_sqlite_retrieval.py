"""Retrieval over the SQLite store: exact vector search, and indexes that can't
go stale.

Uses a real database and real HybridRetriever caching, with the embedding model
stubbed out so the distances are chosen by the test.
"""

import numpy as np
import pytest

from policy_advisor.ingestion import matter_store
from policy_advisor.retrieval.hybrid_retriever import HybridRetriever
from policy_advisor.retrieval.vector_store import VectorIndex

EAST = np.array([1.0, 0.0, 0.0], dtype=np.float32)
NORTH = np.array([0.0, 1.0, 0.0], dtype=np.float32)
NORTH_EAST = np.array([0.6, 0.8, 0.0], dtype=np.float32)


def _record(chunk_id: str, text: str, jurisdiction: str = "federal") -> dict:
    return {
        "chunk_id": chunk_id,
        "source_document": "doc.pdf",
        "text": text,
        "locator": chunk_id,
        "jurisdiction": jurisdiction,
    }


@pytest.fixture
def query_points(monkeypatch):
    """Make every query embed to the vector the test chooses."""
    state = {"vector": EAST}
    monkeypatch.setattr(
        "policy_advisor.retrieval.hybrid_retriever.embed_query", lambda query: state["vector"]
    )
    return state


def _ungated() -> HybridRetriever:
    # 2.0 is the largest possible cosine distance: these tests are about what
    # the index contains, not about the relevance gate.
    return HybridRetriever(max_distance=2.0, certain_distance=2.0)


def test_vector_search_returns_exact_cosine_distances_nearest_first():
    index = VectorIndex.from_records(
        [_record("north", "n"), _record("north-east", "ne"), _record("east", "e")],
        np.vstack([NORTH, NORTH_EAST, EAST]),
    )

    hits = index.search(EAST, k=3)

    assert [meta["chunk_id"] for meta, _, _ in hits] == ["east", "north-east", "north"]
    assert [round(distance, 4) for _, _, distance in hits] == [0.0, 0.4, 1.0]


def test_jurisdiction_filter_applies_before_choosing_k():
    # Chroma filtered by metadata before picking neighbours. Filtering after
    # would let three close Federal rules use up k=1 and return no Lagos rule.
    index = VectorIndex.from_records(
        [_record("f1", "a"), _record("f2", "b"), _record("f3", "c"), _record("lagos", "d", jurisdiction="lagos")],
        np.vstack([EAST, EAST, EAST, NORTH]),
    )

    hits = index.search(EAST, k=1, jurisdiction="lagos")

    assert [meta["chunk_id"] for meta, _, _ in hits] == ["lagos"]


def test_retriever_sees_writes_it_was_never_told_about(fresh_db, query_points):
    # The old per-instance BM25 cache went stale unless the one retriever that
    # held it was explicitly invalidated - and CaseReasoningChain's retriever
    # never was. Now every query checks the matter's content_version.
    matter_store.replace_document_chunks("m", "doc.pdf", [_record("c1", "first")], np.vstack([EAST]))
    retriever = _ungated()
    assert [c.metadata["chunk_id"] for c in retriever.retrieve("q", top_k=5, matter_id="m")] == ["c1"]

    matter_store.replace_document_chunks("m", "doc.pdf", [_record("c2", "second")], np.vstack([EAST]))

    assert [c.metadata["chunk_id"] for c in retriever.retrieve("q", top_k=5, matter_id="m")] == ["c2"]


def test_two_retrievers_agree_after_a_removal(fresh_db, query_points):
    matter_store.replace_document_chunks("m", "doc.pdf", [_record("c1", "text")], np.vstack([EAST]))
    first, second = _ungated(), _ungated()
    first.retrieve("q", top_k=5, matter_id="m")
    second.retrieve("q", top_k=5, matter_id="m")

    matter_store.remove_document("m", "doc.pdf")

    assert first.retrieve("q", top_k=5, matter_id="m") == []
    assert second.retrieve("q", top_k=5, matter_id="m") == []


def test_keyword_and_vector_search_come_from_the_same_rows(fresh_db, query_points):
    # A document replaced in one store but not the other was the failure mode
    # of the old layout. With one set of rows, a replaced chunk vanishes from
    # keyword search and vector search at once.
    matter_store.replace_document_chunks("m", "doc.pdf", [_record("old", "exclusivity clause")], np.vstack([EAST]))
    retriever = _ungated()
    retriever.retrieve("exclusivity", top_k=5, matter_id="m")

    matter_store.replace_document_chunks("m", "doc.pdf", [_record("new", "limitation period")], np.vstack([NORTH]))
    results = retriever.retrieve("exclusivity", top_k=5, matter_id="m")

    assert [c.metadata["chunk_id"] for c in results] == ["new"]


def test_empty_matter_never_loads_the_embedding_model(fresh_db, monkeypatch):
    def fail(query):
        raise AssertionError("embedded a query for a matter with nothing to search")

    monkeypatch.setattr("policy_advisor.retrieval.hybrid_retriever.embed_query", fail)

    assert _ungated().retrieve("anything", top_k=5, matter_id="nothing-here") == []


def test_cached_chunks_are_not_shared_between_queries(fresh_db, query_points):
    # The exact-match short-circuit used to hand out the same cached
    # RetrievedChunk objects on every query, so a score set during one query
    # was still there in the next.
    matter_store.replace_document_chunks(
        "m", "doc.pdf", [_record("Order 5 Rule 3", "the rule")], np.vstack([EAST])
    )
    retriever = _ungated()

    first = retriever.retrieve("Order 5 Rule 3", top_k=5, matter_id="m")[0]
    first.fused_score = -99.0
    second = retriever.retrieve("Order 5 Rule 3", top_k=5, matter_id="m")[0]

    assert second is not first
    assert second.fused_score == 1.0
