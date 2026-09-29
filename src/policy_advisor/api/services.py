"""Process-wide handles on the prototype pipeline. Chains and the retriever
load an embedding model and open Chroma, so they are built lazily on first
use and shared for the life of the process (the Streamlit app does the same
with st.cache_resource). Tests replace the attributes with fakes."""

import threading
from typing import Any

from policy_advisor.config import get_settings
from policy_advisor.logging_utils import get_logger


class AdvisorServices:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rag_chain: Any = None
        self._case_chain: Any = None
        self._retriever: Any = None
        self.ingest_lock = threading.Lock()
        self.logger = get_logger("policy_advisor.api", get_settings().log_level)

    def llm_configured(self) -> bool:
        return get_settings().llm_configured()

    @property
    def rag_chain(self):
        with self._lock:
            if self._rag_chain is None:
                from policy_advisor.generation.chain import RAGChain

                self._rag_chain = RAGChain()
            return self._rag_chain

    @property
    def case_chain(self):
        with self._lock:
            if self._case_chain is None:
                from policy_advisor.generation.case_reasoning import CaseReasoningChain

                self._case_chain = CaseReasoningChain()
            return self._case_chain

    @property
    def retriever(self):
        """Used only to resolve cited locators back to retrieved passages."""
        with self._lock:
            if self._retriever is None:
                from policy_advisor.retrieval.hybrid_retriever import HybridRetriever

                self._retriever = HybridRetriever()
            return self._retriever

    def invalidate_matter(self, matter_id: str) -> None:
        """After add/remove document: drop cached BM25 indexes for the matter."""
        with self._lock:
            for holder in (self._rag_chain, self._case_chain, self._retriever):
                if holder is not None:
                    holder.invalidate_matter(matter_id)
