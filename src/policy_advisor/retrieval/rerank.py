"""Cross-encoder reranking (ADR-001 decision 4).

Fusion scores are min-max normalised per query, so they rank candidates
within one search but say nothing comparable across searches, tiers or
matters. A cross-encoder reads the query and each candidate passage together
and returns a relevance score on one scale, which is what lets results from a
lawyer's matter and from the authority library be merged honestly.

The model (`RERANKER_MODEL`, a sentence-transformers CrossEncoder) loads on
first use, once per process. `RERANK_ENABLED=false` disables reranking. Tests
run with it off and pass their own scorer, so no model is loaded."""

from collections.abc import Callable, Sequence
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from policy_advisor.config import get_settings

if TYPE_CHECKING:
    from policy_advisor.retrieval.hybrid_retriever import RetrievedChunk

Scorer = Callable[[str, Sequence[str]], Sequence[float]]


@lru_cache
def _load_cross_encoder(model_name: str) -> Any:
    from sentence_transformers import CrossEncoder

    return CrossEncoder(model_name)


def default_scorer() -> Scorer | None:
    """The configured cross-encoder as a scorer, or None when disabled. The
    model is loaded on the first call, not here."""
    settings = get_settings()
    model_name = settings.reranker_model.strip()
    if not settings.rerank_enabled or not model_name:
        return None

    def score(query: str, passages: Sequence[str]) -> Sequence[float]:
        if not passages:
            return []
        model = _load_cross_encoder(model_name)
        return [float(s) for s in model.predict([(query, passage) for passage in passages])]

    return score


def rerank(
    query: str, candidates: list["RetrievedChunk"], scorer: Scorer, top_n: int
) -> list["RetrievedChunk"]:
    """Re-order the first `top_n` candidates (already in fused order) by the
    scorer; the rest keep their fused order after them. Every rescored chunk
    carries `rerank_score`, the cross-scope relevance scale."""
    head, tail = candidates[:top_n], candidates[top_n:]
    if not head:
        return candidates
    scores = scorer(query, [c.text for c in head])
    for chunk, score in zip(head, scores):
        chunk.rerank_score = float(score)
    head.sort(
        key=lambda c: c.rerank_score if c.rerank_score is not None else float("-inf"), reverse=True
    )
    return head + tail
