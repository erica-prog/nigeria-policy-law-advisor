"""Playbooks, the selector and the routing evals (docs/23 slice P2-F, AC-7),
and what a playbook changes in a research run."""

from pathlib import Path

import pytest

from eval.run_routing_evals import run_all
from policy_advisor.agents import audit
from policy_advisor.playbooks import InvalidPlaybook, load_playbooks, parse_playbook
from policy_advisor.playbooks.selector import match_playbook, select_route
from tests.test_research_agent import _agent, _registry

ROUTING = Path(__file__).parent.parent / "eval" / "routing"


def _raw(**overrides):
    base = {
        "id": "test-pb",
        "name": "Test",
        "practice_area": "immigration",
        "country": "NG",
        "jurisdictions": ["ng-federal"],
        "triggers": ["visa"],
        "mode": "research",
        "allowed_tools": ["search_case_documents"],
        "checklist": ["Do the thing."],
        "output_sections": ["Answer"],
        "notice": "A notice.",
    }
    base.update(overrides)
    return base


def test_the_four_playbooks_load_and_name_taxonomy_entries_and_eval_files():
    playbooks = load_playbooks()
    assert sorted(playbooks) == [
        "immigration-ng",
        "immigration-us",
        "investigations-ng",
        "investigations-us",
    ]
    for playbook in playbooks.values():
        assert playbook.eval_file and (Path(playbook.eval_file)).exists()
        assert playbook.attribution
        assert playbook.allows_tool("search_case_documents")
        assert not playbook.allows_tool("create_search_alert")
    assert playbooks["investigations-ng"].mode == "analysis"
    assert playbooks["immigration-us"].mode == "research"
    assert "legal-ai-skills" in playbooks["investigations-us"].attribution


def test_playbook_validation_refuses_what_the_taxonomy_does_not_know():
    with pytest.raises(InvalidPlaybook, match="practice_area"):
        parse_playbook(_raw(practice_area="tax"))
    with pytest.raises(InvalidPlaybook, match="not in NG"):
        parse_playbook(_raw(jurisdictions=["us-federal"]))
    with pytest.raises(InvalidPlaybook, match="notice"):
        parse_playbook(_raw(notice=""))
    with pytest.raises(InvalidPlaybook, match="allowed_tools"):
        parse_playbook(_raw(allowed_tools=[]))
    assert parse_playbook(_raw(jurisdictions=["federal"])).jurisdictions == ("ng-federal",)


def test_selector_matches_whole_words_within_the_matters_country():
    assert (
        match_playbook("Can my client get a visa on arrival?", "ng-federal")[0].id
        == "immigration-ng"
    )
    assert (
        match_playbook("Can my client get a visa on arrival?", "us-federal")[0].id
        == "immigration-us"
    )
    assert match_playbook("the visage of the witness", None)[0] is None
    # No jurisdiction: both countries compete and the stronger trigger set wins.
    assert match_playbook("Investigate the EFCC referral", None)[0].id == "investigations-ng"
    assert match_playbook("Investigate the SEC subpoena", None)[0].id == "investigations-us"
    # A phrase outweighs a single word.
    assert match_playbook("adjustment of status", None)[1] == 2


def test_selector_applies_intent_then_playbook_then_the_phase_2_rules():
    common = dict(read_only=False, conversation_started=False, analysed=False)
    s = select_route("Investigate the fraud", has_documents=True, jurisdiction="ng-lagos", **common)
    assert (s.mode, s.playbook_id) == ("analysis", "investigations-ng")
    s = select_route(
        "Investigate the fraud", has_documents=False, jurisdiction="ng-lagos", **common
    )
    assert (s.mode, s.playbook_id) == (
        "research",
        "investigations-ng",
    ) and "no documents" in s.reason
    s = select_route(
        "Investigate the fraud", intent="ask", has_documents=True, jurisdiction="ng-lagos", **common
    )
    assert (s.mode, s.playbook_id) == ("question", "investigations-ng")
    s = select_route(
        "Investigate the fraud",
        has_documents=True,
        jurisdiction="ng-lagos",
        read_only=True,
        conversation_started=False,
        analysed=False,
    )
    assert s.mode == "question"
    s = select_route("Acme stopped paying", has_documents=True, **common)
    assert (s.mode, s.playbook_id) == ("analysis", None)
    s = select_route(
        "When are invoices due?",
        has_documents=True,
        read_only=False,
        conversation_started=True,
        analysed=True,
    )
    assert (s.mode, s.playbook_id) == ("question", None)


def test_every_routing_eval_case_passes():
    outcomes = run_all(sorted(ROUTING.glob("*.yaml")))
    failed = [(o.file, o.id, o.failures) for o in outcomes if not o.passed]
    assert failed == []
    assert len(outcomes) >= 15


def test_research_run_under_a_playbook_narrows_tools_and_extends_the_brief(fresh_db):
    playbook = load_playbooks()["immigration-ng"]

    async def script(tools, system, prompt):
        assert (
            "Playbook: Immigration (Nigeria)" in system and "Work through this checklist" in system
        )
        assert "Status sought and current position" in system
        # analyze_case is not in this playbook's allow-list: refused, audited, no budget spent.
        refused = await tools["analyze_case"].call({"case_facts": "facts"})
        assert "not in this playbook's allow-list" in refused
        found = await tools["search_case_documents"].call({"question": "visa status"})
        assert "Locators you may cite" in found
        return "Status: visitor [Source: Clause 4].", {}

    result = _agent(script, rows=_registry()).run(
        "visa overstay", matter_id="case-1", playbook=playbook
    )
    assert [c.cited_as for c in result.citations] == ["Clause 4"]
    run = audit.get_run(result.run_id)
    assert run.mode == "research:immigration-ng" and run.tool_call_count == 2
    assert [(c.tool, c.status) for c in audit.list_calls(result.run_id)] == [
        ("analyze_case", "denied"),
        ("search_case_documents", "ok"),
    ]
