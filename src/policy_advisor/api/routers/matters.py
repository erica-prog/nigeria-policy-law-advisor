import uuid

from fastapi import APIRouter, Response, status

from policy_advisor.api.conversations import now_iso
from policy_advisor.api.deps import (
    CurrentUser,
    Jobs,
    Matter,
    MatterAccess,
    Services,
    WritableMatter,
    resolve_matter_access,
)
from policy_advisor.api.errors import ApiError
from policy_advisor.api.schemas import MatterCreate, MatterList, MatterOut
from policy_advisor.config import PHASE1_DEMO_MATTER_ID
from policy_advisor.ingestion.matter_store import (
    get_matter_meta,
    list_documents,
    list_matters,
    list_matters_for_user,
    set_matter_owner,
    update_matter_meta,
)
from policy_advisor.ingestion.ingest_document import delete_matter as delete_matter_files
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
        title=SHARED_MATTER_TITLE if matter.read_only else meta.get("title"),
        created_at=meta.get("created_at"),
        updated_at=meta.get("updated_at"),
    )


def _generate_matter_id() -> str:
    taken = set(list_matters()) | {PHASE1_DEMO_MATTER_ID}
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
def create_matter(body: MatterCreate, user: CurrentUser, services: Services) -> MatterOut:
    if body.id is not None and (body.id == PHASE1_DEMO_MATTER_ID or body.id in list_matters()):
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
    services.invalidate_matter(matter.id)
    log_event(services.logger, "matter_deleted", matter_id=matter.id, owner=matter.owner)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
