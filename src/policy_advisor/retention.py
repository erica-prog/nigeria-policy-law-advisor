"""Retention (docs/30 slice P3-G, D9; docs/37): delete what a firm said it
would not keep, and nothing else.

A matter is a candidate when its tenant has a retention period for its tier
and its last activity is older than that period. A matter on hold is never
a candidate, and the job records that it was skipped. The job runs as a dry
run by default and records what it would delete; applying it deletes the
matter's rows (everything cascades from `matters`), its originals and
artefacts in the blob store, and the local sidecar directory, and records
the counts. The shared libraries are never candidates: they belong to the
shared tenant, which has no retention period.

The worker runs it every RETENTION_RUN_HOURS, as a dry run unless
RETENTION_AUTO_APPLY is true; `scripts.run_retention --apply` is the
deliberate way.
"""

import shutil
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from psycopg.types.json import Jsonb

from policy_advisor.config import MATTERS_DIR, LIBRARY_MATTER_IDS
from policy_advisor.db import connect
from policy_advisor.logging_utils import get_logger, log_event
from policy_advisor.tenancy import ALL_TENANTS, SHARED_TENANT, tenant_context

logger = get_logger("policy_advisor.retention")


@dataclass(frozen=True)
class Candidate:
    matter_id: str
    tenant_id: str
    tier: str
    last_activity: datetime
    retention_days: int
    hold: bool
    hold_reason: str | None


@dataclass
class RetentionReport:
    applied: bool
    deleted: list[str] = field(default_factory=list)
    would_delete: list[str] = field(default_factory=list)
    skipped_hold: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "applied": self.applied,
            "deleted": self.deleted,
            "would_delete": self.would_delete,
            "skipped_hold": self.skipped_hold,
        }


def candidates(*, tenant_id: str | None = None) -> list[Candidate]:
    """Expired matters across every firm (the worker's view), or one firm's."""
    with tenant_context(ALL_TENANTS), connect() as conn:
        rows = conn.execute(
            "SELECT m.matter_id, m.tenant_id, m.tier, m.hold, m.hold_reason, "
            "COALESCE(m.updated_at, m.created_at) AS last_activity, "
            "CASE m.tier WHEN 'matter' THEN t.retention_days_matter WHEN 'firm' THEN t.retention_days_firm END AS retention_days "
            "FROM matters m JOIN tenants t ON t.tenant_id = m.tenant_id "
            "WHERE m.tenant_id <> %s AND (%s::text IS NULL OR m.tenant_id = %s) AND m.tier IN ('matter', 'firm') "
            "AND CASE m.tier WHEN 'matter' THEN t.retention_days_matter WHEN 'firm' THEN t.retention_days_firm END IS NOT NULL "
            "AND COALESCE(m.updated_at, m.created_at) < now() - make_interval(days => "
            "  CASE m.tier WHEN 'matter' THEN t.retention_days_matter WHEN 'firm' THEN t.retention_days_firm END) "
            "ORDER BY last_activity",
            (SHARED_TENANT, tenant_id, tenant_id),
        ).fetchall()
    return [
        Candidate(
            r["matter_id"],
            r["tenant_id"],
            r["tier"],
            r["last_activity"],
            int(r["retention_days"]),
            bool(r["hold"]),
            r["hold_reason"],
        )
        for r in rows
        if r["matter_id"] not in LIBRARY_MATTER_IDS
    ]


def _counts(conn: Any, matter_id: str) -> dict[str, int]:
    counts = {}
    for table in (
        "chunks",
        "documents",
        "conversations",
        "chat_exchanges",
        "extractions",
        "artefacts",
    ):
        row = conn.execute(
            f"SELECT count(*) AS n FROM {table} WHERE matter_id = %s", (matter_id,)
        ).fetchone()
        counts[table] = int(row["n"]) if row else 0
    return counts


def _record(matter_id: str, action: str, reason: str, counts: dict[str, int], actor: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO retention_events (matter_id, action, reason, counts, actor) VALUES (%s, %s, %s, %s, %s)",
            (matter_id, action, reason, Jsonb(counts), actor),
        )


def run_retention(
    blob_store: Any,
    *,
    apply: bool = False,
    tenant_id: str | None = None,
    actor: str = "retention-job",
) -> RetentionReport:
    report = RetentionReport(applied=apply)
    for candidate in candidates(tenant_id=tenant_id):
        reason = f"{candidate.tier} tier; last activity {candidate.last_activity.date().isoformat()}; retention {candidate.retention_days} days"
        with tenant_context(candidate.tenant_id):
            if candidate.hold:
                _record(
                    candidate.matter_id,
                    "skipped_hold",
                    f"{reason}; on hold: {candidate.hold_reason or 'no reason given'}",
                    {},
                    actor,
                )
                report.skipped_hold.append(candidate.matter_id)
                continue
            with connect() as conn:
                counts = _counts(conn, candidate.matter_id)
            if not apply:
                _record(candidate.matter_id, "would_delete", reason, counts, actor)
                report.would_delete.append(candidate.matter_id)
                continue
            with connect() as conn:
                conn.execute("DELETE FROM matters WHERE matter_id = %s", (candidate.matter_id,))
            blob_store.delete_prefix(f"{candidate.matter_id}/")
            shutil.rmtree(MATTERS_DIR / candidate.matter_id, ignore_errors=True)
            _record(candidate.matter_id, "deleted", reason, counts, actor)
            report.deleted.append(candidate.matter_id)
            log_event(
                logger,
                "retention_deleted",
                matter_id=candidate.matter_id,
                tenant_id=candidate.tenant_id,
                **counts,
            )
    log_event(
        logger,
        "retention_run",
        applied=apply,
        deleted=len(report.deleted),
        would_delete=len(report.would_delete),
        skipped_hold=len(report.skipped_hold),
    )
    return report


def retention_events(tenant_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, tenant_id, matter_id, action, reason, counts, actor, created_at FROM retention_events "
            "WHERE (%s::text IS NULL OR tenant_id = %s) ORDER BY created_at DESC LIMIT %s",
            (tenant_id, tenant_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]
