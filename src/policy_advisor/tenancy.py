"""Which firm a request is working for (docs/31-tenancy.md).

The tenant travels in a context variable: an ASGI middleware sets it from the
session cookie for the whole request, a worker sets it from the job row, a
script sets it explicitly. `db.connect()` reads it and sets `app.tenant_id`
on the connection for the transaction, which is what the database policies
compare against. With nothing set, the database shows only the shared
libraries: forgetting the tenant produces an empty result, never another
firm's rows.

The database functions here (`membership`, `add_user_to_tenant`,
`create_tenant`) read and write the global `tenants` and `tenant_users`
tables, which have no tenant policy of their own.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal

from policy_advisor.config import LIBRARY_MATTER_IDS
from policy_advisor.db import connect

DEFAULT_TENANT = "default"
# The reference libraries: readable by every tenant, written only by the
# build script, which runs under this tenant.
SHARED_TENANT = "shared"
# The worker's view: every tenant's queue. Only `policy_advisor.worker` and
# `JobTable.run_pending` from a worker process set this; the API never does.
ALL_TENANTS = "*"

Role = Literal["admin", "lawyer", "reviewer"]
ROLES: tuple[Role, ...] = ("admin", "lawyer", "reviewer")

_current: ContextVar[str | None] = ContextVar("policy_advisor_tenant", default=None)


class TenantRequired(RuntimeError):
    """A write that needs a tenant ran with none set."""


@dataclass(frozen=True)
class Membership:
    tenant_id: str
    role: Role


def current_tenant() -> str | None:
    return _current.get()


def require_tenant() -> str:
    tenant = _current.get()
    if not tenant:
        raise TenantRequired("no tenant is set for this request or job")
    return tenant


@contextmanager
def tenant_context(tenant_id: str | None) -> Iterator[None]:
    """Run the block as `tenant_id` (None clears it) in this context only."""
    token = _current.set(tenant_id)
    try:
        yield
    finally:
        _current.reset(token)


def tenant_for_matter(matter_id: str) -> str:
    """The tenant a new row for `matter_id` belongs to: the shared tenant for
    a library collection, otherwise the current one (required, and never the
    worker's all-tenants view)."""
    if matter_id in LIBRARY_MATTER_IDS:
        return SHARED_TENANT
    tenant = require_tenant()
    if tenant == ALL_TENANTS:
        raise TenantRequired("a row cannot belong to every tenant; set the matter's tenant")
    return tenant


def membership(username: str) -> Membership | None:
    """The user's tenant and role. One tenant per user today; the table
    allows more and the first by creation wins until the product needs a
    switcher."""
    with connect() as conn:
        row = conn.execute(
            "SELECT tenant_id, role FROM tenant_users WHERE username = %s ORDER BY created_at LIMIT 1",
            (username,),
        ).fetchone()
    return Membership(tenant_id=row["tenant_id"], role=row["role"]) if row else None


def add_user_to_tenant(
    username: str, tenant_id: str = DEFAULT_TENANT, role: Role = "lawyer"
) -> None:
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}; expected one of {ROLES}")
    with connect() as conn:
        conn.execute(
            "INSERT INTO tenant_users (tenant_id, username, role) VALUES (%s, %s, %s) "
            "ON CONFLICT (tenant_id, username) DO UPDATE SET role = EXCLUDED.role",
            (tenant_id, username, role),
        )


def create_tenant(tenant_id: str, name: str, *, residency: str | None = None) -> None:
    if tenant_id != tenant_id.strip().lower() or not tenant_id:
        raise ValueError("tenant_id must be a non-empty lower-case slug")
    if residency not in (None, "ng", "us"):
        raise ValueError("residency must be 'ng', 'us' or None")
    with connect() as conn:
        conn.execute(
            "INSERT INTO tenants (tenant_id, name, residency) VALUES (%s, %s, %s) "
            "ON CONFLICT (tenant_id) DO UPDATE SET name = EXCLUDED.name, residency = EXCLUDED.residency",
            (tenant_id, name, residency),
        )


def list_tenants() -> list[str]:
    with connect() as conn:
        return [
            r["tenant_id"] for r in conn.execute("SELECT tenant_id FROM tenants ORDER BY tenant_id")
        ]
