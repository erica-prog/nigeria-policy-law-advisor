"""Signed session cookie. The cookie carries only the username, signed with
AUTH_COOKIE_KEY (the same secret the Streamlit login uses) and time-limited to
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


def create_session_token(username: str) -> str:
    return _serializer().dumps({"u": username})


def read_session_token(token: str) -> str | None:
    try:
        payload = _serializer().loads(token, max_age=SESSION_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
    username = payload.get("u") if isinstance(payload, dict) else None
    return username if isinstance(username, str) and username else None


def set_session_cookie(response: Response, username: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        create_session_token(username),
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=get_settings().session_cookie_secure,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")
