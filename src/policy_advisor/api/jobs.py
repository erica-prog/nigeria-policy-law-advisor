"""In-process document-processing job table (docs/architecture/web-mvp.md).
The MVP stand-in for a job queue: status per upload, curated failure
messages, forgotten on restart. Documents that reach `ready` are persisted by
the ingestion module itself, so only in-flight state is volatile."""

import shutil
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from time import time

from policy_advisor.api.schemas import DocumentOut, DocumentStatus
from policy_advisor.ingestion.ingest_document import UnsupportedDocumentError
from policy_advisor.logging_utils import log_event

GENERIC_FAILURE = "Processing failed. The server log has the details."
EMPTY_FAILURE = "No readable text was found in this file."
MODEL_FAILURE = "Processing needs the language model, which is not reachable right now."


@dataclass
class DocumentJob:
    job_id: str
    matter_id: str
    document_name: str
    status: DocumentStatus = "queued"
    chunk_count: int | None = None
    error: str | None = None
    created_at: float = field(default_factory=time)
    temp_dir: Path | None = None

    def to_document(self) -> DocumentOut:
        return DocumentOut(
            name=self.document_name,
            status=self.status,
            job_id=self.job_id,
            chunk_count=self.chunk_count,
            error=self.error,
        )


def failure_message(exc: Exception) -> str:
    if isinstance(exc, UnsupportedDocumentError):
        return "Unsupported document format. Upload a PDF or DOCX file."
    if isinstance(exc, ValueError) and "zero chunks" in str(exc):
        return EMPTY_FAILURE
    name = type(exc).__name__.lower()
    if "authentication" in name or "apiconnection" in name or "apistatus" in name:
        return MODEL_FAILURE
    return GENERIC_FAILURE


class JobTable:
    def __init__(self) -> None:
        self._jobs: dict[str, DocumentJob] = {}
        self._lock = threading.Lock()

    def create(self, matter_id: str, document_name: str, temp_dir: Path) -> DocumentJob:
        job = DocumentJob(
            job_id=uuid.uuid4().hex,
            matter_id=matter_id,
            document_name=document_name,
            temp_dir=temp_dir,
        )
        with self._lock:
            self._jobs[job.job_id] = job
        return job

    def get(self, job_id: str) -> DocumentJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def latest_for_matter(self, matter_id: str) -> dict[str, DocumentJob]:
        """Most recent job per document name, for merging into the document list."""
        with self._lock:
            jobs = sorted(
                (j for j in self._jobs.values() if j.matter_id == matter_id),
                key=lambda j: j.created_at,
            )
        return {job.document_name: job for job in jobs}

    def forget_document(self, matter_id: str, document_name: str) -> None:
        with self._lock:
            for job_id in [
                j.job_id
                for j in self._jobs.values()
                if j.matter_id == matter_id and j.document_name == document_name
            ]:
                del self._jobs[job_id]

    def run(self, job: DocumentJob, services, jurisdiction: str | None) -> None:
        """Executes one ingestion job. Called from FastAPI BackgroundTasks."""
        from policy_advisor.ingestion.ingest_document import add_document

        job.status = "processing"
        file_path = (job.temp_dir or Path()) / job.document_name
        try:
            with services.ingest_lock:
                job.chunk_count = add_document(job.matter_id, file_path, jurisdiction=jurisdiction)
            services.invalidate_matter(job.matter_id)
            job.status = "ready"
            log_event(
                services.logger,
                "document_ingested",
                matter_id=job.matter_id,
                document=job.document_name,
                chunk_count=job.chunk_count,
            )
        except Exception as exc:
            job.status = "failed"
            job.error = failure_message(exc)
            services.logger.error(
                "document_ingest_failed",
                exc_info=exc,
                extra={"fields": {"matter_id": job.matter_id, "document": job.document_name}},
            )
        finally:
            if job.temp_dir is not None:
                shutil.rmtree(job.temp_dir, ignore_errors=True)
                job.temp_dir = None
