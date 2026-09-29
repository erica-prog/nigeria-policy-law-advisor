from fastapi import APIRouter, status

from policy_advisor.api.deps import (
    CurrentUser,
    Matter,
    MatterAccess,
    Services,
    resolve_matter_access,
)
from policy_advisor.api.errors import ApiError
from policy_advisor.api.schemas import MatterCreate, MatterList, MatterOut
from policy_advisor.config import PHASE1_DEMO_MATTER_ID
from policy_advisor.ingestion.matter_store import (
    list_documents,
    list_matters,
    list_matters_for_user,
    set_matter_owner,
)
from policy_advisor.logging_utils import log_event

router = APIRouter(prefix="/api/matters", tags=["matters"])


def matter_out(matter: MatterAccess) -> MatterOut:
    return MatterOut(
        id=matter.id,
        owner=matter.owner,
        read_only=matter.read_only,
        document_count=len(list_documents(matter.id)),
    )


@router.get("", response_model=MatterList)
def list_my_matters(user: CurrentUser) -> MatterList:
    matters = []
    for matter_id in list_matters_for_user(user.username):
        try:
            matters.append(matter_out(resolve_matter_access(matter_id, user)))
        except ApiError:
            continue  # legacy directory without a valid id or owner; never listed
    return MatterList(matters=matters)


@router.post("", response_model=MatterOut, status_code=status.HTTP_201_CREATED)
def create_matter(body: MatterCreate, user: CurrentUser, services: Services) -> MatterOut:
    if body.id == PHASE1_DEMO_MATTER_ID or body.id in list_matters():
        raise ApiError(409, "matter_exists", "A matter with this id already exists.")
    set_matter_owner(body.id, user.username)
    log_event(services.logger, "matter_created", matter_id=body.id, owner=user.username)
    return matter_out(resolve_matter_access(body.id, user))


@router.get("/{matter_id}", response_model=MatterOut)
def get_one_matter(matter: Matter) -> MatterOut:
    return matter_out(matter)
