from dataclasses import dataclass

from policy_advisor.generation.faithfulness import check_faithfulness, extract_cited_locators


@dataclass
class FakeChunk:
    metadata: dict


def test_extracts_all_cited_locators():
    text = "First claim [Source: Order 5 Rule 3]. Second claim [Source: Paragraph 11]."
    assert extract_cited_locators(text) == ["Order 5 Rule 3", "Paragraph 11"]


def test_faithful_when_every_citation_matches_a_retrieved_chunk():
    chunks = [FakeChunk(metadata={"locator": "Order 5 Rule 3"})]
    faithful, unsupported = check_faithfulness("Claim [Source: Order 5 Rule 3].", chunks)
    assert faithful is True
    assert unsupported == []


def test_unfaithful_when_a_citation_has_no_matching_chunk():
    chunks = [FakeChunk(metadata={"locator": "Order 5 Rule 3"})]
    faithful, unsupported = check_faithfulness("Claim [Source: Order 99 Rule 99].", chunks)
    assert faithful is False
    assert unsupported == ["Order 99 Rule 99"]


def test_sub_rule_pinpoint_citation_counts_as_supported():
    """Citing "Order 5 Rule 3(6)" when only "Order 5 Rule 3" was retrieved is
    legitimate added precision, not a fabrication - should not be flagged."""
    chunks = [FakeChunk(metadata={"locator": "Order 5 Rule 3"})]
    faithful, unsupported = check_faithfulness("Claim [Source: Order 5 Rule 3(6)].", chunks)
    assert faithful is True
    assert unsupported == []


def test_no_citations_is_trivially_faithful():
    faithful, unsupported = check_faithfulness("An answer with no citations at all.", [])
    assert faithful is True
    assert unsupported == []


def test_a_longer_rule_number_is_not_a_pinpoint_of_a_shorter_one():
    """ "Order 5 Rule 30" starts with "Order 5 Rule 3" but is a different rule.
    The scheme-aware check (docs/22 AC-5) must flag it."""
    chunks = [FakeChunk(metadata={"locator": "Order 5 Rule 3"})]
    faithful, unsupported = check_faithfulness("Claim [Source: Order 5 Rule 30].", chunks)
    assert faithful is False
    assert unsupported == ["Order 5 Rule 30"]


def test_a_bracket_citing_two_passages_is_two_citations_each_checked():
    """g11 was unfaithful in five nightly runs (docs/39 §9a): the model cited
    "[Source: Paragraph 13; Paragraph 13 (Part 2)]", both parts retrieved, and
    the check read the bracket as one locator that matched nothing."""
    text = "Both [Source: Paragraph 13; Paragraph 13 (Part 2)]. One [Source: Paragraph 11]."
    assert extract_cited_locators(text) == ["Paragraph 13", "Paragraph 13 (Part 2)", "Paragraph 11"]
    chunks = [
        FakeChunk(metadata={"locator": "Paragraph 13"}),
        FakeChunk(metadata={"locator": "Paragraph 13 (Part 2)"}),
        FakeChunk(metadata={"locator": "Paragraph 11"}),
    ]
    assert check_faithfulness(text, chunks) == (True, [])
    # Only the part that was never retrieved is flagged, by its own name.
    faithful, unsupported = check_faithfulness(
        "Claim [Source: Paragraph 13; Paragraph 99].", chunks
    )
    assert faithful is False and unsupported == ["Paragraph 99"]
    assert extract_cited_locators("[Source: ; Paragraph 11 ;]") == ["Paragraph 11"]
