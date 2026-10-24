"""A stand-in connector for tests: the same stdio MCP shape as the vendored
legal-research server (newline-delimited JSON-RPC; initialize, ping,
tools/list, tools/call), answering every call from a recorded fixture under
tests/fixtures/connector_results/<tool>.json. No network, no dependencies.

Run by tests/test_connectors.py through the real MCP client:
  python tests/fixtures/fake_connector_server.py
"""

import json
import sys
from pathlib import Path

FIXTURES = Path(__file__).parent / "connector_results"

TOOLS = [
    {
        "name": "search_us_ecfr",
        "description": "Search the eCFR (fixture).",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "get_us_ecfr_text",
        "description": "One eCFR section (fixture).",
        "inputSchema": {"type": "object", "properties": {"title": {"type": "integer"}}},
    },
    {
        "name": "create_search_alert",
        "description": "Would create an alert (must never be callable).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "search_uk_legislation",
        "description": "A tool no registry row claims.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def send(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def handle(message: dict) -> None:
    method = message.get("method")
    if method in ("notifications/initialized", "notifications/cancelled") or "id" not in message:
        return
    if method == "initialize":
        send(
            {
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": {
                    "protocolVersion": message.get("params", {}).get(
                        "protocolVersion", "2025-06-18"
                    ),
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "fake-connector", "version": "0"},
                },
            }
        )
    elif method == "ping":
        send({"jsonrpc": "2.0", "id": message["id"], "result": {}})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": message["id"], "result": {"tools": TOOLS}})
    elif method == "tools/call":
        name = message.get("params", {}).get("name", "")
        fixture = FIXTURES / f"{name}.json"
        if not fixture.exists():
            send(
                {
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "result": {
                        "content": [{"type": "text", "text": f"Unknown tool: {name}"}],
                        "isError": True,
                    },
                }
            )
            return
        result = json.loads(fixture.read_text(encoding="utf-8"))
        send(
            {
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": {
                    "content": [{"type": "text", "text": json.dumps(result)}],
                    "structuredContent": result,
                    "isError": False,
                },
            }
        )
    else:
        send(
            {
                "jsonrpc": "2.0",
                "id": message["id"],
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }
        )


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            send(
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
            )
            continue
        handle(message)


if __name__ == "__main__":
    main()
