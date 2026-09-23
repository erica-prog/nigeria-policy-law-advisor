"""Restricted web-search fallback for when a matter's own documents don't
contain the answer (CLAUDE-2.md cross-cutting concern). Deliberately not
blended into the corpus-citation faithfulness pipeline in faithfulness.py -
a web result has no provenance the lawyer vetted, so it's surfaced as a
distinct, separately-labeled source (see chain.py's `source` field and
app.py's rendering), not merged in to look equally trustworthy.

Uses Anthropic's native web_search_20250305 server tool (not a third-party
search API) so the existing ANTHROPIC_API_KEY covers it, and so the
domain restriction below is enforced server-side, not by us post-filtering
results we can't fully trust to begin with.

OFFICIAL_SOURCE_DOMAINS is a starter draft, not a finished allowlist -
deciding what counts as an authoritative source for legal research is a
legal-judgment call, not an engineering one. Confirmed so far:
- nass.gov.ng - National Assembly of Nigeria (legislation/Acts)
- ndpc.gov.ng - Nigeria Data Protection Commission (relevant to the
  existing Meta v NDPC judgment in the demo corpus)
Review and extend this list before relying on it for real research."""

from dataclasses import dataclass, field

import anthropic

from policy_advisor.config import get_settings

OFFICIAL_SOURCE_DOMAINS = ["nass.gov.ng", "ndpc.gov.ng"]


@dataclass
class WebSearchCitation:
    url: str
    title: str


@dataclass
class WebSearchResult:
    answer: str
    citations: list[WebSearchCitation] = field(default_factory=list)
    found: bool = True


def search_official_sources(question: str, response_language: str = "English") -> WebSearchResult:
    settings = get_settings()
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key.get_secret_value())

    response = client.messages.create(
        model=settings.anthropic_model,
        max_tokens=1024,
        tools=[
            {
                "type": "web_search_20250305",
                "name": "web_search",
                "allowed_domains": OFFICIAL_SOURCE_DOMAINS,
                "max_uses": 3,
            }
        ],
        messages=[
            {
                "role": "user",
                "content": (
                    f"Respond in {response_language}. Answer using only what you find on the allowed "
                    f"official sources. If you can't find a relevant answer there, say so plainly rather "
                    f"than answering from general knowledge.\n\nQuestion: {question}"
                ),
            }
        ],
    )

    answer_parts = []
    citations: list[WebSearchCitation] = []
    seen_urls = set()
    for block in response.content:
        if block.type != "text":
            continue
        answer_parts.append(block.text)
        for citation in block.citations or []:
            url = getattr(citation, "url", None)
            if url and url not in seen_urls:
                seen_urls.add(url)
                citations.append(WebSearchCitation(url=url, title=getattr(citation, "title", url)))

    answer = "".join(answer_parts).strip()
    return WebSearchResult(answer=answer, citations=citations, found=bool(citations))
