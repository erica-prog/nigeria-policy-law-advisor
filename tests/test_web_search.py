"""Tests web_search.py's response-extraction logic (mocked Anthropic client)
and, at the bottom, a real call proving allowed_domains is actually enforced
- this is the whole point of "restrict to official sources only," so it's
worth proving against the live API rather than trusting the SDK's docs."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from policy_advisor.generation.web_search import search_official_sources

MODULE = "policy_advisor.generation.web_search"


def _text_block(text: str, citations=None):
    return SimpleNamespace(type="text", text=text, citations=citations)


def _client_returning(blocks):
    client = MagicMock()
    client.messages.create.return_value = SimpleNamespace(content=blocks)
    return client


def test_concatenates_text_blocks_into_the_answer():
    blocks = [_text_block("Part one. "), _text_block("Part two.")]
    with patch(f"{MODULE}.anthropic.Anthropic", return_value=_client_returning(blocks)):
        result = search_official_sources("some question")

    assert result.answer == "Part one. Part two."


def test_extracts_deduplicated_citations_across_text_blocks():
    citation_a = SimpleNamespace(url="https://nass.gov.ng/a", title="A")
    citation_b = SimpleNamespace(url="https://nass.gov.ng/b", title="B")
    blocks = [
        _text_block("first", citations=[citation_a]),
        _text_block("second", citations=[citation_a, citation_b]),  # citation_a repeated
    ]
    with patch(f"{MODULE}.anthropic.Anthropic", return_value=_client_returning(blocks)):
        result = search_official_sources("some question")

    urls = [c.url for c in result.citations]
    assert urls == ["https://nass.gov.ng/a", "https://nass.gov.ng/b"]
    assert result.found is True


def test_no_citations_means_not_found():
    blocks = [_text_block("I couldn't find anything relevant.", citations=None)]
    with patch(f"{MODULE}.anthropic.Anthropic", return_value=_client_returning(blocks)):
        result = search_official_sources("some question")

    assert result.found is False
    assert result.citations == []


def test_ignores_non_text_blocks():
    blocks = [
        SimpleNamespace(type="server_tool_use", input={"query": "x"}),
        _text_block("the answer"),
    ]
    with patch(f"{MODULE}.anthropic.Anthropic", return_value=_client_returning(blocks)):
        result = search_official_sources("some question")

    assert result.answer == "the answer"


# --- Real call below: proving allowed_domains is actually enforced. ---


def test_allowed_domains_is_actually_enforced_real_call():
    result = search_official_sources("What is the Nigeria Data Protection Act about?")

    assert result.found is True
    for citation in result.citations:
        assert any(domain in citation.url for domain in ["nass.gov.ng", "ndpc.gov.ng"])
