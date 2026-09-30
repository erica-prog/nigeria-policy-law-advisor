from fastapi import APIRouter, Request, Response, status

from policy_advisor.api.credentials import get_user, verify_credentials
from policy_advisor.api.deps import CurrentUser, Services
from policy_advisor.api.errors import ApiError
from policy_advisor.api.schemas import HealthOut, LoginRequest, SessionOut, UserOut
from policy_advisor.api.sessions import (
    SESSION_COOKIE,
    clear_session_cookie,
    read_session_token,
    set_session_cookie,
)
from policy_advisor.api.user_keys import key_source_for
from policy_advisor.config import get_settings
from policy_advisor.logging_utils import log_event

router = APIRouter(prefix="/api", tags=["auth"])


def user_out(username: str, display_name: str) -> UserOut:
    source = key_source_for(username)
    return UserOut(
        username=username,
        display_name=display_name,
        advisor_ready=source is not None,
        key_source=source,
    )


@router.get("/health", response_model=HealthOut)
def health(services: Services) -> HealthOut:
    return HealthOut(
        llm_configured=services.llm_configured(),
        shared_key_allowed=get_settings().allow_shared_anthropic_key,
    )


@router.post("/auth/login", response_model=UserOut)
def login(body: LoginRequest, response: Response, services: Services) -> UserOut:
    user = verify_credentials(body.username, body.password)
    if user is None:
        log_event(services.logger, "login_failed", username=body.username)
        raise ApiError(401, "invalid_credentials", "Username or password is incorrect.")
    set_session_cookie(response, user.username)
    log_event(services.logger, "login_succeeded", username=user.username)
    return user_out(user.username, user.display_name)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response) -> Response:
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_session_cookie(response)
    return response


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> UserOut:
    return user_out(user.username, user.display_name)


@router.get("/session", response_model=SessionOut)
def session(request: Request) -> SessionOut:
    """Who is logged in, if anyone: 200 either way so the client's first
    request on page load never produces a 401 in the browser console."""
    token = request.cookies.get(SESSION_COOKIE)
    username = read_session_token(token) if token else None
    user = get_user(username) if username else None
    return SessionOut(user=user_out(user.username, user.display_name) if user else None)
