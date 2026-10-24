"""Restricted web-search fallback for when a matter's own documents don't
contain the answer (CLAUDE-2.md cross-cutting concern). Deliberately not
blended into the corpus-citation faithfulness pipeline in faithfulness.py -
a web result has no provenance the lawyer vetted, so it's surfaced as a
distinct, separately-labeled source (see chain.py's `source` field and
api/avatar.py's trust state), not merged in to look equally trustworthy.

Uses Anthropic's native web_search_20250305 server tool (not a third-party
search API) so the existing ANTHROPIC_API_KEY covers it, and so the
domain restriction below is enforced server-side, not by us post-filtering
results we can't fully trust to begin with.

Which domains count as official is a registry question, not a constant:
`sources.web_search_domains()` returns the union of `config.domains` over the
allowed `web_search` rows (docs/24-source-registry.md). No allowed row means
no search at all - the function returns "not found" without calling Anthropic,
because a web search with no restriction would put unvetted pages on the same
footing as the lawyer's documents."""

from dataclasses import dataclass, field

import anthropic

from policy_advisor import sources
from policy_advisor.config import get_settings, workspace_headers
from policy_advisor.llm_retry import call_with_retry


@dataclass
class WebSearchCitation:
    url: str
    title: str


@dataclass
class WebSearchResult:
    answer: str
    citations: list[WebSearchCitation] = field(default_factory=list)
    found: bool = True


def search_official_sources(
    question: str,
    response_language: str = "English",
    api_key: str | None = None,
    domains: list[str] | None = None,
) -> WebSearchResult:
    """`api_key` overrides the server key from settings (the web API passes the
    requesting user's own key); None keeps the previous behaviour. `domains`
    overrides the registry's allow-list (tests and evals); None reads it."""
    allowed_domains = sources.web_search_domains() if domains is None else list(domains)
    if not allowed_domains:
        return WebSearchResult(answer="", citations=[], found=False)
    settings = get_settings()
    client = anthropic.Anthropic(
        api_key=api_key or settings.anthropic_api_key.get_secret_value(),
        default_headers=workspace_headers(settings, api_key) or None,
    )

    response = call_with_retry(
        lambda: client.messages.create(
            model=settings.anthropic_model,
            max_tokens=1024,
            tools=[
                {
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "allowed_domains": allowed_domains,
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
