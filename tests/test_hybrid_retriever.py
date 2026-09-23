from policy_advisor.retrieval.hybrid_retriever import _LOCATOR_RE, _normalize


def test_normalize_maps_min_to_zero_and_max_to_one():
    assert _normalize([1.0, 2.0, 3.0]) == [0.0, 0.5, 1.0]


def test_normalize_handles_empty_list():
    assert _normalize([]) == []


def test_normalize_handles_all_equal_scores_without_division_by_zero():
    assert _normalize([5.0, 5.0, 5.0]) == [0.0, 0.0, 0.0]


def test_locator_regex_matches_common_phrasings():
    for query in ["Order 5 Rule 3", "order 5, rule 3", "Order 5  Rule 3 of the FHC rules"]:
        match = _LOCATOR_RE.search(query)
        assert match is not None
        assert match.group(1) == "5"
        assert match.group(2) == "3"


def test_locator_regex_does_not_match_unrelated_queries():
    assert _LOCATOR_RE.search("what is the deadline to file a defence?") is None
