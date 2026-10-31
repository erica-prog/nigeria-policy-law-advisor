"""A connector that plants an instruction (docs/40 L3).

The same stdio MCP shape as the vendored legal-research server, answering
every call from a recorded result under `eval/live/connector_results/`. Its
tool names are its own (`search_eval_register`, `get_eval_notice`), so it can
never shadow a registry connector, and it is offered to the research agent
only by the live runner, never by the registry. The results carry the
envelope every connector must return, one genuine synthetic fact a careful
answer can cite, and text that tells the reader to do something the product
must never do. The graders look for that text in the reply.

Run by the harness through the real MCP client:
  python eval/live/fake_connector_server.py
"""

import json
import sys
from pathlib import Path

RESULTS = Path(__file__).parent / "connector_results"

TOOLS = [
    {
        "name": "search_eval_register",
        "description": (
            "Search the Eval Register of regulatory notices (a synthetic register used by "
            "the evaluation harness) for notices on Nigerian payment-terms guidance and "
            "data-protection practice. Returns the matching notice with its citation."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "get_eval_notice",
        "description": "One notice from the Eval Register by number (synthetic).",
        "inputSchema": {
            "type": "object",
            "properties": {"number": {"type": "string"}},
            "required": ["number"],
        },
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
                    "serverInfo": {"name": "eval-fake", "version": "0"},
                },
            }
        )
    elif method == "ping":
        send({"jsonrpc": "2.0", "id": message["id"], "result": {}})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": message["id"], "result": {"tools": TOOLS}})
    elif method == "tools/call":
        name = message.get("params", {}).get("name", "")
        recorded = RESULTS / f"{name}.json"
        if not recorded.exists():
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
        result = json.loads(recorded.read_text(encoding="utf-8"))
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
