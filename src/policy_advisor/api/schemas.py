"""Request and response models for docs/contracts/web-api.md. Field names and
shapes are the contract; change the document first."""

from typing import Literal

from pydantic import BaseModel, Field

MATTER_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{1,63}$"

AvatarState = Literal[
    "idle", "listening", "verified_source", "web_source", "unverified", "no_results"
]
SourceKind = Literal["evidence", "authority", "web"]
AnswerSource = Literal["corpus", "web", "none"]
DocumentStatus = Literal["queued", "processing", "ready", "failed"]


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


class UserOut(BaseModel):
    username: str
    display_name: str


class HealthOut(BaseModel):
    status: Literal["ok"] = "ok"
    llm_configured: bool


class MatterCreate(BaseModel):
    id: str = Field(pattern=MATTER_ID_PATTERN)


class MatterOut(BaseModel):
    id: str
    owner: str | None
    read_only: bool
    document_count: int


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


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    allow_web: bool = False
    jurisdiction: Literal["federal", "lagos"] | None = None
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


class AnalyzeRequest(BaseModel):
    case_facts: str = Field(min_length=1, max_length=20000)
    jurisdiction: Literal["federal", "lagos"] | None = None
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
