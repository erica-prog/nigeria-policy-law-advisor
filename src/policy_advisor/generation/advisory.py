"""Advisory mode: a direct recommendation and an outcome assessment.

Deliberately additive. `CaseReasoningChain.analyze()` keeps its existing
contract - it still assesses argument strength and refuses to take a side - so
nothing that relied on that guarantee loses it. This chain runs on top of a
completed analysis and is reached only when the caller asks for it.

On what "likely outcome" can honestly mean here. A real prediction needs
decided cases with known outcomes, so the model can say how positions like this
one have actually fared. This corpus holds procedural rules and a judgment:
authorities, not outcomes. A confident direction derived from it is a reading of
the authorities, which is genuinely useful, and is not a forecast, which would
be invented. Three things keep that distinction from eroding:

  - the prompt forbids appeals to what courts "usually" do and forbids
    probabilities, because both would be fabricated;
  - confidence is computed from the state of the underlying issues rather than
    generated, so the model cannot talk its way past a weak analysis;
  - the top confidence band does not exist, because nothing this corpus can
    supply would earn it.

Getting beyond that is a data problem rather than a prompting one: a corpus of
decided cases with outcomes and metadata, plus an eval that scores predicted
against actual. Worth doing, and out of scope here.
"""

from langchain_anthropic import ChatAnthropic

from policy_advisor.config import get_settings
from policy_advisor.generation.advisory_models import (
    OUTCOME_CONFIDENCE_LOW,
    AdvisoryOpinion,
    AdvisoryResult,
    assess_outcome_confidence,
)
from policy_advisor.generation.advisory_prompt import ADVISORY_PROMPT
from policy_advisor.generation.case_reasoning import CaseReasoningChain
from policy_advisor.generation.case_reasoning_models import CaseReasoningResult, IssueAnalysis
from policy_advisor.generation.faithfulness import is_supported_citation
from policy_advisor.generation.judge_check import judge_check
from policy_advisor.generation.prompt import LANGUAGE_NAMES
from policy_advisor.llm_retry import call_with_retry
from policy_advisor.logging_utils import get_logger, log_event
from policy_advisor.retrieval.hybrid_retriever import RetrievedChunk


def _cited_locators(issues: list[IssueAnalysis]) -> set[str]:
    return {
        authority.locator
        for issue in issues
        for argument in issue.arguments
        for authority in argument.supporting_authorities
    }


def _format_issue_analyses(issues: list[IssueAnalysis]) -> str:
    blocks = []
    for issue in issues:
        lines = [f"Issue: {issue.issue}", f"Confidence: {issue.confidence}"]
        if issue.unverified:
            lines.append("WARNING: this issue failed its citation check - do not rely on it as settled.")
        if issue.from_web:
            lines.append(
                "WARNING: no authority in this matter covers this issue. It was answered from an "
                "official website and has not been checked against the lawyer's documents."
            )
        for argument in issue.arguments:
            cites = ", ".join(a.locator for a in argument.supporting_authorities) or "no authority cited"
            lines.append(f"- [{argument.side}] {argument.summary} (cites: {cites})")
        lines.append(f"Assessment: {issue.assessment}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _opinion_as_text(opinion: AdvisoryOpinion) -> str:
    """Flattened for the judge pass, which reviews prose rather than structure."""
    steps = "\n".join(f"- {step.action}: {step.rationale}" for step in opinion.next_steps)
    authorities = "\n".join(f"- {a.locator}: {a.why}" for a in opinion.key_authorities)
    return (
        f"Recommended position: {opinion.recommended_position}\n"
        f"Outcome direction: {opinion.outcome_direction}\n"
        f"Reasoning: {opinion.likely_outcome}\n"
        f"Turns on: {opinion.turns_on}\n"
        f"Next steps:\n{steps}\n"
        f"Key authorities:\n{authorities}"
    )


class AdvisoryChain:
    def __init__(self, case_chain: CaseReasoningChain | None = None):
        settings = get_settings()
        self._settings = settings
        self._case_chain = case_chain or CaseReasoningChain()
        self._logger = get_logger("policy_advisor.advisory", settings.log_level)
        self._llm = ChatAnthropic(
            model=settings.anthropic_model,
            api_key=settings.anthropic_api_key.get_secret_value(),
            max_tokens=2048,
            default_request_timeout=45,
            max_retries=0,  # retried explicitly via call_with_retry
        )

    def advise(
        self,
        case_facts: str,
        matter_id: str,
        jurisdiction: str | None = None,
        conversation_language: str = "en",
        allow_web_fallback: bool = False,
    ) -> AdvisoryResult:
        analysis = self._case_chain.analyze(
            case_facts,
            matter_id=matter_id,
            jurisdiction=jurisdiction,
            conversation_language=conversation_language,
            allow_web_fallback=allow_web_fallback,
        )
        return self.advise_on(analysis, matter_id=matter_id, conversation_language=conversation_language)

    def advise_on(
        self,
        analysis: CaseReasoningResult,
        matter_id: str,
        conversation_language: str = "en",
    ) -> AdvisoryResult:
        """Split out from `advise` so an analysis can be reviewed, or reused,
        without paying for it twice - and so tests can drive this layer with a
        constructed analysis instead of a live case run."""
        response_language = LANGUAGE_NAMES.get(conversation_language, "English")
        log_event(self._logger, "advisory_started", matter_id=matter_id, issue_count=len(analysis.issues))

        messages = ADVISORY_PROMPT.format_messages(
            case_facts=analysis.case_facts,
            issue_analyses=_format_issue_analyses(analysis.issues),
            overall_position=analysis.overall_position,
            response_language=response_language,
        )
        structured = self._llm.with_structured_output(AdvisoryOpinion)
        opinion: AdvisoryOpinion = call_with_retry(lambda: structured.invoke(messages))

        # The advisory synthesises authorities the issue analyses already
        # grounded. Anything else appearing here came from the model's own
        # knowledge, which is the failure mode the whole pipeline exists to
        # catch - so it is reported rather than silently rendered.
        available = _cited_locators(analysis.issues)
        unsupported = [
            authority.locator
            for authority in opinion.key_authorities
            if not is_supported_citation(authority.locator, available)
        ]

        judge_outcome = judge_check(self._llm, _opinion_as_text(opinion), self._authority_chunks(analysis, matter_id))

        confidence, basis = assess_outcome_confidence(analysis.issues)
        if unsupported or judge_outcome.judge_flagged:
            # Both mean the advice reaches past its evidence, which is exactly
            # the condition the confidence band is meant to express.
            confidence = OUTCOME_CONFIDENCE_LOW
            extra = []
            if unsupported:
                extra.append(f"cites authorities no issue analysis established ({', '.join(unsupported)})")
            if judge_outcome.judge_flagged:
                extra.append("flagged by the faithfulness judge")
            basis = (basis + " " if basis else "") + "The advice itself " + " and ".join(extra) + "."

        log_event(
            self._logger,
            "advisory_completed",
            matter_id=matter_id,
            outcome_direction=opinion.outcome_direction,
            outcome_confidence=confidence,
            unsupported_citations=unsupported,
            judge_flagged=judge_outcome.judge_flagged,
        )

        return AdvisoryResult(
            case_analysis=analysis,
            recommended_position=opinion.recommended_position,
            outcome_direction=opinion.outcome_direction,
            likely_outcome=opinion.likely_outcome,
            turns_on=opinion.turns_on,
            next_steps=opinion.next_steps,
            key_authorities=opinion.key_authorities,
            outcome_confidence=confidence,
            confidence_basis=basis,
            unsupported_citations=unsupported,
            judge_flagged=judge_outcome.judge_flagged,
            judge_notes=judge_outcome.judge_notes,
        )

    def _authority_chunks(self, analysis: CaseReasoningResult, matter_id: str) -> list[RetrievedChunk]:
        """The corpus passages behind this case, for the judge pass.

        Re-retrieved per issue, mirroring `CaseReasoningChain._synthesize`.
        Web-backed issues contribute nothing here on purpose: the judge decides
        whether text stays within its authorities, so handing it web material as
        an authority would have it certify the one source that was never vetted.
        """
        chunks: list[RetrievedChunk] = []
        for issue in analysis.issues:
            if issue.from_web:
                continue
            chunks.extend(
                self._case_chain.retriever.retrieve(
                    issue.issue, top_k=self._settings.retrieval_top_k, matter_id=matter_id
                )
            )
        return chunks
