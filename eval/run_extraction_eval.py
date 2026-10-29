"""Extraction F1 on the labelled synthetic contract set (docs/34; docs/30 AC-3).

Loads eval/extraction/contracts.json into an evaluation matter, runs the
bulk extractor over it (the real model by default, or the labels themselves
with --oracle to prove the scorer and the pipeline), scores every record
against its label on eight fields, prints the table, writes
eval/extraction/last_run.json (or --out), and exits non-zero below --fail-below.

--no-escalation sets the escalation model to the triage model, so no record is
read twice: the triage-only measurement docs/39 D11 compares with the
escalated run.

Usage:
  uv run python -m eval.run_extraction_eval --oracle --fail-below 0.99   # no model: the pipeline and scorer
  uv run python -m eval.run_extraction_eval --limit 12                   # the configured models, 12 documents
  uv run python -m eval.run_extraction_eval --limit 12 --no-escalation --out eval/extraction/last_run_triage.json
  uv run python -m eval.run_extraction_eval --fail-below 0.8             # the gate once a threshold is set
"""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

from eval.extraction.score import FIELDS, Scores, score_document, summarise_kinds
from policy_advisor.bulk.extract import Extractor, ModelResult, langchain_caller
from policy_advisor.bulk.jobs import BulkJobTable, list_extractions
from policy_advisor.bulk.schemas import ContractExtraction, get_schema
from policy_advisor.config import get_settings
from policy_advisor.ingestion import matter_store
from policy_advisor.tenancy import DEFAULT_TENANT, tenant_context

SET_PATH = Path(__file__).parent / "extraction" / "contracts.json"
LAST_RUN = Path(__file__).parent / "extraction" / "last_run.json"
EVAL_MATTER = "eval-extraction"
DIM = 384


def load_set(path: Path = SET_PATH) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))["documents"]


def seed_matter(documents: list[dict[str, Any]], matter_id: str = EVAL_MATTER) -> None:
    """The set as chunks, one per clause, with `Clause N` locators. Embeddings
    are constant: extraction reads whole documents, never searches them."""
    matter_store.set_matter_owner(matter_id, "eval")
    for document in documents:
        records = [
            {
                "chunk_id": f"{document['name']}::{clause['locator'].replace(' ', '')}",
                "text": clause["text"],
                "source_document": document["name"],
                "locator": clause["locator"],
                "doc_type": "generic",
                "matter_id": matter_id,
                "jurisdiction": None,
                "page": 1,
            }
            for clause in document["clauses"]
        ]
        matter_store.replace_document_chunks(
            matter_id,
            document["name"],
            records,
            np.ones((len(records), DIM), dtype=np.float32) / np.sqrt(DIM),
        )


class OracleCaller:
    """Returns the label as the model's answer: the ceiling of the scorer."""

    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self._by_first_clause = {d["clauses"][0]["text"]: d["labels"] for d in documents}

    def __call__(
        self, *, model: str, schema: Any, document_text: str, api_key: str | None
    ) -> ModelResult:
        if schema.schema_id != "contract":
            from policy_advisor.bulk.checks import ObligationConflicts

            return ModelResult(record=ObligationConflicts(), input_tokens=1, output_tokens=1)
        first = document_text.split("\n", 2)[1]
        record = ContractExtraction.model_validate(self._by_first_clause[first])
        return ModelResult(record=record, input_tokens=100, output_tokens=50)


def run(
    documents: list[dict[str, Any]],
    *,
    oracle: bool,
    api_key: str | None = None,
    escalate: bool = True,
) -> tuple[Scores, dict[str, Any]]:
    settings = get_settings()
    escalation = (
        settings.bulk_escalation_model or settings.anthropic_model
        if escalate
        else settings.bulk_triage_model
    )
    with tenant_context(DEFAULT_TENANT):
        seed_matter(documents)
        table = BulkJobTable()
        job = table.create(
            EVAL_MATTER,
            schema_id="contract",
            schema_version=get_schema("contract").version,
            documents=[d["name"] for d in documents],
            triage_model=settings.bulk_triage_model,
            escalation_model=escalation,
            ceiling_tokens=max(settings.bulk_default_ceiling_tokens, 50_000 * len(documents)),
            backend="sync",
            submitted_by="eval",
        )
        caller = OracleCaller(documents) if oracle else langchain_caller(settings)
        started = time.perf_counter()
        final = Extractor(api_key, settings=settings, caller=caller, jobs=table).run(job)
        elapsed = time.perf_counter() - started
        # This job's rows only: a document this run failed must count as
        # missing, not be scored on an earlier run's record.
        records = {
            r["source_document"]: r
            for r in list_extractions(EVAL_MATTER, schema_id="contract")
            if r["job_id"] == job.job_id
        }
    scores = Scores()
    labels = {d["name"]: d["labels"] for d in documents}
    missing = []
    for name, label in labels.items():
        row = records.get(name)
        if row is None:
            missing.append(name)
            score_document(scores, {}, label)
        else:
            score_document(scores, row["record"], label)
    report = {
        "job_status": final.status,
        "error": final.error,
        "spent_tokens": final.spent_tokens,
        "elapsed_seconds": round(elapsed, 1),
        "missing_records": missing,
        "mean_confidence": round(
            sum(float(r["confidence"]) for r in records.values()) / len(records), 3
        )
        if records
        else None,
        "kinds": summarise_kinds(documents),
        "triage_model": job.triage_model,
        "escalation_model": job.escalation_model,
        "escalated": sum(1 for row in records.values() if row["model"] != job.triage_model),
        "oracle": oracle,
        # What the model returned, so a question about the scorer or the labels
        # can be settled by rescoring instead of paying for another run. The
        # set is synthetic (AGENTS.md rule 7), so the records are safe to keep.
        "records": {
            name: {
                "model": row["model"],
                "confidence": float(row["confidence"]),
                "flagged": row["flagged"],
                "record": row["record"],
            }
            for name, row in records.items()
        },
    }
    return scores, report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--oracle", action="store_true", help="use the labels as the model's answers (no key)"
    )
    parser.add_argument("--limit", type=int, default=None, help="only the first N documents")
    parser.add_argument(
        "--fail-below", type=float, default=None, help="exit 1 when mean F1 is below this"
    )
    parser.add_argument("--set", type=Path, default=SET_PATH)
    parser.add_argument(
        "--no-escalation",
        action="store_true",
        help="escalation model = triage model: no record is read twice (docs/39 D11)",
    )
    parser.add_argument("--out", type=Path, default=LAST_RUN, help="where to write the report")
    args = parser.parse_args(argv)

    documents = load_set(args.set)
    if args.limit:
        documents = documents[: args.limit]
    scores, report = run(documents, oracle=args.oracle, escalate=not args.no_escalation)

    print(f"{'field':<22}{'precision':>10}{'recall':>10}{'f1':>8}{'tp':>6}{'fp':>6}{'fn':>6}")
    for name in FIELDS:
        s = scores.fields[name]
        print(
            f"{name:<22}{s.precision:>10.3f}{s.recall:>10.3f}{s.f1:>8.3f}{s.tp:>6}{s.fp:>6}{s.fn:>6}"
        )
    print(
        f"\ndocuments: {scores.documents}  mean F1: {scores.mean_f1:.3f}  job: {report['job_status']}  spent_tokens: {report['spent_tokens']}  escalated: {report['escalated']}  mean confidence: {report['mean_confidence']}"
    )
    if report["missing_records"]:
        print(f"missing records: {len(report['missing_records'])}")
    if report["job_status"] == "done" and report["error"]:
        print(f"documents skipped: {report['error']}")
    args.out.write_text(
        json.dumps({"scores": scores.as_dict(), **report, "limit": args.limit}, indent=1) + "\n",
        encoding="utf-8",
    )
    if report["job_status"] != "done":
        print(f"job did not finish: {report['error']}")
        return 1
    if args.fail_below is not None and scores.mean_f1 < args.fail_below:
        print(f"mean F1 {scores.mean_f1:.3f} is below the threshold {args.fail_below}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
