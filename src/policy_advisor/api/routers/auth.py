from fastapi import APIRouter, Request, Response, status

from policy_advisor.api.credentials import get_user, verify_credentials
from policy_advisor.api.deps import CurrentUser, Services
from policy_advisor.api.errors import ApiError
from policy_advisor.api.schemas import HealthOut, LoginRequest, SessionOut, UserOut
from policy_advisor.tenancy import Role, membership
from policy_advisor.api.sessions import (
    SESSION_COOKIE,
    clear_session_cookie,
    read_session,
    set_session_cookie,
)
from policy_advisor.api.user_keys import key_source_for
from policy_advisor.config import get_settings
from policy_advisor.logging_utils import log_event

router = APIRouter(prefix="/api", tags=["auth"])


def user_out(
    username: str, display_name: str, tenant_id: str | None = None, role: Role | None = None
) -> UserOut:
    source = key_source_for(username)
    return UserOut(
        username=username,
        display_name=display_name,
        advisor_ready=source is not None,
        key_source=source,
        tenant_id=tenant_id,
        role=role,
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
    member = membership(user.username)
    if member is None:
        log_event(services.logger, "login_without_tenant", username=user.username)
        raise ApiError(403, "no_tenant", "Your account is not in a firm yet. Ask an administrator.")
    set_session_cookie(response, user.username, member.tenant_id)
    log_event(
        services.logger, "login_succeeded", username=user.username, tenant_id=member.tenant_id
    )
    return user_out(user.username, user.display_name, member.tenant_id, member.role)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response) -> Response:
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_session_cookie(response)
    return response


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> UserOut:
    return user_out(user.username, user.display_name, user.tenant_id, user.role)


@router.get("/session", response_model=SessionOut)
def session(request: Request) -> SessionOut:
    """Who is logged in, if anyone: 200 either way so the client's first
    request on page load never produces a 401 in the browser console."""
    token = request.cookies.get(SESSION_COOKIE)
    session_claims = read_session(token) if token else None
    user = get_user(session_claims[0]) if session_claims else None
    member = membership(user.username) if user else None
    if user is None or member is None:
        return SessionOut(user=None)
    return SessionOut(
        user=user_out(user.username, user.display_name, member.tenant_id, member.role)
    )
