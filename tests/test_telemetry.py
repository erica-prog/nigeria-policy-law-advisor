"""Tracing and the cost ledger (docs/30 slice P3-B, AC-7): spans carry no
content, every model and tool call becomes a ledger row attributed to tenant,
user, matter and run, totals add up, and telemetry failures never surface."""

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from policy_advisor import telemetry
from policy_advisor.config import get_settings
from policy_advisor.db import connect
from tests.test_research_agent import _agent


def _rows():
    with connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM cost_ledger ORDER BY id").fetchall()]


def _spans(name):
    exporter = telemetry.memory_exporter()
    assert exporter is not None
    return [s for s in exporter.get_finished_spans() if s.name == name]


def setup_module(module):
    telemetry.configure_tracing(get_settings().model_copy(update={"otel_exporter": "memory"}))


def test_span_attributes_never_carry_text():
    cleaned = telemetry.clean_attributes(
        {
            "question": "secret facts",
            "text": "x",
            "matter_id": "case-1",
            "count": 3,
            "long": "a" * 500,
            "none": None,
        }
    )
    assert cleaned == {"matter_id": "case-1", "count": 3, "long": "a" * 200}
    telemetry.memory_exporter().clear()
    with telemetry.span("retrieval", matter_id="case-1", query="the lawyer's question", results=2):
        pass
    [recorded] = _spans("retrieval")
    assert dict(recorded.attributes) == {"matter_id": "case-1", "results": 2}


def test_ledger_rows_carry_the_attribution_and_the_estimate_when_a_price_exists(
    fresh_db, monkeypatch
):
    priced = get_settings().model_copy(
        update={
            "model_prices": {"claude-test": {"input_per_million": 2.0, "output_per_million": 10.0}}
        }
    )
    monkeypatch.setattr(telemetry, "get_settings", lambda: priced)
    with telemetry.attributed(username="alice", matter_id="case-1", run_id="run-1"):
        telemetry.record_cost(
            "model",
            model="claude-test",
            input_tokens=1_000_000,
            output_tokens=100_000,
            duration_ms=12,
        )
        with telemetry.attributed(job_id="job-9"):
            telemetry.record_cost("tool", connector="govinfo", tool="searchGovInfo", duration_ms=40)
    telemetry.record_cost("model", model="unpriced", input_tokens=10, output_tokens=1)
    rows = _rows()
    assert [(r["kind"], r["username"], r["matter_id"], r["run_id"], r["job_id"]) for r in rows] == [
        ("model", "alice", "case-1", "run-1", None),
        ("tool", "alice", "case-1", "run-1", "job-9"),
        ("model", None, None, None, None),
    ]
    assert float(rows[0]["estimated_usd"]) == 3.0  # 1M in at $2 + 100k out at $10
    assert rows[2]["estimated_usd"] is None and rows[2]["input_tokens"] == 10
    assert all(r["tenant_id"] == "default" for r in rows)


def test_langchain_callback_records_a_span_and_a_row_for_each_model_call(fresh_db):
    telemetry.memory_exporter().clear()
    callback = telemetry.make_usage_callback("claude-sonnet-4-6")
    message = AIMessage(
        content="ok", usage_metadata={"input_tokens": 30, "output_tokens": 7, "total_tokens": 37}
    )
    result = LLMResult(
        generations=[[ChatGeneration(message=message)]],
        llm_output={
            "model": "claude-sonnet-4-6",
            "usage": {"input_tokens": 30, "output_tokens": 7},
        },
    )
    with telemetry.attributed(username="alice", matter_id="case-1"):
        callback.on_chat_model_start({}, [[]], run_id="r1")
        callback.on_llm_end(result, run_id="r1")
    [row] = _rows()
    assert (
        row["kind"],
        row["model"],
        row["input_tokens"],
        row["output_tokens"],
        row["username"],
    ) == ("model", "claude-sonnet-4-6", 30, 7, "alice")
    assert row["duration_ms"] is not None
    [recorded] = _spans("model.call")
    assert recorded.attributes["input_tokens"] == 30 and "content" not in recorded.attributes


def test_usage_totals_add_up_by_kind_and_matter(fresh_db):
    with telemetry.attributed(username="alice", matter_id="case-1"):
        telemetry.record_cost("model", model="m", input_tokens=100, output_tokens=10)
        telemetry.record_cost("model", model="m", input_tokens=50, output_tokens=5)
        telemetry.record_cost("tool", connector="c", tool="t", duration_ms=5)
    with telemetry.attributed(username="bob", matter_id="case-2"):
        telemetry.record_cost("model", model="m", input_tokens=1, output_tokens=1)
    alice = telemetry.usage_totals(username="alice")
    assert {
        r["kind"]: (r["calls"], r["input_tokens"], r["output_tokens"]) for r in alice["by_kind"]
    } == {
        "model": (2, 150, 15),
        "tool": (1, 0, 0),
    }
    assert [r["matter_id"] for r in alice["by_matter"]] == ["case-1"]
    everyone = telemetry.usage_totals()
    assert sum(r["calls"] for r in everyone["by_kind"]) == 4


def test_a_failing_ledger_write_is_logged_not_raised(monkeypatch, caplog):
    import policy_advisor.db as db

    def boom():
        raise RuntimeError("database away")

    monkeypatch.setattr(db, "connect", boom)
    with caplog.at_level("WARNING", logger="policy_advisor.telemetry"):
        telemetry.record_cost("model", model="m", input_tokens=1, output_tokens=1)
    assert any("cost_ledger_write_failed" in r.message for r in caplog.records)


def test_a_call_outside_any_tenant_is_named_in_the_log_and_writes_no_row(fresh_db, caplog):
    # The nightly run on 2026-10-28 lost every ledger row of the golden set,
    # case-reasoning and canary jobs: those runners set no tenant, and the
    # insert failed row-level security under a generic "write failed" line.
    from policy_advisor.tenancy import ALL_TENANTS, tenant_context

    with caplog.at_level("WARNING", logger="policy_advisor.telemetry"):
        with tenant_context(None):
            telemetry.record_cost("model", model="m", input_tokens=1, output_tokens=1)
        with tenant_context(ALL_TENANTS):
            telemetry.record_cost("tool", connector="govinfo", tool="search")
    messages = [r.message for r in caplog.records]
    assert messages.count("cost_ledger_no_tenant") == 2
    assert "cost_ledger_write_failed" not in messages
    assert _rows() == []


def test_a_research_run_leaves_a_model_row_with_its_run_id(fresh_db):
    async def script(tools, system, prompt):
        await tools["search_case_documents"].call({"question": "q"})
        return "Answer [Source: Clause 4].", {"input_tokens": 400, "output_tokens": 40}

    result = _agent(script).run("q", matter_id="case-1", username="alice")
    rows = [r for r in _rows() if r["kind"] == "model" and r["run_id"] == result.run_id]
    assert len(rows) == 1 and (
        rows[0]["input_tokens"],
        rows[0]["output_tokens"],
        rows[0]["username"],
    ) == (400, 40, "alice")
    assert _spans("agent.run")[-1].attributes["run_id"] == result.run_id
