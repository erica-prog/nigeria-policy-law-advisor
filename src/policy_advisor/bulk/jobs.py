"""The bulk queue and the records it produces (docs/33): `bulk_jobs`,
`extractions`, `findings`. Same queue shape as document jobs: claim with
SKIP LOCKED, the worker's all-tenants view sees every firm's queue, each
job then runs as its own tenant."""

import socket
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from psycopg.types.json import Jsonb

from policy_advisor.db import connect, transaction

_JOB_COLUMNS = (
    "job_id, tenant_id, matter_id, schema_id, schema_version, documents, status, backend, batch_id, "
    "triage_model, escalation_model, ceiling_tokens, spent_tokens, done_documents, flagged_documents, "
    "error, submitted_by, created_at, finished_at"
)


@dataclass
class BulkJob:
    job_id: str
    tenant_id: str
    matter_id: str
    schema_id: str
    schema_version: int
    documents: list[str]
    status: str
    backend: str
    batch_id: str | None
    triage_model: str
    escalation_model: str
    ceiling_tokens: int
    spent_tokens: int
    done_documents: int
    flagged_documents: int
    error: str | None
    submitted_by: str | None
    created_at: datetime | None
    finished_at: datetime | None

    @property
    def remaining_tokens(self) -> int:
        return max(0, self.ceiling_tokens - self.spent_tokens)


def _job(row: dict[str, Any]) -> BulkJob:
    return BulkJob(**{k: row[k] for k in BulkJob.__dataclass_fields__})


class BulkJobTable:
    def __init__(self, worker_id: str | None = None) -> None:
        self.worker_id = worker_id or f"{socket.gethostname()}:{uuid.uuid4().hex[:8]}"

    def create(
        self,
        matter_id: str,
        *,
        schema_id: str,
        schema_version: int,
        documents: list[str],
        triage_model: str,
        escalation_model: str,
        ceiling_tokens: int,
        backend: str,
        submitted_by: str | None,
    ) -> BulkJob:
        from policy_advisor.tenancy import tenant_for_matter

        job_id = uuid.uuid4().hex
        with connect() as conn:
            row = conn.execute(
                f"INSERT INTO bulk_jobs (job_id, tenant_id, matter_id, schema_id, schema_version, documents, backend, "
                f"triage_model, escalation_model, ceiling_tokens, submitted_by) "
                f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING {_JOB_COLUMNS}",
                (
                    job_id,
                    tenant_for_matter(matter_id),
                    matter_id,
                    schema_id,
                    schema_version,
                    documents,
                    backend,
                    triage_model,
                    escalation_model,
                    ceiling_tokens,
                    submitted_by,
                ),
            ).fetchone()
        assert row is not None
        return _job(dict(row))

    def get(self, job_id: str) -> BulkJob | None:
        with connect() as conn:
            row = conn.execute(
                f"SELECT {_JOB_COLUMNS} FROM bulk_jobs WHERE job_id = %s", (job_id,)
            ).fetchone()
        return _job(dict(row)) if row else None

    def list_for_matter(self, matter_id: str) -> list[BulkJob]:
        with connect() as conn:
            rows = conn.execute(
                f"SELECT {_JOB_COLUMNS} FROM bulk_jobs WHERE matter_id = %s ORDER BY created_at DESC",
                (matter_id,),
            ).fetchall()
        return [_job(dict(r)) for r in rows]

    def claim_next(self) -> BulkJob | None:
        """The oldest job that can make progress: queued, or waiting on a
        batch whose results may have arrived."""
        with connect() as conn, transaction(conn):
            row = conn.execute(
                "UPDATE bulk_jobs SET status = CASE WHEN status = 'queued' THEN 'running' ELSE status END, "
                "locked_by = %s, locked_at = now() WHERE job_id = ("
                "  SELECT job_id FROM bulk_jobs WHERE status IN ('queued', 'waiting_batch') "
                "  AND (locked_at IS NULL OR locked_at < now() - interval '15 minutes') "
                "  ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1"
                f") RETURNING {_JOB_COLUMNS}",
                (self.worker_id,),
            ).fetchone()
        return _job(dict(row)) if row else None

    def update(self, job_id: str, **fields: Any) -> BulkJob:
        allowed = {
            "status",
            "batch_id",
            "spent_tokens",
            "done_documents",
            "flagged_documents",
            "error",
            "ceiling_tokens",
            "finished_at",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"cannot update {sorted(unknown)}")
        sets = ", ".join(f"{k} = %s" for k in fields)
        with connect() as conn:
            row = conn.execute(
                f"UPDATE bulk_jobs SET {sets}, locked_by = NULL, locked_at = NULL WHERE job_id = %s RETURNING {_JOB_COLUMNS}",
                (*fields.values(), job_id),
            ).fetchone()
        if row is None:
            raise KeyError(job_id)
        return _job(dict(row))

    def add_spend(self, job_id: str, tokens: int) -> int:
        with connect() as conn:
            row = conn.execute(
                "UPDATE bulk_jobs SET spent_tokens = spent_tokens + %s WHERE job_id = %s RETURNING spent_tokens",
                (tokens, job_id),
            ).fetchone()
        return int(row["spent_tokens"]) if row else 0

    def resume(self, job_id: str, ceiling_tokens: int) -> BulkJob:
        """Raise the ceiling and queue a paused job again."""
        job = self.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if job.status not in ("paused_ceiling", "failed"):
            raise ValueError(f"job is {job.status}; only a paused or failed job resumes")
        if ceiling_tokens <= job.spent_tokens:
            raise ValueError("the new ceiling must exceed what the job already spent")
        return self.update(job_id, status="queued", ceiling_tokens=ceiling_tokens, error=None)

    def cancel(self, job_id: str) -> BulkJob:
        return self.update(job_id, status="cancelled", finished_at=datetime.now().astimezone())


# --- extractions ---------------------------------------------------------------------


def save_extraction(
    *,
    matter_id: str,
    source_document: str,
    schema_id: str,
    schema_version: int,
    job_id: str | None,
    record: dict[str, Any],
    evidence_spans: list[dict[str, Any]],
    confidence: float,
    model: str,
    flagged: bool,
) -> int:
    with connect() as conn:
        row = conn.execute(
            "INSERT INTO extractions (matter_id, source_document, schema_id, schema_version, job_id, record, "
            "evidence_spans, confidence, model, flagged) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (matter_id, source_document, schema_id, schema_version) DO UPDATE SET "
            "job_id = EXCLUDED.job_id, record = EXCLUDED.record, evidence_spans = EXCLUDED.evidence_spans, "
            "confidence = EXCLUDED.confidence, model = EXCLUDED.model, flagged = EXCLUDED.flagged, created_at = now() "
            "RETURNING id",
            (
                matter_id,
                source_document,
                schema_id,
                schema_version,
                job_id,
                Jsonb(record),
                Jsonb(evidence_spans),
                confidence,
                model,
                flagged,
            ),
        ).fetchone()
    return int(row["id"]) if row else 0


def list_extractions(
    matter_id: str, *, schema_id: str | None = None, source_document: str | None = None
) -> list[dict[str, Any]]:
    clauses, params = ["matter_id = %s"], [matter_id]
    if schema_id:
        clauses.append("schema_id = %s")
        params.append(schema_id)
    if source_document:
        clauses.append("source_document = %s")
        params.append(source_document)
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, matter_id, source_document, schema_id, schema_version, job_id, record, evidence_spans, "
            f"confidence, model, flagged, created_at FROM extractions WHERE {' AND '.join(clauses)} ORDER BY source_document",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def query_extractions(
    matter_id: str,
    *,
    field: str,
    contains: str | None = None,
    source_document: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Items of one record field across the matter's extractions, optionally
    filtered by a case-insensitive substring of their JSON text. Returns
    (document, item, locators) rows the agent can cite."""
    rows = list_extractions(matter_id, source_document=source_document)
    needle = (contains or "").lower().strip()
    out: list[dict[str, Any]] = []
    for row in rows:
        value = row["record"].get(field)
        items = (
            value if isinstance(value, list) else ([value] if value not in (None, "", []) else [])
        )
        for item in items:
            text = (
                str(item) if not isinstance(item, dict) else " ".join(str(v) for v in item.values())
            )
            if needle and needle not in text.lower():
                continue
            locators = []
            if isinstance(item, dict):
                locators = [
                    e.get("locator")
                    for e in item.get("evidence", [])
                    if isinstance(e, dict) and e.get("locator")
                ]
            elif field == "governing_law":
                locators = [
                    e.get("locator")
                    for e in row["record"].get("governing_law_evidence", [])
                    if e.get("locator")
                ]
            out.append(
                {
                    "document": row["source_document"],
                    "field": field,
                    "item": item,
                    "locators": locators,
                    "confidence": float(row["confidence"]),
                }
            )
            if len(out) >= limit:
                return out
    return out


# --- findings --------------------------------------------------------------------------


def save_findings(matter_id: str, job_id: str | None, findings: list[dict[str, Any]]) -> int:
    if not findings:
        return 0
    with connect() as conn, transaction(conn):
        conn.execute(
            "DELETE FROM findings WHERE matter_id = %s AND job_id IS NOT DISTINCT FROM %s AND status = 'open'",
            (matter_id, job_id),
        )
        for f in findings:
            conn.execute(
                "INSERT INTO findings (matter_id, job_id, kind, severity, summary, documents, evidence) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    matter_id,
                    job_id,
                    f["kind"],
                    f.get("severity", "review"),
                    f["summary"],
                    f["documents"],
                    Jsonb(f.get("evidence", [])),
                ),
            )
    return len(findings)


def list_findings(matter_id: str, *, status: str | None = None) -> list[dict[str, Any]]:
    clauses, params = ["matter_id = %s"], [matter_id]
    if status:
        clauses.append("status = %s")
        params.append(status)
    with connect() as conn:
        rows = conn.execute(
            f"SELECT id, matter_id, job_id, kind, severity, summary, documents, evidence, status, created_at "
            f"FROM findings WHERE {' AND '.join(clauses)} ORDER BY severity DESC, created_at DESC",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def get_finding(finding_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT id, matter_id, job_id, kind, severity, summary, documents, evidence, status, created_at "
            "FROM findings WHERE id = %s",
            (finding_id,),
        ).fetchone()
    return dict(row) if row else None


def set_finding_status(finding_id: int, status: str) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute(
            "UPDATE findings SET status = %s WHERE id = %s "
            "RETURNING id, matter_id, job_id, kind, severity, summary, documents, evidence, status, created_at",
            (status, finding_id),
        ).fetchone()
    if row is None:
        raise KeyError(str(finding_id))
    return dict(row)
