"""Document-processing jobs, persisted in the `document_jobs` table.

An upload is accepted, its bytes go to the blob store and a `queued` row is
written. A worker claims the oldest queued row (SKIP LOCKED, so several
workers never take the same one), reads the bytes back from the blob store,
ingests them, and marks the row `ready` or `failed` with a curated message.
By default the API process runs the worker inline after each upload
(INLINE_WORKERS=true), which is also what the tests exercise; a deployment
can run `python -m policy_advisor.worker` instead and set it to false. A job
queued before a restart is still there afterwards (docs/22 AC-9).

Removing a document while it is still being read marks the job `cancelled`;
`run` checks that after ingestion and removes the chunks it just wrote, so a
removed document never reappears (the `verify-document-cancel` skill models
this interleaving)."""

import socket
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from time import time

from policy_advisor.api.schemas import DocumentOut, DocumentStatus
from policy_advisor.db import connect, transaction
from policy_advisor.ingestion.ingest_document import UnsupportedDocumentError
from policy_advisor.logging_utils import log_event

GENERIC_FAILURE = "Processing failed. The server log has the details."
EMPTY_FAILURE = "No readable text was found in this file."
MODEL_FAILURE = "Processing needs the language model, which is not reachable right now."
SCANNED_FAILURE = (
    "This PDF looks scanned, and this server cannot read scanned pages "
    "(the poppler tools are not installed)."
)
MISSING_BYTES_FAILURE = "The uploaded file is no longer available. Upload it again."

CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
# A job left `processing` this long is assumed to belong to a worker that
# died; it goes back to the queue (up to MAX_ATTEMPTS).
STALE_AFTER_SECONDS = 15 * 60
MAX_ATTEMPTS = 3

_COLUMNS = (
    "job_id, matter_id, name, status, storage_key, jurisdiction, uploaded_by, chunk_count, "
    "error, attempts, locked_by, locked_at, created_at, finished_at, tenant_id"
)


@dataclass
class DocumentJob:
    job_id: str
    matter_id: str
    document_name: str
    status: DocumentStatus | str = "queued"
    chunk_count: int | None = None
    error: str | None = None
    created_at: float = field(default_factory=time)
    storage_key: str | None = None
    jurisdiction: str | None = None
    uploaded_by: str | None = None
    attempts: int = 0
    # The firm the upload belongs to (docs/31); a worker runs the job as it.
    tenant_id: str | None = None
    # Kept for callers that set it (the cancel-verification skill); unused.
    temp_dir: Path | None = None

    @property
    def cancelled(self) -> bool:
        """Re-read from the database: a removal during ingestion is visible to
        the worker that is still writing the chunks."""
        with connect() as conn:
            row = conn.execute(
                "SELECT status FROM document_jobs WHERE job_id = %s", (self.job_id,)
            ).fetchone()
        return row is not None and row["status"] == "cancelled"

    def to_document(self) -> DocumentOut:
        status = (
            self.status if self.status in ("queued", "processing", "ready", "failed") else "failed"
        )
        return DocumentOut(
            name=self.document_name,
            status=status,  # type: ignore[arg-type]
            job_id=self.job_id,
            chunk_count=self.chunk_count,
            error=self.error,
        )


def _job_from_row(row: dict) -> DocumentJob:
    created = row["created_at"]
    return DocumentJob(
        job_id=row["job_id"],
        matter_id=row["matter_id"],
        document_name=row["name"],
        status=row["status"],
        chunk_count=row["chunk_count"],
        error=row["error"],
        created_at=created.timestamp() if isinstance(created, datetime) else time(),
        storage_key=row["storage_key"],
        jurisdiction=row["jurisdiction"],
        uploaded_by=row["uploaded_by"],
        attempts=row["attempts"],
        tenant_id=row.get("tenant_id"),
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
    """The queue. Stateless: every instance sees the same rows."""

    def __init__(self, worker_id: str | None = None) -> None:
        self.worker_id = worker_id or f"{socket.gethostname()}:{uuid.uuid4().hex[:8]}"

    def create(
        self,
        matter_id: str,
        document_name: str,
        storage_key: str | None = None,
        *,
        jurisdiction: str | None = None,
        uploaded_by: str | None = None,
    ) -> DocumentJob:
        from policy_advisor.tenancy import tenant_for_matter

        job_id = uuid.uuid4().hex
        with connect() as conn:
            row = conn.execute(
                f"INSERT INTO document_jobs (job_id, matter_id, name, storage_key, jurisdiction, uploaded_by, tenant_id) "
                f"VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING {_COLUMNS}",
                (
                    job_id,
                    matter_id,
                    document_name,
                    storage_key,
                    jurisdiction,
                    uploaded_by,
                    tenant_for_matter(matter_id),
                ),
            ).fetchone()
        assert row is not None
        return _job_from_row(dict(row))

    def get(self, job_id: str) -> DocumentJob | None:
        with connect() as conn:
            row = conn.execute(
                f"SELECT {_COLUMNS} FROM document_jobs WHERE job_id = %s", (job_id,)
            ).fetchone()
        return _job_from_row(dict(row)) if row else None

    def latest_for_matter(self, matter_id: str) -> dict[str, DocumentJob]:
        """Most recent live job per document name, for merging into the document list."""
        with connect() as conn:
            rows = conn.execute(
                f"SELECT DISTINCT ON (name) {_COLUMNS} FROM document_jobs "
                "WHERE matter_id = %s AND status <> 'cancelled' ORDER BY name, created_at DESC",
                (matter_id,),
            ).fetchall()
        return {row["name"]: _job_from_row(dict(row)) for row in rows}

    def forget_document(self, matter_id: str, document_name: str) -> None:
        """Drop the chip. A job still queued or running is marked cancelled so
        the worker removes the chunks instead of keeping the document."""
        with connect() as conn, transaction(conn):
            conn.execute(
                "UPDATE document_jobs SET status = 'cancelled', finished_at = now() "
                "WHERE matter_id = %s AND name = %s AND status IN ('queued', 'processing')",
                (matter_id, document_name),
            )
            conn.execute(
                "DELETE FROM document_jobs WHERE matter_id = %s AND name = %s AND status IN ('ready', 'failed')",
                (matter_id, document_name),
            )

    def forget_matter(self, matter_id: str) -> None:
        with connect() as conn, transaction(conn):
            conn.execute(
                "UPDATE document_jobs SET status = 'cancelled', finished_at = now() "
                "WHERE matter_id = %s AND status IN ('queued', 'processing')",
                (matter_id,),
            )
            conn.execute(
                "DELETE FROM document_jobs WHERE matter_id = %s AND status IN ('ready', 'failed')",
                (matter_id,),
            )

    def claim_next(self) -> DocumentJob | None:
        """Take the oldest queued job for this worker, or None. SKIP LOCKED
        means two workers claiming at once get two different jobs."""
        with connect() as conn, transaction(conn):
            row = conn.execute(
                "UPDATE document_jobs SET status = 'processing', locked_by = %s, locked_at = now(), "
                "attempts = attempts + 1 WHERE job_id = ("
                "  SELECT job_id FROM document_jobs WHERE status = 'queued' "
                "  ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1"
                f") RETURNING {_COLUMNS}",
                (self.worker_id,),
            ).fetchone()
        return _job_from_row(dict(row)) if row else None

    def requeue_stale(self, older_than_seconds: float = STALE_AFTER_SECONDS) -> int:
        """Give jobs abandoned by a dead worker back to the queue; too many
        attempts and they fail instead of looping forever."""
        with connect() as conn, transaction(conn):
            failed = conn.execute(
                "UPDATE document_jobs SET status = 'failed', error = %s, finished_at = now() "
                "WHERE status = 'processing' AND locked_at < now() - make_interval(secs => %s) "
                "AND attempts >= %s",
                (GENERIC_FAILURE, older_than_seconds, MAX_ATTEMPTS),
            ).rowcount
            requeued = conn.execute(
                "UPDATE document_jobs SET status = 'queued', locked_by = NULL, locked_at = NULL "
                "WHERE status = 'processing' AND locked_at < now() - make_interval(secs => %s)",
                (older_than_seconds,),
            ).rowcount
        return int(requeued) + int(failed)

    def _finish(self, job: DocumentJob, status: str, **fields) -> None:
        job.status = status
        sets = ", ".join(f"{k} = %s" for k in fields)
        with connect() as conn:
            conn.execute(
                f"UPDATE document_jobs SET status = %s, finished_at = now(){', ' + sets if sets else ''} "
                "WHERE job_id = %s AND status <> 'cancelled'",
                (status, *fields.values(), job.job_id),
            )

    def run(
        self,
        job: DocumentJob,
        services,
        jurisdiction: str | None = None,
        api_key: str | None = None,
        uploaded_by: str | None = None,
    ) -> None:
        """Execute one claimed job as the tenant it belongs to (docs/31): a
        worker process sees every queue, but the rows the job writes must be
        the firm's own."""
        from policy_advisor.tenancy import tenant_context

        tenant = job.tenant_id
        if tenant is None:
            with connect() as conn:
                row = conn.execute(
                    "SELECT tenant_id FROM document_jobs WHERE job_id = %s", (job.job_id,)
                ).fetchone()
            tenant = row["tenant_id"] if row else None
        from policy_advisor.telemetry import attributed, span

        with (
            tenant_context(tenant),
            attributed(
                username=uploaded_by or job.uploaded_by, matter_id=job.matter_id, job_id=job.job_id
            ),
            span("document.job", job_id=job.job_id, matter_id=job.matter_id),
        ):
            self._run_job(job, services, jurisdiction, api_key, uploaded_by)

    def _run_job(
        self,
        job: DocumentJob,
        services,
        jurisdiction: str | None = None,
        api_key: str | None = None,
        uploaded_by: str | None = None,
    ) -> None:
        """Execute one claimed job. `api_key` is accepted for compatibility and
        unused: web uploads index the original text only (translate=False),
        so no user key is ever needed in a worker process."""
        from policy_advisor.ingestion.ingest_document import add_document, remove_document
        from policy_advisor.ingestion.matter_store import delete_document_record
        from policy_advisor.storage import document_key

        job.status = "processing"
        jurisdiction = jurisdiction if jurisdiction is not None else job.jurisdiction
        key = job.storage_key or document_key(job.matter_id, job.document_name)
        source = services.blob_store.path_for(key)
        try:
            if source is None:
                raise FileNotFoundError(MISSING_BYTES_FAILURE)
            with tempfile.TemporaryDirectory(prefix="pa-ingest-") as tmp:
                file_path = Path(tmp) / job.document_name
                file_path.write_bytes(source.read_bytes())
                with services.ingest_lock:
                    # Index the original text only. Translating every chunk
                    # through Claude would hold the document in "processing"
                    # for the whole file and spend a key before anyone can ask.
                    job.chunk_count = add_document(
                        job.matter_id, file_path, jurisdiction=jurisdiction, translate=False
                    )
            if job.cancelled:
                # The user removed the chip while this was still reading.
                remove_document(job.matter_id, job.document_name)
                services.blob_store.delete(key)
                delete_document_record(job.matter_id, job.document_name)
                return
            services.invalidate_matter(job.matter_id)
            self._finish(job, "ready", chunk_count=job.chunk_count)
            log_event(
                services.logger,
                "document_ingested",
                matter_id=job.matter_id,
                document=job.document_name,
                chunk_count=job.chunk_count,
            )
        except Exception as exc:
            message = (
                MISSING_BYTES_FAILURE
                if isinstance(exc, FileNotFoundError)
                else failure_message(exc)
            )
            job.error = message
            self._finish(job, "failed", error=message)
            # A failed upload leaves nothing behind: no chunks, no original, no record.
            services.blob_store.delete(key)
            delete_document_record(job.matter_id, job.document_name)
            services.logger.error(
                "document_ingest_failed",
                exc_info=exc,
                extra={"fields": {"matter_id": job.matter_id, "document": job.document_name}},
            )

    def run_pending(self, services, limit: int = 10) -> int:
        """Claim and run up to `limit` queued jobs. Returns how many ran."""
        ran = 0
        while ran < limit:
            job = self.claim_next()
            if job is None:
                break
            self.run(job, services)
            ran += 1
        return ran
