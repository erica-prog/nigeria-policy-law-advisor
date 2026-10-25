"""The research agent (docs/23 slice P2-E, AC-5 and AC-6): tools under the
policy and budget, every call audited, citations that resolve only to what
the run retrieved or recorded, connector text wrapped as data. A scripted
loop stands in for the model; the fake stdio connector stands in for a
source. No network, no Claude."""

import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from policy_advisor import sources
from policy_advisor.agents import audit
from policy_advisor.agents.research import AgentUnavailable, ResearchAgent
from policy_advisor.config import get_settings
from policy_advisor.generation.case_reasoning_models import (
    Argument,
    Authority,
    CaseReasoningResult,
    IssueAnalysis,
)
from policy_advisor.generation.chain import AnswerResult
from policy_advisor.retrieval.hybrid_retriever import RetrievedChunk
from policy_advisor.tools import ConnectorSpec

FAKE_SERVER = Path(__file__).parent / "fixtures" / "fake_connector_server.py"


def _chunk(locator, text, *, trust="evidence", document="contract.pdf", matter="case-1"):
    return RetrievedChunk(
        text=text,
        metadata={
            "locator": locator,
            "source_document": document,
            "doc_type": "generic" if trust == "evidence" else "statute",
            "trust_kind": trust,
            "tier": "matter" if trust == "evidence" else "authority",
            "matter_id": matter,
            "page": 1,
        },
    )


@dataclass
class FakeRag:
    calls: list = field(default_factory=list)

    def answer(self, question, **kwargs):
        self.calls.append(kwargs)
        return AnswerResult(
            answer="Invoices fall due in thirty days [Source: Clause 4].",
            retrieved=[
                _chunk("Clause 4", "The Buyer shall pay each invoice within thirty days."),
                _chunk(
                    "Order 5 Rule 3",
                    "A defence shall be filed within 30 days.",
                    trust="authority",
                    document="rules.pdf",
                    matter="phase1-demo",
                ),
            ],
            faithful=True,
            source="corpus",
            usage={"input_tokens": 3, "output_tokens": 2},
        )


@dataclass
class FakeCase:
    def analyze(self, case_facts, **kwargs):
        return CaseReasoningResult(
            case_facts=case_facts,
            issues=[
                IssueAnalysis(
                    issue="Was the defence filed in time?",
                    arguments=[
                        Argument(
                            side="claimant",
                            summary="It was late.",
                            supporting_authorities=[
                                Authority(locator="Order 5 Rule 3", relevance="sets the 30 days")
                            ],
                        )
                    ],
                    assessment="Depends on service.",
                    confidence="supported by retrieved authority",
                )
            ],
            overall_position="Finely balanced.",
            disclaimer="Not advice.",
        )


class FakeRetriever:
    def retrieve(self, query, top_k, matter_id, jurisdiction=None):
        return [
            _chunk(
                "Order 5 Rule 3",
                "A defence shall be filed within 30 days.",
                trust="authority",
                document="rules.pdf",
                matter="phase1-demo",
            )
        ]


def _registry():
    rows = [sources.with_allowed(s, True, "A. Lawyer") for s in sources.load_seed()]
    rows.append(
        sources.Source(
            source_id="fake-ecfr",
            name="eCFR (fixture)",
            country="US",
            connector="fixture",
            allowed=True,
            allowed_by="A. Lawyer",
            config={"tools": ["search_us_ecfr", "get_us_ecfr_text"]},
        )
    )
    return rows


def _fixture_specs():
    return {
        "fixture": ConnectorSpec(
            name="fixture", transport="stdio", command=(sys.executable, str(FAKE_SERVER))
        )
    }


def _agent(script, *, max_calls=12, specs=None, rows=None):
    settings = get_settings().model_copy(
        update={"agent_max_tool_calls": max_calls, "rerank_enabled": False}
    )

    async def loop(tools, system, prompt):
        by_name = {t.name: t for t in tools}
        return await script(by_name, system, prompt)

    return ResearchAgent(
        "sk-test",
        retriever=FakeRetriever(),
        rag_chain=FakeRag(),
        case_chain=FakeCase(),
        settings=settings,
        run_loop=loop,
        sources=rows if rows is not None else _registry(),
        specs=specs if specs is not None else {},
    )


def test_chain_tools_run_under_the_budget_and_every_call_is_audited(fresh_db):
    async def script(tools, system, prompt):
        assert "Reply in English" in system and "Matter: case-1" in prompt
        found = await tools["search_case_documents"].call({"question": "when are invoices due"})
        assert "Locators you may cite: Clause 4, Order 5 Rule 3" in found
        analysis = await tools["analyze_case"].call(
            {"case_facts": "The defence was filed on day 40."}
        )
        assert "Order 5 Rule 3" in analysis
        assert "assess_case_position" not in tools  # no advisory chain was given
        return (
            "Invoices are due in thirty days [Source: Clause 4]; a defence has 30 days [Source: Order 5 Rule 3(1)]. "
            "The Lagos rules say otherwise [Source: Order 99 Rule 9].",
            {"input_tokens": 10, "output_tokens": 4},
        )

    result = _agent(script).run("when are invoices due", matter_id="case-1", username="alice")
    assert result.source == "research" and result.tool_calls == 2 and not result.budget_exhausted
    assert [c.cited_as for c in result.citations] == ["Clause 4", "Order 5 Rule 3(1)"]
    assert [c.kind for c in result.citations] == ["evidence", "authority"]
    assert result.unsupported_citations == ["Order 99 Rule 9"] and result.faithful is False
    run = audit.get_run(result.run_id)
    assert run.status == "done" and run.username == "alice" and run.tool_call_count == 2
    assert run.input_tokens == 10 and run.output_tokens == 4
    calls = audit.list_calls(result.run_id)
    assert [(c.tool, c.status) for c in calls] == [
        ("search_case_documents", "ok"),
        ("analyze_case", "ok"),
    ]
    assert (
        calls[0].arguments == {"question": "when are invoices due"}
        and calls[0].duration_ms is not None
    )


def test_budget_refuses_further_calls_and_marks_the_run(fresh_db):
    async def script(tools, system, prompt):
        first = await tools["search_case_documents"].call({"question": "q1"})
        second = await tools["search_case_documents"].call({"question": "q2"})
        assert "Locators" in first and "budget exhausted" in second
        return "Nothing more [Source: Clause 4].", {}

    result = _agent(script, max_calls=1).run("q", matter_id="case-1")
    assert result.budget_exhausted and result.tool_calls == 2
    assert audit.get_run(result.run_id).status == "budget_exhausted"
    assert [c.status for c in audit.list_calls(result.run_id)] == ["ok", "budget"]


def test_connector_tools_are_policy_gated_wrapped_as_data_and_cited_by_url(fresh_db):
    async def script(tools, system, prompt):
        # Only the two tools the fixture row claims are offered; the alert tool and
        # the unclaimed UK tool never reach the model.
        assert "search_us_ecfr" in tools and "get_us_ecfr_text" in tools
        assert "create_search_alert" not in tools and "search_uk_legislation" not in tools
        rendered = await tools["search_us_ecfr"].call({"query": "adjustment"})
        assert rendered.startswith('<connector_result source_id="fake-ecfr" tool="search_us_ecfr"')
        assert "IGNORE ALL PREVIOUS INSTRUCTIONS" in rendered
        assert rendered.rstrip().endswith("cite it only by its source and locator.")
        return (
            "Adjustment requires presence [Source: https://www.ecfr.gov/current/title-8/part-245]. "
            "Also [Source: https://example.invalid/not-returned].",
            {},
        )

    result = _agent(script, specs=_fixture_specs()).run("adjustment of status", matter_id="case-1")
    assert [c.url for c in result.citations] == ["https://www.ecfr.gov/current/title-8/part-245"]
    assert result.citations[0].kind == "authority" and result.citations[0].trust_kind == "authority"
    assert result.unsupported_citations == ["https://example.invalid/not-returned"]
    calls = audit.list_calls(result.run_id)
    assert [(c.connector, c.tool, c.status, c.source_id) for c in calls] == [
        ("fixture", "search_us_ecfr", "ok", "fake-ecfr")
    ]
    assert calls[0].result_chars and calls[0].result_chars > 100


def test_unsigned_source_hides_its_tools_from_the_model(fresh_db):
    rows = [s for s in _registry() if s.source_id != "fake-ecfr"]
    rows.append(
        sources.Source(
            source_id="fake-ecfr",
            name="eCFR",
            country="US",
            connector="fixture",
            allowed=False,
            config={"tools": ["search_us_ecfr", "get_us_ecfr_text"]},
        )
    )

    async def script(tools, system, prompt):
        assert not any(name.endswith("ecfr") or "ecfr" in name for name in tools)
        return "Nothing to cite.", {}

    result = _agent(script, specs=_fixture_specs(), rows=rows).run("q", matter_id="case-1")
    assert result.citations == [] and result.faithful


def test_model_failure_closes_the_run_as_failed(fresh_db):
    async def script(tools, system, prompt):
        raise AgentUnavailable("model call failed: 529")

    with pytest.raises(AgentUnavailable):
        _agent(script).run("q", matter_id="case-1", username="alice")
    with __import__("policy_advisor.db", fromlist=["connect"]).connect() as conn:
        row = conn.execute(
            "SELECT status, error FROM agent_runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
    assert row["status"] == "failed" and "529" in row["error"]


def test_long_arguments_are_truncated_in_the_audit_table(fresh_db):
    long_facts = "x" * 2000

    async def script(tools, system, prompt):
        await tools["analyze_case"].call({"case_facts": long_facts})
        return "Done.", {}

    result = _agent(script).run("q", matter_id="case-1")
    stored = audit.list_calls(result.run_id)[0].arguments["case_facts"]
    assert len(stored) < 600 and "more characters" in stored


def test_query_extractions_tool_loads_the_cited_chunks(fresh_db):
    import numpy as np

    from policy_advisor.bulk.jobs import save_extraction
    from policy_advisor.ingestion import matter_store

    matter_store.set_matter_owner("case-1", "alice")
    records = [
        {
            "chunk_id": "c4",
            "text": "The Buyer shall pay within sixty days.",
            "source_document": "amendment.docx",
            "locator": "Clause 4",
            "doc_type": "generic",
            "matter_id": "case-1",
            "jurisdiction": None,
            "page": 1,
        }
    ]
    matter_store.replace_document_chunks(
        "case-1", "amendment.docx", records, np.ones((1, 384), dtype=np.float32) / np.sqrt(384)
    )
    save_extraction(
        matter_id="case-1",
        source_document="amendment.docx",
        schema_id="contract",
        schema_version=1,
        job_id=None,
        record={
            "obligations": [
                {
                    "obligor": "the Buyer",
                    "obligation": "pay",
                    "deadline": "sixty days",
                    "evidence": [{"locator": "Clause 4", "quote": "pay within sixty days"}],
                }
            ]
        },
        evidence_spans=[],
        confidence=1.0,
        model="m",
        flagged=False,
    )

    async def script(tools, system, prompt):
        found = await tools["query_extractions"].call({"field": "obligations", "contains": "sixty"})
        assert "amendment.docx" in found and "Clause 4" in found
        return "The amendment moved payment to sixty days [Source: Clause 4].", {}

    result = _agent(script).run("payment terms", matter_id="case-1")
    assert [c.cited_as for c in result.citations] == ["Clause 4"] and result.faithful
    assert [c.tool for c in audit.list_calls(result.run_id)] == ["query_extractions"]
