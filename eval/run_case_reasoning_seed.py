"""Runs the seed cases in eval/case_reasoning_seed_cases.json through
CaseReasoningChain and prints structured output for a human reviewer to
score against eval/case_reasoning_rubric.md. No automated pass/fail here,
deliberately - see the rubric for why.

Usage:
  uv run python -m eval.run_case_reasoning_seed            # all seed cases
  uv run python -m eval.run_case_reasoning_seed --id c01   # just one
"""

import argparse
import json
from pathlib import Path

SEED_CASES_PATH = Path(__file__).parent / "case_reasoning_seed_cases.json"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", default=None, help="run only the seed case with this id")
    args = parser.parse_args()

    from policy_advisor.generation.case_reasoning import CaseReasoningChain

    cases = json.loads(SEED_CASES_PATH.read_text(encoding="utf-8"))
    if args.id:
        cases = [c for c in cases if c["id"] == args.id]

    chain = CaseReasoningChain()
    for case in cases:
        print(f"\n{'=' * 80}\nCASE {case['id']}\n{'=' * 80}")
        print(f"Facts: {case['case_facts']}\n")

        result = chain.analyze(case["case_facts"], matter_id=case["matter_id"], jurisdiction=case["jurisdiction"])

        for issue in result.issues:
            print(f"\n--- Issue: {issue.issue} ---")
            print(f"Confidence: {issue.confidence} (unverified={issue.unverified})")
            for argument in issue.arguments:
                cites = ", ".join(a.locator for a in argument.supporting_authorities) or "none"
                print(f"  [{argument.side}] {argument.summary}")
                print(f"    cites: {cites}")
            print(f"Assessment: {issue.assessment}")

        print(f"\n--- Overall position ---\n{result.overall_position}")
        print(f"\n--- Disclaimer ---\n{result.disclaimer}")
        print(f"\nReviewer notes for this seed case: {case.get('notes', '')}")


if __name__ == "__main__":
    main()
