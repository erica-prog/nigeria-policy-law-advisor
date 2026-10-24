"""Tests the two-band relevance gate that makes "nothing relevant" a thing
retrieval can actually say.

Before this gate, `retrieve()` returned the top k candidates however weak the
match, so `retrieved` was never empty for a matter with documents and the
official-sources fallback in chain.py - which only fires on an empty list -
could not run at all.

Runs offline: the matter's indexes are stubbed, so no API key, embedding model,
or database is needed.
"""

import numpy as np
import pytest

from policy_advisor.retrieval.bm25_index import BM25Document, BM25Index
from policy_advisor.retrieval.hybrid_retriever import HybridRetriever, RetrievedChunk, _MatterIndex

CERTAIN = 0.21
MAX = 0.32


# What the stubbed vector search returns; set by `_retriever`.
_vector_hits: list[tuple[dict, str, float]] = []


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    # The stubbed index below is always current, and vector search returns
    # whatever the test chose, so no database or embedding model is involved.
    monkeypatch.setattr("policy_advisor.retrieval.hybrid_retriever.matter_version", lambda matter_id: 1)
    monkeypatch.setattr("policy_advisor.retrieval.hybrid_retriever.embed_query", lambda query: np.zeros(3))
    monkeypatch.setattr(
        "policy_advisor.retrieval.hybrid_retriever.vector_store.search",
        lambda matter_id, query_vector, k, jurisdiction=None: list(_vector_hits),
    )
    _vector_hits.clear()


def _hit(chunk_id: str, locator: str, distance: float) -> tuple[dict, str, float]:
    return ({"chunk_id": chunk_id, "locator": locator, "jurisdiction": "federal"}, f"text of {chunk_id}", distance)


def _retriever(vector_hits: list[tuple[dict, str, float]], locators: dict | None = None) -> HybridRetriever:
    """A retriever whose vector search returns exactly these (metadata, text,
    distance) hits. Its keyword index holds the same chunks, so the matter
    isn't empty, but no query term matches them, so BM25 adds nothing."""
    _vector_hits[:] = vector_hits
    retriever = HybridRetriever(max_distance=MAX, certain_distance=CERTAIN)
    bm25 = BM25Index([BM25Document(metadata=meta, text=text) for meta, text, _ in vector_hits])
    retriever._indexes["m"] = _MatterIndex(version=1, bm25=bm25, locators=locators or {})
    return retriever


def test_clearly_relevant_results_are_returned():
    retriever = _retriever([_hit("c1", "Order 1 Rule 1", 0.10)])
    assert len(retriever.retrieve("anything", top_k=8, matter_id="m")) == 1


def test_clearly_irrelevant_results_are_gated_out_entirely():
    # Every candidate is further away than MAX, so the matter holds nothing on
    # this subject and retrieval should say so rather than hand back the least
    # bad chunks.
    retriever = _retriever([_hit("c1", "Order 1 Rule 1", 0.80)])
    assert retriever.retrieve("unrelated question", top_k=8, matter_id="m") == []


def test_gate_uses_the_best_candidate_not_the_worst():
    # One good match is enough for the matter to be on-topic; the weak
    # candidates alongside it are a ranking problem, not a relevance one.
    retriever = _retriever([_hit("c1", "Order 1 Rule 1", 0.12), _hit("c2", "Order 9 Rule 9", 0.95)])
    assert len(retriever.retrieve("anything", top_k=8, matter_id="m")) == 2


def test_exact_locator_match_bypasses_the_gate():
    # The lawyer named the rule. Metadata hits carry no vector distance, and
    # second-guessing an explicit citation would be the worst possible time to
    # return nothing.
    retriever = _retriever(
        [_hit("c1", "Order 5 Rule 3", 0.99)],
        locators={"Order 5 Rule 3": [("the rule", {"chunk_id": "c9", "locator": "Order 5 Rule 3"})]},
    )

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


def test_default_threshold_does_not_reject_a_document_that_answers_the_question():
    """Regression: the first RETRIEVAL_MAX_DISTANCE shipped here was 0.32,
    fitted to the demo corpus of long formal civil-procedure text, and it
    hard-rejected an on-topic query against a short contract.

    Measured with the real embedding model against the two-sentence contract in
    tests/test_matter_isolation.py: "how long does the exclusivity clause run
    for" scores 0.3205 and "what is the exclusivity period" 0.3721, both
    answered verbatim by the document, while a genuinely unrelated question
    scores 0.5664. Distances in a lawyer's own matter simply sit higher than in
    the demo corpus, so the hard-reject threshold has to clear them.
    """
    from policy_advisor.config import get_settings

    on_topic_in_a_real_matter = 0.3721
    clearly_unrelated = 0.5664
    max_distance = get_settings().retrieval_max_distance

    assert max_distance > on_topic_in_a_real_matter, (
        "the hard-reject threshold would hide a document that answers the question"
    )
    assert max_distance < clearly_unrelated, (
        "the threshold no longer rejects anything, so every query pays for adjudication"
    )


def test_raw_distance_survives_normalization():
    # fused_score is min-max normalized per query, so the top candidate always
    # scores ~1.0 however irrelevant it is. vector_distance is the only field
    # with an absolute meaning, and the gate is worthless if it gets lost.
    retriever = _retriever([_hit("c1", "Order 1 Rule 1", 0.10), _hit("c2", "Order 2 Rule 2", 0.30)])
    results = retriever.retrieve("anything", top_k=8, matter_id="m")

    assert sorted(c.vector_distance for c in results) == [0.10, 0.30]
    assert max(c.fused_score for c in results) == pytest.approx(0.5)
