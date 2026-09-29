"""Schema for advisory mode: a direct recommendation and an outcome assessment,
layered on top of the issue-by-issue analysis in case_reasoning_models.py.

This is the one place in the product that takes a position rather than
describing how well each side is supported. `CaseReasoningChain.analyze()` is
unchanged and still refuses to do this, so the existing no-prediction guarantee
remains available - advisory mode is a separate, opt-in entry point rather than
a loosening of that one.

The split between what Claude generates (`AdvisoryOpinion`) and what the chain
returns (`AdvisoryResult`) is deliberate and follows the precedent set by
IssueArguments/IssueAnalysis: the model writes the reasoning, the chain decides
how much confidence that reasoning has earned. A model asked to rate its own
confidence will oblige with whatever number sounds right.
"""

from typing import Literal

from pydantic import BaseModel, Field

from policy_advisor.generation.case_reasoning_models import (
    CONFIDENCE_STRONG,
    CaseReasoningResult,
    IssueAnalysis,
)

OutcomeDirection = Literal["favours claimant", "favours respondent", "too close to call"]

# Two bands, and no third. "High" is not reachable from this corpus and adding
# the label would invite someone to earn it by loosening the rule below rather
# than by improving the evidence. See the note in the module docstring of
# advisory.py on what a genuine high-confidence outcome call would require.
OUTCOME_CONFIDENCE_LOW = "low"
OUTCOME_CONFIDENCE_MODERATE = "moderate"

ADVISORY_DISCLAIMER = (
    "This is a reasoned view of how your position stands on the authorities in this matter - not a "
    "prediction of what any court will do, and not a substitute for your own judgment. It has no "
    "access to how comparable cases were actually decided: this matter holds authorities, not "
    "outcomes, so nothing here reflects how often positions like yours succeed in practice. Treat "
    "the direction below as an argument to test, not a forecast."
)


class RecommendedAction(BaseModel):
    action: str = Field(description="A concrete next step the lawyer can take")
    rationale: str = Field(description="One sentence on why this step follows from the analysis")


class KeyAuthority(BaseModel):
    locator: str = Field(description="Exact locator, e.g. 'Order 5 Rule 3' - must already appear in the analysis")
    why: str = Field(description="One sentence on what this authority does for the recommended position")


class AdvisoryOpinion(BaseModel):
    """The part Claude generates. No confidence field on purpose."""

    recommended_position: str = Field(description="The position to run, and why it is the stronger one")
    outcome_direction: OutcomeDirection
    likely_outcome: str = Field(description="What that direction rests on, in two or three sentences")
    turns_on: str = Field(description="The single fact or authority that would most change this assessment")
    next_steps: list[RecommendedAction] = Field(default_factory=list)
    key_authorities: list[KeyAuthority] = Field(default_factory=list)


class AdvisoryResult(BaseModel):
    case_analysis: CaseReasoningResult
    recommended_position: str
    outcome_direction: OutcomeDirection
    likely_outcome: str
    turns_on: str
    next_steps: list[RecommendedAction] = Field(default_factory=list)
    key_authorities: list[KeyAuthority] = Field(default_factory=list)

    # Assigned by the chain from the state of the underlying issues.
    outcome_confidence: str = OUTCOME_CONFIDENCE_LOW
    confidence_basis: str = ""

    # Authorities the advisory cited that no issue analysis had cited. The
    # advisory is a synthesis of work already done and grounded; introducing a
    # new locator here means it came from somewhere that was never checked.
    unsupported_citations: list[str] = Field(default_factory=list)
    judge_flagged: bool = False
    judge_notes: str = ""
    disclaimer: str = ADVISORY_DISCLAIMER


def assess_outcome_confidence(issues: list[IssueAnalysis]) -> tuple[str, str]:
    """How much confidence the underlying analysis has earned, and why.

    Computed rather than generated, and pessimistic by construction: any one
    weak issue caps the whole assessment, because a recommendation is only as
    good as the shakiest step it rests on. A lawyer reading "moderate" beside a
    recommendation built partly on an unverified citation would be misled by
    arithmetic that averaged the weakness away.
    """
    if not issues:
        return OUTCOME_CONFIDENCE_LOW, "No issues were analysed."

    reasons = []
    if any(issue.unverified for issue in issues):
        reasons.append("at least one issue failed its citation check")
    if any(issue.from_web for issue in issues):
        reasons.append("at least one issue rests on a web source not checked against this matter")
    weak = [issue for issue in issues if issue.confidence != CONFIDENCE_STRONG and not issue.from_web]
    if weak:
        reasons.append(f"{len(weak)} of {len(issues)} issues lack strong authority in this matter")

    if reasons:
        return OUTCOME_CONFIDENCE_LOW, "; ".join(reasons) + "."
    return (
        OUTCOME_CONFIDENCE_MODERATE,
        f"All {len(issues)} issues are supported by authority in this matter and passed their "
        "citation checks. Still not a forecast - see the disclaimer.",
    )
