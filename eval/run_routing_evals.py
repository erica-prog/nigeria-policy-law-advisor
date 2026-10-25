"""Behavioural routing evals for the playbook selector (docs/29; the
legal-ai-skills plugin's eval shape). No model: the selector is deterministic,
so every case runs in milliseconds and in CI without a key.

Usage:
  uv run python -m eval.run_routing_evals            # every file under eval/routing/
  uv run python -m eval.run_routing_evals eval/routing/immigration.yaml
"""

import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

from policy_advisor.playbooks.selector import select_route

ROUTING_DIR = Path(__file__).parent / "routing"


@dataclass
class CaseOutcome:
    file: str
    id: str
    passed: bool
    mode: str
    playbook: str
    failures: list[str]


def run_case(case: dict, file: str) -> CaseOutcome:
    selection = select_route(
        case["prompt"],
        intent=case.get("intent", "auto"),
        has_documents=bool(case.get("has_documents", True)),
        read_only=bool(case.get("read_only", False)),
        conversation_started=bool(case.get("conversation_started", False)),
        analysed=bool(case.get("analysed", False)),
        jurisdiction=case.get("jurisdiction"),
    )
    failures: list[str] = []
    expected_skill = case.get("expected_skill", "none")
    got_skill = selection.playbook_id or "none"
    if got_skill != expected_skill:
        failures.append(f"expected playbook {expected_skill}, got {got_skill} ({selection.reason})")
    expected_mode = case.get("expected_behavior")
    if expected_mode and selection.mode != expected_mode:
        failures.append(f"expected mode {expected_mode}, got {selection.mode} ({selection.reason})")
    text = (selection.playbook.route_text() if selection.playbook else "").lower()
    for needle in case.get("must_include") or []:
        if needle.lower() not in text:
            failures.append(f"route text lacks {needle!r}")
    for needle in case.get("must_not_include") or []:
        if needle.lower() in text:
            failures.append(f"route text contains forbidden {needle!r}")
    return CaseOutcome(file, case["id"], not failures, selection.mode, got_skill, failures)


def run_file(path: Path) -> list[CaseOutcome]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [run_case(case, path.name) for case in data.get("cases", [])]


def run_all(paths: list[Path] | None = None) -> list[CaseOutcome]:
    files = paths or sorted(ROUTING_DIR.glob("*.yaml"))
    outcomes: list[CaseOutcome] = []
    for path in files:
        outcomes.extend(run_file(path))
    return outcomes


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    outcomes = run_all([Path(a) for a in args] or None)
    for outcome in outcomes:
        mark = "PASS" if outcome.passed else "FAIL"
        print(f"{mark} {outcome.file}:{outcome.id} -> {outcome.mode} / {outcome.playbook}")
        for failure in outcome.failures:
            print(f"      {failure}")
    failed = [o for o in outcomes if not o.passed]
    print(f"\n{len(outcomes) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
