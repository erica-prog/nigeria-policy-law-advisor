from unittest.mock import MagicMock

from policy_advisor.generation.judge_check import JudgeVerdict, judge_check
from policy_advisor.retrieval.hybrid_retriever import RetrievedChunk


def _llm_returning(verdict: str, notes: str) -> MagicMock:
    llm = MagicMock()
    llm.with_structured_output.return_value.invoke.return_value = JudgeVerdict(verdict=verdict, notes=notes)
    return llm


def test_allowed_verdict_is_faithful_and_not_flagged():
    chunks = [RetrievedChunk(text="t", metadata={"locator": "Order 1 Rule 1"})]
    outcome = judge_check(_llm_returning("ALLOWED", "looks fine"), "some analysis text", chunks)

    assert outcome.faithful is True
    assert outcome.judge_flagged is False
    assert outcome.judge_notes == "looks fine"


def test_flagged_verdict_is_unfaithful_and_flagged():
    outcome = judge_check(_llm_returning("FLAGGED", "overreaches the cited rule"), "some analysis text", [])

    assert outcome.faithful is False
    assert outcome.judge_flagged is True
    assert outcome.judge_notes == "overreaches the cited rule"


def test_verdict_comparison_is_case_insensitive():
    outcome = judge_check(_llm_returning("flagged", "x"), "text", [])
    assert outcome.judge_flagged is True
