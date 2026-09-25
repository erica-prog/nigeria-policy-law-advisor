"""Case-outcome reasoning (CLAUDE-2.md capability 2): identify issues ->
analyze each against retrieved authority -> synthesize an overall position.

Tiered faithfulness checking per the resolved design: the cheap deterministic
citation check runs on every step; one corrective retry follows a failed
check; the expensive Claude-as-judge check runs at step boundaries (each
issue) and on the final synthesis; a step still unresolved after the retry is
flagged low-confidence in the structured output rather than silently passed
through. Every response carries the mandatory disclaimer - never optional,
never just a UI footer."""

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from policy_advisor.config import get_settings
from policy_advisor.generation.case_reasoning_models import (
    CONFIDENCE_FROM_WEB,
    CONFIDENCE_LIMITED,
    CONFIDENCE_NO_AUTHORITY,
    CONFIDENCE_STRONG,
    CONFIDENCE_UNVERIFIED,
    CaseReasoningResult,
    IssueAnalysis,
    IssueArguments,
)
from policy_advisor.generation.case_reasoning_prompt import (
    ARGUMENT_GENERATION_PROMPT,
    ISSUE_IDENTIFICATION_PROMPT,
    SYNTHESIS_PROMPT,
)
from policy_advisor.generation.faithfulness import is_supported_citation
from policy_advisor.generation.judge_check import judge_check
from policy_advisor.generation.prompt import LANGUAGE_NAMES
from policy_advisor.generation.relevance_check import judge_relevance
from policy_advisor.generation.web_search import search_official_sources
from policy_advisor.llm_retry import call_with_retry
from policy_advisor.logging_utils import get_logger, log_event
from policy_advisor.retrieval.hybrid_retriever import HybridRetriever, RetrievedChunk

DISCLAIMER = (
    "This is a research aid, not a prediction of any court's actual decision and not a substitute for "
    "your own professional judgment or independent research. It assesses how strongly each side's "
    "position is supported by the authorities retrieved from this matter's corpus only."
)


class IssueList(BaseModel):
    issues: list[str]


def _cited_locators(issue_args: IssueArguments) -> list[str]:
    return [
        authority.locator
        for argument in issue_args.arguments
        for authority in argument.supporting_authorities
    ]


def _unsupported_citations(issue_args: IssueArguments, available_locators: set[str]) -> list[str]:
    return [loc for loc in _cited_locators(issue_args) if not is_supported_citation(loc, available_locators)]


def _issue_args_as_text(issue_args: IssueArguments) -> str:
    lines = [f"Issue: {issue_args.issue}"]
    for argument in issue_args.arguments:
        cites = ", ".join(a.locator for a in argument.supporting_authorities) or "none"
        lines.append(f"- [{argument.side}] {argument.summary} (cites: {cites})")
    lines.append(f"Assessment: {issue_args.assessment}")
    return "\n".join(lines)


class CaseReasoningChain:
    def __init__(self):
        settings = get_settings()
        self._settings = settings
        self._retriever = HybridRetriever()
        self._logger = get_logger("policy_advisor.case_reasoning", settings.log_level)
        self._llm = ChatAnthropic(
            model=settings.anthropic_model,
            api_key=settings.anthropic_api_key.get_secret_value(),
            max_tokens=2048,
            default_request_timeout=45,
            max_retries=0,
        )

    @property
    def retriever(self) -> HybridRetriever:
        """Exposed so the advisory layer can reuse this retriever rather than
        constructing a second one - a new HybridRetriever reloads the embedding
        model and rebuilds the per-matter BM25 cache from scratch."""
        return self._retriever

    def _identify_issues(self, case_facts: str, response_language: str) -> list[str]:
        structured = self._llm.with_structured_output(IssueList)
        messages = ISSUE_IDENTIFICATION_PROMPT.format_messages(
            case_facts=case_facts, response_language=response_language
        )
        result: IssueList = call_with_retry(lambda: structured.invoke(messages))
        return result.issues

    def _generate_issue_arguments(
        self, case_facts: str, issue: str, context: str, response_language: str, correction: str | None = None
    ) -> IssueArguments:
        messages = ARGUMENT_GENERATION_PROMPT.format_messages(
            case_facts=case_facts, issue=issue, context=context, response_language=response_language
        )
        if correction:
            # Self-contained correction (not relying on message-history replay
            # of the prior structured-output call, which doesn't round-trip
            # cleanly through tool-calling-based structured output).
            messages.append(HumanMessage(content=correction))
        structured = self._llm.with_structured_output(IssueArguments)
        return call_with_retry(lambda: structured.invoke(messages))

    def _issue_from_web(self, issue: str, response_language: str) -> IssueAnalysis:
        """This matter has no authority on the issue, so consult the allowlisted
        official sources instead.

        The result deliberately produces no `arguments` and no
        `supporting_authorities`. Everything in those fields has survived the
        citation check against passages the lawyer chose to put in the matter;
        web material has not been checked that way and must not arrive wearing
        the same clothes. It is also never handed to `judge_check` - the judge
        decides whether text stays within its authorities, so passing web text
        as an authority would have it certify the very thing that was never
        vetted.
        """
        web_result = search_official_sources(issue, response_language=response_language)
        log_event(self._logger, "issue_web_fallback_used", issue=issue, found=web_result.found)

        if not web_result.found:
            return IssueAnalysis(
                issue=issue,
                arguments=[],
                assessment=(
                    "No authority in this matter addresses this issue, and the official sources "
                    "available to search had nothing relevant either."
                ),
                confidence=CONFIDENCE_NO_AUTHORITY,
            )

        return IssueAnalysis(
            issue=issue,
            arguments=[],
            # Written here rather than generated, so a model can't soften it.
            assessment=(
                "No authority in this matter addresses this issue. The summary below came from an "
                "official source on the web and has not been checked against this matter's "
                "documents - verify it directly before relying on it."
            ),
            confidence=CONFIDENCE_FROM_WEB,
            web_summary=web_result.answer,
            web_sources=web_result.citations,
        )

    def _analyze_issue(
        self,
        case_facts: str,
        issue: str,
        matter_id: str,
        jurisdiction: str | None,
        response_language: str,
        allow_web_fallback: bool = False,
    ) -> IssueAnalysis:
        retrieved: list[RetrievedChunk] = self._retriever.retrieve(
            issue, top_k=self._settings.retrieval_top_k, matter_id=matter_id, jurisdiction=jurisdiction
        )

        if retrieved and self._retriever.needs_relevance_adjudication(retrieved):
            verdict = judge_relevance(self._llm, issue, retrieved)
            log_event(
                self._logger,
                "issue_relevance_adjudicated",
                issue=issue,
                relevant=verdict.relevant,
                reason=verdict.reason,
            )
            if not verdict.relevant:
                retrieved = []

        if not retrieved and allow_web_fallback:
            return self._issue_from_web(issue, response_language)

        available_locators = {c.metadata["locator"] for c in retrieved}
        context = "\n\n".join(f"[{c.metadata['locator']}]\n{c.text}" for c in retrieved) or "(no authorities retrieved)"

        issue_args = self._generate_issue_arguments(case_facts, issue, context, response_language)
        unsupported = _unsupported_citations(issue_args, available_locators)
        unverified = False

        if unsupported:
            log_event(self._logger, "issue_check_failed_retrying", issue=issue, unsupported=unsupported)
            correction = (
                f"Your previous answer cited {unsupported}, which are not in the provided context. "
                "Revise your answer and cite ONLY locators that appear in the context passages above."
            )
            issue_args = self._generate_issue_arguments(
                case_facts, issue, context, response_language, correction=correction
            )
            unsupported = _unsupported_citations(issue_args, available_locators)
            if unsupported:
                unverified = True
                log_event(self._logger, "issue_check_failed_after_retry", issue=issue, unsupported=unsupported)

        judge_outcome = judge_check(self._llm, _issue_args_as_text(issue_args), retrieved)
        if judge_outcome.judge_flagged:
            unverified = True
            log_event(self._logger, "issue_judge_flagged", issue=issue, notes=judge_outcome.judge_notes)

        if not retrieved:
            confidence = CONFIDENCE_NO_AUTHORITY
        elif unverified:
            confidence = CONFIDENCE_UNVERIFIED
        elif any(arg.supporting_authorities for arg in issue_args.arguments):
            confidence = CONFIDENCE_STRONG
        else:
            confidence = CONFIDENCE_LIMITED

        return IssueAnalysis(
            issue=issue_args.issue,
            arguments=issue_args.arguments,
            assessment=issue_args.assessment,
            confidence=confidence,
            unverified=unverified,
            retrieved_locators=sorted(available_locators),
        )

    def _synthesize(
        self,
        case_facts: str,
        issues: list[IssueAnalysis],
        matter_id: str,
        response_language: str,
        jurisdiction: str | None = None,
    ) -> str:
        # Web-backed issues are labelled rather than summarised into the prompt.
        # Including the web text would let the overall position absorb it as if
        # it were authority, and the judge pass below - which checks the
        # synthesis against the *corpus* chunks - would then flag the whole
        # summary for material it was handed on purpose.
        issue_summaries = "\n\n".join(
            f"Issue: {i.issue}\nConfidence: {i.confidence}\nAssessment: {i.assessment}"
            + (
                "\nAuthority source: an official website, NOT this matter's documents. Describe this "
                "issue as unresolved on the available authority; do not treat it as established."
                if i.from_web
                else ""
            )
            for i in issues
        )
        messages = SYNTHESIS_PROMPT.format_messages(
            case_facts=case_facts, issue_summaries=issue_summaries, response_language=response_language
        )
        overall_position = call_with_retry(lambda: self._llm.invoke(messages)).content

        # Final synthesis is checked against the union of every issue's own
        # retrieved authorities - it must only restate what the per-issue steps
        # already grounded, not introduce anything new. The jurisdiction filter
        # has to match what those steps actually used: without it this widens
        # to other jurisdictions' rules and the judge ends up assessing the
        # synthesis against authorities the analysis never had, flagging
        # citations that were properly supported at the time.
        all_chunks: list[RetrievedChunk] = []
        for issue in issues:
            if issue.from_web:
                continue
            all_chunks.extend(
                self._retriever.retrieve(
                    issue.issue,
                    top_k=self._settings.retrieval_top_k,
                    matter_id=matter_id,
                    jurisdiction=jurisdiction,
                )
            )
        judge_outcome = judge_check(self._llm, overall_position, all_chunks)
        if judge_outcome.judge_flagged:
            log_event(self._logger, "synthesis_judge_flagged", notes=judge_outcome.judge_notes)
            overall_position += (
                "\n\n[Faithfulness check flagged this summary - one or more issue analyses above may state "
                "this more precisely; treat this overall summary with extra caution.]"
            )
        return overall_position

    def analyze(
        self,
        case_facts: str,
        matter_id: str,
        jurisdiction: str | None = None,
        conversation_language: str = "en",
        allow_web_fallback: bool = False,
    ) -> CaseReasoningResult:
        response_language = LANGUAGE_NAMES.get(conversation_language, "English")
        log_event(self._logger, "case_analysis_started", matter_id=matter_id, conversation_language=conversation_language)
        issues = self._identify_issues(case_facts, response_language)
        issue_analyses = [
            self._analyze_issue(
                case_facts, issue, matter_id, jurisdiction, response_language, allow_web_fallback
            )
            for issue in issues
        ]
        overall_position = self._synthesize(
            case_facts, issue_analyses, matter_id, response_language, jurisdiction
        )
        web_backed = sum(1 for i in issue_analyses if i.from_web)
        log_event(
            self._logger,
            "case_analysis_completed",
            matter_id=matter_id,
            issue_count=len(issue_analyses),
            unverified_count=sum(1 for i in issue_analyses if i.unverified),
            web_backed_issue_count=web_backed,
        )
        disclaimer = DISCLAIMER
        if web_backed:
            disclaimer += (
                f" {web_backed} of these issues had no authority in this matter and were answered from "
                "an official website instead; those are labelled and have not been checked against "
                "your documents."
            )
        return CaseReasoningResult(
            case_facts=case_facts,
            issues=issue_analyses,
            overall_position=overall_position,
            disclaimer=disclaimer,
            jurisdiction=jurisdiction,
        )
