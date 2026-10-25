"""Bulk analysis over a matter (docs/33): start a job, follow it, resume it
after it paused at its ceiling, read the records and the findings.

Depends on `Matter`, so the ownership and tenant checks run first (rule 1);
starting or resuming a job needs a writable matter, since it spends the
submitter's key; reading records and findings needs read access."""

from fastapi import APIRouter, BackgroundTasks, status

from policy_advisor.api.deps import CurrentUser, Matter, Services, WritableMatter
from policy_advisor.api.errors import ApiError
from policy_advisor.api.routers.advice import require_key
from policy_advisor.api.schemas import (
    BulkJobCreate,
    ReviewEventList,
    ReviewRequest,
    BulkJobList,
    BulkJobOut,
    BulkResumeRequest,
    ExtractionList,
    ExtractionOut,
    FindingList,
    FindingOut,
)
from policy_advisor.bulk.extract import Extractor, run_pending
from policy_advisor.bulk.jobs import BulkJob, BulkJobTable, list_extractions, list_findings
from policy_advisor.bulk.schemas import SCHEMAS, get_schema
from policy_advisor.config import get_settings
from policy_advisor.ingestion.matter_store import list_documents
from policy_advisor.logging_utils import log_event

router = APIRouter(prefix="/api/matters/{matter_id}/bulk", tags=["bulk"])


def job_out(job: BulkJob) -> BulkJobOut:
    return BulkJobOut(
        job_id=job.job_id,
        matter_id=job.matter_id,
        schema_id=job.schema_id,
        schema_version=job.schema_version,
        documents=list(job.documents),
        status=job.status,  # type: ignore[arg-type]
        backend=job.backend,  # type: ignore[arg-type]
        triage_model=job.triage_model,
        escalation_model=job.escalation_model,
        ceiling_tokens=job.ceiling_tokens,
        spent_tokens=job.spent_tokens,
        done_documents=job.done_documents,
        flagged_documents=job.flagged_documents,
        error=job.error,
        created_at=job.created_at.isoformat() if job.created_at else None,
        finished_at=job.finished_at.isoformat() if job.finished_at else None,
    )


def _run_inline(api_key: str, table: BulkJobTable) -> None:
    run_pending(
        None, limit=1, extractor_factory=lambda job: Extractor(api_key, jobs=table), jobs=table
    )


@router.post("", response_model=BulkJobOut, status_code=status.HTTP_202_ACCEPTED)
def start_bulk_job(
    body: BulkJobCreate,
    matter: WritableMatter,
    user: CurrentUser,
    services: Services,
    background: BackgroundTasks,
) -> BulkJobOut:
    api_key = require_key(user)
    settings = get_settings()
    try:
        schema = get_schema(body.schema_id)
    except KeyError:
        raise ApiError(
            400, "unknown_schema", f"Unknown schema; known: {', '.join(sorted(SCHEMAS))}."
        ) from None
    available = list_documents(matter.id)
    documents = body.documents or available
    missing = sorted(set(documents) - set(available))
    if missing:
        raise ApiError(400, "unknown_documents", f"Not in this matter: {', '.join(missing[:5])}.")
    if not documents:
        raise ApiError(400, "no_documents", "This matter has no indexed documents to analyse.")
    backend = body.backend or (
        "batch" if len(documents) > settings.bulk_batch_threshold else "sync"
    )
    table = BulkJobTable()
    job = table.create(
        matter.id,
        schema_id=schema.schema_id,
        schema_version=schema.version,
        documents=documents,
        triage_model=settings.bulk_triage_model,
        escalation_model=settings.bulk_escalation_model or settings.anthropic_model,
        ceiling_tokens=body.ceiling_tokens or settings.bulk_default_ceiling_tokens,
        backend=backend,
        submitted_by=user.username,
    )
    log_event(
        services.logger,
        "bulk_job_submitted",
        job_id=job.job_id,
        matter_id=matter.id,
        documents=len(documents),
        backend=backend,
    )
    if settings.inline_workers:
        background.add_task(_run_inline, api_key, table)
    return job_out(job)


@router.get("", response_model=BulkJobList)
def list_bulk_jobs(matter: Matter) -> BulkJobList:
    return BulkJobList(jobs=[job_out(j) for j in BulkJobTable().list_for_matter(matter.id)])


@router.get("/{job_id}", response_model=BulkJobOut)
def get_bulk_job(job_id: str, matter: Matter) -> BulkJobOut:
    job = BulkJobTable().get(job_id)
    if job is None or job.matter_id != matter.id:
        raise ApiError(404, "not_found", "Not found.")
    return job_out(job)


@router.post("/{job_id}/resume", response_model=BulkJobOut, status_code=status.HTTP_202_ACCEPTED)
def resume_bulk_job(
    job_id: str,
    body: BulkResumeRequest,
    matter: WritableMatter,
    user: CurrentUser,
    background: BackgroundTasks,
) -> BulkJobOut:
    api_key = require_key(user)
    table = BulkJobTable()
    job = table.get(job_id)
    if job is None or job.matter_id != matter.id:
        raise ApiError(404, "not_found", "Not found.")
    try:
        job = table.resume(job_id, body.ceiling_tokens)
    except ValueError as exc:
        raise ApiError(409, "cannot_resume", str(exc)) from None
    if get_settings().inline_workers:
        background.add_task(_run_inline, api_key, table)
    return job_out(job)


@router.post("/{job_id}/cancel", response_model=BulkJobOut)
def cancel_bulk_job(job_id: str, matter: WritableMatter) -> BulkJobOut:
    table = BulkJobTable()
    job = table.get(job_id)
    if job is None or job.matter_id != matter.id:
        raise ApiError(404, "not_found", "Not found.")
    if job.status in ("done", "cancelled"):
        return job_out(job)
    return job_out(table.cancel(job_id))


@router.get("/records/{schema_id}", response_model=ExtractionList)
def list_records(schema_id: str, matter: Matter, document: str | None = None) -> ExtractionList:
    rows = list_extractions(matter.id, schema_id=schema_id, source_document=document)
    return ExtractionList(
        records=[
            ExtractionOut(
                document=r["source_document"],
                schema_id=r["schema_id"],
                schema_version=r["schema_version"],
                job_id=r["job_id"],
                record=r["record"],
                evidence_spans=r["evidence_spans"],
                confidence=float(r["confidence"]),
                model=r["model"],
                flagged=r["flagged"],
                created_at=r["created_at"].isoformat() if r.get("created_at") else None,
            )
            for r in rows
        ]
    )


def finding_out(r: dict) -> FindingOut:
    return FindingOut(
        id=r["id"],
        job_id=r["job_id"],
        kind=r["kind"],
        severity=r["severity"],  # type: ignore[arg-type]
        summary=r["summary"],
        documents=list(r["documents"]),
        evidence=r["evidence"],
        status=r["status"],  # type: ignore[arg-type]
        created_at=r["created_at"].isoformat() if r.get("created_at") else None,
    )


@router.get("/findings/all", response_model=FindingList)
def list_matter_findings(matter: Matter, status_filter: str | None = None) -> FindingList:
    return FindingList(
        findings=[finding_out(r) for r in list_findings(matter.id, status=status_filter)]
    )


@router.post("/findings/{finding_id}/review", response_model=FindingOut)
def review_finding(
    finding_id: int, body: ReviewRequest, matter: Matter, user: CurrentUser
) -> FindingOut:
    from policy_advisor import review

    try:
        updated = review.transition_finding(
            finding_id, body.action, user=user, access=matter, note=body.note
        )
    except KeyError:
        raise ApiError(404, "not_found", "Not found.") from None
    except review.ReviewRefused as exc:
        status_code = {"role_forbidden": 403}.get(exc.code, 409)
        raise ApiError(status_code, exc.code, str(exc)) from exc
    return finding_out(updated)


@router.get("/findings/{finding_id}/events", response_model=ReviewEventList)
def finding_events(finding_id: int, matter: Matter) -> ReviewEventList:
    from policy_advisor import review
    from policy_advisor.api.routers.artefacts import event_out
    from policy_advisor.bulk.jobs import get_finding

    finding = get_finding(finding_id)
    if finding is None or finding["matter_id"] != matter.id:
        raise ApiError(404, "not_found", "Not found.")
    return ReviewEventList(
        events=[event_out(e) for e in review.events_for("finding", str(finding_id))]
    )
