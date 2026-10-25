# 28 — The research agent

**Status:** implemented (docs/23 slice P2-E). **Decisions:** ADR-001 decision 6;
docs/23 D1, D5, D6.

## What it is

One more way a chat message can be answered. Until now the server chose between
a fixed question pipeline and a fixed case analysis. Research mode hands the
question to Claude with tools, under a policy, and keeps the result honest the
same way the fixed chains do: every citation must point at something the run can
show.

The agent runs when the lawyer asks for research (`intent: "research"`) or when
the matter has no documents to analyse. It is in `src/policy_advisor/agents/`.

## What the model may call

| Tool | Behind it | Records |
|---|---|---|
| `search_case_documents` | `RAGChain.answer` over the matter, then the shared libraries | the passages retrieved, for citation |
| `analyze_case` | `CaseReasoningChain.analyze` | the passages behind each issue |
| `assess_case_position` | `AdvisoryChain.advise`, now on the lawyer's own key | the same |
| `search_official_web` | the registry-gated web search, only when `allow_web` is true | the URLs returned |
| every connector tool the policy shows | the vendored US server, CourtListener, GovInfo, the Nigeria connector (docs/26, docs/27) | every URL in the result, with its source id |

The three chains keep their own grounding checks; the agent adds nothing to them
and takes nothing away. A tool call costs one unit of the run's budget whether it
is a chain, the web or a connector.

## The gate every call passes

1. The registry row that claims the tool is signed off; otherwise the tool is
   not even shown to the model.
2. The tool matches no deny pattern (alerts, subscriptions, purchases, writes).
3. The playbook's allow-list names it, when the run has one (slice P2-F).
4. The run has budget left: `AGENT_MAX_TOOL_CALLS`, default 12. A call that
   exhausts it returns a message telling the model to answer with what it has,
   and the run is recorded as `budget_exhausted`.

Refused calls are audited and never spend budget. The runner's own iteration cap
sits two above the budget as a backstop.

## Why the SDK tool runner (D1)

The Anthropic SDK's async tool runner handles the request loop: it sends the
tools, runs the ones the model calls, feeds results back, and stops when the
model stops calling. Writing that loop by hand buys nothing and costs the error
handling. What the project owns is what matters: which tools exist, what each one
returns, and the check on the answer. Tests replace the runner with a scripted
loop (`run_loop`) and drive the same tools; no model is involved.

Teaching note for the Python side: the runner is asynchronous, so the agent is
`async` and the chat job calls it with `asyncio.run`. The chains stay synchronous
and run in a worker thread (`asyncio.to_thread`), which is why a slow retrieval
does not block the connector sessions.

## Citations: the deterministic check

After the model's final message, every `[Source: X]` is resolved:

- a locator of a passage retrieved during this run, under the locator schemes
  (a pinpoint inside a retrieved rule counts); or
- a URL that a tool recorded during this run (web search or a connector result).

Anything else is listed as unsupported and the answer is `faithful: false`. The
model cannot cite a case it remembers, a URL it guessed, or a passage from a
previous run. Each citation carries its `kind` (`evidence`, `authority`, `web`)
and `trust_kind`, so the client shows where each statement came from. The avatar
follows the weakest provenance cited: a web URL makes it `web_source`; an
unsupported citation makes it `unverified`.

## Untrusted by construction (AC-6)

A connector result reaches the model only as the labelled block from
`tools/results.py`. The test fixture returns a result containing "IGNORE ALL
PREVIOUS INSTRUCTIONS" and an early closing tag; the agent test shows the model
receives the whole thing inside one block that closes once, and the system prompt
says what to do with it. Whether a given model obeys is a behavioural question
for the routing evals in P2-F, run against a real model.

## The audit (migration 0008)

`agent_runs`: one row per run with the matter, user, question, model, status
(`running`, `done`, `budget_exhausted`, `failed`), tool-call count, token counts
and timestamps. `agent_tool_calls`: one row per call with the connector, tool,
source id, truncated arguments, status (`ok`, `denied`, `budget`, `error`),
result size and duration. Results are measured, not stored. A lawyer's key paid
for the run, so the record says what it bought.

## The API and the client

`ChatRequest.intent` accepts `"research"`; `ChatReply.mode` is `"research"` and
`answer.source` is `"research"`, with `run_id`, `tool_calls` and
`budget_exhausted` on the answer (contract: docs/contracts/web-api.md). The job
narrates a `researching` stage. The client labels the card and shows each
citation's own kind.

## Settings

`AGENT_MODEL` (blank means `ANTHROPIC_MODEL`), `AGENT_MAX_TOKENS` (16,000 per
turn), `AGENT_MAX_TOOL_CALLS` (12), plus the connector settings from docs/26.

## Not done here

- Playbooks and the selector that replaces `decide_mode` (P2-F).
- A run against a real model is not part of the test suite; the routing evals
  in P2-F are where that happens. Until then the agent has been exercised only
  with the scripted loop and the fixture connector.
- Retrieval over the committed library text is still not gated by the registry
  (docs/25); the agent cites it as authority with its `source_id` where the
  chunk carries one.
