"""The tool-policy layer (ADR-001 decision 6): the one gate between the
model wanting a tool and the tool running.

A call is granted only when all of these hold:

1. The tool belongs to a registry source (docs/24) and a lawyer has signed
   that source off (`allowed`). A tool no row claims is refused, which is how
   the vendored server's United Kingdom tools stay invisible.
2. Its name matches no deny pattern: the global list below (alerts,
   subscriptions, anything that files, posts or writes) plus the source's own
   `config.denied_tool_patterns`. The plugin blocked CourtListener's alert
   tools with a hook; here it is a pattern, so no playbook can opt back in.
3. The playbook's allow-list, when there is one, names it.
4. The run has budget left (`max_calls`).

The policy is built from `Source` rows and plain values so it can be tested
without a database; `ToolPolicy.from_registry()` loads the rows.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from typing import TYPE_CHECKING

from policy_advisor.sources import Source, list_sources

if TYPE_CHECKING:
    from mcp.types import Tool

DEFAULT_DENY_PATTERNS: tuple[str, ...] = (
    "*alert*",
    "*subscribe*",
    "*submit*",
    "*comment*",
    "*create*",
    "*delete*",
    "*update*",
    "*upload*",
    "*write*",
    "*file_*",
    "*post_*",
)


class ToolDenied(PermissionError):
    pass


class ToolBudgetExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class ToolGrant:
    connector: str
    tool: str
    source: Source


@dataclass
class ToolPolicy:
    sources: list[Source]
    allow: tuple[str, ...] | None = None  # playbook allow-list of tool names or patterns
    deny_patterns: tuple[str, ...] = DEFAULT_DENY_PATTERNS
    max_calls: int = 12
    calls: int = field(default=0, init=False)

    @classmethod
    def from_registry(
        cls, *, allow: Iterable[str] | None = None, max_calls: int = 12
    ) -> "ToolPolicy":
        return cls(
            sources=list_sources(),
            allow=tuple(allow) if allow is not None else None,
            max_calls=max_calls,
        )

    def source_for(self, connector: str, tool: str) -> Source | None:
        """The registry row that claims this tool: one naming it in
        `config.tools`, else the connector's row that names none."""
        candidates = [s for s in self.sources if s.connector == connector]
        for source in candidates:
            if tool in (source.config.get("tools") or []):
                return source
        for source in candidates:
            if not source.config.get("tools"):
                return source
        return None

    def grant(self, connector: str, tool: str) -> ToolGrant:
        source = self.source_for(connector, tool)
        if source is None:
            raise ToolDenied(f"{connector}/{tool}: no registry source claims this tool")
        if not source.allowed:
            raise ToolDenied(
                f"{connector}/{tool}: source {source.source_id!r} is not signed off "
                f"(`manage_sources allow {source.source_id} --by <name>`)"
            )
        patterns = tuple(self.deny_patterns) + tuple(
            source.config.get("denied_tool_patterns") or []
        )
        for pattern in patterns:
            if fnmatchcase(tool, pattern):
                raise ToolDenied(f"{connector}/{tool}: denied by pattern {pattern!r}")
        if self.allow is not None and not any(fnmatchcase(tool, p) for p in self.allow):
            raise ToolDenied(f"{connector}/{tool}: not in this playbook's allow-list")
        return ToolGrant(connector=connector, tool=tool, source=source)

    def visible_tools(
        self, connector: str, tools: "Iterable[Tool]"
    ) -> list[tuple["Tool", ToolGrant]]:
        """The tools the model may be shown: those a grant would succeed for."""
        visible = []
        for tool in tools:
            try:
                visible.append((tool, self.grant(connector, tool.name)))
            except ToolDenied:
                continue
        return visible

    def spend(self) -> int:
        """Count one call against the run's budget; raise once it is spent."""
        if self.calls >= self.max_calls:
            raise ToolBudgetExceeded(
                f"this run already made {self.calls} tool calls (limit {self.max_calls})"
            )
        self.calls += 1
        return self.calls
