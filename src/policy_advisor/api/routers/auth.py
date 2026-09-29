from fastapi import APIRouter, Response, status

from policy_advisor.api.credentials import verify_credentials
from policy_advisor.api.deps import CurrentUser, Services
from policy_advisor.api.errors import ApiError
from policy_advisor.api.schemas import HealthOut, LoginRequest, UserOut
from policy_advisor.api.sessions import clear_session_cookie, set_session_cookie
from policy_advisor.logging_utils import log_event

router = APIRouter(prefix="/api", tags=["auth"])


@router.get("/health", response_model=HealthOut)
def health(services: Services) -> HealthOut:
    return HealthOut(llm_configured=services.llm_configured())


@router.post("/auth/login", response_model=UserOut)
def login(body: LoginRequest, response: Response, services: Services) -> UserOut:
    user = verify_credentials(body.username, body.password)
    if user is None:
        log_event(services.logger, "login_failed", username=body.username)
        raise ApiError(401, "invalid_credentials", "Username or password is incorrect.")
    set_session_cookie(response, user.username)
    log_event(services.logger, "login_succeeded", username=user.username)
    return UserOut(username=user.username, display_name=user.display_name)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response) -> Response:
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_session_cookie(response)
    return response


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> UserOut:
    return UserOut(username=user.username, display_name=user.display_name)
