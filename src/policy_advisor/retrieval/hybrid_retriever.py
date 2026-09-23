"""Hybrid retrieval: dense (vector) search for paraphrased questions, sparse
(BM25) for exact rule/order-number matches that embeddings handle unreliably
(docs/01, docs/03). Candidates from both are min-max normalized and merged
by weighted sum; an optional jurisdiction filter is applied after fusion so a
Federal High Court rule doesn't get treated as interchangeable with a Lagos
Magistrates' Court rule just because both mention the same topic.

Every query is scoped to one matter (CLAUDE-2.md capability 1) - there is no
"search everything" mode. The vector store is one shared Chroma collection
filtered by `matter_id` at query time; BM25 is a separate in-memory index per
matter, built lazily and cached, since it has no native metadata filter and
its IDF statistics shouldn't mix across unrelated matters' documents."""

import re
from dataclasses import dataclass, field

from policy_advisor.retrieval.bm25_index import BM25Index, load_bm25_index
from policy_advisor.retrieval.vector_store import load_vector_store

_LOCATOR_RE = re.compile(r"order\s+(\d+)\D{1,5}?rule\s+(\d+)", re.IGNORECASE)


@dataclass
class RetrievedChunk:
    text: str
    metadata: dict
    vector_score: float | None = None
    bm25_score: float | None = None
    fused_score: float = field(default=0.0)


def _normalize(scores: list[float]) -> list[float]:
    if not scores:
        return []
    lo, hi = min(scores), max(scores)
    if hi - lo < 1e-9:
        return [0.0 for _ in scores]
    return [(s - lo) / (hi - lo) for s in scores]


def _build_locator_index(bm25_index: BM25Index) -> dict[str, list[RetrievedChunk]]:
    locator_index: dict[str, list[RetrievedChunk]] = {}
    for doc in bm25_index.documents:
        locator = doc.metadata.get("locator")
        if locator:
            locator_index.setdefault(locator, []).append(RetrievedChunk(text=doc.text, metadata=doc.metadata))
    return locator_index


class HybridRetriever:
    def __init__(self, vector_weight: float = 0.5, bm25_weight: float = 0.5):
        self._vector_store = load_vector_store()
        self._vector_weight = vector_weight
        self._bm25_weight = bm25_weight
        self._bm25_by_matter: dict[str, BM25Index] = {}
        self._locator_index_by_matter: dict[str, dict[str, list[RetrievedChunk]]] = {}

    def _bm25_for_matter(self, matter_id: str) -> BM25Index:
        if matter_id not in self._bm25_by_matter:
            self._bm25_by_matter[matter_id] = load_bm25_index(matter_id)
        return self._bm25_by_matter[matter_id]

    def _locator_index_for_matter(self, matter_id: str) -> dict[str, list[RetrievedChunk]]:
        if matter_id not in self._locator_index_by_matter:
            self._locator_index_by_matter[matter_id] = _build_locator_index(self._bm25_for_matter(matter_id))
        return self._locator_index_by_matter[matter_id]

    def invalidate_matter(self, matter_id: str) -> None:
        """Call after add_document/remove_document so a stale in-memory BM25
        cache doesn't keep serving the pre-update chunk list for this matter."""
        self._bm25_by_matter.pop(matter_id, None)
        self._locator_index_by_matter.pop(matter_id, None)

    def _exact_locator_matches(self, query: str, matter_id: str, jurisdiction: str | None) -> list[RetrievedChunk]:
        """A lawyer typing "Order 5 Rule 3" wants that rule, not whatever a
        vector/BM25 search over rule *bodies* happens to rank highest - rules
        almost never restate their own number in their own text. Short-circuit
        straight to the named chunk(s) by metadata, skipping fuzzy search."""
        match = _LOCATOR_RE.search(query)
        if not match:
            return []
        locator = f"Order {int(match.group(1))} Rule {int(match.group(2))}"
        candidates = self._locator_index_for_matter(matter_id).get(locator, [])
        if jurisdiction:
            jurisdiction_matches = [c for c in candidates if c.metadata.get("jurisdiction") == jurisdiction]
            candidates = jurisdiction_matches or candidates
        for candidate in candidates:
            candidate.fused_score = 1.0
        return candidates

    def _vector_filter(self, matter_id: str, jurisdiction: str | None) -> dict:
        if jurisdiction:
            return {"$and": [{"matter_id": matter_id}, {"jurisdiction": jurisdiction}]}
        return {"matter_id": matter_id}

    def retrieve(
        self, query: str, top_k: int, matter_id: str, jurisdiction: str | None = None
    ) -> list[RetrievedChunk]:
        exact_matches = self._exact_locator_matches(query, matter_id, jurisdiction)
        exact_chunk_ids = {c.metadata["chunk_id"] for c in exact_matches}

        candidate_pool = max(top_k * 4, 20)

        by_chunk_id: dict[str, RetrievedChunk] = {}

        # Chroma's similarity_search_with_score returns distance (lower = more
        # similar); negate before normalizing so higher always means "better"
        # in the fused score, regardless of the underlying metric.
        vector_hits = self._vector_store.similarity_search_with_score(
            query, k=candidate_pool, filter=self._vector_filter(matter_id, jurisdiction)
        )
        vector_similarities = _normalize([-distance for _, distance in vector_hits])
        for (doc, _distance), norm_score in zip(vector_hits, vector_similarities):
            chunk_id = doc.metadata["chunk_id"]
            by_chunk_id[chunk_id] = RetrievedChunk(
                text=doc.page_content, metadata=doc.metadata, vector_score=norm_score
            )

        bm25_hits = self._bm25_for_matter(matter_id).search(query, top_k=candidate_pool)
        bm25_scores = _normalize([score for _, score in bm25_hits])
        for (bm25_doc, _raw_score), norm_score in zip(bm25_hits, bm25_scores):
            chunk_id = bm25_doc.metadata["chunk_id"]
            existing = by_chunk_id.get(chunk_id)
            if existing:
                existing.bm25_score = norm_score
            else:
                by_chunk_id[chunk_id] = RetrievedChunk(
                    text=bm25_doc.text, metadata=bm25_doc.metadata, bm25_score=norm_score
                )

        candidates = list(by_chunk_id.values())
        for candidate in candidates:
            candidate.fused_score = self._vector_weight * (candidate.vector_score or 0.0) + (
                self._bm25_weight * (candidate.bm25_score or 0.0)
            )

        if jurisdiction:
            filtered = [c for c in candidates if c.metadata.get("jurisdiction") == jurisdiction]
            if filtered:
                candidates = filtered

        candidates = [c for c in candidates if c.metadata["chunk_id"] not in exact_chunk_ids]
        candidates.sort(key=lambda c: c.fused_score, reverse=True)
        return (exact_matches + candidates)[:top_k]
