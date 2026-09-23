from policy_advisor.generation.case_reasoning import _cited_locators, _unsupported_citations
from policy_advisor.generation.case_reasoning_models import Argument, Authority, IssueArguments


def _issue_args(locators: list[str]) -> IssueArguments:
    return IssueArguments(
        issue="some issue",
        arguments=[
            Argument(side="claimant", summary="s", supporting_authorities=[Authority(locator=loc, relevance="r")])
            for loc in locators
        ],
        assessment="z",
    )


def test_cited_locators_flattens_across_all_arguments():
    assert _cited_locators(_issue_args(["X", "Y"])) == ["X", "Y"]


def test_unsupported_citations_detects_missing_locator():
    assert _unsupported_citations(_issue_args(["Order 99 Rule 1"]), {"Order 1 Rule 1"}) == ["Order 99 Rule 1"]


def test_unsupported_citations_empty_when_all_supported():
    assert _unsupported_citations(_issue_args(["Order 1 Rule 1"]), {"Order 1 Rule 1"}) == []


def test_unsupported_citations_allows_sub_rule_pinpoint():
    assert _unsupported_citations(_issue_args(["Order 1 Rule 1(2)"]), {"Order 1 Rule 1"}) == []
