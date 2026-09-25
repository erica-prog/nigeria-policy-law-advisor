"""Automated regression gate for case reasoning and advisory mode.

Kept separate from run_case_reasoning_seed.py rather than bolted onto it. That
script deliberately has no pass/fail because the thing it exists to surface -
whether the legal analysis is any good - needs a human against the rubric, and
a green tick there would imply a judgement nothing in CI is entitled to make.

This checks something narrower and genuinely machine-decidable: that the
structural guarantees the pipeline advertises still hold, whatever the quality
of the reasoning inside them. Every one of these corresponds to a promise made
elsewhere in the codebase, so a violation is a real regression rather than a
stylistic drift.

Usage:
  uv run python -m eval.run_case_reasoning_invariants               # every seed case
  uv run python -m eval.run_case_reasoning_invariants --id c01      # one case (PR-sized)
  uv run python -m eval.run_case_reasoning_invariants --with-advisory
"""

import argparse
import json
from pathlib import Path

SEED_CASES_PATH = Path(__file__).parent / "case_reasoning_seed_cases.json"

# case_reasoning_prompt.py asks for "2-6 issues"; drifting outside that means
# the issue-identification step stopped doing what the rest of the chain
# assumes about its output.
MIN_ISSUES = 2
MAX_ISSUES = 6


def _check_case(result) -> list[str]:
    from policy_advisor.generation.case_reasoning_models import CONFIDENCE_STRONG
    from policy_advisor.generation.faithfulness import is_supported_citation

    violations: list[str] = []

    if not result.disclaimer.strip():
        # case_reasoning.py's docstring: "never optional, never just a UI footer".
        violations.append("disclaimer is empty")

    if not MIN_ISSUES <= len(result.issues) <= MAX_ISSUES:
        violations.append(f"issue count {len(result.issues)} outside {MIN_ISSUES}-{MAX_ISSUES}")

    for issue in result.issues:
        label = issue.issue[:60]

        if issue.unverified and issue.confidence == CONFIDENCE_STRONG:
            violations.append(f"[{label}] marked unverified but still labelled '{CONFIDENCE_STRONG}'")

        if issue.from_web:
            # The separation the whole web-fallback design rests on: web
            # material never arrives as a checked authority.
            if issue.arguments:
                violations.append(f"[{label}] web-backed issue carries arguments, which are never web-derived")
            if not issue.web_summary:
                violations.append(f"[{label}] has web sources but no summary")
            if issue.retrieved_locators:
                violations.append(f"[{label}] is web-backed yet records corpus authorities")
            continue

        # The locators the chain actually had, recorded during analysis. An
        # earlier version of this check re-ran retrieval here and dropped the
        # jurisdiction filter, so it judged citations against a different set
        # of authorities and reported violations the chain's own check never
        # saw. Re-deriving state the pipeline already knows is how a gate ends
        # up testing itself rather than the pipeline.
        available = set(issue.retrieved_locators)
        cited = [
            authority.locator
            for argument in issue.arguments
            for authority in argument.supporting_authorities
        ]
        unsupported = [loc for loc in cited if not is_supported_citation(loc, available)]
        if unsupported and not issue.unverified:
            # Either the citation check passed, or the issue is flagged. An
            # unsupported citation on an unflagged issue means the flag stopped
            # working, which is the failure the whole tiered check prevents.
            violations.append(f"[{label}] cites {unsupported} but is not flagged unverified")

    return violations


def _check_advisory(advisory) -> list[str]:
    from policy_advisor.generation.advisory_models import (
        OUTCOME_CONFIDENCE_LOW,
        assess_outcome_confidence,
    )

    violations: list[str] = []
    expected, _ = assess_outcome_confidence(advisory.case_analysis.issues)

    if advisory.unsupported_citations or advisory.judge_flagged:
        expected = OUTCOME_CONFIDENCE_LOW
    if advisory.outcome_confidence != expected:
        violations.append(
            f"outcome confidence is {advisory.outcome_confidence!r}, expected {expected!r} "
            "from the state of the underlying issues"
        )

    if not advisory.disclaimer.strip():
        violations.append("advisory disclaimer is empty")
    if not advisory.confidence_basis.strip():
        violations.append("advisory states a confidence with no stated basis")

    # The prompt forbids these outright: the corpus contains no decided
    # outcomes, so any frequency claim is invented rather than derived.
    forecast_language = ("usually", "typically", "tend to", "% chance", "percent chance", "probability of")
    prose = f"{advisory.likely_outcome} {advisory.recommended_position}".lower()
    found = [phrase for phrase in forecast_language if phrase in prose]
    if found:
        violations.append(f"advisory uses forecast language {found} with no decided cases behind it")

    return violations


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", default=None, help="run only the seed case with this id")
    parser.add_argument("--with-advisory", action="store_true", help="also check advisory mode")
    args = parser.parse_args()

    from policy_advisor.generation.advisory import AdvisoryChain
    from policy_advisor.generation.case_reasoning import CaseReasoningChain

    cases = json.loads(SEED_CASES_PATH.read_text(encoding="utf-8"))
    if args.id:
        cases = [c for c in cases if c["id"] == args.id]
        if not cases:
            raise SystemExit(f"No seed case with id {args.id!r}")

    case_chain = CaseReasoningChain()
    advisory_chain = AdvisoryChain(case_chain=case_chain) if args.with_advisory else None

    all_violations: dict[str, list[str]] = {}
    for case in cases:
        result = case_chain.analyze(
            case["case_facts"], matter_id=case["matter_id"], jurisdiction=case["jurisdiction"]
        )
        violations = _check_case(result)

        if advisory_chain is not None:
            advisory = advisory_chain.advise_on(result, matter_id=case["matter_id"])
            violations += _check_advisory(advisory)

        status = "FAIL" if violations else "ok"
        print(f"{case['id']}: {status} ({len(result.issues)} issues)")
        for violation in violations:
            print(f"    - {violation}")
        if violations:
            all_violations[case["id"]] = violations

    print(f"\n--- summary ---\ncases: {len(cases)}, failing: {len(all_violations)}")
    if all_violations:
        raise SystemExit(f"Structural invariants violated in: {', '.join(sorted(all_violations))}")


if __name__ == "__main__":
    main()
