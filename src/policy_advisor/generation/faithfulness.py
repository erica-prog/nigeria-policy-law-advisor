"""Cheapest faithfulness check (docs/06): verify every locator the answer
cites actually appeared among the chunks sent to context. Deterministic, no
extra LLM call - catches the most dangerous failure mode (citing something
that was never retrieved) before a Claude-as-judge pass is worth the cost."""

import re

LOCATOR_RE = re.compile(r"\[Source:\s*([^\]]+)\]")


def extract_cited_locators(answer_text: str) -> list[str]:
    return [match.group(1).strip() for match in LOCATOR_RE.finditer(answer_text)]


def is_supported_citation(cited_locator: str, available_locators: set[str]) -> bool:
    """Claude may cite a sub-rule pinpoint within a retrieved chunk (e.g.
    "Order 5 Rule 3(6)") that's more precise than the chunk-level locator
    ("Order 5 Rule 3") - that's legitimate, not a fabrication, so a prefix
    match counts as supported. Only a locator with no retrieved chunk behind
    it at all is flagged."""
    return any(
        cited_locator == available or cited_locator.startswith(available)
        for available in available_locators
    )


def check_faithfulness(answer_text: str, retrieved_chunks) -> tuple[bool, list[str]]:
    available_locators = {chunk.metadata["locator"] for chunk in retrieved_chunks}
    cited = extract_cited_locators(answer_text)
    unsupported = [locator for locator in cited if not is_supported_citation(locator, available_locators)]
    return (len(unsupported) == 0, unsupported)
