"""Connector canaries (docs/38; docs/30 D10): is each live source still
reachable and still answering in the shape we depend on?

For every connector the registry seed describes that is available in this
environment (its script present, its credential set), open a session, list
tools, and make one small read call through the same policy the agent uses.
Each row says reachable, tool count, the call's outcome and the envelope
fields that came back. Network and keys are required, so this runs
nightly, never on a pull request; a failure opens an issue rather than
blocking a merge (nightly.yml).

Usage:
  uv run python -m eval.run_connector_canaries           # every available connector
  uv run python -m eval.run_connector_canaries --only govinfo
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

from policy_advisor import sources
from policy_advisor.config import get_settings
from policy_advisor.tools import ToolPolicy, connector_specs, list_tools, open_session
from policy_advisor.tools.client import guarded_call

LAST_RUN = Path(__file__).parent / "connector_canaries.json"

# One harmless read per connector: (tool, arguments).
PROBES: dict[str, tuple[str, dict[str, Any]]] = {
    "legal-research": (
        "search_us_ecfr",
        {"query": "adjustment of status", "title": 8, "part": "245", "limit": 2},
    ),
    "govinfo": ("searchGovInfo", {"userQuery": "8 CFR 245 adjustment of status", "pageSize": "2"}),
    "courtlistener": ("search", {"q": "Zadvydas v. Davis", "type": "o"}),
    "ng-sources": ("search_ng_official", {"query": "visa on arrival", "site": "nis", "limit": 2}),
}


async def probe(name: str, spec: Any, policy: ToolPolicy, timeout: float) -> dict[str, Any]:
    row: dict[str, Any] = {"connector": name, "transport": spec.transport, "reachable": False}
    started = time.perf_counter()
    try:
        async with open_session(spec, timeout=timeout) as session:
            tools = await list_tools(session)
            row["reachable"] = True
            row["tools"] = len(tools)
            visible = policy.visible_tools(name, tools)
            row["visible_tools"] = len(visible)
            tool, arguments = PROBES.get(name, (None, {}))
            if tool is None or tool not in {t.name for t in tools}:
                row["probe"] = "skipped: no probe for this connector or the tool is gone"
                row["ok"] = tool is None
            else:
                result, rendered = await guarded_call(
                    session, policy, name, tool, arguments, timeout=timeout
                )
                row["probe"] = tool
                row["envelope"] = {
                    k: bool(getattr(result, k, None))
                    for k in ("source", "source_url", "retrieved_at", "coverage_warning")
                }
                row["result_chars"] = len(rendered)
                row["ok"] = True
    except Exception as exc:
        row["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
        row["ok"] = False
    row["seconds"] = round(time.perf_counter() - started, 1)
    return row


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--only", default=None, help="one connector name")
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args(argv)
    settings = get_settings()
    seed = [sources.with_allowed(s, True, "canary") for s in sources.load_seed()]
    specs = connector_specs(seed, settings)
    policy = ToolPolicy(sources=seed, max_calls=50)
    rows = []
    for name, spec in sorted(specs.items()):
        if args.only and name != args.only:
            continue
        if not spec.available:
            rows.append(
                {
                    "connector": name,
                    "transport": spec.transport,
                    "reachable": False,
                    "ok": None,
                    "skipped": spec.unavailable_reason,
                }
            )
            continue
        rows.append(asyncio.run(probe(name, spec, policy, args.timeout)))
    for row in rows:
        mark = "SKIP" if row.get("ok") is None else ("PASS" if row["ok"] else "FAIL")
        print(
            f"{mark} {row['connector']:<16} {row.get('probe') or row.get('skipped') or ''} {row.get('error') or ''}".rstrip()
        )
    LAST_RUN.write_text(
        json.dumps(
            {"rows": rows, "ran_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, indent=1
        )
        + "\n",
        encoding="utf-8",
    )
    failed = [r for r in rows if r.get("ok") is False]
    print(
        f"\n{sum(1 for r in rows if r.get('ok'))} passed, {len(failed)} failed, {sum(1 for r in rows if r.get('ok') is None)} skipped"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
