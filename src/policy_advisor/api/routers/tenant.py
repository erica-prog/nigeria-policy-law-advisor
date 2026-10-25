"""The firm's own settings (docs/37): residency, retention, and the
retention trail. Any member reads; an admin changes. The database's own
location bounds what residency may be asked for."""

from fastapi import APIRouter, Depends
from typing import Annotated

from policy_advisor import tenancy
from policy_advisor.api.credentials import UserRecord
from policy_advisor.api.deps import CurrentUser, require_role
from policy_advisor.api.errors import ApiError
from policy_advisor.api.schemas import (
    RetentionEventList,
    RetentionEventOut,
    TenantOut,
    TenantUpdate,
)
from policy_advisor import config
from policy_advisor.retention import retention_events

router = APIRouter(prefix="/api/tenant", tags=["tenant"])


def tenant_out(settings: tenancy.TenantSettings) -> TenantOut:
    deployment = config.get_settings()  # through the module: tests patch it there
    return TenantOut(
        tenant_id=settings.tenant_id,
        name=settings.name,
        residency=settings.residency,  # type: ignore[arg-type]
        retention_days_matter=settings.retention_days_matter,
        retention_days_firm=settings.retention_days_firm,
        deployment_residency=deployment.database_residency.strip().lower() or None,  # type: ignore[arg-type]
        retention_min_days=deployment.retention_min_days,
    )


@router.get("", response_model=TenantOut)
def my_tenant(user: CurrentUser) -> TenantOut:
    assert user.tenant_id is not None
    settings = tenancy.get_tenant(user.tenant_id)
    if settings is None:
        raise ApiError(404, "not_found", "Not found.")
    return tenant_out(settings)


@router.put("", response_model=TenantOut)
def update_my_tenant(
    body: TenantUpdate, user: Annotated[UserRecord, Depends(require_role("admin"))]
) -> TenantOut:
    assert user.tenant_id is not None
    fields = body.model_dump(exclude_unset=True)
    try:
        settings = tenancy.update_tenant(user.tenant_id, **fields)
    except tenancy.ResidencyNotHonoured as exc:
        raise ApiError(400, "residency_not_honoured", str(exc)) from None
    except tenancy.RetentionTooShort as exc:
        raise ApiError(400, "retention_too_short", str(exc)) from None
    except ValueError as exc:
        raise ApiError(400, "invalid_settings", str(exc)) from None
    return tenant_out(settings)


@router.get("/retention-events", response_model=RetentionEventList)
def my_retention_events(
    user: Annotated[UserRecord, Depends(require_role("admin", "lawyer"))],
) -> RetentionEventList:
    return RetentionEventList(
        events=[
            RetentionEventOut(
                id=r["id"],
                matter_id=r["matter_id"],
                action=r["action"],  # type: ignore[arg-type]
                reason=r["reason"],
                counts=r["counts"],
                actor=r["actor"],
                created_at=r["created_at"].isoformat() if r.get("created_at") else None,
            )
            for r in retention_events(user.tenant_id)
        ]
    )
