"""The `templates` and `artefacts` tables (docs/35)."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from psycopg.types.json import Jsonb

from policy_advisor.db import connect

DEFAULT_MEMO_TEMPLATE = "memo-default"
DEFAULT_SLIDES_TEMPLATE = "slides-default"
PACKAGED_PREFIX = "packaged:"


@dataclass(frozen=True)
class Template:
    template_id: str
    tenant_id: str
    kind: str
    name: str
    docx_key: str | None
    style: dict[str, Any]
    is_default: bool


@dataclass(frozen=True)
class ArtefactRecord:
    artefact_id: str
    tenant_id: str
    matter_id: str
    kind: str
    title: str
    status: str
    instruction: str
    outline: dict[str, Any]
    manifest: dict[str, Any]
    docx_key: str | None
    pptx_key: str | None
    template_id: str | None
    run_id: str | None
    created_by: str | None
    created_at: datetime | None
    updated_at: datetime | None


_T = "template_id, tenant_id, kind, name, docx_key, style, is_default"
_A = "artefact_id, tenant_id, matter_id, kind, title, status, instruction, outline, manifest, docx_key, pptx_key, template_id, run_id, created_by, created_at, updated_at"


def list_templates(kind: str | None = None) -> list[Template]:
    with connect() as conn:
        rows = conn.execute(
            f"SELECT {_T} FROM templates WHERE (%s::text IS NULL OR kind = %s) ORDER BY is_default DESC, name",
            (kind, kind),
        ).fetchall()
    return [Template(**dict(r)) for r in rows]


def get_template(template_id: str) -> Template | None:
    with connect() as conn:
        row = conn.execute(
            f"SELECT {_T} FROM templates WHERE template_id = %s", (template_id,)
        ).fetchone()
    return Template(**dict(row)) if row else None


def save_artefact(
    *,
    matter_id: str,
    kind: str,
    title: str,
    instruction: str,
    outline: dict[str, Any],
    manifest: dict[str, Any],
    docx_key: str | None,
    pptx_key: str | None,
    template_id: str | None,
    run_id: str | None,
    created_by: str | None,
) -> ArtefactRecord:
    from policy_advisor.tenancy import tenant_for_matter

    artefact_id = uuid.uuid4().hex
    with connect() as conn:
        row = conn.execute(
            f"INSERT INTO artefacts (artefact_id, tenant_id, matter_id, kind, title, instruction, outline, manifest, docx_key, pptx_key, template_id, run_id, created_by) "
            f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING {_A}",
            (
                artefact_id,
                tenant_for_matter(matter_id),
                matter_id,
                kind,
                title[:300],
                instruction[:20000],
                Jsonb(outline),
                Jsonb(manifest),
                docx_key,
                pptx_key,
                template_id,
                run_id,
                created_by,
            ),
        ).fetchone()
    assert row is not None
    return ArtefactRecord(**dict(row))


def get_artefact(artefact_id: str) -> ArtefactRecord | None:
    with connect() as conn:
        row = conn.execute(
            f"SELECT {_A} FROM artefacts WHERE artefact_id = %s", (artefact_id,)
        ).fetchone()
    return ArtefactRecord(**dict(row)) if row else None


def list_artefacts(matter_id: str) -> list[ArtefactRecord]:
    with connect() as conn:
        rows = conn.execute(
            f"SELECT {_A} FROM artefacts WHERE matter_id = %s ORDER BY created_at DESC",
            (matter_id,),
        ).fetchall()
    return [ArtefactRecord(**dict(r)) for r in rows]


def set_artefact_status(artefact_id: str, status: str) -> ArtefactRecord:
    with connect() as conn:
        row = conn.execute(
            f"UPDATE artefacts SET status = %s, updated_at = now() WHERE artefact_id = %s RETURNING {_A}",
            (status, artefact_id),
        ).fetchone()
    if row is None:
        raise KeyError(artefact_id)
    return ArtefactRecord(**dict(row))
