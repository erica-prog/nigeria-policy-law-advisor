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
from policy_advisor.generation.case_reasoning_models import CaseReasoningResult, IssueAnalysis, IssueArguments
from policy_advisor.generation.case_reasoning_prompt import (
    ARGUMENT_GENERATION_PROMPT,
    ISSUE_IDENTIFICATION_PROMPT,
    SYNTHESIS_PROMPT,
)
from policy_advisor.generation.faithfulness import is_supported_citation
from policy_advisor.generation.judge_check import judge_check
from policy_advisor.generation.prompt import LANGUAGE_NAMES
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

    def _identify_issues(self, case_facts: str, response_language: str) -> list[str]:
        structured = self._llm.with_structured_output(IssueList)
        messages = ISSUE_IDENTIFICATION_PROMPT.format_messages(
            case_facts=case_facts, response_language=response_language
        )
        result: IssueList = structured.invoke(messages)
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
        return structured.invoke(messages)

    def _analyze_issue(
        self, case_facts: str, issue: str, matter_id: str, jurisdiction: str | None, response_language: str
    ) -> IssueAnalysis:
        retrieved: list[RetrievedChunk] = self._retriever.retrieve(
            issue, top_k=self._settings.retrieval_top_k, matter_id=matter_id, jurisdiction=jurisdiction
        )
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
            confidence = "no authority found in corpus"
        elif unverified:
            confidence = "unverified - flagged by faithfulness check"
        elif any(arg.supporting_authorities for arg in issue_args.arguments):
            confidence = "strongly supported"
        else:
            confidence = "plausible, limited authority"

        return IssueAnalysis(
            issue=issue_args.issue,
            arguments=issue_args.arguments,
            assessment=issue_args.assessment,
            confidence=confidence,
            unverified=unverified,
        )

    def _synthesize(
        self, case_facts: str, issues: list[IssueAnalysis], matter_id: str, response_language: str
    ) -> str:
        issue_summaries = "\n\n".join(
            f"Issue: {i.issue}\nConfidence: {i.confidence}\nAssessment: {i.assessment}" for i in issues
        )
        messages = SYNTHESIS_PROMPT.format_messages(
            case_facts=case_facts, issue_summaries=issue_summaries, response_language=response_language
        )
        overall_position = self._llm.invoke(messages).content

        # Final synthesis is checked against the union of every issue's own
        # retrieved authorities, not a fresh retrieval pass - it must only
        # restate what the per-issue steps already grounded, not introduce
        # anything new.
        all_chunks: list[RetrievedChunk] = []
        for issue in issues:
            all_chunks.extend(
                self._retriever.retrieve(issue.issue, top_k=self._settings.retrieval_top_k, matter_id=matter_id)
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
    ) -> CaseReasoningResult:
        response_language = LANGUAGE_NAMES.get(conversation_language, "English")
        log_event(self._logger, "case_analysis_started", matter_id=matter_id, conversation_language=conversation_language)
        issues = self._identify_issues(case_facts, response_language)
        issue_analyses = [
            self._analyze_issue(case_facts, issue, matter_id, jurisdiction, response_language) for issue in issues
        ]
        overall_position = self._synthesize(case_facts, issue_analyses, matter_id, response_language)
        log_event(
            self._logger,
            "case_analysis_completed",
            matter_id=matter_id,
            issue_count=len(issue_analyses),
            unverified_count=sum(1 for i in issue_analyses if i.unverified),
        )
        return CaseReasoningResult(
            case_facts=case_facts,
            issues=issue_analyses,
            overall_position=overall_position,
            disclaimer=DISCLAIMER,
        )
