"""Artefacts (docs/35): draft a memo or deck for a matter, list and read
drafts with their provenance manifests, download the files. Drafting needs
a writable matter and the caller's own key, which pays; reading needs read
access. Nothing here sends or files anything."""

from fastapi import APIRouter, status
from fastapi.responses import StreamingResponse

from policy_advisor import review
from policy_advisor.api.deps import CurrentUser, Matter, Services, WritableMatter
from policy_advisor.api.errors import ApiError
from policy_advisor.api.routers.advice import require_key
from policy_advisor.api.schemas import (
    ArtefactList,
    ArtefactOut,
    DraftRequest,
    ReviewEventList,
    ReviewEventOut,
    ReviewQueueOut,
    ReviewRequest,
    TemplateList,
    TemplateOut,
)
from policy_advisor.artefacts.outline import OutlineError
from policy_advisor.artefacts.store import (
    ArtefactRecord,
    get_artefact,
    get_template,
    list_artefacts,
    list_templates,
)
from policy_advisor.api.routers.bulk import finding_out
from policy_advisor.logging_utils import log_event

router = APIRouter(prefix="/api", tags=["artefacts"])

MEDIA = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


def artefact_out(record: ArtefactRecord) -> ArtefactOut:
    return ArtefactOut(
        artefact_id=record.artefact_id,
        matter_id=record.matter_id,
        kind=record.kind,  # type: ignore[arg-type]
        title=record.title,
        status=record.status,  # type: ignore[arg-type]
        instruction=record.instruction,
        outline=record.outline,
        manifest=record.manifest,
        has_docx=record.docx_key is not None,
        has_pptx=record.pptx_key is not None,
        template_id=record.template_id,
        run_id=record.run_id,
        created_by=record.created_by,
        created_at=record.created_at.isoformat() if record.created_at else None,
        updated_at=record.updated_at.isoformat() if record.updated_at else None,
    )


@router.get("/templates", response_model=TemplateList)
def templates(user: CurrentUser, kind: str | None = None) -> TemplateList:
    return TemplateList(
        templates=[
            TemplateOut(
                template_id=t.template_id,
                kind=t.kind,  # type: ignore[arg-type]
                name=t.name,
                is_default=t.is_default,
                shared=t.tenant_id == "shared",
            )  # type: ignore[arg-type]
            for t in list_templates(kind)
        ]
    )


@router.post(
    "/matters/{matter_id}/artefacts",
    response_model=ArtefactOut,
    status_code=status.HTTP_201_CREATED,
)
def draft_artefact(
    body: DraftRequest, matter: WritableMatter, user: CurrentUser, services: Services
) -> ArtefactOut:
    api_key = require_key(user)
    if body.template_id and get_template(body.template_id) is None:
        raise ApiError(404, "template_not_found", "No such template.")
    try:
        result = services.drafting_agent_for(api_key).draft(
            body.instruction,
            matter_id=matter.id,
            username=user.username,
            kind=body.kind,
            template_id=body.template_id,
            jurisdiction=body.jurisdiction,
            allow_web=body.allow_web,
        )
    except OutlineError as exc:
        raise ApiError(502, "draft_failed", str(exc)) from exc
    log_event(
        services.logger,
        "artefact_created",
        artefact_id=result.artefact.artefact_id,
        matter_id=matter.id,
        kind=body.kind,
    )
    return artefact_out(result.artefact)


@router.get("/matters/{matter_id}/artefacts", response_model=ArtefactList)
def list_matter_artefacts(matter: Matter) -> ArtefactList:
    return ArtefactList(artefacts=[artefact_out(a) for a in list_artefacts(matter.id)])


def _owned(artefact_id: str, matter_id: str) -> ArtefactRecord:
    record = get_artefact(artefact_id)
    if record is None or record.matter_id != matter_id:
        raise ApiError(404, "not_found", "Not found.")
    return record


@router.get("/matters/{matter_id}/artefacts/{artefact_id}", response_model=ArtefactOut)
def get_matter_artefact(artefact_id: str, matter: Matter) -> ArtefactOut:
    return artefact_out(_owned(artefact_id, matter.id))


@router.get("/matters/{matter_id}/artefacts/{artefact_id}/file")
def download_artefact(
    artefact_id: str, matter: Matter, services: Services, format: str = "docx"
) -> StreamingResponse:
    record = _owned(artefact_id, matter.id)
    if format not in MEDIA:
        raise ApiError(400, "bad_format", "format must be docx or pptx")
    key = record.docx_key if format == "docx" else record.pptx_key
    if not key or not services.blob_store.exists(key):
        raise ApiError(404, "not_found", f"This artefact has no {format} file.")
    handle = services.blob_store.open(key)
    safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in record.title)[:60] or "artefact"
    return StreamingResponse(
        handle,
        media_type=MEDIA[format],
        headers={"Content-Disposition": f'attachment; filename="{safe}.{format}"'},
    )


def _refused(exc: review.ReviewRefused) -> ApiError:
    status_code = {"role_forbidden": 403, "wrong_state": 409, "immutable": 409}.get(exc.code, 409)
    return ApiError(status_code, exc.code, str(exc))


def event_out(event: review.ReviewEvent) -> ReviewEventOut:
    return ReviewEventOut(
        id=event.id,
        subject_kind=event.subject_kind,  # type: ignore[arg-type]
        subject_id=event.subject_id,
        action=event.action,
        from_status=event.from_status,
        to_status=event.to_status,
        username=event.username,
        role=event.role,
        note=event.note,
        created_at=event.created_at.isoformat() if event.created_at else None,
    )


@router.post("/matters/{matter_id}/artefacts/{artefact_id}/review", response_model=ArtefactOut)
def review_artefact(
    artefact_id: str, body: ReviewRequest, matter: Matter, user: CurrentUser, services: Services
) -> ArtefactOut:
    """A review transition (docs/36). Reviewers act on read-only matters;
    the role rules are in `review.py`, the trail in `review_events`."""
    try:
        record = review.transition_artefact(
            artefact_id, body.action, user=user, access=matter, note=body.note
        )
    except KeyError:
        raise ApiError(404, "not_found", "Not found.") from None
    except review.ReviewRefused as exc:
        raise _refused(exc) from exc
    log_event(
        services.logger,
        "artefact_reviewed",
        artefact_id=artefact_id,
        action=body.action,
        status=record.status,
        username=user.username,
    )
    return artefact_out(record)


@router.get("/matters/{matter_id}/artefacts/{artefact_id}/events", response_model=ReviewEventList)
def artefact_events(artefact_id: str, matter: Matter) -> ReviewEventList:
    _owned(artefact_id, matter.id)
    return ReviewEventList(
        events=[event_out(e) for e in review.events_for("artefact", artefact_id)]
    )


@router.get("/matters/{matter_id}/review-queue", response_model=ReviewQueueOut)
def matter_review_queue(matter: Matter) -> ReviewQueueOut:
    queue = review.review_queue(matter.id)
    return ReviewQueueOut(
        artefacts=[artefact_out(a) for a in queue["artefacts"]],
        findings=[finding_out(f) for f in queue["findings"]],
    )
