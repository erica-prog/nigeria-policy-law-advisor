"""Process-wide handles on the prototype pipeline. The retriever loads an
embedding model and opens Chroma, so it is built lazily on first use and
shared for the life of the process (the Streamlit app does the same with
st.cache_resource). Chains hold a Claude client, and since revision 3 each
web user thinks with their own key, so chains are cached per key
fingerprint and all share the one retriever.

Tests replace `_rag_chain` / `_case_chain` / `_retriever` with fakes (one
fake serves every key), or set `rag_chain_factory` / `case_chain_factory`
to observe which key each user's request was built with."""

import threading
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from policy_advisor.api.user_keys import AttemptLimiter, key_fingerprint
from policy_advisor.config import get_settings
from policy_advisor.logging_utils import get_logger

# Distinct keys whose chains stay warm; beyond this the least recently used
# chain is dropped (its retriever is shared, so rebuilding is cheap).
MAX_CACHED_CHAINS = 32


class AdvisorServices:
    def __init__(self) -> None:
        # Re-entrant: building a chain under the lock reads `self.retriever`.
        self._lock = threading.RLock()
        self._rag_chain: Any = None
        self._case_chain: Any = None
        self._retriever: Any = None
        self._rag_chains: OrderedDict[str, Any] = OrderedDict()
        self._case_chains: OrderedDict[str, Any] = OrderedDict()
        self.rag_chain_factory: Callable[[str], Any] | None = None
        self.case_chain_factory: Callable[[str], Any] | None = None
        self.ingest_lock = threading.Lock()
        self.key_attempts = AttemptLimiter()
        self.logger = get_logger("policy_advisor.api", get_settings().log_level)

    def llm_configured(self) -> bool:
        """Server-level view (for /api/health): is there a server key at all?"""
        return get_settings().llm_configured()

    @property
    def retriever(self):
        """Shared by every chain; also used to resolve cited locators."""
        with self._lock:
            if self._retriever is None:
                from policy_advisor.retrieval.hybrid_retriever import HybridRetriever

                self._retriever = HybridRetriever()
            return self._retriever

    def _build_rag_chain(self, api_key: str):
        if self.rag_chain_factory is not None:
            return self.rag_chain_factory(api_key)
        from policy_advisor.generation.chain import RAGChain

        return RAGChain(api_key=api_key, retriever=self.retriever)

    def _build_case_chain(self, api_key: str):
        if self.case_chain_factory is not None:
            return self.case_chain_factory(api_key)
        from policy_advisor.generation.case_reasoning import CaseReasoningChain

        return CaseReasoningChain(api_key=api_key, retriever=self.retriever)

    @staticmethod
    def _cached(cache: OrderedDict[str, Any], api_key: str, build: Callable[[str], Any]):
        fingerprint = key_fingerprint(api_key)
        chain = cache.get(fingerprint)
        if chain is None:
            chain = build(api_key)
            cache[fingerprint] = chain
            while len(cache) > MAX_CACHED_CHAINS:
                cache.popitem(last=False)
        else:
            cache.move_to_end(fingerprint)
        return chain

    def rag_chain_for(self, api_key: str):
        """RAGChain bound to `api_key` (the requesting user's key)."""
        with self._lock:
            if self._rag_chain is not None:
                return self._rag_chain
            return self._cached(self._rag_chains, api_key, self._build_rag_chain)

    def case_chain_for(self, api_key: str):
        """CaseReasoningChain bound to `api_key` (the requesting user's key)."""
        with self._lock:
            if self._case_chain is not None:
                return self._case_chain
            return self._cached(self._case_chains, api_key, self._build_case_chain)

    def invalidate_matter(self, matter_id: str) -> None:
        """After add/remove document: drop cached BM25 indexes for the matter."""
        with self._lock:
            holders = [self._rag_chain, self._case_chain, self._retriever]
            holders.extend(self._rag_chains.values())
            holders.extend(self._case_chains.values())
            for holder in holders:
                # Chains built before this method existed, and test doubles,
                # must not turn a successful upload or deletion into a 500.
                invalidate = getattr(holder, "invalidate_matter", None)
                if invalidate is not None:
                    invalidate(matter_id)
