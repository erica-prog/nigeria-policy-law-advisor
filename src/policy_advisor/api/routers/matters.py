import uuid

from fastapi import APIRouter, Response, status

from policy_advisor.api.conversations import now_iso
from typing import Annotated

from fastapi import Depends

from policy_advisor.api.credentials import UserRecord
from policy_advisor.tenancy import membership
from policy_advisor.api.deps import (
    CurrentUser,
    require_role,
    Jobs,
    Matter,
    MatterAccess,
    Services,
    WritableMatter,
    resolve_matter_access,
)
from policy_advisor.api.errors import ApiError
from policy_advisor.api.schemas import (
    HoldRequest,
    MatterCreate,
    MatterList,
    MatterOut,
    ShareRequest,
)
from policy_advisor.config import LIBRARY_MATTER_IDS
from policy_advisor.ingestion.ingest_document import delete_matter as delete_matter_files
from policy_advisor.ingestion.matter_store import (
    get_matter_meta,
    list_documents,
    list_matters,
    list_matters_for_user,
    set_hold,
    set_matter_owner,
    share_matter,
    shared_with,
    unshare_matter,
    update_matter_meta,
)
from policy_advisor.logging_utils import log_event

router = APIRouter(prefix="/api/matters", tags=["matters"])

SHARED_MATTER_TITLE = "Reference library (shared, read-only)"


def matter_out(matter: MatterAccess) -> MatterOut:
    meta = get_matter_meta(matter.id)
    return MatterOut(
        id=matter.id,
        owner=matter.owner,
        read_only=matter.read_only,
        document_count=len(list_documents(matter.id)),
        title=SHARED_MATTER_TITLE if matter.id in LIBRARY_MATTER_IDS else meta.get("title"),
        created_at=meta.get("created_at"),
        updated_at=meta.get("updated_at"),
        shared_with=[] if matter.id in LIBRARY_MATTER_IDS else shared_with(matter.id),
        hold=bool(meta.get("hold")),
        hold_reason=meta.get("hold_reason"),
    )


def _generate_matter_id() -> str:
    taken = set(list_matters()) | LIBRARY_MATTER_IDS
    while True:
        candidate = f"case-{uuid.uuid4().hex[:12]}"
        if candidate not in taken:
            return candidate


@router.get("", response_model=MatterList)
def list_my_matters(user: CurrentUser) -> MatterList:
    matters = []
    for matter_id in list_matters_for_user(user.username):
        try:
            matters.append(matter_out(resolve_matter_access(matter_id, user)))
        except ApiError:
            continue  # legacy directory without a valid id or owner; never listed
    # Stable sorts, least significant first: most recently used at the top, the
    # shared library (no timestamps) last.
    matters.sort(key=lambda m: m.id)
    matters.sort(key=lambda m: m.updated_at or m.created_at or "", reverse=True)
    matters.sort(key=lambda m: m.read_only)
    return MatterList(matters=matters)


@router.post("", response_model=MatterOut, status_code=status.HTTP_201_CREATED)
def create_matter(
    body: MatterCreate,
    user: Annotated[UserRecord, Depends(require_role("admin", "lawyer"))],
    services: Services,
) -> MatterOut:
    if body.id is not None and (body.id in LIBRARY_MATTER_IDS or body.id in list_matters()):
        raise ApiError(409, "matter_exists", "A matter with this id already exists.")
    matter_id = body.id or _generate_matter_id()
    set_matter_owner(matter_id, user.username)
    stamp = now_iso()
    update_matter_meta(matter_id, title=body.title, created_at=stamp, updated_at=stamp)
    log_event(services.logger, "matter_created", matter_id=matter_id, owner=user.username)
    return matter_out(resolve_matter_access(matter_id, user))


@router.get("/{matter_id}", response_model=MatterOut)
def get_one_matter(matter: Matter) -> MatterOut:
    return matter_out(matter)


@router.delete("/{matter_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_one_matter(matter: WritableMatter, services: Services, jobs: Jobs) -> Response:
    """Deletes the case, its documents and its chat. The shared library is refused."""
    jobs.forget_matter(matter.id)
    delete_matter_files(matter.id)
    # Retained originals go with the matter (the documents rows cascade).
    services.blob_store.delete_prefix(f"{matter.id}/")
    services.invalidate_matter(matter.id)
    log_event(services.logger, "matter_deleted", matter_id=matter.id, owner=matter.owner)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{matter_id}/share", response_model=MatterOut)
def share_one_matter(body: ShareRequest, matter: WritableMatter, user: CurrentUser) -> MatterOut:
    """Share a matter with a user of the same firm (docs/31). Only the owner
    may share; the database policies keep the share inside the tenant, and a
    reviewer who is given `can_write` still cannot write."""
    if matter.owner != user.username:
        raise ApiError(403, "not_owner", "Only the owner can share this matter.")
    if body.username == user.username:
        raise ApiError(400, "invalid_share", "You already own this matter.")
    target = membership(body.username)
    if target is None or target.tenant_id != user.tenant_id:
        raise ApiError(404, "user_not_found", "No such user in your firm.")
    share_matter(matter.id, body.username, can_write=body.can_write)
    return matter_out(matter)


@router.delete("/{matter_id}/share/{username}", status_code=status.HTTP_204_NO_CONTENT)
def unshare_one_matter(username: str, matter: WritableMatter, user: CurrentUser) -> Response:
    if matter.owner != user.username:
        raise ApiError(403, "not_owner", "Only the owner can change sharing.")
    unshare_matter(matter.id, username)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{matter_id}/hold", response_model=MatterOut)
def hold_one_matter(body: HoldRequest, matter: Matter, user: CurrentUser) -> MatterOut:
    """A legal hold (docs/37): the owner or a firm admin places or lifts it;
    the retention job never deletes a held matter. A reviewer may not."""
    if matter.id in LIBRARY_MATTER_IDS:
        raise ApiError(
            403, "matter_read_only", "The shared libraries are never deleted and cannot be held."
        )
    if matter.owner != user.username and user.role != "admin":
        raise ApiError(403, "not_owner", "Only the owner or a firm admin can change a hold.")
    set_hold(matter.id, body.on, reason=body.reason, by=user.username)
    return matter_out(matter)
