"""Retrieval over the Postgres store: exact vector search in SQL, and a keyword
index that can't go stale.

Uses a real database and real HybridRetriever caching, with the embedding model
stubbed out so the distances are chosen by the test.
"""

import numpy as np
import pytest

from policy_advisor.ingestion import matter_store
from policy_advisor.retrieval import vector_store
from policy_advisor.retrieval.hybrid_retriever import HybridRetriever
from policy_advisor.retrieval.vector_store import VectorIndex

DIMENSIONS = 384


def _unit(*leading: float) -> np.ndarray:
    vector = np.zeros(DIMENSIONS, dtype=np.float32)
    vector[: len(leading)] = leading
    return vector


EAST = _unit(1.0, 0.0)
NORTH = _unit(0.0, 1.0)
NORTH_EAST = _unit(0.6, 0.8)


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


def test_sql_search_returns_exact_cosine_distances_nearest_first(fresh_db):
    matter_store.replace_document_chunks(
        "m",
        "doc.pdf",
        [_record("north", "n"), _record("north-east", "ne"), _record("east", "e")],
        np.vstack([NORTH, NORTH_EAST, EAST]),
    )

    hits = vector_store.search("m", EAST, k=3)

    assert [meta["chunk_id"] for meta, _, _ in hits] == ["east", "north-east", "north"]
    assert [round(distance, 4) for _, _, distance in hits] == [0.0, 0.4, 1.0]


def test_sql_jurisdiction_filter_applies_before_the_limit(fresh_db):
    # Chroma filtered by metadata before picking neighbours. Filtering after
    # would let three close Federal rules use up k=1 and return no Lagos rule.
    matter_store.replace_document_chunks(
        "m",
        "doc.pdf",
        [
            _record("f1", "a"),
            _record("f2", "b"),
            _record("f3", "c"),
            _record("lagos", "d", jurisdiction="lagos"),
        ],
        np.vstack([EAST, EAST, EAST, NORTH]),
    )

    hits = vector_store.search("m", EAST, k=1, jurisdiction="lagos")

    assert [meta["chunk_id"] for meta, _, _ in hits] == ["lagos"]


def test_sql_search_never_crosses_matters(fresh_db):
    matter_store.replace_document_chunks(
        "mine", "doc.pdf", [_record("mine", "a")], np.vstack([NORTH])
    )
    matter_store.replace_document_chunks(
        "theirs", "doc.pdf", [_record("theirs", "b")], np.vstack([EAST])
    )

    hits = vector_store.search("mine", EAST, k=10)

    assert [meta["chunk_id"] for meta, _, _ in hits] == ["mine"]


def test_sql_ties_break_in_insertion_order(fresh_db):
    # Postgres has no implicit row order. Without an explicit tiebreak, equally
    # distant chunks could come back in a different order on each query.
    matter_store.replace_document_chunks(
        "m",
        "doc.pdf",
        [_record("first", "a"), _record("second", "b"), _record("third", "c")],
        np.vstack([EAST, EAST, EAST]),
    )

    assert [meta["chunk_id"] for meta, _, _ in vector_store.search("m", EAST, k=3)] == [
        "first",
        "second",
        "third",
    ]


def test_sql_search_agrees_with_the_in_memory_index(fresh_db):
    # The eval script compares embedding models with VectorIndex, in memory;
    # production searches in SQL. They must rank identically on the same data.
    rng = np.random.default_rng(7)
    vectors = rng.normal(size=(20, DIMENSIONS)).astype(np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    records = [
        _record(f"c{i}", f"t{i}", jurisdiction="federal" if i % 3 else "lagos") for i in range(20)
    ]
    matter_store.replace_document_chunks("m", "doc.pdf", records, vectors)
    query = vectors[4] * 0.7 + vectors[9] * 0.3
    query /= np.linalg.norm(query)

    in_sql = vector_store.search("m", query, k=8, jurisdiction="federal")
    in_memory = VectorIndex.from_records(records, vectors).search(
        query, k=8, jurisdiction="federal"
    )

    assert [m["chunk_id"] for m, _, _ in in_sql] == [m["chunk_id"] for m, _, _ in in_memory]
    np.testing.assert_allclose([d for _, _, d in in_sql], [d for _, _, d in in_memory], atol=1e-5)


def test_retriever_sees_writes_it_was_never_told_about(fresh_db, query_points):
    # The old per-instance BM25 cache went stale unless the one retriever that
    # held it was explicitly invalidated - and CaseReasoningChain's retriever
    # never was. Now every query checks the matter's content_version.
    matter_store.replace_document_chunks(
        "m", "doc.pdf", [_record("c1", "first")], np.vstack([EAST])
    )
    retriever = _ungated()
    assert [c.metadata["chunk_id"] for c in retriever.retrieve("q", top_k=5, matter_id="m")] == [
        "c1"
    ]

    matter_store.replace_document_chunks(
        "m", "doc.pdf", [_record("c2", "second")], np.vstack([EAST])
    )

    assert [c.metadata["chunk_id"] for c in retriever.retrieve("q", top_k=5, matter_id="m")] == [
        "c2"
    ]


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
    matter_store.replace_document_chunks(
        "m", "doc.pdf", [_record("old", "exclusivity clause")], np.vstack([EAST])
    )
    retriever = _ungated()
    retriever.retrieve("exclusivity", top_k=5, matter_id="m")

    matter_store.replace_document_chunks(
        "m", "doc.pdf", [_record("new", "limitation period")], np.vstack([NORTH])
    )
    results = retriever.retrieve("exclusivity", top_k=5, matter_id="m")

    assert [c.metadata["chunk_id"] for c in results] == ["new"]


def test_empty_matter_never_embeds_or_searches(fresh_db, monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("did work for a matter with nothing to search")

    monkeypatch.setattr("policy_advisor.retrieval.hybrid_retriever.embed_query", fail)
    monkeypatch.setattr("policy_advisor.retrieval.hybrid_retriever.vector_store.search", fail)

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


# ---- full-text search backend (docs/22 slice P1-C, AC-7) ----


def _fts() -> HybridRetriever:
    """Force the database keyword backend regardless of matter size."""
    return HybridRetriever(max_distance=2.0, certain_distance=2.0, keyword_index_max_chunks=0)


def test_keyword_search_in_sql_ranks_the_matching_passage_first(fresh_db):
    matter_store.replace_document_chunks(
        "m",
        "doc.pdf",
        [
            _record("c1", "The exclusivity clause runs for two years from signing."),
            _record("c2", "Payment is due within thirty days of each invoice."),
            _record("c3", "Either party may terminate on ninety days written notice."),
        ],
        np.vstack([EAST, NORTH, NORTH_EAST]),
    )
    hits = vector_store.keyword_search("m", "exclusivity clause", k=3)
    assert [meta["chunk_id"] for meta, _, _ in hits] == ["c1"]
    assert vector_store.keyword_search("m", "thirty days", k=3, jurisdiction="lagos") == []


def test_both_keyword_backends_agree_on_the_top_result(fresh_db, query_points):
    records = [
        _record("Order 5 Rule 3", "A defence shall be filed within thirty days of service."),
        _record("Order 9 Rule 14", "Misjoinder of parties does not defeat a proceeding."),
        _record("Paragraph 11", "The court held that consent must be freely given."),
    ]
    matter_store.replace_document_chunks("m", "doc.pdf", records, np.vstack([NORTH, NORTH, NORTH]))
    bm25, fts = _ungated(), _fts()
    assert bm25.keyword_backend("m") == "bm25" and fts.keyword_backend("m") == "fts"

    for query in ("Order 9 Rule 14", "misjoinder of parties", "consent freely given"):
        top_bm25 = bm25.retrieve(query, top_k=3, matter_id="m")[0].metadata["chunk_id"]
        top_fts = fts.retrieve(query, top_k=3, matter_id="m")[0].metadata["chunk_id"]
        assert top_bm25 == top_fts, query


def test_fts_backend_short_circuits_exact_locators_from_the_database(fresh_db, query_points):
    matter_store.replace_document_chunks(
        "m",
        "doc.pdf",
        [_record("Order 5 Rule 3", "the rule"), _record("other", "x")],
        np.vstack([NORTH, EAST]),
    )
    retriever = _fts()
    first = retriever.retrieve("Order 5 Rule 3", top_k=5, matter_id="m")[0]
    assert first.metadata["chunk_id"] == "Order 5 Rule 3" and first.fused_score == 1.0


def test_backend_switches_with_matter_size(fresh_db, query_points):
    matter_store.replace_document_chunks(
        "m", "doc.pdf", [_record("a", "a"), _record("b", "b")], np.vstack([EAST, NORTH])
    )
    assert (
        HybridRetriever(
            max_distance=2.0, certain_distance=2.0, keyword_index_max_chunks=1
        ).keyword_backend("m")
        == "fts"
    )
    assert (
        HybridRetriever(
            max_distance=2.0, certain_distance=2.0, keyword_index_max_chunks=2
        ).keyword_backend("m")
        == "bm25"
    )


def test_fts_backend_does_no_work_for_an_empty_matter(fresh_db, monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("did work for a matter with nothing to search")

    monkeypatch.setattr("policy_advisor.retrieval.hybrid_retriever.embed_query", fail)
    assert _fts().retrieve("anything", top_k=5, matter_id="nothing-here") == []


def test_authority_tier_has_an_hnsw_index_and_search_still_names_it(fresh_db):
    from policy_advisor.db import connect

    with connect() as conn:
        names = {
            row["indexname"]
            for row in conn.execute("SELECT indexname FROM pg_indexes WHERE tablename = 'chunks'")
        }
    assert {"chunks_authority_embedding", "chunks_search_vector", "chunks_by_locator"} <= names
    matter_store.set_matter_tier("lib", "authority")
    matter_store.replace_document_chunks(
        "lib", "rules.pdf", [_record("r1", "a"), _record("r2", "b")], np.vstack([EAST, NORTH])
    )
    hits = vector_store.search("lib", EAST, k=2, tier="authority")
    assert [meta["chunk_id"] for meta, _, _ in hits] == ["r1", "r2"]


# ---- scope widening and reranking (docs/22 slice P1-D, AC-10) ----


def test_widening_answers_from_the_library_only_when_the_matter_has_nothing(fresh_db, query_points):
    matter_store.set_matter_tier("lib", "authority")
    matter_store.replace_document_chunks(
        "lib", "rules.pdf", [_record("Order 1 Rule 2", "came into force")], np.vstack([EAST])
    )
    retriever = _ungated()

    chunks, scope = retriever.retrieve_widening(
        "when in force", top_k=5, matter_id="empty-case", library_matters=["lib"]
    )
    assert scope == "library"
    assert [c.metadata["matter_id"] for c in chunks] == ["lib"]
    assert chunks[0].metadata["tier"] == "authority"

    matter_store.replace_document_chunks(
        "case", "memo.docx", [_record("Section 1", "our own note")], np.vstack([EAST])
    )
    chunks, scope = retriever.retrieve_widening(
        "note", top_k=5, matter_id="case", library_matters=["lib"]
    )
    assert scope == "matter" and [c.metadata["matter_id"] for c in chunks] == ["case"]

    assert retriever.retrieve_widening(
        "anything", top_k=5, matter_id="empty-case", library_matters=[]
    ) == ([], None)


def test_injected_scorer_reorders_fused_candidates_and_puts_exact_matches_first(
    fresh_db, query_points
):
    matter_store.replace_document_chunks(
        "m",
        "doc.pdf",
        [
            _record("Order 5 Rule 3", "the rule"),
            _record("c1", "alpha"),
            _record("c2", "alpha beta gamma"),
        ],
        np.vstack([NORTH, EAST, EAST]),
    )
    longest_wins = lambda query, passages: [float(len(p)) for p in passages]  # noqa: E731
    retriever = HybridRetriever(max_distance=2.0, certain_distance=2.0, scorer=longest_wins)

    results = retriever.retrieve("Order 5 Rule 3 alpha", top_k=3, matter_id="m")

    assert (
        results[0].metadata["chunk_id"] == "Order 5 Rule 3"
    )  # exact locator match, never rescored
    assert [c.metadata["chunk_id"] for c in results[1:]] == ["c2", "c1"]
    assert results[1].rerank_score == float(len("alpha beta gamma"))
