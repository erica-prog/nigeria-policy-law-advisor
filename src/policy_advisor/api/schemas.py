"""Request and response models for docs/contracts/web-api.md. Field names and
shapes are the contract; change the document first."""

from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, Field

from policy_advisor.jurisdictions import UnknownJurisdiction, load_taxonomy, normalize_jurisdiction


def _jurisdiction_id(value: str | None) -> str | None:
    """Requests may name a jurisdiction by taxonomy id or legacy alias; the API
    works with the id. Unknown values fail validation with the accepted list."""
    try:
        return normalize_jurisdiction(value)
    except UnknownJurisdiction:
        accepted = ", ".join(load_taxonomy().accepted_values())
        raise ValueError(f"unknown jurisdiction {value!r}; accepted: {accepted}") from None


JurisdictionId = Annotated[str | None, BeforeValidator(_jurisdiction_id)]

MATTER_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{1,63}$"

AvatarState = Literal[
    "idle", "listening", "verified_source", "web_source", "unverified", "no_results"
]
SourceKind = Literal["evidence", "authority", "web"]
# The four corpus trust labels (ADR-001). `kind` above stays for the client;
# `firm` renders as authority there until the client learns the fourth label.
TrustKind = Literal["evidence", "firm", "authority", "web"]
# `research`: the research agent (docs/28) answered from a mix of the matter, the
# library, connectors and the web; every citation says which.
AnswerSource = Literal["corpus", "library", "web", "research", "none"]
DocumentStatus = Literal["queued", "processing", "ready", "failed"]


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


KeySource = Literal["user", "shared"]


TenantRole = Literal["admin", "lawyer", "reviewer"]


class UserOut(BaseModel):
    username: str
    display_name: str
    # Revision 3 (BYOK): can THIS user think, and with whose key?
    advisor_ready: bool = False
    key_source: KeySource | None = None
    # Tenancy (docs/31): the firm this user works in and what they may do.
    tenant_id: str | None = None
    role: TenantRole | None = None


class ShareRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    can_write: bool = False


class SessionOut(BaseModel):
    user: UserOut | None = None


class HealthOut(BaseModel):
    status: Literal["ok"] = "ok"
    # Server-level view: is a server key configured at all? Whether a given
    # user can think is on /api/me (advisor_ready).
    llm_configured: bool
    shared_key_allowed: bool = False


class ClaudeKeyIn(BaseModel):
    api_key: str = Field(min_length=1, max_length=512)


class ClaudeKeyOut(BaseModel):
    # `configured`: this user has a key on file. `source`: whose key would be
    # used right now (`user`, `shared`, or None = cannot think). The key itself
    # is never returned; `last4` is for "ends in …abcd" only.
    configured: bool
    last4: str | None = None
    source: KeySource | None = None
    advisor_ready: bool = False


MATTER_TITLE_MAX = 80


class MatterCreate(BaseModel):
    # Both optional since revision 2: the chat client creates a matter behind
    # the scenes and lets the server pick an id. Explicit ids remain valid.
    id: str | None = Field(default=None, pattern=MATTER_ID_PATTERN)
    title: str | None = Field(default=None, min_length=1, max_length=MATTER_TITLE_MAX)


class MatterOut(BaseModel):
    id: str
    owner: str | None
    read_only: bool
    document_count: int
    # Human title shown in "Your cases". None until the first chat message
    # derives one (or the caller sets it at creation).
    title: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    # Users of the same firm this matter is shared with (docs/31).
    shared_with: list[str] = Field(default_factory=list)


class MatterList(BaseModel):
    matters: list[MatterOut]


class DocumentOut(BaseModel):
    name: str
    status: DocumentStatus
    job_id: str | None = None
    chunk_count: int | None = None
    error: str | None = None


class DocumentList(BaseModel):
    documents: list[DocumentOut]


class SourceReference(BaseModel):
    id: str
    kind: SourceKind
    document: str
    locator: str | None = None
    cited_as: str | None = None
    page: int | None = None
    quote: str | None = None
    url: str | None = None
    doc_type: str | None = None
    jurisdiction: str | None = None
    trust_kind: TrustKind | None = None
    tier: str | None = None


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    allow_web: bool = False
    jurisdiction: JurisdictionId = None
    language: Literal["en", "fr"] = "en"


class AskResponse(BaseModel):
    answer: str
    source: AnswerSource
    faithful: bool
    unsupported_citations: list[str] = Field(default_factory=list)
    citations: list[SourceReference] = Field(default_factory=list)
    retrieved: list[SourceReference] = Field(default_factory=list)
    avatar_state: AvatarState
    usage: dict[str, int] = Field(default_factory=dict)
    # Research agent runs only (docs/28): the audit row and how the run ended.
    run_id: str | None = None
    tool_calls: int | None = None
    budget_exhausted: bool = False


class AnalyzeRequest(BaseModel):
    case_facts: str = Field(min_length=1, max_length=20000)
    jurisdiction: JurisdictionId = None
    language: Literal["en", "fr"] = "en"


class AuthorityOut(BaseModel):
    locator: str
    relevance: str
    source: SourceReference | None = None


class ArgumentOut(BaseModel):
    side: str
    summary: str
    authorities: list[AuthorityOut] = Field(default_factory=list)


class IssueOut(BaseModel):
    issue: str
    arguments: list[ArgumentOut]
    assessment: str
    confidence: str
    unverified: bool


class GapOut(BaseModel):
    issue: str
    note: str


class AnalyzeResponse(BaseModel):
    issues: list[IssueOut]
    overall_position: str
    disclaimer: str
    missing_evidence: list[GapOut] = Field(default_factory=list)
    contradictions: list[GapOut] = Field(default_factory=list)
    counterarguments: list[GapOut] = Field(default_factory=list)
    citations: list[SourceReference] = Field(default_factory=list)
    avatar_state: AvatarState


# ---- Chat (revision 2, docs/contracts/web-api.md "Chat") ----

ChatIntent = Literal["auto", "analyze", "ask", "research"]
ChatMode = Literal["analysis", "research", "question"]
ChipAction = Literal["analyze", "add_documents", "ask_web", "ask"]


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20000)
    allow_web: bool = False
    intent: ChatIntent = "auto"
    jurisdiction: JurisdictionId = None
    language: Literal["en", "fr"] = "en"


class Chip(BaseModel):
    label: str
    action: ChipAction


class UserMessage(BaseModel):
    id: str
    role: Literal["user"] = "user"
    text: str
    created_at: str


class ChatReply(BaseModel):
    id: str
    role: Literal["assistant"] = "assistant"
    created_at: str
    mode: ChatMode
    # The playbook that shaped this reply (docs/29), by id; None when none matched.
    playbook: str | None = None
    # Short sentence for the speech bubble. The full result is in `answer`
    # (question/research) or `analysis` (analysis); exactly one is set.
    bubble: str
    # Plain-language caveat the client must show next to the reply, e.g. that
    # research mode is not evidence from the user's documents (rules 4 and 5).
    notice: str | None = None
    avatar_state: AvatarState
    citations: list[SourceReference] = Field(default_factory=list)
    answer: AskResponse | None = None
    analysis: AnalyzeResponse | None = None
    chips: list[Chip] = Field(default_factory=list)


class ChatHistory(BaseModel):
    title: str | None
    has_analysis: bool
    messages: list[UserMessage | ChatReply]


# ---- Chat jobs (revision 3): the same reply, produced in the background so
# the client can narrate progress while the server works. ----

ChatJobStatus = Literal["queued", "running", "done", "failed"]
ChatStage = Literal[
    "reading_documents", "searching_web", "researching", "checking_citations", "writing"
]


class ChatJobError(BaseModel):
    code: str
    message: str


class ChatJobOut(BaseModel):
    job_id: str
    status: ChatJobStatus
    # Current stage while running (the bubble narrates it); `stages` is the
    # sequence so far, in order.
    stage: ChatStage | None = None
    stages: list[ChatStage] = Field(default_factory=list)
    reply: ChatReply | None = None
    error: ChatJobError | None = None


# ---- Usage (docs/32): what a user, or a firm's admin, has spent ----


class UsageRow(BaseModel):
    kind: str | None = None
    matter_id: str | None = None
    calls: int
    input_tokens: int
    output_tokens: int
    estimated_usd: float | None = None


class UsageOut(BaseModel):
    scope: Literal["me", "tenant"]
    days: int
    by_kind: list[UsageRow] = Field(default_factory=list)
    by_matter: list[UsageRow] = Field(default_factory=list)


# ---- Bulk analysis (docs/33) ----

BulkJobStatus = Literal[
    "queued", "running", "waiting_batch", "paused_ceiling", "done", "failed", "cancelled"
]
BulkBackend = Literal["sync", "batch"]


class BulkJobCreate(BaseModel):
    schema_id: str = Field(default="contract", min_length=1, max_length=64)
    documents: list[str] | None = None  # None: every indexed document of the matter
    ceiling_tokens: int | None = Field(default=None, ge=10_000, le=200_000_000)
    backend: BulkBackend | None = None  # None: by size against BULK_BATCH_THRESHOLD


class BulkResumeRequest(BaseModel):
    ceiling_tokens: int = Field(ge=10_000, le=200_000_000)


class BulkJobOut(BaseModel):
    job_id: str
    matter_id: str
    schema_id: str
    schema_version: int
    documents: list[str]
    status: BulkJobStatus
    backend: BulkBackend
    triage_model: str
    escalation_model: str
    ceiling_tokens: int
    spent_tokens: int
    done_documents: int
    flagged_documents: int
    error: str | None = None
    created_at: str | None = None
    finished_at: str | None = None


class BulkJobList(BaseModel):
    jobs: list[BulkJobOut]


class ExtractionOut(BaseModel):
    document: str
    schema_id: str
    schema_version: int
    job_id: str | None
    record: dict
    evidence_spans: list[dict]
    confidence: float
    model: str
    flagged: bool
    created_at: str | None = None


class ExtractionList(BaseModel):
    records: list[ExtractionOut]


FindingSeverity = Literal["info", "review", "high"]
FindingStatus = Literal["open", "reviewed", "dismissed"]


class FindingOut(BaseModel):
    id: int
    job_id: str | None
    kind: str
    severity: FindingSeverity
    summary: str
    documents: list[str]
    evidence: list[dict]
    status: FindingStatus
    created_at: str | None = None


class FindingList(BaseModel):
    findings: list[FindingOut]


# ---- Artefacts (docs/35) ----

ArtefactKind = Literal["memo", "slides", "both"]
ArtefactStatus = Literal["draft", "reviewed", "approved", "rejected"]


class DraftRequest(BaseModel):
    instruction: str = Field(min_length=10, max_length=20000)
    kind: ArtefactKind = "memo"
    template_id: str | None = None
    jurisdiction: JurisdictionId = None
    allow_web: bool = False


class ArtefactOut(BaseModel):
    artefact_id: str
    matter_id: str
    kind: ArtefactKind
    title: str
    status: ArtefactStatus
    instruction: str
    outline: dict
    manifest: dict
    has_docx: bool
    has_pptx: bool
    template_id: str | None = None
    run_id: str | None = None
    created_by: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class ArtefactList(BaseModel):
    artefacts: list[ArtefactOut]


class TemplateOut(BaseModel):
    template_id: str
    kind: Literal["memo", "slides"]
    name: str
    is_default: bool
    shared: bool


class TemplateList(BaseModel):
    templates: list[TemplateOut]
