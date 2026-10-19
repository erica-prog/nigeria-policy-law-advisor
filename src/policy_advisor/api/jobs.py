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
SCANNED_FAILURE = (
    "This PDF looks scanned, and this server cannot read scanned pages "
    "(the poppler tools are not installed)."
)


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
    cancelled: bool = False

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
    if type(exc).__name__ == "PDFInfoNotInstalledError":
        return SCANNED_FAILURE
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
        """Drop the chip. If ingestion is still running, mark it cancelled so
        the finished chunks are deleted instead of becoming a document."""
        with self._lock:
            for job in [
                j
                for j in self._jobs.values()
                if j.matter_id == matter_id and j.document_name == document_name
            ]:
                job.cancelled = True
                del self._jobs[job.job_id]

    def forget_matter(self, matter_id: str) -> None:
        with self._lock:
            for job in [j for j in self._jobs.values() if j.matter_id == matter_id]:
                job.cancelled = True
                del self._jobs[job.job_id]

    def run(
        self, job: DocumentJob, services, jurisdiction: str | None, api_key: str | None = None
    ) -> None:
        """Executes one ingestion job. Called from FastAPI BackgroundTasks.
        `api_key` is the uploading user's resolved Claude key: ingestion-time
        translation runs on it, and is skipped (detection only) when the user
        has none, so uploads never spend the server's key."""
        from policy_advisor.ingestion.ingest_document import add_document, remove_document

        job.status = "processing"
        file_path = (job.temp_dir or Path()) / job.document_name
        try:
            with services.ingest_lock:
                # Index the original text only. Translating every chunk through
                # Claude (the prototype's English/French pass) holds the document
                # in "processing" for the whole file and spends the user's key
                # before anyone can ask about it. Review uses the original text.
                job.chunk_count = add_document(
                    job.matter_id,
                    file_path,
                    jurisdiction=jurisdiction,
                    api_key=api_key,
                    translate=False,
                )
            if job.cancelled:
                # The user removed the chip while this was still reading.
                remove_document(job.matter_id, job.document_name)
                return
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
