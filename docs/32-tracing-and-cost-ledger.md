# 32 — Tracing and the cost ledger

**Status:** implemented (docs/30 slice P3-B). **Decisions:** ADR-001 action 17; docs/30 D8.

## Why both

A lawyer's own key pays for every model call, and a firm needs to know what each
person and each matter cost; that is the ledger. When a request is slow or fails,
someone needs to see which step it was: retrieval, the model, a connector, the
database; that is tracing. One set of hooks produces both.

## Where the hooks are

| Hook | Span | Ledger row |
|---|---|---|
| Every request | `http.request`: method, path, tenant, status | |
| Every LangChain model call (`llm.chat_model`) | `model.call`: model, tokens, duration | `model` with the provider's token counts |
| Web search (`web_search.py`) | `web_search.call` | `web_search` with tokens |
| Retrieval (`HybridRetriever.retrieve`) | `retrieval`: matter, top-k, result count | |
| Connector tool call (`tools.client.guarded_call`) | `tool.call`: connector, tool, source | `tool` with duration |
| Research agent run | `agent.run`: run id, matter, model | `model` with the run's summed tokens |
| Document job (`JobTable.run`) | `document.job`: job, matter | |

Model calls are captured by a LangChain callback attached to every chat model, so
the three chains, the judge, the relevance adjudicator and translation are covered
without touching them. The raw SDK calls (web search, the agent's runner) record
their own usage.

## What a span may carry

Ids, names, counts and durations. Never a question, a passage, a prompt or a
document. `telemetry.clean_attributes` drops keys that name text and cuts any string
over 200 characters, and the test suite asserts the request span carries none. The
path attribute contains matter ids, which are identifiers, not content.

## Attribution

Every ledger row names the tenant (from the database context, docs/31), the user, the
matter, and the agent run or document job. The request middleware sets user and
matter from the signed cookie and the path; the worker sets them from the job row;
the agent sets them from its arguments. Rows are tenant-scoped under a forced policy
like every other table; there are no shared ledger rows.

## Prices

`MODEL_PRICES` is a JSON map of model to prices per million tokens. The repository
ships none: the estimated cost is NULL until a deployment sets its own, because a
guessed price shown as a fact is worse than a blank. Token counts are always recorded.

## Reading it

```bash
curl -b cookies.txt "http://localhost:8000/api/usage?days=30"
```

`GET /api/usage` returns the caller's totals by kind and by matter; a firm admin may
pass `scope=tenant` for the whole firm. The database policies bound the rows to the
tenant whatever is asked.

## Exporting traces

`OTEL_EXPORTER=none` (default) creates spans and drops them. `console` prints them.
`otlp` sends them over HTTP to the collector named by the standard
`OTEL_EXPORTER_OTLP_ENDPOINT` (and `OTEL_EXPORTER_OTLP_HEADERS` when needed); which
collector a firm runs is a deployment choice. Tests use `memory` and read the spans
back.

## Not done here

- Latency budgets and alerts: the spans exist; thresholds belong with the collector.
- A per-run ceiling for the research agent is enforced by tool calls, not by the
  ledger; bulk jobs (P3-C) will enforce a cost ceiling from this ledger's prices.
- Telemetry failures are logged and swallowed: a ledger write that fails never fails
  the request, so a database incident could leave a gap in the ledger. The log
  records it.
