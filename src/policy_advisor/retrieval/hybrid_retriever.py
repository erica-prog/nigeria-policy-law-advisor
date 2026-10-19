"""Hybrid retrieval: dense (vector) search for paraphrased questions, sparse
(BM25) for exact rule/order-number matches that embeddings handle unreliably
(docs/01, docs/03). Candidates from both are min-max normalized and merged
by weighted sum; an optional jurisdiction filter is applied after fusion so a
Federal High Court rule doesn't get treated as interchangeable with a Lagos
Magistrates' Court rule just because both mention the same topic.

Every query is scoped to one matter (CLAUDE-2.md capability 1) - there is no
"search everything" mode. Vector search runs in Postgres against the matter's
rows. BM25 has no database equivalent here, so each matter's keyword index is
built from those same rows, held in memory, and rebuilt when the matter's
content_version changes. Reading the version on every query is one indexed
lookup, and it means an upload is visible to every retriever instance and every
app process - not only the one that happened to be told about it."""

import re
from dataclasses import dataclass, field

from policy_advisor.config import get_settings
from policy_advisor.ingestion.embed import embed_query
from policy_advisor.ingestion.matter_store import load_matter_snapshot, matter_version
from policy_advisor.retrieval import vector_store
from policy_advisor.retrieval.bm25_index import BM25Index, build_bm25_index

_LOCATOR_RE = re.compile(r"order\s+(\d+)\D{1,5}?rule\s+(\d+)", re.IGNORECASE)


@dataclass
class RetrievedChunk:
    text: str
    metadata: dict
    vector_score: float | None = None
    bm25_score: float | None = None
    fused_score: float = field(default=0.0)
    # Raw cosine distance, kept *before* normalization. vector_score
    # and fused_score are min-max normalized per query, so the best candidate
    # always scores ~1.0 however irrelevant it is - they carry ranking, not
    # relevance. This is the only field with an absolute, cross-query meaning,
    # and it's what the relevance gate below tests.
    vector_distance: float | None = None


def _normalize(scores: list[float]) -> list[float]:
    if not scores:
        return []
    lo, hi = min(scores), max(scores)
    if hi - lo < 1e-9:
        return [0.0 for _ in scores]
    return [(s - lo) / (hi - lo) for s in scores]


def _build_locator_index(bm25_index: BM25Index) -> dict[str, list[tuple[str, dict]]]:
    """Locator -> (text, metadata) for the exact-match short-circuit. Plain
    tuples rather than RetrievedChunks, because a cached RetrievedChunk would
    carry one query's fused_score into the next."""
    locator_index: dict[str, list[tuple[str, dict]]] = {}
    for doc in bm25_index.documents:
        locator = doc.metadata.get("locator")
        if locator:
            locator_index.setdefault(locator, []).append((doc.text, doc.metadata))
    return locator_index


@dataclass
class _MatterIndex:
    version: int
    bm25: BM25Index
    locators: dict[str, list[tuple[str, dict]]]

    @property
    def is_empty(self) -> bool:
        return not self.bm25.documents


class HybridRetriever:
    def __init__(
        self,
        vector_weight: float = 0.5,
        bm25_weight: float = 0.5,
        max_distance: float | None = None,
        certain_distance: float | None = None,
    ):
        settings = get_settings()
        self._vector_weight = vector_weight
        self._bm25_weight = bm25_weight
        self._max_distance = settings.retrieval_max_distance if max_distance is None else max_distance
        self._certain_distance = (
            settings.retrieval_certain_distance if certain_distance is None else certain_distance
        )
        self._indexes: dict[str, _MatterIndex] = {}

    def _index_for_matter(self, matter_id: str) -> _MatterIndex:
        cached = self._indexes.get(matter_id)
        if cached is not None and cached.version == matter_version(matter_id):
            return cached
        version, records = load_matter_snapshot(matter_id)
        bm25 = build_bm25_index(records)
        index = _MatterIndex(version=version, bm25=bm25, locators=_build_locator_index(bm25))
        self._indexes[matter_id] = index
        return index

    def invalidate_matter(self, matter_id: str) -> None:
        """Drop this matter's cached indexes now rather than on the next query.

        No longer required for correctness - every query checks the matter's
        content_version and rebuilds when it has moved - but kept so callers can
        release memory or force a rebuild."""
        self._indexes.pop(matter_id, None)

    def _exact_locator_matches(
        self, query: str, index: _MatterIndex, jurisdiction: str | None
    ) -> list[RetrievedChunk]:
        """A lawyer typing "Order 5 Rule 3" wants that rule, not whatever a
        vector/BM25 search over rule *bodies* happens to rank highest - rules
        almost never restate their own number in their own text. Short-circuit
        straight to the named chunk(s) by metadata, skipping fuzzy search."""
        match = _LOCATOR_RE.search(query)
        if not match:
            return []
        locator = f"Order {int(match.group(1))} Rule {int(match.group(2))}"
        candidates = index.locators.get(locator, [])
        if jurisdiction:
            jurisdiction_matches = [c for c in candidates if c[1].get("jurisdiction") == jurisdiction]
            candidates = jurisdiction_matches or candidates
        return [RetrievedChunk(text=text, metadata=metadata, fused_score=1.0) for text, metadata in candidates]

    def retrieve(
        self, query: str, top_k: int, matter_id: str, jurisdiction: str | None = None
    ) -> list[RetrievedChunk]:
        index = self._index_for_matter(matter_id)
        exact_matches = self._exact_locator_matches(query, index, jurisdiction)
        exact_chunk_ids = {c.metadata["chunk_id"] for c in exact_matches}

        candidate_pool = max(top_k * 4, 20)

        by_chunk_id: dict[str, RetrievedChunk] = {}

        # Skip embedding the query for an empty matter - it would load the
        # model, and query the database, for nothing.
        vector_hits = (
            []
            if index.is_empty
            else vector_store.search(matter_id, embed_query(query), k=candidate_pool, jurisdiction=jurisdiction)
        )
        # Distance: lower is more similar. Negate before normalizing so higher
        # always means "better" in the fused score.
        vector_similarities = _normalize([-distance for _, _, distance in vector_hits])
        for (metadata, text, distance), norm_score in zip(vector_hits, vector_similarities):
            by_chunk_id[metadata["chunk_id"]] = RetrievedChunk(
                text=text,
                metadata=metadata,
                vector_score=norm_score,
                vector_distance=distance,
            )

        bm25_hits = index.bm25.search(query, top_k=candidate_pool)
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

        if not exact_matches and not self._clears_relevance_floor(candidates):
            # Nothing in this matter is actually about the question. Returning
            # the least-bad eight chunks here is what made "no relevant
            # results" unrepresentable, and with it the web fallback in
            # chain.py unreachable - it only fires on an empty list.
            return []

        candidates.sort(key=lambda c: c.fused_score, reverse=True)
        return (exact_matches + candidates)[:top_k]

    def _clears_relevance_floor(self, candidates: list[RetrievedChunk]) -> bool:
        """Whether this matter plausibly holds anything relevant to the query.

        Deliberately a query-level gate rather than a per-candidate filter: it
        can only change the outcome for queries whose *best* match is weak, so
        it cannot quietly trim recall on questions the corpus does answer.

        Tested on raw cosine distance because that is the only absolute signal
        available - see the note on RetrievedChunk.vector_distance. BM25-only
        candidates are not rescued here: rank_bm25 scores depend on corpus
        statistics and have no comparable scale across matters, so there is no
        honest absolute threshold for them. The calibration script reports
        whether any known-answerable question would be gated out on that basis.

        This only rejects the clearly-irrelevant. Anything between the two
        thresholds survives to be adjudicated by `needs_relevance_adjudication`.
        """
        if self._max_distance is None:
            return True
        distances = [c.vector_distance for c in candidates if c.vector_distance is not None]
        return bool(distances) and min(distances) <= self._max_distance

    def needs_relevance_adjudication(self, chunks: list[RetrievedChunk]) -> bool:
        """Whether these results are too borderline to trust on distance alone.

        Calibration measured only 0.0007 between the hardest answerable
        question and the easiest unanswerable one, so a single threshold there
        is fitted to the golden set rather than to the corpus. Results in the
        middle band get a cheap Claude call instead of a coin flip - see
        generation/relevance_check.py. Exact locator matches carry no distance
        and are never adjudicated: the lawyer named the rule.
        """
        if self._certain_distance >= self._max_distance:
            return False
        distances = [c.vector_distance for c in chunks if c.vector_distance is not None]
        return bool(distances) and min(distances) > self._certain_distance
