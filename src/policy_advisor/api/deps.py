"""Request dependencies. `get_matter` is the permission check from
docs/architecture/web-mvp.md: every document, retrieval or generation handler
depends on it, so ownership is verified before any of them runs (rule 1)."""

import re
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Path, Request

from policy_advisor.api.chat_jobs import ChatJobTable
from policy_advisor.api.credentials import UserRecord, get_user
from policy_advisor.api.errors import ApiError
from policy_advisor.api.jobs import JobTable
from policy_advisor.api.schemas import MATTER_ID_PATTERN
from policy_advisor.api.services import AdvisorServices
from policy_advisor.api.sessions import SESSION_COOKIE, read_session_token
from policy_advisor.config import PHASE1_DEMO_MATTER_ID
from policy_advisor.ingestion.matter_store import get_matter_owner

_MATTER_ID_RE = re.compile(MATTER_ID_PATTERN)


@dataclass(frozen=True)
class MatterAccess:
    id: str
    owner: str | None
    read_only: bool


def current_user(request: Request) -> UserRecord:
    token = request.cookies.get(SESSION_COOKIE)
    username = read_session_token(token) if token else None
    user = get_user(username) if username else None
    if user is None:
        raise ApiError(401, "unauthenticated", "Log in to continue.")
    return user


CurrentUser = Annotated[UserRecord, Depends(current_user)]


def resolve_matter_access(matter_id: str, user: UserRecord) -> MatterAccess:
    if not _MATTER_ID_RE.match(matter_id):
        raise ApiError(404, "matter_not_found", "Matter not found.")
    if matter_id == PHASE1_DEMO_MATTER_ID:
        return MatterAccess(id=matter_id, owner=None, read_only=True)
    owner = get_matter_owner(matter_id)
    if owner is None or owner != user.username:
        raise ApiError(404, "matter_not_found", "Matter not found.")
    return MatterAccess(id=matter_id, owner=owner, read_only=False)


def get_matter(matter_id: Annotated[str, Path()], user: CurrentUser) -> MatterAccess:
    return resolve_matter_access(matter_id, user)


def get_writable_matter(matter: Annotated[MatterAccess, Depends(get_matter)]) -> MatterAccess:
    if matter.read_only:
        raise ApiError(403, "matter_read_only", "This shared matter is read-only.")
    return matter


Matter = Annotated[MatterAccess, Depends(get_matter)]
WritableMatter = Annotated[MatterAccess, Depends(get_writable_matter)]


def get_services(request: Request) -> AdvisorServices:
    return request.app.state.services


def get_jobs(request: Request) -> JobTable:
    return request.app.state.jobs


def get_chat_jobs(request: Request) -> ChatJobTable:
    return request.app.state.chat_jobs


Services = Annotated[AdvisorServices, Depends(get_services)]
Jobs = Annotated[JobTable, Depends(get_jobs)]
ChatJobs = Annotated[ChatJobTable, Depends(get_chat_jobs)]
