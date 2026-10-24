"""Claude client construction and structured-output handling, in one place.

Every chain used to build its own `ChatAnthropic` and read structured output
straight off the runnable. Two typing problems lived at each of those sites:
the client's constructor is declared through pydantic aliases (`model_name`,
`max_tokens_to_sample`, `timeout`, `api_key`), and `with_structured_output`
returns `dict | BaseModel`. Both are settled here, so the call sites stay typed
and mypy can be a blocking check rather than advisory.
"""

from typing import Any, TypeVar

from langchain_anthropic import ChatAnthropic
from pydantic import BaseModel, SecretStr

from policy_advisor.config import Settings, workspace_headers

M = TypeVar("M", bound=BaseModel)


def chat_model(
    settings: Settings, api_key: str | None, *, max_tokens: int, timeout: float
) -> ChatAnthropic:
    """A Claude client for `api_key` (a user's own key) or, when None, the
    server key. Retries are off here: every caller applies the one policy in
    `llm_retry` so each attempt is visible in the logs. The workspace header is
    attached only alongside the server key (`config.workspace_headers`).

    Built with `model_validate` rather than the constructor: pydantic declares
    the client's fields through aliases, and the constructor signature the type
    checker derives from them does not match what the class accepts at runtime.
    Validation is the same either way."""
    key = api_key or settings.anthropic_api_key.get_secret_value()
    return ChatAnthropic.model_validate(
        {
            "model": settings.anthropic_model,
            "anthropic_api_key": SecretStr(key),
            "max_tokens": max_tokens,
            "default_request_timeout": timeout,
            "max_retries": 0,
            "default_headers": workspace_headers(settings, api_key) or None,
        }
    )


def as_model(result: Any, schema: type[M]) -> M:
    """Coerce what `with_structured_output(...).invoke` returned into `schema`.

    LangChain types that result as `dict | BaseModel`; in practice it is the
    model, but a dict is accepted and validated rather than trusted."""
    if isinstance(result, schema):
        return result
    if isinstance(result, dict):
        return schema.model_validate(result)
    raise TypeError(
        f"structured output returned {type(result).__name__}, expected {schema.__name__}"
    )


def message_text(message: Any) -> str:
    """The text of a chat response. `content` is a string for ordinary replies
    and a list of blocks when the model returns several; only text blocks count."""
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "".join(parts)
