"""Orchestration: hybrid retrieval -> grounded prompt -> Claude, with a
timeout and retry/backoff around the Claude call (docs/06: "timeouts
everywhere an external call happens") and a citation-faithfulness check on
the result before it's returned to the caller."""

from dataclasses import dataclass, field

from langchain_anthropic import ChatAnthropic

from policy_advisor.config import get_settings
from policy_advisor.llm_retry import with_llm_retry
from policy_advisor.generation.faithfulness import check_faithfulness
from policy_advisor.generation.prompt import LANGUAGE_NAMES, PROMPT, format_context
from policy_advisor.generation.relevance_check import judge_relevance
from policy_advisor.generation.web_search import WebSearchCitation, search_official_sources
from policy_advisor.logging_utils import get_logger, log_event, timed_request
from policy_advisor.retrieval.hybrid_retriever import HybridRetriever, RetrievedChunk

NOT_FOUND_MESSAGE = (
    "I couldn't find anything relevant to this in the available rules or judgments. "
    "Please rephrase, narrow the question, or consult the source documents directly."
)

NOT_FOUND_EVEN_ON_WEB_MESSAGE = (
    "I couldn't find this in your documents, and the official sources I'm allowed to search didn't "
    "have a relevant answer either. Please rephrase, narrow the question, or consult a source directly."
)


@dataclass
class AnswerResult:
    answer: str
    retrieved: list[RetrievedChunk]
    faithful: bool
    unsupported_citations: list[str] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    # "corpus": grounded + faithfulness-checked against this matter's own
    # documents (the default path). "web": came from the restricted
    # official-sources fallback instead - never checked the same way, so the
    # UI must render it as a visually distinct, lower-trust block, not a
    # normal chat bubble (see app.py).
    source: str = "corpus"
    web_citations: list[WebSearchCitation] = field(default_factory=list)


class RAGChain:
    def __init__(self):
        settings = get_settings()
        self._settings = settings
        self._retriever = HybridRetriever()
        self._logger = get_logger("policy_advisor.chain", settings.log_level)
        self._llm = ChatAnthropic(
            model=settings.anthropic_model,
            api_key=settings.anthropic_api_key.get_secret_value(),
            max_tokens=1024,
            default_request_timeout=30,
            max_retries=0,  # retried explicitly below, so each attempt is logged
        )

    @with_llm_retry
    def _call_llm(self, messages):
        return self._llm.invoke(messages)

    def invalidate_matter(self, matter_id: str) -> None:
        """Call after add_document/remove_document for this matter, so the
        cached BM25 index doesn't keep serving the pre-update chunk list."""
        self._retriever.invalidate_matter(matter_id)

    def answer(
        self,
        question: str,
        matter_id: str,
        jurisdiction: str | None = None,
        conversation_language: str = "en",
        allow_web_fallback: bool = False,
    ) -> AnswerResult:
        with timed_request(self._logger, question) as fields:
            fields["matter_id"] = matter_id
            fields["conversation_language"] = conversation_language
            retrieved = self._retriever.retrieve(
                question, top_k=self._settings.retrieval_top_k, matter_id=matter_id, jurisdiction=jurisdiction
            )
            fields["retrieved_chunk_ids"] = [c.metadata["chunk_id"] for c in retrieved]
            fields["retrieved_scores"] = [round(c.fused_score, 4) for c in retrieved]

            response_language = LANGUAGE_NAMES.get(conversation_language, "English")

            # Borderline on distance alone - see relevance_check.py for why a
            # single threshold can't carry this decision. Discarding the chunks
            # here routes the question down the same path as "found nothing",
            # which is what it is.
            if retrieved and self._retriever.needs_relevance_adjudication(retrieved):
                verdict = judge_relevance(self._llm, question, retrieved)
                fields["relevance_adjudicated"] = True
                fields["relevance_verdict"] = verdict.relevant
                log_event(
                    self._logger,
                    "relevance_adjudicated",
                    matter_id=matter_id,
                    relevant=verdict.relevant,
                    reason=verdict.reason,
                )
                if not verdict.relevant:
                    retrieved = []

            if not retrieved:
                if not allow_web_fallback:
                    fields["outcome"] = "no_relevant_chunks"
                    return AnswerResult(answer=NOT_FOUND_MESSAGE, retrieved=[], faithful=True, source="none")

                fields["outcome"] = "web_fallback_attempted"
                web_result = search_official_sources(question, response_language=response_language)
                log_event(self._logger, "web_fallback_used", matter_id=matter_id, found=web_result.found)
                if not web_result.found:
                    return AnswerResult(
                        answer=NOT_FOUND_EVEN_ON_WEB_MESSAGE, retrieved=[], faithful=True, source="none"
                    )
                return AnswerResult(
                    answer=web_result.answer,
                    retrieved=[],
                    faithful=True,  # not corpus-checked - "faithful" here just means no citation-check failed
                    source="web",
                    web_citations=web_result.citations,
                )
            messages = PROMPT.format_messages(
                context=format_context(retrieved, response_language=conversation_language),
                question=question,
                response_language=response_language,
            )

            try:
                response = self._call_llm(messages)
            except Exception as exc:
                fields["outcome"] = "llm_call_failed"
                log_event(self._logger, "llm_call_failed", error=str(exc))
                return AnswerResult(
                    answer="The advisor is temporarily unavailable - please try again shortly.",
                    retrieved=retrieved,
                    faithful=True,
                )

            faithful, unsupported = check_faithfulness(response.content, retrieved)
            usage = getattr(response, "usage_metadata", None) or {}
            fields["outcome"] = "answered"
            fields["citations_unsupported"] = unsupported
            fields["token_usage"] = dict(usage)

            return AnswerResult(
                answer=response.content,
                retrieved=retrieved,
                faithful=faithful,
                unsupported_citations=unsupported,
                usage=dict(usage),
            )
