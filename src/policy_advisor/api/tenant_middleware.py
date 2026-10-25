"""Sets the tenant context for a request from the session cookie's tenant
claim (docs/31), before any dependency or handler runs a query.

Why a middleware and not the `current_user` dependency: synchronous
dependencies and handlers run in worker threads that receive a copy of the
request's context, so a context variable set inside a dependency never
reaches the handler. The middleware runs in the request's own context, and
every thread the request spawns inherits what it set. The claim is signed
(itsdangerous), so it cannot be forged; `current_user` still checks it
against `tenant_users` on every request.
"""

from starlette.types import ASGIApp, Receive, Scope, Send

from policy_advisor.api.sessions import SESSION_COOKIE, read_session
from policy_advisor.tenancy import tenant_context


def _cookie(scope: Scope, name: str) -> str | None:
    for key, value in scope.get("headers", []):
        if key == b"cookie":
            for part in value.decode("latin-1").split(";"):
                k, _, v = part.strip().partition("=")
                if k == name:
                    return v
    return None


class TenantMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        token = _cookie(scope, SESSION_COOKIE)
        session = read_session(token) if token else None
        tenant = session[1] if session else None
        with tenant_context(tenant):
            await self.app(scope, receive, send)
