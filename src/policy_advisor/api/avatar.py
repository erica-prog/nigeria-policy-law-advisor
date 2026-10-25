"""The single place that turns pipeline outcomes into an avatar state
(docs/15-avatar-character-asset.md, docs/contracts/web-api.md "Avatar
state"). The browser maps the state to a frame and caption; it never
re-derives trust from the answer text."""

from policy_advisor.api.schemas import AvatarState

NO_AUTHORITY_CONFIDENCE = "no authority found in corpus"


def avatar_state_for_answer(source: str, faithful: bool) -> AvatarState:
    if source == "none":
        return "no_results"
    if source == "web":
        return "web_source"
    # "library" answers are grounded and citation-checked like "corpus" ones;
    # the bubble and the citation kinds say they are authority, not evidence.
    if not faithful:
        return "unverified"
    return "verified_source"


def avatar_state_for_analysis(issues) -> AvatarState:
    """`issues` are IssueAnalysis-like objects with `confidence` and `unverified`."""
    if not issues or all(issue.confidence == NO_AUTHORITY_CONFIDENCE for issue in issues):
        return "no_results"
    if any(issue.unverified for issue in issues):
        return "unverified"
    return "verified_source"


def avatar_state_for_research(citation_kinds: list[str], faithful: bool) -> AvatarState:
    """A research run (docs/28) cites passages, connector results and web
    pages; the state follows the weakest provenance in the answer."""
    if not citation_kinds:
        return "no_results"
    if not faithful:
        return "unverified"
    if "web" in citation_kinds:
        return "web_source"
    return "verified_source"
