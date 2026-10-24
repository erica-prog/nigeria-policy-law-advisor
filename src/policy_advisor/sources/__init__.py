"""The source registry (ADR-001 decision 5; docs/23 slice P2-A).

One row per external source: a hosted or local MCP connector, the web-search
allow-list, or a static library corpus. The row is the only place that says
whether the advisor may draw on a source. Three readers depend on that:

- `web_search.py` asks `web_search_domains()` for the domains it may search,
  so an allow-list change is a registry change, not a deploy.
- The agent loop (slice P2-E) calls `require_allowed()` before any connector
  tool and refuses when the row is missing or not signed off.
- Every chunk ingested from a source carries its `source_id` (migration
  0003), so provenance leads back to the licence terms that constrain it.

`allowed` is a lawyer's sign-off and only `set_allowed()` changes it; `seed()`
never does, so re-running the seed after a lawyer disallowed a source does not
quietly switch it back on. The seed file ships as package data; migration 0007
inserts the one row that was hard-coded before the table existed, so a
deployment that never runs the seed keeps yesterday's behaviour.
"""

import json
from dataclasses import dataclass, field, replace
from datetime import datetime
from importlib import resources
from typing import Any

from psycopg.types.json import Jsonb

from policy_advisor.db import DictConnection, connect
from policy_advisor.ingestion.matter_store import TIERS
from policy_advisor.jurisdictions import load_taxonomy

TRUST_KINDS = ("evidence", "firm", "authority", "web")
# The connector every web row uses; its `config.domains` feed web search.
WEB_SEARCH_CONNECTOR = "web_search"

_COLUMNS = (
    "source_id",
    "name",
    "country",
    "tier",
    "trust_kind",
    "connector",
    "tool_prefix",
    "licence",
    "freshness",
    "config",
    "allowed",
    "allowed_by",
    "allowed_at",
    "notes",
)


class UnknownSource(KeyError):
    pass


class SourceNotAllowed(PermissionError):
    """The source exists but no lawyer has signed it off, or it was withdrawn."""


@dataclass(frozen=True)
class Source:
    source_id: str
    name: str
    country: str
    connector: str
    tier: str = "authority"
    trust_kind: str = "authority"
    tool_prefix: str | None = None
    licence: dict[str, Any] = field(default_factory=dict)
    freshness: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)
    allowed: bool = False
    allowed_by: str | None = None
    allowed_at: datetime | None = None
    notes: str = ""

    def validate(self) -> "Source":
        """Raise ValueError for a row that would mislabel what it points at."""
        if not self.source_id.strip() or self.source_id != self.source_id.strip().lower():
            raise ValueError(
                f"source_id must be a non-empty lower-case slug, got {self.source_id!r}"
            )
        if self.country not in load_taxonomy().countries:
            raise ValueError(
                f"source {self.source_id}: unknown country {self.country!r}; "
                f"add it to jurisdictions/data/taxonomy.json first"
            )
        if self.tier not in TIERS:
            raise ValueError(f"source {self.source_id}: unknown tier {self.tier!r}")
        if self.trust_kind not in TRUST_KINDS:
            raise ValueError(f"source {self.source_id}: unknown trust_kind {self.trust_kind!r}")
        if not self.connector.strip():
            raise ValueError(f"source {self.source_id}: connector is required")
        if self.connector == WEB_SEARCH_CONNECTOR:
            domains = self.config.get("domains")
            if not isinstance(domains, list) or not all(
                isinstance(d, str) and d.strip() for d in domains
            ):
                raise ValueError(
                    f"source {self.source_id}: a web_search source needs config.domains, "
                    "a list of host names"
                )
            if self.trust_kind != "web":
                raise ValueError(f"source {self.source_id}: web_search sources are trust_kind web")
        return self


@dataclass(frozen=True)
class SeedReport:
    inserted: list[str]
    updated: list[str]

    @property
    def total(self) -> int:
        return len(self.inserted) + len(self.updated)


def _from_row(row: dict[str, Any]) -> Source:
    return Source(**{column: row[column] for column in _COLUMNS})


def _from_json(item: dict[str, Any]) -> Source:
    return Source(
        source_id=item["source_id"],
        name=item["name"],
        country=item["country"],
        connector=item["connector"],
        tier=item.get("tier", "authority"),
        trust_kind=item.get("trust_kind", "authority"),
        tool_prefix=item.get("tool_prefix"),
        licence=dict(item.get("licence", {})),
        freshness=dict(item.get("freshness", {})),
        config=dict(item.get("config", {})),
        allowed=bool(item.get("allowed", False)),
        allowed_by=item.get("allowed_by"),
        notes=item.get("notes", ""),
    ).validate()


def load_seed(path: str | None = None) -> list[Source]:
    """The sources shipped with the package, or those in a JSON file of the
    same shape. Validated, so a bad file fails before the first write."""
    if path is None:
        text = (
            resources.files("policy_advisor.sources.data")
            .joinpath("seed_sources.json")
            .read_text(encoding="utf-8")
        )
    else:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    raw = json.loads(text)
    sources = [_from_json(item) for item in raw["sources"]]
    ids = [s.source_id for s in sources]
    if len(set(ids)) != len(ids):
        raise ValueError("seed file lists a source_id twice")
    return sources


def _upsert(conn: DictConnection, source: Source) -> bool:
    """Insert, or refresh every descriptive column of an existing row. Never
    touches `allowed`, `allowed_by` or `allowed_at` on a row that exists.
    Returns True when the row was new."""
    source.validate()
    row = conn.execute(
        """
        INSERT INTO sources (source_id, name, country, tier, trust_kind, connector, tool_prefix,
                             licence, freshness, config, allowed, allowed_by, allowed_at, notes)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                CASE WHEN %s THEN now() ELSE NULL END, %s)
        ON CONFLICT (source_id) DO UPDATE SET
            name = EXCLUDED.name, country = EXCLUDED.country, tier = EXCLUDED.tier,
            trust_kind = EXCLUDED.trust_kind, connector = EXCLUDED.connector,
            tool_prefix = EXCLUDED.tool_prefix, licence = EXCLUDED.licence,
            freshness = EXCLUDED.freshness, config = EXCLUDED.config, notes = EXCLUDED.notes,
            updated_at = now()
        RETURNING (xmax = 0) AS inserted
        """,
        (
            source.source_id,
            source.name,
            source.country,
            source.tier,
            source.trust_kind,
            source.connector,
            source.tool_prefix,
            Jsonb(source.licence),
            Jsonb(source.freshness),
            Jsonb(source.config),
            source.allowed,
            source.allowed_by if source.allowed else None,
            source.allowed,
            source.notes,
        ),
    ).fetchone()
    return bool(row and row["inserted"])


def upsert_source(source: Source) -> bool:
    with connect() as conn:
        return _upsert(conn, source)


def seed(sources: list[Source] | None = None) -> SeedReport:
    """Write the packaged seed (or the given rows) into the registry in one
    transaction. Existing rows keep their sign-off state."""
    rows = load_seed() if sources is None else sources
    inserted: list[str] = []
    updated: list[str] = []
    with connect() as conn, conn.transaction():
        for source in rows:
            (inserted if _upsert(conn, source) else updated).append(source.source_id)
    return SeedReport(inserted=inserted, updated=updated)


def get_source(source_id: str) -> Source | None:
    with connect() as conn:
        row = conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM sources WHERE source_id = %s", (source_id,)
        ).fetchone()
    return _from_row(dict(row)) if row else None


def list_sources(
    *, country: str | None = None, connector: str | None = None, allowed: bool | None = None
) -> list[Source]:
    clauses = ["TRUE"]
    params: list[Any] = []
    if country is not None:
        clauses.append("country = %s")
        params.append(country)
    if connector is not None:
        clauses.append("connector = %s")
        params.append(connector)
    if allowed is not None:
        clauses.append("allowed = %s")
        params.append(allowed)
    with connect() as conn:
        rows = conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM sources WHERE {' AND '.join(clauses)} "
            "ORDER BY country, source_id",
            params,
        ).fetchall()
    return [_from_row(dict(row)) for row in rows]


def set_allowed(source_id: str, allowed: bool, *, by: str) -> Source:
    """Record a lawyer's sign-off (or its withdrawal). `by` names who decided;
    it is required so the audit trail is never anonymous."""
    if not by.strip():
        raise ValueError("`by` must name who made the decision")
    with connect() as conn:
        row = conn.execute(
            "UPDATE sources SET allowed = %s, allowed_by = %s, allowed_at = now(), updated_at = now() "
            f"WHERE source_id = %s RETURNING {', '.join(_COLUMNS)}",
            (allowed, by.strip(), source_id),
        ).fetchone()
    if row is None:
        raise UnknownSource(source_id)
    return _from_row(dict(row))


def delete_source(source_id: str) -> bool:
    with connect() as conn:
        return bool(conn.execute("DELETE FROM sources WHERE source_id = %s", (source_id,)).rowcount)


def require_allowed(source_id: str) -> Source:
    """The source, if a lawyer has signed it off. The gate every connector
    call passes through; raising rather than returning None so a caller
    cannot forget to check."""
    source = get_source(source_id)
    if source is None:
        raise UnknownSource(source_id)
    if not source.allowed:
        raise SourceNotAllowed(
            f"source {source_id!r} is not allowed; a lawyer signs it off with "
            f"`uv run python -m scripts.manage_sources allow {source_id} --by <name>`"
        )
    return source


def allowed_connectors() -> set[str]:
    return {s.connector for s in list_sources(allowed=True)}


def web_search_domains(country: str | None = None) -> list[str]:
    """Host names web search may be restricted to: the union of
    `config.domains` over every allowed web_search row. Empty means web
    search must not run at all (fail closed), which is what an empty
    registry or a withdrawn sign-off should mean."""
    domains: list[str] = []
    for source in list_sources(connector=WEB_SEARCH_CONNECTOR, allowed=True, country=country):
        for domain in source.config.get("domains", []):
            if domain not in domains:
                domains.append(domain)
    return domains


def with_allowed(source: Source, allowed: bool, by: str) -> Source:
    """A copy with a different sign-off, for building fixtures and seeds."""
    return replace(source, allowed=allowed, allowed_by=by)
