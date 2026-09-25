"""Tests the two-band relevance gate that makes "nothing relevant" a thing
retrieval can actually say.

Before this gate, `retrieve()` returned the top k candidates however weak the
match, so `retrieved` was never empty for a matter with documents and the
official-sources fallback in chain.py - which only fires on an empty list -
could not run at all.

Runs offline: the vector store and BM25 index are stubbed, so no API key,
embedding model, or built index is needed.
"""

from unittest.mock import MagicMock, patch

import pytest
from langchain_core.documents import Document

from policy_advisor.retrieval.bm25_index import BM25Index
from policy_advisor.retrieval.hybrid_retriever import HybridRetriever, RetrievedChunk

CERTAIN = 0.21
MAX = 0.32


def _document(chunk_id: str, locator: str) -> Document:
    return Document(
        page_content=f"text of {chunk_id}",
        metadata={"chunk_id": chunk_id, "locator": locator, "jurisdiction": "federal"},
    )


def _retriever(vector_hits: list[tuple[Document, float]]) -> HybridRetriever:
    """A retriever whose vector store returns exactly these (document, distance)
    pairs and whose BM25 index is empty."""
    store = MagicMock()
    store.similarity_search_with_score.return_value = vector_hits
    with patch("policy_advisor.retrieval.hybrid_retriever.load_vector_store", return_value=store):
        retriever = HybridRetriever(max_distance=MAX, certain_distance=CERTAIN)
    retriever._bm25_by_matter["m"] = BM25Index([])
    retriever._locator_index_by_matter["m"] = {}
    return retriever


def test_clearly_relevant_results_are_returned():
    retriever = _retriever([(_document("c1", "Order 1 Rule 1"), 0.10)])
    assert len(retriever.retrieve("anything", top_k=8, matter_id="m")) == 1


def test_clearly_irrelevant_results_are_gated_out_entirely():
    # Every candidate is further away than MAX, so the matter holds nothing on
    # this subject and retrieval should say so rather than hand back the least
    # bad chunks.
    retriever = _retriever([(_document("c1", "Order 1 Rule 1"), 0.80)])
    assert retriever.retrieve("unrelated question", top_k=8, matter_id="m") == []


def test_gate_uses_the_best_candidate_not_the_worst():
    # One good match is enough for the matter to be on-topic; the weak
    # candidates alongside it are a ranking problem, not a relevance one.
    retriever = _retriever(
        [(_document("c1", "Order 1 Rule 1"), 0.12), (_document("c2", "Order 9 Rule 9"), 0.95)]
    )
    assert len(retriever.retrieve("anything", top_k=8, matter_id="m")) == 2


def test_exact_locator_match_bypasses_the_gate():
    # The lawyer named the rule. Metadata hits carry no vector distance, and
    # second-guessing an explicit citation would be the worst possible time to
    # return nothing.
    retriever = _retriever([(_document("c1", "Order 5 Rule 3"), 0.99)])
    retriever._locator_index_by_matter["m"] = {
        "Order 5 Rule 3": [RetrievedChunk(text="the rule", metadata={"chunk_id": "c9", "locator": "Order 5 Rule 3"})]
    }

    results = retriever.retrieve("Order 5 Rule 3", top_k=8, matter_id="m")

    # Not gated out despite every scored candidate being far away, and the
    # named rule leads. Weaker candidates still ride along behind it, as they
    # did before the gate existed - the gate decides whether the query is
    # answerable here, not which chunks are worth keeping.
    assert results[0].metadata["chunk_id"] == "c9"
    assert results != []


@pytest.mark.parametrize(
    "distance, expected",
    [
        (0.10, False),  # comfortably relevant, no adjudication needed
        (CERTAIN, False),  # boundary is inclusive on the confident side
        (0.25, True),  # in the band where distance alone can't decide
        (MAX, True),  # still in the band at the far edge
    ],
)
def test_adjudication_is_requested_only_inside_the_band(distance, expected):
    retriever = _retriever([])
    chunks = [RetrievedChunk(text="t", metadata={"locator": "L"}, vector_distance=distance)]
    assert retriever.needs_relevance_adjudication(chunks) is expected


def test_adjudication_is_skipped_when_the_band_is_collapsed():
    # Setting CERTAIN >= MAX is the documented way to opt out of the extra
    # Claude call and fall back to a plain threshold.
    retriever = _retriever([])
    retriever._certain_distance = MAX
    chunks = [RetrievedChunk(text="t", metadata={"locator": "L"}, vector_distance=0.25)]
    assert retriever.needs_relevance_adjudication(chunks) is False


def test_exact_matches_are_never_adjudicated():
    # No vector distance to reason about, and nothing to gain from asking.
    retriever = _retriever([])
    chunks = [RetrievedChunk(text="t", metadata={"locator": "Order 5 Rule 3"}, fused_score=1.0)]
    assert retriever.needs_relevance_adjudication(chunks) is False


def test_raw_distance_survives_normalization():
    # fused_score is min-max normalized per query, so the top candidate always
    # scores ~1.0 however irrelevant it is. vector_distance is the only field
    # with an absolute meaning, and the gate is worthless if it gets lost.
    retriever = _retriever(
        [(_document("c1", "Order 1 Rule 1"), 0.10), (_document("c2", "Order 2 Rule 2"), 0.30)]
    )
    results = retriever.retrieve("anything", top_k=8, matter_id="m")

    assert sorted(c.vector_distance for c in results) == [0.10, 0.30]
    assert max(c.fused_score for c in results) == pytest.approx(0.5)
