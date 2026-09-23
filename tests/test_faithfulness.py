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
