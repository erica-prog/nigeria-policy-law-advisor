"""Tests that advisory mode's confidence is earned rather than asserted.

Advisory mode is the one place the product takes a position, so the guard that
matters is not what it says but how sure it is allowed to sound. Confidence is
computed from the state of the underlying issues, never generated, for the same
reason `IssueAnalysis.confidence` is: a model asked to rate its own work will
produce whatever number reads well.

Runs offline - the LLM, retriever and judge are mocked.
"""

from unittest.mock import MagicMock, patch

from policy_advisor.generation.advisory import AdvisoryChain
from policy_advisor.generation.advisory_models import (
    OUTCOME_CONFIDENCE_LOW,
    OUTCOME_CONFIDENCE_MODERATE,
    AdvisoryOpinion,
    KeyAuthority,
    assess_outcome_confidence,
)
from policy_advisor.generation.case_reasoning_models import (
    CONFIDENCE_FROM_WEB,
    CONFIDENCE_LIMITED,
    CONFIDENCE_STRONG,
    Argument,
    Authority,
    CaseReasoningResult,
    IssueAnalysis,
    StepCheckOutcome,
)
from policy_advisor.generation.web_search import WebSearchCitation

JUDGE_CHECK_PATH = "policy_advisor.generation.advisory.judge_check"


def _strong(locator: str = "Order 5 Rule 3") -> IssueAnalysis:
    return IssueAnalysis(
        issue="an issue",
        arguments=[
            Argument(
                side="claimant",
                summary="s",
                supporting_authorities=[Authority(locator=locator, relevance="r")],
            )
        ],
        assessment="a",
        confidence=CONFIDENCE_STRONG,
    )


def _unverified() -> IssueAnalysis:
    return IssueAnalysis(issue="i", arguments=[], assessment="a", confidence=CONFIDENCE_STRONG, unverified=True)


def _from_web() -> IssueAnalysis:
    return IssueAnalysis(
        issue="i",
        arguments=[],
        assessment="a",
        confidence=CONFIDENCE_FROM_WEB,
        web_summary="the site says x",
        web_sources=[WebSearchCitation(url="https://nass.gov.ng/a", title="t")],
    )


def _limited() -> IssueAnalysis:
    return IssueAnalysis(issue="i", arguments=[], assessment="a", confidence=CONFIDENCE_LIMITED)


def test_all_strong_issues_reach_moderate():
    confidence, basis = assess_outcome_confidence([_strong(), _strong()])
    assert confidence == OUTCOME_CONFIDENCE_MODERATE
    assert basis  # a band is never reported without its reason


def test_one_unverified_issue_caps_the_whole_assessment():
    # Pessimistic by construction: a recommendation is only as good as the
    # shakiest step under it, and averaging the weakness away would mislead.
    confidence, basis = assess_outcome_confidence([_strong(), _strong(), _unverified()])
    assert confidence == OUTCOME_CONFIDENCE_LOW
    assert "citation check" in basis


def test_one_web_backed_issue_caps_the_whole_assessment():
    confidence, basis = assess_outcome_confidence([_strong(), _from_web()])
    assert confidence == OUTCOME_CONFIDENCE_LOW
    assert "web source" in basis


def test_limited_authority_caps_the_assessment():
    confidence, _ = assess_outcome_confidence([_strong(), _limited()])
    assert confidence == OUTCOME_CONFIDENCE_LOW


def test_no_issues_is_low_not_moderate():
    confidence, _ = assess_outcome_confidence([])
    assert confidence == OUTCOME_CONFIDENCE_LOW


def _advisory_chain() -> AdvisoryChain:
    chain = object.__new__(AdvisoryChain)
    chain._settings = MagicMock(retrieval_top_k=8)
    chain._logger = MagicMock()
    chain._llm = MagicMock()
    chain._case_chain = MagicMock()
    chain._case_chain.retriever.retrieve.return_value = []
    return chain


def _analysis(issues: list[IssueAnalysis]) -> CaseReasoningResult:
    return CaseReasoningResult(
        case_facts="facts", issues=issues, overall_position="position", disclaimer="d"
    )


def _opinion(authorities: list[str]) -> AdvisoryOpinion:
    return AdvisoryOpinion(
        recommended_position="run the limitation point",
        outcome_direction="favours claimant",
        likely_outcome="the authorities support it",
        turns_on="whether service was effected in time",
        next_steps=[],
        key_authorities=[KeyAuthority(locator=loc, why="w") for loc in authorities],
    )


def _advise(chain: AdvisoryChain, analysis, opinion, judge_flagged=False):
    structured = MagicMock()
    structured.invoke.return_value = opinion
    chain._llm.with_structured_output.return_value = structured
    with patch(
        JUDGE_CHECK_PATH,
        return_value=StepCheckOutcome(faithful=not judge_flagged, judge_flagged=judge_flagged, judge_notes="n"),
    ):
        return chain.advise_on(analysis, matter_id="m")


def test_advice_citing_an_authority_no_issue_established_is_flagged_and_capped():
    # A locator appearing for the first time in the advice came from the
    # model's own knowledge, not from anything that was checked - the exact
    # failure the rest of the pipeline exists to catch.
    chain = _advisory_chain()
    result = _advise(chain, _analysis([_strong()]), _opinion(["Order 99 Rule 99"]))

    assert result.unsupported_citations == ["Order 99 Rule 99"]
    assert result.outcome_confidence == OUTCOME_CONFIDENCE_LOW
    assert "no issue analysis established" in result.confidence_basis


def test_advice_reusing_an_established_authority_keeps_its_earned_confidence():
    chain = _advisory_chain()
    result = _advise(chain, _analysis([_strong("Order 5 Rule 3")]), _opinion(["Order 5 Rule 3"]))

    assert result.unsupported_citations == []
    assert result.outcome_confidence == OUTCOME_CONFIDENCE_MODERATE


def test_a_flagged_judge_verdict_caps_confidence():
    chain = _advisory_chain()
    result = _advise(chain, _analysis([_strong()]), _opinion(["Order 5 Rule 3"]), judge_flagged=True)

    assert result.judge_flagged is True
    assert result.outcome_confidence == OUTCOME_CONFIDENCE_LOW


def test_sub_rule_pinpoints_count_as_supported():
    # Same rule as faithfulness.is_supported_citation: citing Order 5 Rule 3(6)
    # off a chunk located at Order 5 Rule 3 is a precise reference, not an
    # invention.
    chain = _advisory_chain()
    result = _advise(chain, _analysis([_strong("Order 5 Rule 3")]), _opinion(["Order 5 Rule 3(6)"]))

    assert result.unsupported_citations == []


def test_web_backed_issues_are_not_offered_to_the_judge_as_authority():
    chain = _advisory_chain()
    analysis = _analysis([_strong(), _from_web()])
    chain._case_chain.retriever.retrieve.return_value = [MagicMock(metadata={"locator": "Order 5 Rule 3"})]

    _advise(chain, analysis, _opinion(["Order 5 Rule 3"]))

    # One corpus-grounded issue, one web-backed: only the former is retrieved
    # for the judge's authority set.
    assert chain._case_chain.retriever.retrieve.call_count == 1


def test_the_disclaimer_is_always_present():
    chain = _advisory_chain()
    result = _advise(chain, _analysis([_strong()]), _opinion(["Order 5 Rule 3"]))

    assert "not a prediction" in result.disclaimer
    assert "authorities, not" in result.disclaimer
