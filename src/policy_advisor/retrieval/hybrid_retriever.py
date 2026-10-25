"""Hybrid retrieval: dense (vector) search for paraphrased questions, sparse
(BM25) for exact rule/order-number matches that embeddings handle unreliably
(docs/01, docs/03). Candidates from both are min-max normalized and merged
by weighted sum; an optional jurisdiction filter is applied after fusion so a
Federal High Court rule doesn't get treated as interchangeable with a Lagos
Magistrates' Court rule just because both mention the same topic.

Every query is scoped to one matter (CLAUDE-2.md capability 1) - there is no
"search everything" mode. Vector search runs in Postgres against the matter's
rows. Keyword search has two backends chosen per matter by size
(`KEYWORD_INDEX_MAX_CHUNKS`): a BM25 index built in memory from the matter's
rows and rebuilt when its content_version changes, or Postgres full-text
search (migration 0004) for matters too large to hold in memory - the
authority tier and bulk-analysis matters. Reading the matter's version, tier
and size on every query is one indexed lookup, and it means an upload is
visible to every retriever instance and every app process - not only the one
that happened to be told about it."""

import re
from dataclasses import dataclass, field

from policy_advisor.config import get_settings
from policy_advisor.ingestion.embed import embed_query
from policy_advisor.ingestion.matter_store import load_matter_snapshot, matter_summary
from policy_advisor.jurisdictions import storage_values
from policy_advisor.retrieval import vector_store
from policy_advisor.retrieval.bm25_index import BM25Index, build_bm25_index
from policy_advisor.telemetry import span
from policy_advisor.retrieval.rerank import Scorer, default_scorer, rerank

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
    # Cross-encoder relevance (retrieval/rerank.py): one scale across queries,
    # tiers and matters, unlike the per-query normalised scores above.
    rerank_score: float | None = None


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
    tier: str
    chunk_count: int
    # "bm25": `bm25` and `locators` are populated from an in-memory snapshot.
    # "fts": both are None and every lookup goes to the database.
    backend: str
    bm25: BM25Index | None = None
    locators: dict[str, list[tuple[str, dict]]] | None = None

    @property
    def is_empty(self) -> bool:
        return self.chunk_count == 0


class HybridRetriever:
    def __init__(
        self,
        vector_weight: float = 0.5,
        bm25_weight: float = 0.5,
        max_distance: float | None = None,
        certain_distance: float | None = None,
        keyword_index_max_chunks: int | None = None,
        scorer: Scorer | None = None,
        rerank_top_n: int | None = None,
    ):
        settings = get_settings()
        # Per-tier gate thresholds: the two constructor values are the base;
        # RETRIEVAL_GATE_OVERRIDES refines them for a tier.
        self._gate_overrides = settings.retrieval_gate_overrides
        self._scorer = scorer if scorer is not None else default_scorer()
        self._rerank_top_n = settings.rerank_top_n if rerank_top_n is None else rerank_top_n
        self._vector_weight = vector_weight
        self._bm25_weight = bm25_weight
        self._keyword_index_max_chunks = (
            settings.keyword_index_max_chunks
            if keyword_index_max_chunks is None
            else keyword_index_max_chunks
        )
        self._max_distance = (
            settings.retrieval_max_distance if max_distance is None else max_distance
        )
        self._certain_distance = (
            settings.retrieval_certain_distance if certain_distance is None else certain_distance
        )
        self._indexes: dict[str, _MatterIndex] = {}

    def _index_for_matter(self, matter_id: str) -> _MatterIndex:
        summary = matter_summary(matter_id)
        cached = self._indexes.get(matter_id)
        if cached is not None and cached.version == summary.version:
            return cached
        if summary.chunk_count <= self._keyword_index_max_chunks:
            version, records = load_matter_snapshot(matter_id)
            bm25 = build_bm25_index(records)
            index = _MatterIndex(
                version=version,
                tier=summary.tier,
                chunk_count=len(records),
                backend="bm25",
                bm25=bm25,
                locators=_build_locator_index(bm25),
            )
        else:
            index = _MatterIndex(
                version=summary.version,
                tier=summary.tier,
                chunk_count=summary.chunk_count,
                backend="fts",
            )
        self._indexes[matter_id] = index
        return index

    def keyword_backend(self, matter_id: str) -> str:
        """Which keyword backend this matter is searched with right now."""
        return self._index_for_matter(matter_id).backend

    def invalidate_matter(self, matter_id: str) -> None:
        """Drop this matter's cached indexes now rather than on the next query.

        No longer required for correctness - every query checks the matter's
        content_version and rebuilds when it has moved - but kept so callers can
        release memory or force a rebuild."""
        self._indexes.pop(matter_id, None)

    def _exact_locator_matches(
        self, query: str, index: _MatterIndex, matter_id: str, jurisdiction: str | None
    ) -> list[RetrievedChunk]:
        """A lawyer typing "Order 5 Rule 3" wants that rule, not whatever a
        vector/BM25 search over rule *bodies* happens to rank highest - rules
        almost never restate their own number in their own text. Short-circuit
        straight to the named chunk(s) by metadata, skipping fuzzy search."""
        match = _LOCATOR_RE.search(query)
        if not match:
            return []
        locator = f"Order {int(match.group(1))} Rule {int(match.group(2))}"
        if index.locators is not None:
            candidates = index.locators.get(locator, [])
        else:
            candidates = vector_store.locator_matches(matter_id, locator)
        values = storage_values(jurisdiction)
        if values:
            jurisdiction_matches = [c for c in candidates if c[1].get("jurisdiction") in values]
            candidates = jurisdiction_matches or candidates
        return [
            RetrievedChunk(text=text, metadata=metadata, fused_score=1.0)
            for text, metadata in candidates
        ]

    def retrieve(
        self, query: str, top_k: int, matter_id: str, jurisdiction: str | None = None
    ) -> list[RetrievedChunk]:
        with span(
            "retrieval", matter_id=matter_id, top_k=top_k, jurisdiction=jurisdiction
        ) as current:
            chunks = self._retrieve(query, top_k, matter_id, jurisdiction)
            current.set_attribute("results", len(chunks))
            return chunks

    def _retrieve(
        self, query: str, top_k: int, matter_id: str, jurisdiction: str | None = None
    ) -> list[RetrievedChunk]:
        index = self._index_for_matter(matter_id)
        if index.is_empty:
            # Nothing to search: don't load the embedding model or hit the database.
            return []
        exact_matches = self._exact_locator_matches(query, index, matter_id, jurisdiction)
        exact_chunk_ids = {c.metadata["chunk_id"] for c in exact_matches}

        candidate_pool = max(top_k * 4, 20)

        by_chunk_id: dict[str, RetrievedChunk] = {}

        vector_hits = vector_store.search(
            matter_id,
            embed_query(query),
            k=candidate_pool,
            jurisdiction=jurisdiction,
            tier=index.tier,
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

        # Keyword hits as (metadata, text, score) from whichever backend this
        # matter uses; both scores are relative within the query, so the same
        # min-max normalisation applies before fusion.
        if index.bm25 is not None:
            keyword_hits = [
                (doc.metadata, doc.text, score)
                for doc, score in index.bm25.search(query, top_k=candidate_pool)
            ]
        else:
            keyword_hits = vector_store.keyword_search(
                matter_id, query, k=candidate_pool, jurisdiction=jurisdiction
            )
        if index.bm25 is not None:
            keyword_scores = _normalize([score for _, _, score in keyword_hits])
        else:
            # Full-text search returns only rows that match, so a lone hit is
            # the best match, not a tie at zero: scale by the top rank (ranks
            # are non-negative) rather than min-max over the matches.
            top_rank = max((score for _, _, score in keyword_hits), default=0.0)
            keyword_scores = [
                score / top_rank if top_rank > 0 else 1.0 for _, _, score in keyword_hits
            ]
        for (metadata, text, _raw_score), norm_score in zip(keyword_hits, keyword_scores):
            chunk_id = metadata["chunk_id"]
            existing = by_chunk_id.get(chunk_id)
            if existing:
                existing.bm25_score = norm_score
            else:
                by_chunk_id[chunk_id] = RetrievedChunk(
                    text=text, metadata=metadata, bm25_score=norm_score
                )

        candidates = list(by_chunk_id.values())
        for candidate in candidates:
            candidate.fused_score = self._vector_weight * (candidate.vector_score or 0.0) + (
                self._bm25_weight * (candidate.bm25_score or 0.0)
            )

        values = storage_values(jurisdiction)
        if values:
            filtered = [c for c in candidates if c.metadata.get("jurisdiction") in values]
            if filtered:
                candidates = filtered

        candidates = [c for c in candidates if c.metadata["chunk_id"] not in exact_chunk_ids]

        if not exact_matches and not self._clears_relevance_floor(candidates, index.tier):
            # Nothing in this matter is actually about the question. Returning
            # the least-bad eight chunks here is what made "no relevant
            # results" unrepresentable, and with it the web fallback in
            # chain.py unreachable - it only fires on an empty list.
            return []

        candidates.sort(key=lambda c: c.fused_score, reverse=True)
        if self._scorer is not None:
            candidates = rerank(query, candidates, self._scorer, self._rerank_top_n)
        return (exact_matches + candidates)[:top_k]

    def retrieve_widening(
        self,
        query: str,
        top_k: int,
        matter_id: str,
        jurisdiction: str | None = None,
        library_matters: list[str] | None = None,
    ) -> tuple[list[RetrievedChunk], str | None]:
        """The scope ladder (ADR-001 decision 4): the matter first; when its
        gate says nothing there is about the question, each library collection
        in turn. Returns the chunks and the scope they came from ("matter",
        "library", or None when nothing anywhere clears the gate). Chunks keep
        their own `matter_id` and `tier` metadata, so the caller can label
        library passages as authority."""
        chunks = self.retrieve(query, top_k, matter_id, jurisdiction)
        if chunks:
            return chunks, "matter"
        for library in library_matters or []:
            if library == matter_id:
                continue
            chunks = self.retrieve(query, top_k, library, jurisdiction)
            if chunks:
                return chunks, "library"
        return [], None

    def gate_for(self, tier: str | None) -> tuple[float, float]:
        """(certain, max) distance thresholds for a tier."""
        override = self._gate_overrides.get(tier or "", {})
        return (
            float(override.get("certain", self._certain_distance)),
            float(override.get("max", self._max_distance)),
        )

    def _clears_relevance_floor(
        self, candidates: list[RetrievedChunk], tier: str | None = None
    ) -> bool:
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
        _, max_distance = self.gate_for(tier)
        distances = [c.vector_distance for c in candidates if c.vector_distance is not None]
        return bool(distances) and min(distances) <= max_distance

    def needs_relevance_adjudication(self, chunks: list[RetrievedChunk]) -> bool:
        """Whether these results are too borderline to trust on distance alone.

        Calibration measured only 0.0007 between the hardest answerable
        question and the easiest unanswerable one, so a single threshold there
        is fitted to the golden set rather than to the corpus. Results in the
        middle band get a cheap Claude call instead of a coin flip - see
        generation/relevance_check.py. Exact locator matches carry no distance
        and are never adjudicated: the lawyer named the rule.
        """
        tier = chunks[0].metadata.get("tier") if chunks else None
        certain, max_distance = self.gate_for(tier)
        if certain >= max_distance:
            return False
        distances = [c.vector_distance for c in chunks if c.vector_distance is not None]
        return bool(distances) and min(distances) > certain
