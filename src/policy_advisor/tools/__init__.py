"""External sources as MCP tools, behind a policy (docs/23 slice P2-C;
ADR-001 decisions 5 and 6; docs/26-connectors-and-tool-policy.md).

- `connectors.py`: which process or URL reaches each registry connector.
- `client.py`: one MCP client for both transports (stdio for local servers
  such as the vendored `legal-research-server.mjs`, streamable HTTP for the
  hosted CourtListener and GovInfo servers).
- `policy.py`: the gate every tool call passes: a registry row that a lawyer
  signed off, no deny-listed tool, within the run's budget.
- `results.py`: the one result shape connectors return and the labelled,
  untrusted wrapper in which the model sees it.

The agent loop (slice P2-E) composes these; nothing here calls Claude.
"""

from policy_advisor.tools.client import ConnectorError, call_tool, list_tools, open_session
from policy_advisor.tools.connectors import ConnectorSpec, connector_specs
from policy_advisor.tools.policy import (
    DEFAULT_DENY_PATTERNS,
    ToolBudgetExceeded,
    ToolDenied,
    ToolGrant,
    ToolPolicy,
)
from policy_advisor.tools.results import ConnectorResult, parse_call_result, render_untrusted

__all__ = [
    "DEFAULT_DENY_PATTERNS",
    "ConnectorError",
    "ConnectorResult",
    "ConnectorSpec",
    "ToolBudgetExceeded",
    "ToolDenied",
    "ToolGrant",
    "ToolPolicy",
    "call_tool",
    "connector_specs",
    "list_tools",
    "open_session",
    "parse_call_result",
    "render_untrusted",
]
