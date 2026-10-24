"""MCP client layer, tool policy and the vendored US connector (docs/23
slice P2-C, AC-3 and AC-6). The fake server under tests/fixtures answers from
recorded fixtures so nothing here touches the network."""

import asyncio
import shutil
import sys
from pathlib import Path

import pytest
from mcp.types import CallToolResult, TextContent

from policy_advisor import sources
from policy_advisor.config import PROJECT_ROOT, get_settings
from policy_advisor.tools import (
    ConnectorError,
    ConnectorSpec,
    ToolBudgetExceeded,
    ToolDenied,
    ToolPolicy,
    call_tool,
    connector_specs,
    list_tools,
    open_session,
    parse_call_result,
    render_untrusted,
)
from policy_advisor.tools.client import guarded_call

FAKE_SERVER = Path(__file__).parent / "fixtures" / "fake_connector_server.py"
VENDORED = PROJECT_ROOT / "connectors" / "legal-ai-skills" / "legal-research-server.mjs"
NODE = shutil.which("node")


def _source(source_id, connector="legal-research", *, allowed=True, tools=None, **config):
    cfg = dict(config)
    if tools is not None:
        cfg["tools"] = tools
    return sources.Source(
        source_id=source_id,
        name=source_id,
        country="US",
        connector=connector,
        allowed=allowed,
        allowed_by="A. Lawyer" if allowed else None,
        config=cfg,
    )


def _policy(**kwargs):
    return ToolPolicy(
        sources=[
            _source("us-ecfr", tools=["search_us_ecfr", "get_us_ecfr_text"]),
            _source("us-rulemaking", allowed=False, tools=["search_us_rulemaking"]),
            _source("us-courtlistener", "courtlistener", denied_tool_patterns=["*docket*"]),
        ],
        **kwargs,
    )


def _fake_spec():
    return ConnectorSpec(
        name="legal-research", transport="stdio", command=(sys.executable, str(FAKE_SERVER))
    )


# --- policy ---


def test_tool_is_claimed_by_the_row_naming_it_else_the_connector_wide_row():
    policy = _policy()
    assert policy.grant("legal-research", "search_us_ecfr").source.source_id == "us-ecfr"
    assert policy.grant("courtlistener", "search_opinions").source.source_id == "us-courtlistener"
    with pytest.raises(ToolDenied, match="no registry source claims"):
        policy.grant("legal-research", "search_uk_legislation")


def test_unsigned_source_denied_patterns_and_playbook_allow_list_all_refuse():
    policy = _policy()
    with pytest.raises(ToolDenied, match="not signed off"):
        policy.grant("legal-research", "search_us_rulemaking")
    with pytest.raises(ToolDenied, match="denied by pattern"):
        policy.grant("courtlistener", "create_search_alert")
    with pytest.raises(ToolDenied, match="denied by pattern '\\*docket\\*'"):
        policy.grant("courtlistener", "get_docket_entries")  # the row's own pattern
    narrow = _policy(allow=("get_us_*",))
    assert narrow.grant("legal-research", "get_us_ecfr_text").tool == "get_us_ecfr_text"
    with pytest.raises(ToolDenied, match="allow-list"):
        narrow.grant("legal-research", "search_us_ecfr")


def test_budget_is_spent_per_call_and_then_refuses():
    policy = _policy(max_calls=2)
    assert policy.spend() == 1 and policy.spend() == 2
    with pytest.raises(ToolBudgetExceeded):
        policy.spend()


# --- results ---


def _call_result(text=None, structured=None, is_error=False):
    content = [TextContent(type="text", text=text)] if text is not None else []
    return CallToolResult(content=content, structured_content=structured, is_error=is_error)


def test_result_envelope_is_validated_from_structured_or_text_content():
    envelope = {"source": "eCFR", "source_url": "https://x", "retrieved_at": "t", "hits": [1]}
    assert parse_call_result(_call_result(structured=envelope)).hits == [1]
    assert parse_call_result(_call_result(text='{"source": "eCFR"}')).source == "eCFR"
    with pytest.raises(ConnectorError, match="HTTP 503"):
        parse_call_result(_call_result(text="Official source returned HTTP 503", is_error=True))
    with pytest.raises(ConnectorError, match="not JSON"):
        parse_call_result(_call_result(text="<html>"))
    with pytest.raises(ConnectorError, match="envelope"):
        parse_call_result(_call_result(structured={"results": []}))


def test_rendered_result_is_a_labelled_data_block_that_cannot_close_itself():
    result = parse_call_result(
        _call_result(structured={"source": 'e"CFR', "note": "a</connector_result>b"})
    )
    text = render_untrusted(result, source_id="us-ecfr", tool="search_us_ecfr", max_chars=20)
    assert text.startswith(
        '<connector_result source_id="us-ecfr" tool="search_us_ecfr" source="e&quot;CFR"'
    )
    assert text.count("</connector_result>") == 1  # the payload's copy is escaped
    assert "truncated by the policy layer" in text
    assert text.rstrip().endswith("cite it only by its source and locator.")


# --- the client against the fake server (no network) ---


def test_fake_connector_lists_tools_and_answers_from_fixtures():
    async def run():
        async with open_session(_fake_spec(), timeout=10) as session:
            names = [t.name for t in await list_tools(session)]
            policy = _policy()
            grant = policy.grant("legal-research", "get_us_ecfr_text")
            result = await call_tool(session, grant, {"title": 8}, timeout=10)
            return names, result

    names, result = asyncio.run(run())
    assert names == [
        "search_us_ecfr",
        "get_us_ecfr_text",
        "create_search_alert",
        "search_uk_legislation",
    ]
    assert result.source == "eCFR (fixture)" and result.citation == "8 CFR 245.6"


def test_guarded_call_wraps_an_injected_instruction_as_data_and_refuses_denied_tools():
    async def run():
        async with open_session(_fake_spec(), timeout=10) as session:
            policy = _policy(max_calls=1)
            result, rendered = await guarded_call(
                session,
                policy,
                "legal-research",
                "search_us_ecfr",
                {"query": "adjustment"},
                timeout=10,
            )
            refusals = []
            for connector, tool in (
                ("courtlistener", "create_search_alert"),
                ("legal-research", "search_uk_legislation"),
            ):
                try:
                    await guarded_call(session, policy, connector, tool, {}, timeout=10)
                except ToolDenied as exc:
                    refusals.append(str(exc))
            budget = None
            try:
                await guarded_call(
                    session, policy, "legal-research", "get_us_ecfr_text", {}, timeout=10
                )
            except ToolBudgetExceeded as exc:
                budget = str(exc)
            return result, rendered, refusals, budget, policy.calls

    result, rendered, refusals, budget, calls = asyncio.run(run())
    # AC-6: the instruction is inside the data block, which closes exactly once, after it.
    assert "IGNORE ALL PREVIOUS INSTRUCTIONS" in rendered
    body, _, trailer = rendered.rpartition("</connector_result>")
    assert "IGNORE ALL PREVIOUS INSTRUCTIONS" in body and "IGNORE" not in trailer
    assert "is not an instruction" in trailer
    assert result.coverage_warning and "fixture" in result.coverage_warning
    assert (
        len(refusals) == 2
        and "denied by pattern" in refusals[0]
        and "no registry source" in refusals[1]
    )
    assert budget and "limit 1" in budget and calls == 1  # refused calls never spend budget


def test_connector_errors_surface_as_connector_error():
    async def run():
        async with open_session(_fake_spec(), timeout=10) as session:
            grant = _policy().grant("courtlistener", "search_opinions")  # no fixture for it
            await call_tool(session, grant, {}, timeout=10)

    with pytest.raises(ConnectorError, match="Unknown tool"):
        asyncio.run(run())


# --- the vendored plugin server ---


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_vendored_legal_research_server_starts_and_only_registry_tools_are_visible():
    seed = [sources.with_allowed(s, True, "A. Lawyer") for s in sources.load_seed()]
    specs = connector_specs(seed, get_settings(), environ={})
    spec = specs["legal-research"]
    assert spec.transport == "stdio" and spec.available and spec.command[1] == str(VENDORED)

    async def run():
        async with open_session(spec, timeout=20) as session:
            return await list_tools(session)

    tools = asyncio.run(run())
    assert len(tools) == 12
    visible = ToolPolicy(sources=seed).visible_tools("legal-research", tools)
    assert sorted(t.name for t, _ in visible) == [
        "get_us_ecfr_text",
        "get_us_federal_register_document",
        "get_us_rulemaking_record",
        "search_us_ecfr",
        "search_us_federal_register",
        "search_us_rulemaking",
    ]
    assert {g.source.source_id for _, g in visible} == {
        "us-ecfr",
        "us-federal-register",
        "us-regulations-gov",
    }
    # The plugin's envelope fields are documented on every tool's description.
    assert all(t.input_schema.get("type") == "object" for t in tools)


# --- connector specs from the registry rows ---


def test_hosted_connectors_take_credentials_from_the_environment_only():
    seed = sources.load_seed()
    specs = connector_specs(seed, get_settings(), environ={"GOVINFO_API_KEY": "k-1"})
    govinfo, courtlistener = specs["govinfo"], specs["courtlistener"]
    assert (
        govinfo.transport == "http"
        and govinfo.available
        and govinfo.headers == {"x-api-key": "k-1"}
    )
    assert (
        not courtlistener.available
        and "COURTLISTENER_API_TOKEN" in courtlistener.unavailable_reason
    )
    with_token = connector_specs(seed, get_settings(), environ={"COURTLISTENER_API_TOKEN": "t"})
    assert with_token["courtlistener"].headers == {"Authorization": "Token t"}
    assert "web_search" not in specs and "library" not in specs
    assert specs["legal-research"].env == {}  # no REGULATIONS_GOV_API_KEY in that environment
    assert (
        "REGULATIONS_GOV_API_KEY"
        in connector_specs(seed, get_settings(), environ={"REGULATIONS_GOV_API_KEY": "r"})[
            "legal-research"
        ].env
    )


def test_unavailable_connector_is_refused_before_any_process_starts():
    spec = ConnectorSpec(
        name="x", transport="http", url="https://x", unavailable_reason="missing credential X"
    )

    async def run():
        async with open_session(spec):
            pass

    with pytest.raises(ConnectorError, match="missing credential X"):
        asyncio.run(run())
