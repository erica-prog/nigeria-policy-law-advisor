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

import re

from policy_advisor.api.sessions import SESSION_COOKIE, read_session
from policy_advisor.telemetry import attributed, span
from policy_advisor.tenancy import tenant_context

_MATTER_PATH_RE = re.compile(r"^/api/matters/([a-z0-9][a-z0-9-]{1,63})(?:/|$)")


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
        username = session[0] if session else None
        path = scope.get("path", "")
        matter = _MATTER_PATH_RE.match(path)
        status: dict[str, int] = {}

        async def send_with_status(message) -> None:  # type: ignore[no-untyped-def]
            if message.get("type") == "http.response.start":
                status["code"] = int(message.get("status", 0))
            await send(message)

        # Who and what this request is for (docs/32), then the request span:
        # method, path and status; the path carries ids, never content.
        with (
            tenant_context(tenant),
            attributed(username=username, matter_id=matter.group(1) if matter else None),
            span(
                "http.request", method=scope.get("method", ""), path=path, tenant_id=tenant
            ) as current,
        ):
            await self.app(scope, receive, send_with_status)
            if "code" in status:
                current.set_attribute("status_code", status["code"])
