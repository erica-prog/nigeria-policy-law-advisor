"""Chat: one endpoint behind the chat-first client. The server decides
whether a message is a case description (case analysis), a question about
the documents (RAG answer), or, when the matter has no documents, general
legal research from official web sources that is labelled as such and never
presented as evidence (AGENTS.md rules 4 and 5).

Depends on `Matter`, so ownership is verified before any document listing,
retrieval or generation (rule 1). The ask/analyze endpoints in advice.py stay
as they are; this module composes them."""

from fastapi import APIRouter

from policy_advisor.api.conversations import (
    append_messages,
    derive_title,
    first_user_message,
    has_analysis,
    load_messages,
    new_message_id,
    now_iso,
)
from policy_advisor.api.deps import Matter, Services
from policy_advisor.api.routers.advice import require_llm, run_analyze, run_ask
from policy_advisor.api.schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    AskRequest,
    AskResponse,
    ChatHistory,
    ChatIntent,
    ChatMode,
    ChatReply,
    ChatRequest,
    Chip,
    UserMessage,
)
from policy_advisor.ingestion.matter_store import (
    get_matter_meta,
    list_documents,
    update_matter_meta,
)
from policy_advisor.logging_utils import log_event

router = APIRouter(prefix="/api/matters/{matter_id}", tags=["chat"])

RESEARCH_NOTICE = (
    "This is general legal research from official web sources and the model, not evidence "
    "from your documents. Nothing here proves a fact of your case. Add your documents with + "
    "for an analysis based on them."
)
WEB_NOTICE = (
    "Your documents do not cover this, so the answer comes from official websites. It is "
    "legal authority at best and cannot replace missing case evidence."
)

CHIP_ANALYZE = Chip(label="Analyse my case now", action="analyze")
CHIP_ADD_DOCS = Chip(label="Add your documents with +", action="add_documents")
CHIP_ASK_WEB = Chip(label="Also search official web sources", action="ask_web")
CHIP_ASK = Chip(label="Ask a follow-up question", action="ask")


def decide_mode(
    intent: ChatIntent, history: list, has_documents: bool, read_only: bool
) -> ChatMode:
    """Analysis when the user asked for it, or for the first message of a
    conversation that has not been analysed yet. The shared read-only library
    is not anyone's case, so its first message is treated as a question."""
    wants_analysis = intent == "analyze" or (
        intent == "auto"
        and not read_only
        and not has_analysis(history)
        and first_user_message(history) is None
    )
    if not wants_analysis:
        return "question"
    return "analysis" if has_documents else "research"


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def bubble_for_answer(
    answer: AskResponse, mode: ChatMode, allow_web: bool
) -> tuple[str, list[Chip]]:
    if mode == "research":
        if answer.source == "web":
            return (
                "There are no documents in this case yet, so this is general legal research "
                "from official web sources, not evidence from your case. Add your documents "
                "with + and I will analyse them.",
                [CHIP_ADD_DOCS],
            )
        return (
            "There are no documents in this case yet, and I did not find anything relevant on "
            "the official web sources either. Add your documents with + and I will analyse them.",
            [CHIP_ADD_DOCS],
        )
    if answer.source == "web":
        return (
            "Your documents do not cover this, so this comes from official websites. It is legal "
            "authority, not evidence from your case.",
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
def chat(body: ChatRequest, matter: Matter, services: Services) -> ChatReply:
    require_llm(services)
    persist = not matter.read_only
    history = load_messages(matter.id) if persist else []
    documents = list_documents(matter.id)
    mode = decide_mode(body.intent, history, bool(documents), matter.read_only)

    user_message = UserMessage(id=new_message_id(), text=body.message, created_at=now_iso())
    answer: AskResponse | None = None
    analysis: AnalyzeResponse | None = None
    notice: str | None = None

    if mode == "analysis":
        analysis = run_analyze(
            AnalyzeRequest(
                case_facts=body.message, jurisdiction=body.jurisdiction, language=body.language
            ),
            matter,
            services,
        )
        bubble, chips = bubble_for_analysis(analysis)
        avatar_state = analysis.avatar_state
        citations = analysis.citations
    else:
        # Research mode has no documents to retrieve from, so the web fallback is
        # forced on; the reply is labelled as research, never as evidence.
        allow_web = True if mode == "research" else body.allow_web
        answer = run_ask(
            AskRequest(
                question=body.message,
                allow_web=allow_web,
                jurisdiction=body.jurisdiction,
                language=body.language,
            ),
            matter,
            services,
        )
        bubble, chips = bubble_for_answer(answer, mode, allow_web)
        avatar_state = answer.avatar_state
        citations = answer.citations
        if mode == "research":
            notice = RESEARCH_NOTICE
        elif answer.source == "web":
            notice = WEB_NOTICE

    reply = ChatReply(
        id=new_message_id(),
        created_at=now_iso(),
        mode=mode,
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
        intent=body.intent,
        avatar_state=avatar_state,
        document_count=len(documents),
    )
    return reply
