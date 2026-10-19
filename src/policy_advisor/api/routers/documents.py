import re
import tempfile
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, File, Form, Response, UploadFile, status

from policy_advisor.api.conversations import now_iso
from policy_advisor.api.deps import CurrentUser, Jobs, Matter, Services, WritableMatter
from policy_advisor.api.errors import ApiError
from policy_advisor.api.schemas import DocumentList, DocumentOut
from policy_advisor.api.user_keys import resolve_anthropic_key
from policy_advisor.config import get_settings
from policy_advisor.ingestion.chunk import SUPPORTED_SUFFIXES
from policy_advisor.ingestion.ingest_document import remove_document
from policy_advisor.ingestion.matter_store import list_documents, update_matter_meta
from policy_advisor.logging_utils import log_event

router = APIRouter(prefix="/api/matters/{matter_id}/documents", tags=["documents"])

_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._ ()\-]")
_READ_CHUNK = 1024 * 1024


def safe_document_name(raw_name: str | None) -> str:
    name = Path(raw_name or "").name.strip()
    name = _UNSAFE_CHARS.sub("_", name).lstrip(".")
    if not name or Path(name).suffix.lower() not in SUPPORTED_SUFFIXES:
        raise ApiError(415, "unsupported_document", "Upload a PDF or DOCX file.")
    return name[:200]


def _document_rows(matter_id: str, jobs: Jobs) -> list[DocumentOut]:
    latest = jobs.latest_for_matter(matter_id)
    rows: dict[str, DocumentOut] = {
        name: DocumentOut(name=name, status="ready") for name in list_documents(matter_id)
    }
    for name, job in latest.items():
        if job.status == "ready" and name in rows:
            rows[name] = job.to_document()
        elif job.status != "ready":
            rows[name] = job.to_document()
    return sorted(rows.values(), key=lambda d: d.name.lower())


@router.get("", response_model=DocumentList)
def list_matter_documents(matter: Matter, jobs: Jobs) -> DocumentList:
    return DocumentList(documents=_document_rows(matter.id, jobs))


@router.get("/{name}", response_model=DocumentOut)
def get_document(name: str, matter: Matter, jobs: Jobs) -> DocumentOut:
    for row in _document_rows(matter.id, jobs):
        if row.name == name:
            return row
    raise ApiError(404, "document_not_found", "Document not found.")


@router.post("", response_model=DocumentOut, status_code=status.HTTP_202_ACCEPTED)
async def upload_document(
    matter: WritableMatter,
    jobs: Jobs,
    services: Services,
    user: CurrentUser,
    background: BackgroundTasks,
    file: Annotated[UploadFile, File()],
    jurisdiction: Annotated[Literal["federal", "lagos"] | None, Form()] = None,
) -> DocumentOut:
    name = safe_document_name(file.filename)
    max_bytes = get_settings().max_upload_mb * 1024 * 1024
    temp_dir = Path(tempfile.mkdtemp(prefix="pa-upload-"))
    written = 0
    with (temp_dir / name).open("wb") as out:
        while chunk := await file.read(_READ_CHUNK):
            written += len(chunk)
            if written > max_bytes:
                out.close()
                (temp_dir / name).unlink(missing_ok=True)
                temp_dir.rmdir()
                raise ApiError(413, "upload_too_large", "The uploaded file is too large.")
            out.write(chunk)
    if written == 0:
        (temp_dir / name).unlink(missing_ok=True)
        temp_dir.rmdir()
        raise ApiError(415, "unsupported_document", "The uploaded file is empty.")

    job = jobs.create(matter.id, name, temp_dir)
    update_matter_meta(matter.id, updated_at=now_iso())
    log_event(services.logger, "document_upload_queued", matter_id=matter.id, document=name)
    # Translation during ingestion runs on the uploader's own key (or is
    # skipped when they have none); the server key is never spent here.
    background.add_task(jobs.run, job, services, jurisdiction, resolve_anthropic_key(user.username))
    return job.to_document()


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(name: str, matter: WritableMatter, jobs: Jobs, services: Services) -> Response:
    known = {row.name for row in _document_rows(matter.id, jobs)}
    if name not in known:
        raise ApiError(404, "document_not_found", "Document not found.")
    if name in list_documents(matter.id):
        remove_document(matter.id, name)
        services.invalidate_matter(matter.id)
    jobs.forget_document(matter.id, name)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
