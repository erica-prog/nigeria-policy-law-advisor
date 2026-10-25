"""The drafting rubric and the canaries' table (docs/30 slice P3-H, AC-9)."""

import json
from pathlib import Path

from eval.drafting.rubric import CRITERIA, score_artefact
from eval.run_connector_canaries import PROBES
from eval.run_drafting_eval import score_fixtures
from policy_advisor import sources

FIXTURES = Path(__file__).parent.parent / "eval" / "drafting" / "fixtures"


def test_rubric_rewards_a_grounded_structured_draft_and_penalises_each_defect():
    good = json.loads((FIXTURES / "good-memo.json").read_text())
    scored = score_artefact(good["outline"], good["manifest"])
    assert scored.score == 1.0 and set(scored.criteria) == set(CRITERIA) and scored.notes == []
    evidence = json.loads((FIXTURES / "evidence-as-authority.json").read_text())
    scored = score_artefact(evidence["outline"], evidence["manifest"])
    assert (
        scored.criteria["no_evidence_as_authority"] == 0.0 and scored.criteria["grounded"] == 0.75
    )
    assert any("case evidence" in n for n in scored.notes)
    flagged = json.loads((FIXTURES / "unverified-and-flagged.json").read_text())
    scored = score_artefact(flagged["outline"], flagged["manifest"])
    assert scored.criteria["judge_clean"] == 0.75 and scored.criteria["limitations_stated"] == 0.0
    assert scored.criteria["instruction_separated"] == 0.0
    thin = {
        "title": "t",
        "sections": [{"heading": "Notes", "paragraphs": []}],
        "limitations": ["x"],
    }
    scored = score_artefact(thin, {"entries": [], "judge": []})
    assert scored.criteria["structured"] == 0.0 and scored.criteria["bounded"] == 0.0


def test_fixture_gate_passes_as_recorded():
    rows = {r["id"]: r for r in score_fixtures()}
    assert rows["good-memo"]["score"] == 1.0
    assert rows["evidence-as-authority"]["score"] <= rows["evidence-as-authority"]["expected_max"]
    assert rows["unverified-and-flagged"]["score"] <= rows["unverified-and-flagged"]["expected_max"]


def test_every_external_connector_in_the_seed_has_a_canary_probe():
    connectors = {s.connector for s in sources.load_seed()} - {"web_search", "library", "none"}
    assert connectors <= set(PROBES), f"no probe for {connectors - set(PROBES)}"
