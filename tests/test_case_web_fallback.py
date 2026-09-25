"""Tests that an issue with no authority in the matter can reach the official
sources, and that what comes back stays visibly separate from vetted material.

The separation is the point. `arguments` and `supporting_authorities` have all
survived the citation check against passages the lawyer put in the matter; web
material has not. If it arrived in the same fields, nothing downstream - the
UI, the advisory layer, the CI invariants - could tell the two apart.

Mocks the LLM, retriever and web search, so this runs offline.
"""

from unittest.mock import MagicMock, patch

from policy_advisor.generation.case_reasoning import CaseReasoningChain
from policy_advisor.generation.case_reasoning_models import (
    CONFIDENCE_FROM_WEB,
    CONFIDENCE_NO_AUTHORITY,
)
from policy_advisor.generation.web_search import WebSearchCitation, WebSearchResult

JUDGE_CHECK_PATH = "policy_advisor.generation.case_reasoning.judge_check"
WEB_SEARCH_PATH = "policy_advisor.generation.case_reasoning.search_official_sources"


def _chain() -> CaseReasoningChain:
    chain = object.__new__(CaseReasoningChain)
    chain._retriever = MagicMock()
    chain._retriever.retrieve.return_value = []  # matter holds nothing on this issue
    chain._retriever.needs_relevance_adjudication.return_value = False
    chain._logger = MagicMock()
    chain._settings = MagicMock(retrieval_top_k=8)
    chain._llm = MagicMock()
    return chain


def _found() -> WebSearchResult:
    return WebSearchResult(
        answer="The Act says X.",
        citations=[WebSearchCitation(url="https://nass.gov.ng/act", title="The Act")],
        found=True,
    )


def test_web_fallback_is_not_used_unless_asked_for():
    chain = _chain()
    with patch(WEB_SEARCH_PATH) as search, patch(JUDGE_CHECK_PATH, return_value=MagicMock(judge_flagged=False)):
        with patch.object(chain, "_generate_issue_arguments", return_value=MagicMock(issue="i", arguments=[], assessment="a")):
            result = chain._analyze_issue("facts", "i", "m", None, "English", allow_web_fallback=False)

    search.assert_not_called()
    assert result.confidence == CONFIDENCE_NO_AUTHORITY
    assert result.from_web is False


def test_web_result_is_kept_out_of_arguments_and_authorities():
    chain = _chain()
    with patch(WEB_SEARCH_PATH, return_value=_found()):
        result = chain._analyze_issue("facts", "i", "m", None, "English", allow_web_fallback=True)

    assert result.from_web is True
    assert result.confidence == CONFIDENCE_FROM_WEB
    assert result.arguments == []  # nothing web-derived may look like checked authority
    assert result.web_summary == "The Act says X."
    assert [c.url for c in result.web_sources] == ["https://nass.gov.ng/act"]


def test_web_backed_issue_is_never_sent_to_the_judge():
    # judge_check decides whether text stays within its authorities. Handing it
    # web text as the authority would have it certify the one source that was
    # never vetted - an ALLOWED verdict on material nobody checked.
    chain = _chain()
    with patch(WEB_SEARCH_PATH, return_value=_found()), patch(JUDGE_CHECK_PATH) as judge:
        chain._analyze_issue("facts", "i", "m", None, "English", allow_web_fallback=True)

    judge.assert_not_called()


def test_web_backed_issue_records_no_corpus_authorities():
    chain = _chain()
    with patch(WEB_SEARCH_PATH, return_value=_found()):
        result = chain._analyze_issue("facts", "i", "m", None, "English", allow_web_fallback=True)

    assert result.retrieved_locators == []


def test_web_search_finding_nothing_falls_back_to_no_authority():
    chain = _chain()
    empty = WebSearchResult(answer="", citations=[], found=False)
    with patch(WEB_SEARCH_PATH, return_value=empty):
        result = chain._analyze_issue("facts", "i", "m", None, "English", allow_web_fallback=True)

    assert result.confidence == CONFIDENCE_NO_AUTHORITY
    assert result.from_web is False
    assert result.web_summary is None


def test_irrelevant_retrieval_is_adjudicated_away_and_reaches_the_web():
    # The matter returns chunks, but they are borderline on distance and the
    # adjudicator says they are off-topic. That has to behave exactly like
    # retrieving nothing, or the fallback stays unreachable for the realistic
    # case rather than only the empty-matter one.
    chain = _chain()
    chain._retriever.retrieve.return_value = [MagicMock(metadata={"locator": "Order 1 Rule 1"})]
    chain._retriever.needs_relevance_adjudication.return_value = True

    with patch(
        "policy_advisor.generation.case_reasoning.judge_relevance",
        return_value=MagicMock(relevant=False, reason="different area of law"),
    ), patch(WEB_SEARCH_PATH, return_value=_found()) as search:
        result = chain._analyze_issue("facts", "i", "m", None, "English", allow_web_fallback=True)

    search.assert_called_once()
    assert result.from_web is True
