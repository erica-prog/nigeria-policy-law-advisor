"""Tracing and the cost ledger (ADR-001 action 17; docs/30 slice P3-B;
docs/32-tracing-and-cost-ledger.md).

Two things, from one set of hooks:

- **Spans.** OpenTelemetry spans around each request, retrieval, model call,
  connector tool call and document job. Attributes are ids, names, counts
  and durations: never a question, a passage or a document. The exporter is a
  setting: none (default), console, OTLP over HTTP (endpoint from the standard
  `OTEL_EXPORTER_OTLP_ENDPOINT`), or memory for tests.
- **The ledger.** A `cost_ledger` row for every model call, web search and
  connector call, attributed to the tenant (from the database context), the
  user, the matter and the run or job. Token counts are the record; the
  estimated cost is filled from `MODEL_PRICES` when a price is configured.

Who, which matter: an `Attribution` travels in a context variable, set by the
request middleware from the cookie and the path, by the worker from the job
row, and by the agent from its arguments. Telemetry must never break the
request it describes, so ledger writes swallow and log their own errors.
"""

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
)
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from policy_advisor.config import Settings, get_settings

logger = logging.getLogger("policy_advisor.telemetry")

SERVICE = "policy-advisor"
_provider: TracerProvider | None = None
_memory: InMemorySpanExporter | None = None


@dataclass(frozen=True)
class Attribution:
    username: str | None = None
    matter_id: str | None = None
    run_id: str | None = None
    job_id: str | None = None


_attribution: ContextVar[Attribution] = ContextVar(
    "policy_advisor_attribution", default=Attribution()
)


def attribution() -> Attribution:
    return _attribution.get()


@contextmanager
def attributed(**fields: str | None) -> Iterator[None]:
    """Set who and what the calls in this block are for; unset fields keep
    the enclosing value."""
    current = _attribution.get()
    token = _attribution.set(replace(current, **{k: v for k, v in fields.items() if v is not None}))
    try:
        yield
    finally:
        _attribution.reset(token)


# --- tracing ---------------------------------------------------------------------


def configure_tracing(settings: Settings | None = None) -> None:
    """Install the tracer provider once, per OTEL_EXPORTER. Calling it again
    with a different exporter replaces the processors (tests)."""
    global _provider, _memory
    settings = settings or get_settings()
    exporter_name = settings.otel_exporter.strip().lower()
    if _provider is None:
        _provider = TracerProvider(resource=Resource.create({SERVICE_NAME: SERVICE}))
        trace.set_tracer_provider(_provider)
    for processor in list(getattr(_provider, "_active_span_processor", None)._span_processors):  # type: ignore[union-attr]
        processor.shutdown()
    _provider._active_span_processor._span_processors = ()  # type: ignore[attr-defined]
    _memory = None
    exporter: SpanExporter | None
    if exporter_name == "console":
        exporter = ConsoleSpanExporter()
        _provider.add_span_processor(SimpleSpanProcessor(exporter))
    elif exporter_name == "otlp":
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        _provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    elif exporter_name == "memory":
        _memory = InMemorySpanExporter()
        _provider.add_span_processor(SimpleSpanProcessor(_memory))
    # "none": a provider with no processors; spans are created and dropped.


def memory_exporter() -> InMemorySpanExporter | None:
    return _memory


def tracer() -> trace.Tracer:
    return trace.get_tracer(SERVICE)


_FORBIDDEN_ATTRIBUTE_KEYS = frozenset(
    {"question", "text", "prompt", "answer", "content", "query", "message"}
)


def clean_attributes(attributes: dict[str, Any]) -> dict[str, Any]:
    """Only ids, names, counts and durations reach a span: a key that names
    text is dropped, and any string over 200 characters is cut."""
    out: dict[str, Any] = {}
    for key, value in attributes.items():
        if key in _FORBIDDEN_ATTRIBUTE_KEYS or value is None:
            continue
        if isinstance(value, str) and len(value) > 200:
            value = value[:200]
        if isinstance(value, str | int | float | bool):
            out[key] = value
    return out


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[trace.Span]:
    with tracer().start_as_current_span(name) as current:
        for key, value in clean_attributes(attributes).items():
            current.set_attribute(key, value)
        yield current


# --- the ledger -----------------------------------------------------------------


def estimate_usd(
    model: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
    settings: Settings | None = None,
) -> float | None:
    """From the configured price table (per million tokens); None when the
    model has no price, so the ledger never shows a guess as a fact."""
    settings = settings or get_settings()
    price = (settings.model_prices or {}).get(model or "")
    if not price:
        return None
    cost = 0.0
    cost += (input_tokens or 0) / 1_000_000 * float(price.get("input_per_million", 0))
    cost += (output_tokens or 0) / 1_000_000 * float(price.get("output_per_million", 0))
    return round(cost, 6)


def record_cost(
    kind: str,
    *,
    model: str | None = None,
    connector: str | None = None,
    tool: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    duration_ms: int | None = None,
    attribution_override: Attribution | None = None,
) -> None:
    """Write one ledger row for the current attribution. Never raises.

    A row belongs to one tenant, and the database takes it from the
    connection's tenant (`app_tenant()`). With no tenant set, or with the
    worker's all-tenants view, there is no tenant to charge: the row is not
    written and the warning says why, rather than surfacing later as a
    row-level security error that reads like a database fault."""
    from policy_advisor.db import connect
    from policy_advisor.tenancy import ALL_TENANTS, current_tenant

    who = attribution_override or _attribution.get()
    tenant = current_tenant()
    if not tenant or tenant == ALL_TENANTS:
        logger.warning(
            "cost_ledger_no_tenant",
            extra={"fields": {"kind": kind, "model": model, "tool": tool, "tenant": tenant}},
        )
        return
    try:
        with connect() as conn:
            conn.execute(
                "INSERT INTO cost_ledger (username, matter_id, run_id, job_id, kind, model, connector, tool, "
                "input_tokens, output_tokens, estimated_usd, duration_ms) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    who.username,
                    who.matter_id,
                    who.run_id,
                    who.job_id,
                    kind,
                    model,
                    connector,
                    tool,
                    input_tokens,
                    output_tokens,
                    estimate_usd(model, input_tokens, output_tokens),
                    duration_ms,
                ),
            )
    except Exception as exc:  # telemetry never breaks the request it describes
        logger.warning(
            "cost_ledger_write_failed", extra={"fields": {"kind": kind, "error": str(exc)}}
        )


def usage_totals(*, username: str | None = None, days: int = 30) -> dict[str, Any]:
    """Totals for the current tenant (the policies scope the rows) and,
    when given, one user: by kind and by matter."""
    from policy_advisor.db import connect

    clauses = ["created_at >= now() - make_interval(days => %s)"]
    params: list[Any] = [days]
    if username is not None:
        clauses.append("username = %s")
        params.append(username)
    where = " AND ".join(clauses)
    with connect() as conn:
        by_kind = conn.execute(
            f"SELECT kind, count(*) AS calls, coalesce(sum(input_tokens), 0) AS input_tokens, "
            f"coalesce(sum(output_tokens), 0) AS output_tokens, sum(estimated_usd) AS estimated_usd "
            f"FROM cost_ledger WHERE {where} GROUP BY kind ORDER BY kind",
            params,
        ).fetchall()
        by_matter = conn.execute(
            f"SELECT matter_id, count(*) AS calls, coalesce(sum(input_tokens), 0) AS input_tokens, "
            f"coalesce(sum(output_tokens), 0) AS output_tokens, sum(estimated_usd) AS estimated_usd "
            f"FROM cost_ledger WHERE {where} GROUP BY matter_id ORDER BY calls DESC LIMIT 50",
            params,
        ).fetchall()
    return {
        "days": days,
        "by_kind": [dict(r) for r in by_kind],
        "by_matter": [dict(r) for r in by_matter],
    }


# --- LangChain hook ----------------------------------------------------------------


def _usage_of(response: Any) -> tuple[int | None, int | None, str | None]:
    """(input, output, model) from a LangChain LLMResult, whichever place
    the provider put them."""
    output = getattr(response, "llm_output", None) or {}
    usage = output.get("usage") or {}
    model = output.get("model") or output.get("model_name")
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    if input_tokens is None:
        for generation in (response.generations or [[]])[0]:
            message = getattr(generation, "message", None)
            meta = getattr(message, "usage_metadata", None) or {}
            if meta:
                input_tokens, output_tokens = meta.get("input_tokens"), meta.get("output_tokens")
                model = model or (getattr(message, "response_metadata", None) or {}).get("model")
                break
    return input_tokens, output_tokens, model


def make_usage_callback(model: str) -> Any:
    """A LangChain callback that records every model call the chat model
    makes: one span and one ledger row, with the token counts the provider
    reported. Built per model so a call without `llm_output` still names it."""
    from langchain_core.callbacks import BaseCallbackHandler

    class UsageCallback(BaseCallbackHandler):
        raise_error = False

        def __init__(self) -> None:
            super().__init__()
            self._started: dict[Any, float] = {}

        def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs) -> None:  # type: ignore[override]
            self._started[run_id] = time.perf_counter()

        def on_llm_start(self, serialized, prompts, *, run_id, **kwargs) -> None:  # type: ignore[override]
            self._started[run_id] = time.perf_counter()

        def on_llm_end(self, response, *, run_id, **kwargs) -> None:  # type: ignore[override]
            started = self._started.pop(run_id, None)
            duration = int((time.perf_counter() - started) * 1000) if started else None
            input_tokens, output_tokens, reported = _usage_of(response)
            with span(
                "model.call",
                model=reported or model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                duration_ms=duration,
            ):
                pass
            record_cost(
                "model",
                model=reported or model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                duration_ms=duration,
            )

        def on_llm_error(self, error, *, run_id, **kwargs) -> None:  # type: ignore[override]
            self._started.pop(run_id, None)

    return UsageCallback()
