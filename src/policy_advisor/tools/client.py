"""One MCP client for both transports (docs/23 D2).

`open_session` starts a local server as a child process (stdio) or connects
to a hosted one (streamable HTTP) and yields an initialised `ClientSession`.
`list_tools` and `call_tool` are thin; the policy decides what is called, and
`results.parse_call_result` decides what comes back. Timeouts are per call,
from `CONNECTOR_TIMEOUT_SECONDS`, so a stalled source cannot hold a run.

Teaching note for the Python side: the `mcp` package is async, so these are
`async def` and are driven with `asyncio.run(...)` in scripts and tests; the
agent loop (slice P2-E) runs on the async tool runner for the same reason.
"""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx2
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, Tool

from policy_advisor.config import get_settings
from policy_advisor.tools.connectors import ConnectorSpec
from policy_advisor.tools.policy import ToolGrant, ToolPolicy
from policy_advisor.tools.results import (
    ConnectorError,
    ConnectorResult,
    parse_call_result,
    render_untrusted,
)


def _single_leaf(group: BaseExceptionGroup) -> BaseException | None:
    """The one exception inside a (possibly nested) exception group, or None
    when there are several."""
    leaves: list[BaseException] = []
    stack: list[BaseException] = [group]
    while stack:
        exc = stack.pop()
        if isinstance(exc, BaseExceptionGroup):
            stack.extend(exc.exceptions)
        else:
            leaves.append(exc)
    return leaves[0] if len(leaves) == 1 else None


@asynccontextmanager
async def open_session(
    spec: ConnectorSpec, *, timeout: float | None = None
) -> AsyncIterator[ClientSession]:
    """An initialised session, closed on exit. An exception raised while the
    session is open leaves the transport's task groups as an exception group;
    a lone cause is re-raised plain so callers can catch `ConnectorError` or
    `ToolDenied` directly."""
    if not spec.available:
        raise ConnectorError(f"connector {spec.name!r} is unavailable: {spec.unavailable_reason}")
    timeout = timeout or get_settings().connector_timeout_seconds
    try:
        async with _transport_session(spec, timeout) as session:
            yield session
    except BaseExceptionGroup as group:
        leaf = _single_leaf(group)
        if leaf is None:
            raise
        raise leaf from None


@asynccontextmanager
async def _transport_session(spec: ConnectorSpec, timeout: float) -> AsyncIterator[ClientSession]:
    if spec.transport == "stdio":
        # The child gets a minimal environment plus the connector's own
        # credential: never the server's Anthropic key or database URL.
        env = {"PATH": os.environ.get("PATH", ""), **spec.env}
        params = StdioServerParameters(
            command=spec.command[0], args=list(spec.command[1:]), env=env
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=timeout) as session:
                await session.initialize()
                yield session
        return
    if spec.transport == "http":
        assert spec.url is not None
        async with httpx2.AsyncClient(headers=spec.headers, timeout=timeout) as http:
            async with streamable_http_client(spec.url, http_client=http) as streams:
                async with ClientSession(
                    streams[0], streams[1], read_timeout_seconds=timeout
                ) as session:
                    await session.initialize()
                    yield session
        return
    raise ConnectorError(f"connector {spec.name!r} has no transport")


async def list_tools(session: ClientSession) -> list[Tool]:
    return list((await session.list_tools()).tools)


async def call_tool(
    session: ClientSession,
    grant: ToolGrant,
    arguments: dict[str, Any],
    *,
    timeout: float | None = None,
) -> ConnectorResult:
    timeout = timeout or get_settings().connector_timeout_seconds
    result = await session.call_tool(grant.tool, arguments, read_timeout_seconds=timeout)
    if not isinstance(result, CallToolResult):
        raise ConnectorError(
            f"{grant.connector}/{grant.tool}: unexpected result type {type(result).__name__}"
        )
    return parse_call_result(
        result,
        default_source=grant.source.name,
        default_url=grant.source.config.get("url") or grant.source.licence.get("terms_url"),
    )


async def guarded_call(
    session: ClientSession,
    policy: ToolPolicy,
    connector: str,
    tool: str,
    arguments: dict[str, Any],
    *,
    timeout: float | None = None,
    max_chars: int | None = None,
) -> tuple[ConnectorResult, str]:
    """The whole path a tool call takes: grant, spend budget, call, validate,
    wrap. Returns the result and the text block the model may see."""
    grant = policy.grant(connector, tool)
    policy.spend()
    result = await call_tool(session, grant, arguments, timeout=timeout)
    rendered = render_untrusted(
        result,
        source_id=grant.source.source_id,
        tool=tool,
        max_chars=max_chars or get_settings().connector_result_max_chars,
    )
    return result, rendered
