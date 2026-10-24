# 26 — Connectors and the tool policy

**Status:** implemented (docs/23 slice P2-C). **Decisions:** ADR-001 decisions 5 and 6;
docs/23 D1 to D4, D7.

## What this slice gives the advisor

A way to call external legal sources as tools, and a gate that decides which
calls may happen. Nothing in it talks to Claude yet: the agent loop (slice P2-E)
composes these pieces. The pieces are in `src/policy_advisor/tools/`.

| Module | Job |
|---|---|
| `connectors.py` | Turns registry rows (docs/24) into a `ConnectorSpec`: a child process to start over stdio, or a hosted URL over streamable HTTP, with the credential read from the environment variable the row names. |
| `client.py` | One MCP client for both transports: `open_session`, `list_tools`, `call_tool`, and `guarded_call`, the full path a tool call takes. |
| `policy.py` | `ToolPolicy`: the gate. A registry source a lawyer signed off, no deny-listed tool, the playbook's allow-list, and a per-run call budget. |
| `results.py` | The connector result envelope and `render_untrusted`, the only way connector text reaches a prompt. |

## The connectors

- **`legal-research`** is the plugin's `legal-research-server.mjs`, vendored
  unmodified under `connectors/legal-ai-skills/` with its licence and an
  `UPSTREAM.md` that pins the commit and records the file hashes. It is plain Node
  with no dependencies, so the runtime image gains the Node 20 binary and nothing
  else. Six of its twelve tools are claimed by registry rows (eCFR, Federal
  Register, Regulations.gov); the United Kingdom tools are not, so the policy
  never shows them.
- **`courtlistener`** and **`govinfo`** are hosted MCP servers reached over HTTP.
  Their rows name the credential variable and the header it goes in. A missing
  credential marks the connector unavailable before any request is made.
- **`ng-sources`** is slice P2-D; its rows already exist.

## Why one in-process client (D2)

The Messages API can call hosted MCP servers for you, but only URL servers, and
then the server-side call bypasses any gate we write. One client in our process
means every tool, local or hosted, passes the same `ToolPolicy`, lands in the
same audit record (P2-E), and is wrapped by the same `render_untrusted`. The
cost is that the stdio servers run as child processes of the API, which is why
each call has a timeout and the child gets a minimal environment: its own
credential and `PATH`, never the server's Anthropic key or database URL.

## The policy, in order

1. **Claimed.** The tool must belong to a registry source: a row that lists it in
   `config.tools`, or the connector's row that lists none. Unclaimed tools do
   not exist as far as the model is concerned.
2. **Signed off.** The row's `allowed` must be true. The seed ships every
   connector source off; `manage_sources allow` turns one on.
3. **Not denied.** Global patterns (`*alert*`, `*subscribe*`, `*submit*`,
   `*comment*`, `*create*`, `*delete*`, `*update*`, `*upload*`, `*write*`,
   `*file_*`, `*post_*`) plus the row's own `denied_tool_patterns`. The plugin
   blocked CourtListener's alert tools with a hook; here it is a pattern no
   playbook can lift.
4. **Allowed by the playbook**, when the run has one.
5. **Within budget.** `AGENT_MAX_TOOL_CALLS` per run; a refused call never
   spends it.

## Untrusted by construction (AGENTS.md rule 6)

A connector result is validated against the envelope every connector returns
(`source`, `source_url`, `retrieved_at`, `coverage_warning`, optionally
`access_status` and `licence`), then rendered as a labelled block: the payload as
JSON, a closing tag that appears exactly once, and a trailing sentence that it is
data. The test fixture contains "IGNORE ALL PREVIOUS INSTRUCTIONS" and an early
closing tag; the test shows both end up inside the block. That is the mechanism;
the behavioural check (does the model actually ignore it) belongs to P2-E's
evals with a real model.

## Operating it

Settings (`.env.example`): `CONNECTORS_DIR`, `NODE_COMMAND`,
`CONNECTOR_TIMEOUT_SECONDS`, `CONNECTOR_RESULT_MAX_CHARS`, `AGENT_MAX_TOOL_CALLS`,
and the three credentials `REGULATIONS_GOV_API_KEY`, `COURTLISTENER_API_TOKEN`,
`GOVINFO_API_KEY`. Credentials are server-held and never per user (D7).

Tests: `tests/test_connectors.py` drives the real MCP client against a fake
stdio server that answers from recorded fixtures, and starts the vendored Node
server to list its tools (skipped where Node is absent). The vendored server's
own tests run with `node --test`; CI runs both.

Not yet verified: a live call to CourtListener or GovInfo. The smoke test
needs credentials and network, is run by hand, and should record the result
and the CourtListener terms (whether text may be stored) in docs/23.

## Teaching note: async Python

The `mcp` package is asynchronous. `open_session` is an async context manager:
it starts the server, yields a session, and shuts the server down when the
block ends, even on error. Scripts and tests drive it with `asyncio.run(...)`;
the agent loop will run on the SDK's async tool runner for the same reason.
