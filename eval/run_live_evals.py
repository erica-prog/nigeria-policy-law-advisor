"""The live evaluation harness (docs/40, docs/39 AC-2): graded cases against
the real model through the web API.

Every case is one chat on a matter built from its file's synthetic
documents. The deterministic checks run first (mode, phrases, citations,
faithfulness, whether the planted connector was reached); a case that lists
`judge` then goes to Claude-as-judge on a distinct model (docs/39 D2). The
report, `eval/live/last_run.json`, holds one row per case with every check,
and on the run: the model under test, the judge model and prompt version,
the version of the case files, and the run's cost from the ledger.

Usage:
  uv run python -m eval.run_live_evals                   # every case, nightly
  uv run python -m eval.run_live_evals --limit 10         # round-robin over kinds
  uv run python -m eval.run_live_evals --kind injection --no-judge
  uv run python -m eval.run_live_evals --fail-below 0.8   # the gate once D3 sets it

A run spends the server key; there is no fixture mode here because the
question is how the real model behaves. The tests drive the same runner with
scripted model loops and a fake judge.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from eval.live import CASES_DIR, LoadedCase, cases_version, load_cases, sample
from eval.live.grade import Check, deterministic_checks, reply_text, verdict
from eval.live.harness import HarnessError, LiveHarness, build_harness

LAST_RUN = Path(__file__).parent / "live" / "last_run.json"

JudgeFn = Callable[[LoadedCase, str, dict[str, Any]], tuple[str, str]]
"""(case, reply text, reply) -> (verdict PASS|FAIL, notes)."""


def _tool_calls(reply: dict[str, Any]) -> list[Any]:
    run_id = (reply.get("answer") or {}).get("run_id")
    if not run_id:
        return []
    from policy_advisor.agents import audit

    try:
        return audit.list_calls(run_id)
    except Exception:  # the audit table is evidence, not a dependency of the grade
        return []


def run_case(harness: LiveHarness, loaded: LoadedCase, judge: JudgeFn | None) -> dict[str, Any]:
    case = loaded.case
    row: dict[str, Any] = {"id": case.id, "kind": loaded.kind, "file": loaded.file}
    started = time.perf_counter()
    try:
        matter_id = harness.ensure_matter(loaded.matter)
        harness.use_fake_connector(case.fake_connector)
        reply = harness.chat(matter_id, case)
    except HarnessError as exc:
        row.update(verdict="error", error=str(exc)[:500], checks=[])
        return row
    calls = _tool_calls(reply) if case.fake_connector else []
    checks: list[Check] = deterministic_checks(case, reply, calls)
    outcome = verdict(checks, case)
    row.update(
        mode=reply.get("mode"),
        playbook=reply.get("playbook"),
        citations=[
            {"document": c.get("document"), "locator": c.get("locator"), "kind": c.get("kind")}
            for c in (reply.get("citations") or [])
        ],
        tool_calls=[
            {
                "connector": getattr(c, "connector", None),
                "tool": getattr(c, "tool", None),
                "status": getattr(c, "status", None),
            }
            for c in calls
        ],
        deterministic=outcome,
        text=reply_text(reply)[:4000],
    )
    if "judge" in case.graders and outcome != "not_exercised":
        if judge is None:
            row["judge"] = {"verdict": "skipped", "notes": "judge disabled for this run"}
        else:
            try:
                judged, notes = judge(loaded, reply_text(reply), reply)
            except Exception as exc:  # a judge failure is recorded, never a pass
                judged, notes = "ERROR", str(exc)[:300]
            row["judge"] = {"verdict": judged, "notes": notes}
            if judged != "PASS":
                outcome = "fail"
                checks.append(Check("judge", False, notes[:300]))
    row["checks"] = [c.as_dict() for c in checks]
    row["verdict"] = outcome
    row["duration_ms"] = int((time.perf_counter() - started) * 1000)
    return row


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counted = [r for r in rows if r["verdict"] in ("pass", "fail", "error")]
    passed = sum(1 for r in counted if r["verdict"] == "pass")
    by_kind: dict[str, dict[str, int]] = {}
    for row in rows:
        bucket = by_kind.setdefault(
            row["kind"], {"pass": 0, "fail": 0, "error": 0, "not_exercised": 0}
        )
        bucket[row["verdict"]] += 1
    return {
        "cases": len(rows),
        "passed": passed,
        "failed": sum(1 for r in counted if r["verdict"] == "fail"),
        "errors": sum(1 for r in counted if r["verdict"] == "error"),
        "not_exercised": sum(1 for r in rows if r["verdict"] == "not_exercised"),
        "pass_rate": round(passed / len(counted), 4) if counted else 0.0,
        "by_kind": by_kind,
    }


def ledger_for(username: str) -> dict[str, Any]:
    from policy_advisor.telemetry import usage_totals

    try:
        totals = usage_totals(username=username, days=1)
    except Exception as exc:
        return {"error": str(exc)[:200]}
    rows = totals.get("by_kind", [])
    return {
        "calls": sum(int(r.get("calls") or 0) for r in rows),
        "input_tokens": sum(int(r.get("input_tokens") or 0) for r in rows),
        "output_tokens": sum(int(r.get("output_tokens") or 0) for r in rows),
        "estimated_usd": (
            round(
                sum(float(r["estimated_usd"]) for r in rows if r.get("estimated_usd") is not None),
                4,
            )
            if any(r.get("estimated_usd") is not None for r in rows)
            else None
        ),
        "by_kind": [dict(r) for r in rows],
    }


def run(
    cases: list[LoadedCase],
    harness: LiveHarness,
    *,
    judge: JudgeFn | None,
    model: str,
    judge_model: str | None,
    judge_prompt_version: str | None,
    cases_dir: Path = CASES_DIR,
) -> dict[str, Any]:
    rows = [run_case(harness, loaded, judge) for loaded in cases]
    return {
        "version": 1,
        "model": model,
        "judge_model": judge_model if judge is not None else None,
        "judge_prompt_version": judge_prompt_version if judge is not None else None,
        "cases_version": cases_version(cases_dir),
        "username": harness.username,
        "summary": summarise(rows),
        "ledger": ledger_for(harness.username),
        "rows": rows,
    }


def print_report(report: dict[str, Any]) -> None:
    print(f"{'case':<64} {'kind':<22} {'verdict':<14} failed checks")
    for row in report["rows"]:
        failed = [c["name"] for c in row.get("checks", []) if not c["passed"]]
        print(f"{row['id']:<64} {row['kind']:<22} {row['verdict']:<14} {', '.join(failed)}")
        if row.get("error"):
            print(f"    ! {row['error']}")
        judged = row.get("judge")
        if judged and judged.get("verdict") not in ("PASS", "skipped"):
            print(f"    judge: {judged['notes'][:200]}")
    summary = report["summary"]
    print()
    for kind, bucket in sorted(summary["by_kind"].items()):
        print(f"{kind:<22} " + " ".join(f"{k}: {v}" for k, v in bucket.items()))
    ledger = report["ledger"]
    print(
        f"\ncases: {summary['cases']}  pass rate: {summary['pass_rate']:.3f} "
        f"(passed {summary['passed']}, failed {summary['failed']}, errors {summary['errors']}, "
        f"not exercised {summary['not_exercised']})  model: {report['model']}  "
        f"judge: {report['judge_model']} v{report['judge_prompt_version']}  "
        f"ledger: {ledger.get('calls')} calls, {ledger.get('input_tokens')} in, "
        f"{ledger.get('output_tokens')} out"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="at most this many cases, round-robin over kinds"
    )
    parser.add_argument("--kind", default=None, help="only this kind of case")
    parser.add_argument("--no-judge", action="store_true", help="deterministic checks only")
    parser.add_argument(
        "--fail-below", type=float, default=None, help="exit 1 when the pass rate is below this"
    )
    parser.add_argument("--out", type=Path, default=LAST_RUN)
    parser.add_argument("--cases-dir", type=Path, default=CASES_DIR)
    args = parser.parse_args(argv)

    from policy_advisor.config import get_settings
    from policy_advisor.tenancy import DEFAULT_TENANT, tenant_context

    settings = get_settings()
    if not settings.anthropic_api_key.get_secret_value():
        print("ANTHROPIC_API_KEY is not set; the live harness needs the server key.")
        return 2
    cases = sample(load_cases(args.cases_dir, kind=args.kind), args.limit)
    if not cases:
        print("no cases matched")
        return 2
    judge: JudgeFn | None = None
    judge_model: str | None = None
    judge_version: str | None = None
    if not args.no_judge:
        from eval.live.judge import JUDGE_PROMPT_VERSION, make_judge

        judge = make_judge(settings)
        judge_model, judge_version = settings.judge_model, JUDGE_PROMPT_VERSION
    with tenant_context(DEFAULT_TENANT):
        harness = build_harness()
        report = run(
            cases,
            harness,
            judge=judge,
            model=settings.anthropic_model,
            judge_model=judge_model,
            judge_prompt_version=judge_version,
            cases_dir=args.cases_dir,
        )
    args.out.write_text(json.dumps(report, indent=1, default=str) + "\n", encoding="utf-8")
    print_report(report)
    rate = report["summary"]["pass_rate"]
    if args.fail_below is not None and rate < args.fail_below:
        print(f"pass rate {rate:.3f} is below the threshold {args.fail_below}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
