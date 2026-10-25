"""Chat: one endpoint behind the chat-first client. The server decides
whether a message is a case description (case analysis), a question about
the documents (RAG answer), or research: the research agent (docs/28) over
the matter, the libraries, the connectors and, when allowed, the web, chosen
when the lawyer asks for it or when the matter has no documents. Research is
labelled as such and never presented as evidence (AGENTS.md rules 4 and 5).

Depends on `Matter`, so ownership is verified before any document listing,
retrieval or generation (rule 1). The ask/analyze endpoints in advice.py stay
as they are; this module composes them."""

from fastapi import APIRouter, status

from policy_advisor.agents.research import AgentUnavailable
from policy_advisor.api.avatar import avatar_state_for_research
from policy_advisor.api.citations import retrieved_references
from policy_advisor.api.conversations import (
    append_messages,
    derive_title,
    first_user_message,
    has_analysis,
    load_messages,
    new_message_id,
    now_iso,
)
from policy_advisor.api.deps import ChatJobs, CurrentUser, Jobs, Matter, MatterAccess, Services
from policy_advisor.api.errors import ApiError
from policy_advisor.api.jobs import JobTable
from policy_advisor.api.routers.advice import require_key, run_analyze, run_ask
from policy_advisor.api.schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    AskRequest,
    AskResponse,
    ChatHistory,
    ChatIntent,
    ChatJobOut,
    ChatMode,
    ChatReply,
    ChatRequest,
    Chip,
    UserMessage,
)
from policy_advisor.api.services import AdvisorServices
from policy_advisor.ingestion.matter_store import (
    get_matter_meta,
    list_documents,
    update_matter_meta,
)
from policy_advisor.logging_utils import log_event
from policy_advisor.playbooks import Playbook
from policy_advisor.playbooks.selector import Selection, select_route

router = APIRouter(prefix="/api/matters/{matter_id}", tags=["chat"])

RESEARCH_NOTICE = (
    "This is legal research across your documents, the reference library, official sources "
    "and (when allowed) official websites. Each citation says where it came from; only "
    "passages from your documents are evidence. Nothing from an outside source proves a fact "
    "of your case."
)
WEB_NOTICE = (
    "Your documents do not cover this, so the answer comes from official websites. It is "
    "legal authority at best and cannot replace missing case evidence."
)

CHIP_ANALYZE = Chip(label="Analyse my case now", action="analyze")
CHIP_ADD_DOCS = Chip(label="Add your documents with +", action="add_documents")
CHIP_ASK_WEB = Chip(label="Also search official web sources", action="ask_web")
CHIP_ASK = Chip(label="Ask a follow-up question", action="ask")


def decide_route(
    message: str,
    intent: ChatIntent,
    history: list,
    has_documents: bool,
    read_only: bool,
    jurisdiction: str | None,
) -> Selection:
    """The playbook selector (docs/29) over the Phase 2 rules: analysis when
    the user asked for it, or for the first message of a conversation that
    has not been analysed yet; research when the lawyer asks for it or there
    are no documents; a matched playbook may choose its own mode. The shared
    read-only library is not anyone's case, so its first message is a question."""
    return select_route(
        message,
        intent=intent,
        has_documents=has_documents,
        read_only=read_only,
        conversation_started=first_user_message(history) is not None,
        analysed=has_analysis(history),
        jurisdiction=jurisdiction,
    )


def decide_mode(
    intent: ChatIntent, history: list, has_documents: bool, read_only: bool
) -> ChatMode:
    """The mode alone, for callers that do not route by playbook."""
    selection = decide_route("", intent, history, has_documents, read_only, None)
    return selection.mode  # type: ignore[return-value]


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def bubble_for_research(answer: AskResponse) -> tuple[str, list[Chip]]:
    kinds = {c.kind for c in answer.citations}
    if not answer.citations:
        text = "I researched this but found nothing I can cite. "
        if answer.budget_exhausted:
            text += "I ran out of tool calls before finishing. "
        return (
            text + "Add your documents with + or narrow the question.",
            [CHIP_ADD_DOCS, CHIP_ASK],
        )
    parts = []
    if "evidence" in kinds:
        parts.append("your documents")
    if "authority" in kinds:
        parts.append("legal authority")
    if "web" in kinds:
        parts.append("official websites")
    text = (
        f"Here is my research, with {_plural(len(answer.citations), 'citation')} from "
        f"{', '.join(parts)}; each one says which."
    )
    if not answer.faithful:
        text += f" {_plural(len(answer.unsupported_citations), 'citation')} did not check out; read with care."
    if answer.budget_exhausted:
        text += " I reached the tool-call limit, so parts may be incomplete."
    return text, [CHIP_ASK]


def bubble_for_answer(
    answer: AskResponse, mode: ChatMode, allow_web: bool
) -> tuple[str, list[Chip]]:
    if mode == "research":
        return bubble_for_research(answer)
    if answer.source == "web":
        return (
            "Your documents do not cover this, so this comes from official websites. It is legal "
            "authority, not evidence from your case.",
            [CHIP_ASK],
        )
    if answer.source == "library":
        return (
            "Your documents do not cover this, so this comes from the reference library. It is "
            f"legal authority, not evidence from your case: {_plural(len(answer.citations), 'citation')} "
            "you can open.",
            [CHIP_ASK],
        )
    if answer.source == "none":
        chips = [CHIP_ASK] if allow_web else [CHIP_ASK_WEB]
        return ("I could not find that in your documents.", chips)
    if not answer.faithful:
        return (
            "I found an answer, but "
            f"{_plural(len(answer.unsupported_citations), 'citation')} did not check out. "
            "Read it with care.",
            [CHIP_ASK],
        )
    return (
        f"Here is what your documents say, with {_plural(len(answer.citations), 'citation')} "
        "you can open.",
        [CHIP_ASK],
    )


def bubble_for_analysis(analysis: AnalyzeResponse) -> tuple[str, list[Chip]]:
    issues = len(analysis.issues)
    missing = len(analysis.missing_evidence)
    unverified = sum(1 for issue in analysis.issues if issue.unverified)
    if analysis.avatar_state == "no_results":
        return (
            "I read your documents but found nothing that supports "
            f"{'the issue' if issues == 1 else 'these issues'}. The gaps are listed below; "
            "you may need to add more documents.",
            [CHIP_ADD_DOCS, CHIP_ASK],
        )
    text = f"I have analysed your case: {_plural(issues, 'issue')}"
    if missing:
        text += f", {missing} without support in your documents"
    text += ". The details are below."
    if unverified:
        text += f" {_plural(unverified, 'citation')} could not be verified; treat those parts with care."
    chips = [CHIP_ASK]
    if missing:
        chips.insert(0, CHIP_ADD_DOCS)
    return text, chips


@router.get("/chat", response_model=ChatHistory)
def chat_history(matter: Matter) -> ChatHistory:
    messages = [] if matter.read_only else load_messages(matter.id)
    return ChatHistory(
        title=get_matter_meta(matter.id).get("title"),
        has_analysis=has_analysis(messages),
        messages=messages,
    )


@router.post("/chat", response_model=ChatReply)
def chat(
    body: ChatRequest, matter: Matter, services: Services, user: CurrentUser, jobs: Jobs
) -> ChatReply:
    """Synchronous form: the reply in one response. The client uses the job
    form below so it can narrate progress; this stays for scripts and tests."""
    return run_chat(body, matter, services, require_key(user), jobs, username=user.username)


@router.post("/chat/jobs", response_model=ChatJobOut, status_code=status.HTTP_202_ACCEPTED)
def start_chat_job(
    body: ChatRequest,
    matter: Matter,
    services: Services,
    user: CurrentUser,
    jobs: Jobs,
    chat_jobs: ChatJobs,
) -> ChatJobOut:
    """Same routing and side effects as POST /chat, run in a worker thread.
    Ownership and the key check happen here, before the job exists."""
    api_key = require_key(user)
    job = chat_jobs.submit(
        matter.id,
        user.username,
        lambda: run_chat(body, matter, services, api_key, jobs, username=user.username),
        logger=services.logger,
    )
    return job.to_out()


@router.get("/chat/jobs/{job_id}", response_model=ChatJobOut)
def chat_job_status(job_id: str, matter: Matter, user: CurrentUser, jobs: ChatJobs) -> ChatJobOut:
    # `matter` has already passed the ownership check; the job must also be
    # this user's on this matter, otherwise it does not exist as far as the
    # caller is concerned.
    job = jobs.get(job_id, matter.id, user.username)
    if job is None:
        raise ApiError(404, "not_found", "Not found.")
    return job.to_out()


def _documents_still_being_read(jobs: JobTable | None, matter_id: str) -> list[str]:
    """Names of uploads that are queued or processing and so not searchable yet."""
    if jobs is None:
        return []
    return [
        name
        for name, job in jobs.latest_for_matter(matter_id).items()
        if job.status in ("queued", "processing")
    ]


def _still_reading_reply(names: list[str]) -> tuple[AskResponse, str]:
    shown = ", ".join(names[:3])
    if len(names) > 3:
        shown += f" and {len(names) - 3} more"
    sentence = f"I'm still reading {shown}. Ask me again in a moment and I will answer from it."
    answer = AskResponse(
        answer=sentence,
        source="none",
        faithful=True,
        avatar_state="listening",
    )
    return answer, f"I'm still reading {shown}."


def run_research(
    body: ChatRequest,
    matter: MatterAccess,
    services: AdvisorServices,
    api_key: str,
    username: str | None,
    playbook: Playbook | None = None,
) -> AskResponse:
    """The research agent as an `AskResponse` with `source == "research"`."""
    try:
        result = services.research_agent_for(api_key).run(
            body.message,
            matter_id=matter.id,
            username=username,
            jurisdiction=body.jurisdiction,
            conversation_language=body.language,
            allow_web=body.allow_web,
            playbook=playbook,
        )
    except AgentUnavailable as exc:
        # The cause (a model error, a request id) goes to the log, never to
        # the client, which gets the same envelope the ask pipeline uses.
        log_event(services.logger, "research_unavailable", matter_id=matter.id, error=str(exc))
        raise ApiError(
            503, "llm_unavailable", "The advisor model did not respond. Try again shortly."
        ) from exc
    kinds = [c.kind for c in result.citations]
    usage = {k: int(v) for k, v in (result.usage or {}).items() if isinstance(v, int | float)}
    return AskResponse(
        answer=result.answer,
        source="research",
        faithful=result.faithful,
        unsupported_citations=list(result.unsupported_citations),
        citations=result.citations,
        retrieved=retrieved_references(result.retrieved),
        avatar_state=avatar_state_for_research(kinds, result.faithful),
        usage=usage,
        run_id=result.run_id,
        tool_calls=result.tool_calls,
        budget_exhausted=result.budget_exhausted,
    )


def run_chat(
    body: ChatRequest,
    matter: MatterAccess,
    services: AdvisorServices,
    api_key: str,
    jobs: JobTable | None = None,
    username: str | None = None,
) -> ChatReply:
    persist = not matter.read_only
    history = load_messages(matter.id) if persist else []
    documents = list_documents(matter.id)
    pending = _documents_still_being_read(jobs, matter.id)
    selection = decide_route(
        body.message, body.intent, history, bool(documents), matter.read_only, body.jurisdiction
    )
    mode: ChatMode = selection.mode  # type: ignore[assignment]
    playbook = selection.playbook

    user_message = UserMessage(id=new_message_id(), text=body.message, created_at=now_iso())
    answer: AskResponse | None = None
    analysis: AnalyzeResponse | None = None
    notice: str | None = None

    # Nothing is searchable yet, but an upload is in flight. Say so instead of
    # answering "there are no documents" or spending a model call on a guess.
    if pending and not documents:
        answer, bubble = _still_reading_reply(pending)
        chips: list[Chip] = []
        mode = "question"
        avatar_state = answer.avatar_state
        citations = answer.citations
    elif mode == "analysis":
        analysis = run_analyze(
            AnalyzeRequest(
                case_facts=body.message, jurisdiction=body.jurisdiction, language=body.language
            ),
            matter,
            services,
            api_key,
        )
        bubble, chips = bubble_for_analysis(analysis)
        avatar_state = analysis.avatar_state
        citations = analysis.citations
        if playbook is not None:
            notice = playbook.notice
    elif mode == "research":
        # The agent (docs/28): the matter, the libraries, the connectors and,
        # when the lawyer allows it, the web. Labelled as research, never as
        # evidence.
        answer = run_research(body, matter, services, api_key, username, playbook)
        bubble, chips = bubble_for_research(answer)
        avatar_state = answer.avatar_state
        citations = answer.citations
        notice = playbook.notice if playbook else RESEARCH_NOTICE
    else:
        answer = run_ask(
            AskRequest(
                question=body.message,
                allow_web=body.allow_web,
                jurisdiction=body.jurisdiction,
                language=body.language,
            ),
            matter,
            services,
            api_key,
        )
        bubble, chips = bubble_for_answer(answer, mode, body.allow_web)
        avatar_state = answer.avatar_state
        citations = answer.citations
        if answer.source == "web":
            notice = WEB_NOTICE

    reply = ChatReply(
        id=new_message_id(),
        created_at=now_iso(),
        mode=mode,
        playbook=playbook.id if playbook else None,
        bubble=bubble,
        notice=notice,
        avatar_state=avatar_state,
        citations=citations,
        answer=answer,
        analysis=analysis,
        chips=chips,
    )

    if persist:
        meta = get_matter_meta(matter.id)
        fields: dict[str, str | None] = {"updated_at": reply.created_at}
        if not meta.get("title") and first_user_message(history) is None:
            fields["title"] = derive_title(body.message)
        update_matter_meta(matter.id, **fields)
        append_messages(matter.id, user_message, reply)

    log_event(
        services.logger,
        "chat_replied",
        matter_id=matter.id,
        mode=mode,
        playbook=playbook.id if playbook else None,
        route_reason=selection.reason,
        intent=body.intent,
        avatar_state=avatar_state,
        document_count=len(documents),
    )
    return reply
