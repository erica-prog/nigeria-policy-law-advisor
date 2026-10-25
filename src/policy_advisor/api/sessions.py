"""Signed session cookie. The cookie carries only the username, signed with
AUTH_COOKIE_KEY and time-limited to
seven days like the prototype. HttpOnly + SameSite=Lax: JavaScript cannot read
it and cross-site POSTs do not carry it."""

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from starlette.responses import Response

from policy_advisor.config import get_settings

SESSION_COOKIE = "pa_session"
SESSION_MAX_AGE_SECONDS = 7 * 24 * 3600
_SALT = "policy-advisor-web-session"


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().auth_cookie_key.get_secret_value(), salt=_SALT)


def create_session_token(username: str, tenant_id: str | None = None) -> str:
    payload: dict[str, str] = {"u": username}
    if tenant_id:
        payload["t"] = tenant_id
    return _serializer().dumps(payload)


def read_session(token: str) -> tuple[str, str | None] | None:
    """(username, tenant claim) from a valid cookie, else None. The tenant is
    a claim the request dependency checks against `tenant_users`; the
    middleware uses it to set the tenant before any query runs."""
    try:
        payload = _serializer().loads(token, max_age=SESSION_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(payload, dict):
        return None
    username = payload.get("u")
    if not isinstance(username, str) or not username:
        return None
    tenant = payload.get("t")
    return username, (tenant if isinstance(tenant, str) and tenant else None)


def read_session_token(token: str) -> str | None:
    session = read_session(token)
    return session[0] if session else None


def set_session_cookie(response: Response, username: str, tenant_id: str | None = None) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        create_session_token(username, tenant_id),
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=get_settings().session_cookie_secure,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")
