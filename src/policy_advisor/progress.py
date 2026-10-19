"""Coarse progress stages for long model calls, so a caller (the web API's chat
jobs) can tell the user what is happening without threading a callback through
every chain signature. A chain calls `report_stage(...)` at the points where the
stage really changes; the caller installs a listener with `stage_listener()` for
the duration of one request. With no listener installed (Streamlit, scripts,
tests) the calls are no-ops, so no existing behaviour changes."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Literal

Stage = Literal["reading_documents", "searching_web", "checking_citations", "writing"]
STAGES: tuple[Stage, ...] = ("reading_documents", "searching_web", "checking_citations", "writing")

_listener: ContextVar[Callable[[Stage], None] | None] = ContextVar(
    "policy_advisor_stage_listener", default=None
)


def report_stage(stage: Stage) -> None:
    listener = _listener.get()
    if listener is not None:
        listener(stage)


@contextmanager
def stage_listener(callback: Callable[[Stage], None]) -> Iterator[None]:
    """Install `callback` for the current context (thread / task) only."""
    token = _listener.set(callback)
    try:
        yield
    finally:
        _listener.reset(token)
