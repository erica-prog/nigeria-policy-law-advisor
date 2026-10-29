"""The drafting rubric as a scored eval (docs/38; docs/30 AC-9).

Two modes. `--fixtures` scores the artefacts recorded under
eval/drafting/fixtures/ (outline plus manifest, as the system stores them)
without a model: the pull-request gate, which fails below --fail-below.
Without `--fixtures`, it drafts each instruction in
eval/drafting/instructions.yaml with the configured models against the
shared libraries, scores the stored artefact, prints the table and writes
eval/drafting/last_run.json: the nightly run, advisory until a threshold
is set.

Usage:
  uv run python -m eval.run_drafting_eval --fixtures --fail-below 0.9
  uv run python -m eval.run_drafting_eval            # live, needs a key
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

from eval.drafting.rubric import CRITERIA, score_artefact

FIXTURES = Path(__file__).parent / "drafting" / "fixtures"
INSTRUCTIONS = Path(__file__).parent / "drafting" / "instructions.yaml"
LAST_RUN = Path(__file__).parent / "drafting" / "last_run.json"
EVAL_MATTER = "eval-drafting"


def score_fixtures() -> list[dict[str, Any]]:
    rows = []
    for path in sorted(FIXTURES.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        scored = score_artefact(
            data["outline"],
            data["manifest"],
            tuple(data.get("expected_sections") or ())
            or None
            or ("Question", "Short answer", "Authority", "Analysis", "Next steps"),
        )
        rows.append(
            {
                "id": path.stem,
                "expected_min": data.get("expected_min"),
                "expected_max": data.get("expected_max"),
                **scored.as_dict(),
            }
        )
    return rows


def score_live() -> list[dict[str, Any]]:
    from policy_advisor.api.services import AdvisorServices
    from policy_advisor.artefacts.draft import DRAFT_PLAYBOOK, DraftingAgent
    from policy_advisor.config import get_settings
    from policy_advisor.ingestion import matter_store
    from policy_advisor.tenancy import DEFAULT_TENANT, tenant_context

    settings = get_settings()
    api_key = settings.anthropic_api_key.get_secret_value()
    services = AdvisorServices()
    rows = []
    with tenant_context(DEFAULT_TENANT):
        matter_store.set_matter_owner(EVAL_MATTER, "eval")
        agent = DraftingAgent(
            services.research_agent_for(api_key), blob_store=services.blob_store, api_key=api_key
        )
        for case in yaml.safe_load(INSTRUCTIONS.read_text(encoding="utf-8"))["cases"]:
            try:
                result = agent.draft(
                    case["instruction"],
                    matter_id=EVAL_MATTER,
                    username="eval",
                    kind=case.get("kind", "memo"),
                    jurisdiction=case.get("jurisdiction"),
                )
                scored = score_artefact(
                    result.outline.model_dump(),
                    result.manifest.as_dict(),
                    DRAFT_PLAYBOOK.output_sections,
                )
                rows.append(
                    {
                        "id": case["id"],
                        "artefact_id": result.artefact.artefact_id,
                        "run_id": result.artefact.run_id,
                        "headings": [section.heading for section in result.outline.sections],
                        **scored.as_dict(),
                    }
                )
            except Exception as exc:  # one failed draft must not hide the others
                rows.append(
                    {
                        "id": case["id"],
                        "error": str(exc)[:300],
                        "score": 0.0,
                        "criteria": {},
                        "notes": [],
                    }
                )
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--fixtures", action="store_true", help="score the recorded fixtures; no model"
    )
    parser.add_argument("--fail-below", type=float, default=None)
    args = parser.parse_args(argv)
    rows = score_fixtures() if args.fixtures else score_live()

    print(f"{'case':<34}{'score':>7}  " + "  ".join(f"{c[:10]:>10}" for c in CRITERIA))
    for row in rows:
        criteria = row.get("criteria", {})
        print(
            f"{row['id']:<34}{row['score']:>7.3f}  "
            + "  ".join(f"{criteria.get(c, 0.0):>10.2f}" for c in CRITERIA)
        )
        for note in row.get("notes", [])[:4]:
            print(f"    - {note}")
        if row.get("error"):
            print(f"    ! {row['error']}")
    mean = sum(r["score"] for r in rows) / len(rows) if rows else 0.0
    print(f"\nmean rubric score: {mean:.3f} over {len(rows)} draft(s)")
    LAST_RUN.write_text(
        json.dumps({"mean": round(mean, 4), "rows": rows, "fixtures": args.fixtures}, indent=1)
        + "\n",
        encoding="utf-8",
    )

    failures = []
    if args.fixtures:
        # Fixtures carry what they should score, so a scorer that stops
        # noticing a defect fails here, not only in a dashboard.
        for row in rows:
            lo, hi = row.get("expected_min"), row.get("expected_max")
            if lo is not None and row["score"] < lo:
                failures.append(
                    f"{row['id']} scored {row['score']:.3f}, below its expected minimum {lo}"
                )
            if hi is not None and row["score"] > hi:
                failures.append(
                    f"{row['id']} scored {row['score']:.3f}, above its expected maximum {hi}: the scorer missed a defect"
                )
    if args.fail_below is not None and not args.fixtures and mean < args.fail_below:
        failures.append(f"mean {mean:.3f} is below {args.fail_below}")
    for failure in failures:
        print(failure)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
