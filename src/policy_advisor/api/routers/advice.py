"""Ask and analyze: the only handlers that reach the language model. Both
depend on `Matter`, so ownership is checked before the chains run, and both
pass the matter id from that dependency, never from the body."""

from fastapi import APIRouter

from policy_advisor.api.avatar import (
    NO_AUTHORITY_CONFIDENCE,
    avatar_state_for_analysis,
    avatar_state_for_answer,
)
from policy_advisor.api.citations import (
    citations_from_answer,
    find_chunk_for_locator,
    chunk_reference,
    retrieved_references,
    web_reference,
)
from policy_advisor.api.deps import Matter, Services
from policy_advisor.api.errors import ApiError
from policy_advisor.api.schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    AnswerSource,
    ArgumentOut,
    AskRequest,
    AskResponse,
    AuthorityOut,
    GapOut,
    IssueOut,
    SourceReference,
)
from policy_advisor.config import get_settings
from policy_advisor.generation.chain import LLM_UNAVAILABLE_MESSAGE
from policy_advisor.logging_utils import log_event

router = APIRouter(prefix="/api/matters/{matter_id}", tags=["advice"])

MISSING_SUPPORT_NOTE = "No passage in this matter's documents was retrieved for this issue."


def _require_llm(services: Services) -> None:
    if not services.llm_configured():
        raise ApiError(
            503,
            "llm_unavailable",
            "The advisor model is not configured on this server (ANTHROPIC_API_KEY).",
        )


@router.post("/ask", response_model=AskResponse)
def ask(body: AskRequest, matter: Matter, services: Services) -> AskResponse:
    _require_llm(services)
    result = services.rag_chain.answer(
        body.question,
        matter_id=matter.id,
        jurisdiction=body.jurisdiction,
        conversation_language=body.language,
        allow_web_fallback=body.allow_web,
    )
    if result.answer == LLM_UNAVAILABLE_MESSAGE:
        raise ApiError(
            503, "llm_unavailable", "The advisor model did not respond. Try again shortly."
        )

    source: AnswerSource = "corpus"
    if result.source == "web":
        source = "web"
    elif result.source == "none":
        source = "none"
    if source == "web":
        citations = [web_reference(c, f"w{i + 1}") for i, c in enumerate(result.web_citations)]
        retrieved: list[SourceReference] = []
    else:
        citations = citations_from_answer(result.answer, result.retrieved)
        retrieved = retrieved_references(result.retrieved)

    usage = {k: int(v) for k, v in (result.usage or {}).items() if isinstance(v, int | float)}
    log_event(
        services.logger,
        "ask_answered",
        matter_id=matter.id,
        source=source,
        faithful=result.faithful,
        citation_count=len(citations),
    )
    return AskResponse(
        answer=result.answer,
        source=source,
        faithful=result.faithful,
        unsupported_citations=list(result.unsupported_citations),
        citations=citations,
        retrieved=retrieved,
        avatar_state=avatar_state_for_answer(source, result.faithful),
        usage=usage,
    )


@router.post("/analyze", response_model=AnalyzeResponse)
def analyze(body: AnalyzeRequest, matter: Matter, services: Services) -> AnalyzeResponse:
    _require_llm(services)
    result = services.case_chain.analyze(
        body.case_facts,
        matter_id=matter.id,
        jurisdiction=body.jurisdiction,
        conversation_language=body.language,
    )

    top_k = get_settings().retrieval_top_k
    issues_out: list[IssueOut] = []
    citations: list[SourceReference] = []
    missing: list[GapOut] = []
    seen_chunk_ids: set[str] = set()

    for issue in result.issues:
        # Same query the chain ran for this issue, so locators resolve against
        # the passages the model actually saw.
        chunks = services.retriever.retrieve(
            issue.issue, top_k=top_k, matter_id=matter.id, jurisdiction=body.jurisdiction
        )
        arguments_out = []
        for argument in issue.arguments:
            authorities_out = []
            for authority in argument.supporting_authorities:
                chunk = find_chunk_for_locator(authority.locator, chunks)
                source = None
                if chunk is not None:
                    chunk_id = str(chunk.metadata.get("chunk_id", ""))
                    if chunk_id not in seen_chunk_ids:
                        seen_chunk_ids.add(chunk_id)
                        citations.append(
                            chunk_reference(
                                chunk, f"c{len(citations) + 1}", cited_as=authority.locator
                            )
                        )
                    source = chunk_reference(
                        chunk, f"c{len(citations)}", cited_as=authority.locator
                    )
                authorities_out.append(
                    AuthorityOut(
                        locator=authority.locator, relevance=authority.relevance, source=source
                    )
                )
            arguments_out.append(
                ArgumentOut(
                    side=argument.side, summary=argument.summary, authorities=authorities_out
                )
            )
        issues_out.append(
            IssueOut(
                issue=issue.issue,
                arguments=arguments_out,
                assessment=issue.assessment,
                confidence=issue.confidence,
                unverified=issue.unverified,
            )
        )
        if issue.confidence == NO_AUTHORITY_CONFIDENCE:
            missing.append(GapOut(issue=issue.issue, note=MISSING_SUPPORT_NOTE))

    log_event(
        services.logger,
        "analysis_completed",
        matter_id=matter.id,
        issue_count=len(issues_out),
        unverified_count=sum(1 for i in issues_out if i.unverified),
    )
    return AnalyzeResponse(
        issues=issues_out,
        overall_position=result.overall_position,
        disclaimer=result.disclaimer,
        missing_evidence=missing,
        contradictions=[],
        counterarguments=[],
        citations=citations,
        avatar_state=avatar_state_for_analysis(result.issues),
    )
