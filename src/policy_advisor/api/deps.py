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
from policy_advisor.api.sessions import SESSION_COOKIE, read_session
from policy_advisor.config import LIBRARY_MATTER_IDS
from policy_advisor.ingestion.matter_store import get_matter_owner, share_for
from policy_advisor.tenancy import current_tenant, membership

_MATTER_ID_RE = re.compile(MATTER_ID_PATTERN)


@dataclass(frozen=True)
class MatterAccess:
    id: str
    owner: str | None
    read_only: bool


def current_user(request: Request) -> UserRecord:
    """The logged-in user with their tenant and role. The tenant the cookie
    claims must still be the one `tenant_users` records: a user moved or
    removed is logged out at their next request. The TenantMiddleware has
    already set the tenant context from the same cookie, so this lookup and
    everything after it run under the right policies."""
    token = request.cookies.get(SESSION_COOKIE)
    session = read_session(token) if token else None
    user = get_user(session[0]) if session else None
    if user is None or session is None:
        raise ApiError(401, "unauthenticated", "Log in to continue.")
    member = membership(user.username)
    if member is None or (session[1] is not None and session[1] != member.tenant_id):
        raise ApiError(401, "unauthenticated", "Log in again to continue.")
    if current_tenant() != member.tenant_id:
        # A cookie from before tenancy carries no tenant claim; the middleware
        # could not set one, so this request runs with none.
        raise ApiError(401, "unauthenticated", "Log in again to continue.")
    return UserRecord(
        username=user.username,
        display_name=user.display_name,
        tenant_id=member.tenant_id,
        role=member.role,
    )


CurrentUser = Annotated[UserRecord, Depends(current_user)]


def resolve_matter_access(matter_id: str, user: UserRecord) -> MatterAccess:
    """Owner: full access. Shared with this user: read, or write when the
    share says so. Reviewer role: never write. Another tenant's matter is
    invisible under the database policies, so it is a 404 like any unknown id."""
    if not _MATTER_ID_RE.match(matter_id):
        raise ApiError(404, "matter_not_found", "Matter not found.")
    if matter_id in LIBRARY_MATTER_IDS:
        return MatterAccess(id=matter_id, owner=None, read_only=True)
    owner = get_matter_owner(matter_id)
    if owner is None:
        raise ApiError(404, "matter_not_found", "Matter not found.")
    if owner == user.username:
        return MatterAccess(id=matter_id, owner=owner, read_only=user.role == "reviewer")
    share = share_for(matter_id, user.username)
    if share is None:
        raise ApiError(404, "matter_not_found", "Matter not found.")
    writable = bool(share.get("can_write")) and user.role != "reviewer"
    return MatterAccess(id=matter_id, owner=owner, read_only=not writable)


def get_matter(matter_id: Annotated[str, Path()], user: CurrentUser) -> MatterAccess:
    return resolve_matter_access(matter_id, user)


def get_writable_matter(matter: Annotated[MatterAccess, Depends(get_matter)]) -> MatterAccess:
    if matter.read_only:
        raise ApiError(403, "matter_read_only", "This matter is read-only for you.")
    return matter


def require_role(*roles: str):
    """A dependency that admits only these roles (docs/31): reviewers read
    and approve, they do not create matters or upload."""

    def check(user: CurrentUser) -> UserRecord:
        if user.role not in roles:
            raise ApiError(403, "role_forbidden", f"Your role ({user.role}) cannot do this.")
        return user

    return check


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
