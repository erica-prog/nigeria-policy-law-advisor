"""Cross-encoder reranking (docs/22 slice P1-D, AC-10) with an injected scorer."""

from policy_advisor.retrieval.hybrid_retriever import RetrievedChunk
from policy_advisor.retrieval.rerank import rerank


def _chunk(cid: str, text: str, fused: float) -> RetrievedChunk:
    return RetrievedChunk(text=text, metadata={"chunk_id": cid, "locator": cid}, fused_score=fused)


def _length_scorer(query, passages):
    return [float(len(p)) for p in passages]


def test_rerank_reorders_the_head_and_keeps_the_tail_in_fused_order():
    candidates = [
        _chunk("a", "short", 0.9),
        _chunk("b", "a much longer passage", 0.8),
        _chunk("c", "mid-length", 0.7),
        _chunk("d", "tail stays", 0.1),
    ]
    out = rerank("q", candidates, _length_scorer, top_n=3)
    assert [c.metadata["chunk_id"] for c in out] == ["b", "c", "a", "d"]
    assert out[0].rerank_score == float(len("a much longer passage"))
    assert out[-1].rerank_score is None  # beyond top_n: not rescored


def test_rerank_with_nothing_to_score_is_a_no_op():
    assert rerank("q", [], _length_scorer, top_n=5) == []
