"""How each registry connector is reached (docs/23 D2, D7).

A connector is named by registry rows (`connector` column). Its row's
`config` says how to start or reach it: `server` (a script under
`CONNECTORS_DIR`, run with Node over stdio) or `url` (a hosted MCP server over
streamable HTTP). Credentials never sit in the registry: `credential_env`
names the environment variable, read here at call time and sent as the
header `auth_header` (with `auth_prefix`) for HTTP, or passed into the child
process environment for stdio. A connector whose credential is missing is
reported unavailable rather than tried.
"""

import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, field

from dotenv import dotenv_values

from policy_advisor.config import PROJECT_ROOT, Settings, get_settings
from policy_advisor.sources import Source

LOCAL_CONNECTORS = frozenset({"web_search", "library"})


@dataclass(frozen=True)
class ConnectorSpec:
    name: str
    transport: str  # "stdio" | "http"
    command: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    unavailable_reason: str | None = None

    @property
    def available(self) -> bool:
        return self.unavailable_reason is None


def default_environ() -> Mapping[str, str]:
    """The process environment over the repository's `.env`: credentials are
    usually only in the file (Docker exports it; a local `uvicorn` does not),
    and pydantic-settings reads the file only for declared settings."""
    merged: dict[str, str] = {
        key: value for key, value in dotenv_values(PROJECT_ROOT / ".env").items() if value
    }
    merged.update(os.environ)
    return merged


def _credential(config: dict, environ: Mapping[str, str]) -> tuple[str | None, str | None]:
    """(value, missing variable name)."""
    name = config.get("credential_env")
    if not name:
        return None, None
    value = environ.get(name, "").strip()
    return (value or None), (None if value else name)


def spec_for(
    sources: list[Source],
    settings: Settings | None = None,
    environ: Mapping[str, str] | None = None,
) -> ConnectorSpec | None:
    """One spec for the rows of a single connector, or None when the connector
    is served in-process (web search, the library) or has no transport."""
    settings = settings or get_settings()
    env_map: Mapping[str, str] = default_environ() if environ is None else environ
    name = sources[0].connector
    if name in LOCAL_CONNECTORS:
        return None
    configs = [s.config for s in sources]
    server = next((c["server"] for c in configs if c.get("server")), None)
    url = next((c["url"] for c in configs if c.get("url")), None)
    env: dict[str, str] = {}
    headers: dict[str, str] = {}
    missing: list[str] = []
    for config in configs:
        value, absent = _credential(config, env_map)
        if absent:
            missing.append(absent)
        if value and config.get("auth_header"):
            headers[config["auth_header"]] = f"{config.get('auth_prefix', '')}{value}"
        elif value and config.get("credential_env"):
            env[config["credential_env"]] = value
    if server:
        path = settings.connectors_dir / server
        reason = None
        if shutil.which(settings.node_command) is None:
            reason = f"{settings.node_command!r} is not installed"
        elif not path.exists():
            reason = f"connector script {path} is missing"
        return ConnectorSpec(
            name=name,
            transport="stdio",
            command=(settings.node_command, str(path)),
            env=env,
            unavailable_reason=reason,
        )
    if url:
        # A hosted server with a required credential that is absent is
        # unavailable; one whose rows name no credential is public.
        reason = f"missing credential {', '.join(missing)}" if missing else None
        return ConnectorSpec(
            name=name, transport="http", url=url, headers=headers, unavailable_reason=reason
        )
    return ConnectorSpec(
        name=name, transport="none", unavailable_reason="no server or url in the registry"
    )


def connector_specs(
    sources: list[Source],
    settings: Settings | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, ConnectorSpec]:
    by_connector: dict[str, list[Source]] = {}
    for source in sources:
        by_connector.setdefault(source.connector, []).append(source)
    specs: dict[str, ConnectorSpec] = {}
    for name, rows in sorted(by_connector.items()):
        spec = spec_for(rows, settings, environ)
        if spec is not None:
            specs[name] = spec
    return specs
