"""Read-only health and safety check of the database behind DATABASE_URL.

Safe to point at the live hosted database: it changes nothing. It opens a
plain connection rather than going through policy_advisor.db, so it never
applies a migration - on a project that hasn't been set up it says so instead
of quietly creating tables. It prints the host, never the connection string,
which carries the database password.

Fails (exit 1) on anything that would put client data at risk or break
retrieval:
  - pgvector missing, or the vector operator not resolvable
  - row-level security off on any of the app's tables
  - a hosted provider's public API roles (anon, authenticated) holding any privilege
Warns on the connection type, and reports what's stored and how far away the
database is - which matters because each question makes several round trips.

Usage:
  uv run python -m scripts.check_database
"""

import statistics
import sys
import time
from urllib.parse import urlparse

import psycopg

APP_TABLES = (
    "matters",
    "chunks",
    "chat_exchanges",
    "index_meta",
    "documents",
    "conversations",
    "users",
    "user_keys",
    "document_jobs",
    "sources",
    "agent_runs",
    "agent_tool_calls",
    "tenants",
    "tenant_users",
    "matter_shares",
    "cost_ledger",
    "bulk_jobs",
    "extractions",
    "findings",
    "schema_migrations",
)
API_ROLES = ("anon", "authenticated")
SEARCH_PATH = "public, extensions"


def _connection_warnings(url: str) -> list[str]:
    parsed = urlparse(url)
    host, port = (parsed.hostname or ""), parsed.port
    warnings = []
    if "-pooler" in host or port == 6543:
        warnings.append(
            "This looks like a pooled connection (PgBouncer in transaction mode). It rejects the "
            "prepared statements psycopg creates and cannot hold SET ROLE across statements, which "
            "the tenant isolation needs. Use the unpooled connection string (docs/16)."
        )
    return warnings


def run_checks(url: str) -> tuple[list[str], list[str], list[str]]:
    """(facts, warnings, failures) about the database at `url`."""
    facts: list[str] = [f"host: {urlparse(url).hostname}"]
    warnings = _connection_warnings(url)
    failures: list[str] = []

    with psycopg.connect(url) as conn:
        conn.execute(f"SET search_path TO {SEARCH_PATH}")
        version = conn.execute("SHOW server_version").fetchone()
        facts.append(f"postgres: {version[0] if version else '?'}")

        vector = conn.execute(
            "SELECT extversion, extnamespace::regnamespace::text FROM pg_extension WHERE extname = 'vector'"
        ).fetchone()
        if vector is None:
            failures.append("pgvector is not installed - run build_index, which enables it.")
        else:
            facts.append(f"pgvector: {vector[0]} (schema {vector[1]})")
            distance = conn.execute("SELECT '[1,0]'::vector <=> '[0,1]'::vector").fetchone()
            if distance is None or abs(distance[0] - 1.0) > 1e-6:
                failures.append("The vector distance operator returned an unexpected result.")

        present = {
            row[0]
            for row in conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename = ANY(%s)",
                (list(APP_TABLES),),
            )
        }
        missing = [t for t in APP_TABLES if t not in present]
        if missing:
            failures.append(
                f"Tables not created yet: {', '.join(missing)}. Run "
                "`uv run python -m policy_advisor.ingestion.build_index` once to set up the database."
            )
            return facts, warnings, failures

        for table in APP_TABLES:
            rls = conn.execute(
                "SELECT relrowsecurity FROM pg_class WHERE oid = %s::regclass", (f"public.{table}",)
            ).fetchone()
            if not (rls and rls[0]):
                failures.append(
                    f"Row-level security is OFF on {table}: the public API could read it."
                )

        existing_roles = {
            row[0]
            for row in conn.execute(
                "SELECT rolname FROM pg_roles WHERE rolname = ANY(%s)", (list(API_ROLES),)
            )
        }
        for role in sorted(existing_roles):
            for table in APP_TABLES:
                granted = conn.execute(
                    "SELECT has_table_privilege(%s, %s, 'SELECT, INSERT, UPDATE, DELETE')",
                    (role, f"public.{table}"),
                ).fetchone()
                if granted and granted[0]:
                    failures.append(f"Role {role} has privileges on {table}: revoke them.")
        if not existing_roles:
            facts.append("api roles: none (no provider API roles on this database)")
        else:
            facts.append(f"api roles checked: {', '.join(sorted(existing_roles))}")

        # Tenancy (docs/31): the application role must exist and every
        # tenant-scoped table must force its isolation policy.
        role = conn.execute(
            "SELECT rolbypassrls, rolsuper FROM pg_roles WHERE rolname = 'policy_advisor_app'"
        ).fetchone()
        if role is None:
            failures.append("Role policy_advisor_app is missing: migration 0009 has not run.")
        elif role[0] or role[1]:
            failures.append("Role policy_advisor_app can bypass row-level security.")
        for table in (
            "matters",
            "chunks",
            "documents",
            "document_jobs",
            "conversations",
            "chat_exchanges",
            "agent_runs",
            "matter_shares",
        ):
            forced = conn.execute(
                "SELECT relforcerowsecurity FROM pg_class WHERE oid = %s::regclass",
                (f"public.{table}",),
            ).fetchone()
            policy = conn.execute(
                "SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = %s "
                "AND policyname = 'tenant_isolation'",
                (table,),
            ).fetchone()
            if not (forced and forced[0]) or policy is None:
                failures.append(f"Tenant isolation is not enforced on {table}.")
        tenants = conn.execute("SELECT count(*) FROM tenants").fetchone()
        facts.append(f"tenants: {tenants[0] if tenants else 0}")
        applied = [
            row[0] for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")
        ]
        facts.append(f"migrations applied: {applied}")
        model = conn.execute(
            "SELECT value FROM index_meta WHERE key = 'embedding_model'"
        ).fetchone()
        facts.append(f"embedding model: {model[0] if model else 'none recorded yet'}")
        for table in ("matters", "chunks", "chat_exchanges"):
            count = conn.execute(f"SELECT count(*) FROM {table}").fetchone()
            facts.append(f"{table}: {count[0] if count else 0} rows")
        registry = conn.execute(
            "SELECT count(*), count(*) FILTER (WHERE allowed) FROM sources"
        ).fetchone()
        total, allowed = (registry[0], registry[1]) if registry else (0, 0)
        facts.append(f"sources: {total} registered, {allowed} allowed")
        if total == 0:
            warnings.append(
                "The source registry is empty: web search is off until "
                "`uv run python -m scripts.manage_sources seed` runs."
            )

        # End-to-end: a stored chunk searched for with its own embedding should
        # come back first, at distance zero.
        probe = conn.execute(
            "SELECT matter_id, chunk_id, embedding FROM chunks ORDER BY inserted_seq LIMIT 1"
        ).fetchone()
        if probe is not None:
            nearest = conn.execute(
                "SELECT chunk_id, embedding <=> %s AS distance FROM chunks WHERE matter_id = %s "
                "ORDER BY distance, inserted_seq LIMIT 1",
                (probe[2], probe[0]),
            ).fetchone()
            if nearest is None or nearest[0] != probe[1] or nearest[1] > 1e-5:
                failures.append(
                    "A chunk searched for with its own embedding didn't come back first."
                )
            else:
                facts.append("vector search: a stored chunk finds itself at distance 0")

        timings = []
        for _ in range(10):
            start = time.perf_counter()
            conn.execute("SELECT 1").fetchone()
            timings.append((time.perf_counter() - start) * 1000)
        facts.append(f"round trip: {statistics.median(timings):.1f} ms median over 10")

    return facts, warnings, failures


def main() -> None:
    from policy_advisor.db import database_url

    facts, warnings, failures = run_checks(database_url())
    for fact in facts:
        print(f"  {fact}")
    for warning in warnings:
        print(f"WARN  {warning}")
    for failure in failures:
        print(f"FAIL  {failure}")
    print("\nOK" if not failures else f"\n{len(failures)} problem(s) found.")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
