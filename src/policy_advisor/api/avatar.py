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
