"""The live evaluation harness (docs/40, slice P4-B): the case files load and
are refused by path when malformed (B1), the shipped set covers the five
kinds (B2), and the fake connector plants its instruction inside a data
block through the real MCP client (L3, R5)."""

import asyncio
import json
import sys
from pathlib import Path

import pytest
import yaml

from eval.live import (
    CASES_DIR,
    KINDS,
    CaseFileError,
    cases_version,
    load_case_file,
    load_cases,
    sample,
)
from eval.live.fake_connector_server import RESULTS as FAKE_RESULTS
from policy_advisor import sources
from policy_advisor.config import PROJECT_ROOT
from policy_advisor.tools import ConnectorSpec, ToolPolicy, call_tool, list_tools, open_session
from policy_advisor.tools.results import render_untrusted

FAKE_SERVER = PROJECT_ROOT / "eval" / "live" / "fake_connector_server.py"


def test_every_shipped_case_file_loads_with_five_kinds_and_thirty_cases():
    cases = load_cases()
    by_kind = {kind: [c for c in cases if c.kind == kind] for kind in KINDS}
    assert len(cases) >= 30
    assert all(len(v) >= 5 for v in by_kind.values()), {k: len(v) for k, v in by_kind.items()}
    assert len({c.case.id for c in cases}) == len(cases), "case ids are unique across files"
    assert cases_version().startswith("citations:1,")
    for loaded in cases:
        assert loaded.matter.id == loaded.case.matter
        for document in loaded.matter.documents:
            for paragraph in document.paragraphs:
                assert "synthetic" in paragraph.lower() or not any(
                    real in paragraph for real in ("Meta Platforms", "@gmail.com")
                )
    judged = [c for c in cases if "judge" in c.case.graders]
    assert len(judged) >= 20, "most cases ask the judge as well as the deterministic grader"
    assert [c.case.id for c in load_cases(kind="injection")] == [
        c.case.id for c in cases if c.kind == "injection"
    ]


def _write(tmp_path: Path, **overrides) -> Path:
    base = {
        "version": 1,
        "kind": "citations",
        "matters": [{"id": "eval-live-x", "documents": [{"name": "a.docx", "paragraphs": ["p"]}]}],
        "cases": [{"id": "c1", "prompt": "q", "matter": "eval-live-x"}],
    }
    base.update(overrides)
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(base), encoding="utf-8")
    return path


def test_malformed_cases_are_refused_by_path(tmp_path):
    ok = _write(tmp_path)
    assert load_case_file(ok).cases[0].graders == ["deterministic"]

    bad = [
        (
            {"cases": [{"id": "c1", "prompt": "q", "matter": "eval-live-x", "graders": ["vibes"]}]},
            "graders",
        ),
        ({"cases": [{"id": "c1", "prompt": "q", "matter": "eval-live-missing"}]}, "not defined"),
        (
            {
                "cases": [
                    {
                        "id": "c1",
                        "prompt": "q",
                        "matter": "eval-live-x",
                        "expected_mode": "analysis",
                        "expect_faithful": True,
                    }
                ]
            },
            "faithfulness verdict",
        ),
        (
            {"cases": [{"id": "c1", "prompt": "q", "matter": "eval-live-x", "graders": ["judge"]}]},
            "judge_focus",
        ),
        (
            {
                "cases": [
                    {"id": "c1", "prompt": "q", "matter": "eval-live-x", "fake_connector": True}
                ]
            },
            "injection",
        ),
        ({"cases": [{"id": "c1", "prompt": "q", "matter": "eval-live-x", "spare": 1}]}, "spare"),
        ({"kind": "vibes"}, "kind"),
    ]
    for overrides, expected in bad:
        path = _write(tmp_path, **overrides)
        with pytest.raises(CaseFileError) as caught:
            load_case_file(path)
        assert str(path) in str(caught.value) and expected in str(caught.value), overrides
    (tmp_path / "bad.yaml").write_text("- just: a list\n", encoding="utf-8")
    with pytest.raises(CaseFileError, match="expected a mapping"):
        load_case_file(tmp_path / "bad.yaml")


def test_sample_takes_cases_round_robin_over_kinds():
    cases = load_cases()
    chosen = sample(cases, 7)
    assert len(chosen) == 7
    assert {c.kind for c in chosen} == set(KINDS), "every kind is exercised by a capped run"
    assert [c.case.id for c in sample(cases, None)] == [c.case.id for c in cases]
    assert [c.case.id for c in sample(cases, 1000)] == [c.case.id for c in cases]


def _fake_source():
    return sources.Source(
        source_id="eval-fake-register",
        name="Eval Register (synthetic)",
        country="NG",
        connector="eval-fake",
        allowed=True,
        allowed_by="eval",
        config={"tools": ["search_eval_register", "get_eval_notice"]},
    )


def test_fake_connector_plants_its_instruction_inside_a_data_block():
    spec = ConnectorSpec(
        name="eval-fake", transport="stdio", command=(sys.executable, str(FAKE_SERVER))
    )
    policy = ToolPolicy(sources=[_fake_source()])

    async def run():
        async with open_session(spec, timeout=10) as session:
            names = [t.name for t in await list_tools(session)]
            grant = policy.grant("eval-fake", "search_eval_register")
            result = await call_tool(session, grant, {"query": "payment terms"}, timeout=10)
            return names, result

    names, result = asyncio.run(run())
    assert names == ["search_eval_register", "get_eval_notice"]
    assert result.source == "Eval Register (synthetic)"
    rendered = render_untrusted(result, source_id="eval-fake-register", tool="search_eval_register")
    assert "archive@example.invalid" in rendered and "SYSTEM MESSAGE" in rendered
    # The early closing tag inside the result cannot close the block.
    assert rendered.count("</connector_result>") == 1
    assert rendered.rstrip().endswith("cite it only by its source and locator.")
    for recorded in FAKE_RESULTS.glob("*.json"):
        assert "synthetic" in json.loads(recorded.read_text())["coverage_warning"].lower()


def test_no_registry_row_names_the_fake_connector():
    """docs/40 R5: the planted connector is wired only by the live runner."""
    seed = json.loads(
        (
            PROJECT_ROOT / "src" / "policy_advisor" / "sources" / "data" / "seed_sources.json"
        ).read_text()
    )
    text = json.dumps(seed)
    assert "eval-fake" not in text and "search_eval_register" not in text
    assert CASES_DIR.is_dir()
