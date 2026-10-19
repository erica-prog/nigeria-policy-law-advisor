from types import SimpleNamespace

import pytest

from policy_advisor.api.avatar import avatar_state_for_analysis, avatar_state_for_answer


@pytest.mark.parametrize(
    ("source", "faithful", "expected"),
    [
        ("corpus", True, "verified_source"),
        ("corpus", False, "unverified"),
        ("web", True, "web_source"),
        ("web", False, "web_source"),
        ("none", True, "no_results"),
        ("none", False, "no_results"),
    ],
)
def test_answer_mapping(source, faithful, expected):
    assert avatar_state_for_answer(source, faithful) == expected


def _issue(confidence="strongly supported", unverified=False):
    return SimpleNamespace(confidence=confidence, unverified=unverified)


def test_analysis_with_no_issues_is_no_results():
    assert avatar_state_for_analysis([]) == "no_results"


def test_analysis_with_only_unsupported_issues_is_no_results():
    issues = [_issue("no authority found in corpus"), _issue("no authority found in corpus")]
    assert avatar_state_for_analysis(issues) == "no_results"


def test_any_unverified_issue_wins_over_supported_ones():
    issues = [_issue(), _issue("unverified - flagged by faithfulness check", unverified=True)]
    assert avatar_state_for_analysis(issues) == "unverified"


def test_supported_issues_are_verified_source_even_with_one_gap():
    issues = [_issue(), _issue("no authority found in corpus")]
    assert avatar_state_for_analysis(issues) == "verified_source"
