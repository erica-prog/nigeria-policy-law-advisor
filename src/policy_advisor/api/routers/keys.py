"""Bring-your-own Claude key: GET/PUT/DELETE /api/me/claude-key (revision 3,
docs/contracts/web-api.md "Claude key"). PUT validates the format, checks the
key against Anthropic with the cheapest authenticated call, then stores it
encrypted. The key is never echoed; only `last4` comes back."""

from fastapi import APIRouter, Response, status

from policy_advisor.api.deps import CurrentUser, Services
from policy_advisor.api.schemas import ClaudeKeyIn, ClaudeKeyOut
from policy_advisor.api.user_keys import (
    delete_user_key,
    get_user_key_info,
    key_source_for,
    store_user_key,
    validate_key_format,
    verify_key_with_anthropic,
)
from policy_advisor.logging_utils import log_event

router = APIRouter(prefix="/api/me/claude-key", tags=["claude-key"])


def key_out(username: str) -> ClaudeKeyOut:
    info = get_user_key_info(username)
    source = key_source_for(username)
    return ClaudeKeyOut(
        configured=info is not None,
        last4=info.last4 if info else None,
        source=source,
        advisor_ready=source is not None,
    )


@router.get("", response_model=ClaudeKeyOut)
def get_claude_key(user: CurrentUser) -> ClaudeKeyOut:
    return key_out(user.username)


@router.put("", response_model=ClaudeKeyOut)
def put_claude_key(body: ClaudeKeyIn, user: CurrentUser, services: Services) -> ClaudeKeyOut:
    services.key_attempts.check(user.username)
    api_key = validate_key_format(body.api_key)
    verify_key_with_anthropic(api_key)
    info = store_user_key(user.username, api_key)
    log_event(services.logger, "claude_key_stored", username=user.username, last4=info.last4)
    return key_out(user.username)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def delete_claude_key(user: CurrentUser, services: Services) -> Response:
    if delete_user_key(user.username):
        log_event(services.logger, "claude_key_removed", username=user.username)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
