"""Run the retention job (docs/37). Dry run by default: it records what it
would delete and deletes nothing. `--apply` deletes expired matters that
are not on hold, their originals and artefacts, and records each deletion.

Usage:
  uv run python -m scripts.run_retention                 # dry run, every firm
  uv run python -m scripts.run_retention --tenant acme   # one firm
  uv run python -m scripts.run_retention --apply         # delete
"""

import argparse
import json
import sys

from policy_advisor.api.services import AdvisorServices
from policy_advisor.retention import run_retention


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--apply", action="store_true", help="delete instead of reporting")
    parser.add_argument("--tenant", default=None)
    args = parser.parse_args(argv)
    report = run_retention(
        AdvisorServices().blob_store,
        apply=args.apply,
        tenant_id=args.tenant,
        actor="scripts.run_retention",
    )
    print(json.dumps(report.as_dict(), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
