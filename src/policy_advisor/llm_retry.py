"""One retry/backoff policy for every outbound Claude call.

docs/06 asks for backoff on transient upstream failures, but until now only
`RAGChain._call_llm` had it. That left the paths that need it most unprotected:
a single case analysis makes roughly `1 + 3N + 2` Claude calls (about 11-15 for
four issues), so it is far more likely than a one-shot question to meet a
transient rate limit somewhere in the middle - and it loses minutes of work
when it does.

The deny-list below is the one judgement in here. Retrying is the default,
matching the original behaviour, but a bad key or a malformed request will fail
identically on every attempt, so retrying those only delays the error the
caller needs to see and spends quota doing it.
"""

from collections.abc import Callable
from typing import TypeVar

import anthropic
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

T = TypeVar("T")

# Resolved with getattr so a future anthropic release that renames or drops one
# of these degrades to "retry it" rather than crashing on import.
_NON_RETRYABLE = tuple(
    exc
    for exc in (
        getattr(anthropic, name, None)
        for name in (
            "AuthenticationError",
            "PermissionDeniedError",
            "BadRequestError",
            "NotFoundError",
            "UnprocessableEntityError",
        )
    )
    if isinstance(exc, type)
)


def is_retryable(exception: BaseException) -> bool:
    return not isinstance(exception, _NON_RETRYABLE)


# Same policy the chain has always used: three attempts, exponential backoff
# capped at 8s, and the original exception re-raised rather than tenacity's
# RetryError, so callers keep seeing the error type they already handle.
with_llm_retry = retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, max=8),
    retry=retry_if_exception(is_retryable),
)


def call_with_retry(operation: Callable[[], T]) -> T:
    """Apply the same policy to a call site that isn't a method.

    Most Claude calls in this codebase are one-off expressions rather than
    dedicated methods (`structured.invoke(messages)`, `client.messages.create(...)`),
    and wrapping each in its own decorated method to get retries would be more
    indirection than the retry is worth.
    """
    return with_llm_retry(operation)()
