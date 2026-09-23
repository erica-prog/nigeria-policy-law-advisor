"""Regression eval against eval/golden_set.json (docs/03, docs/06): retrieval
quality (recall@k, precision@k, MRR) and answer faithfulness, re-run whenever
chunking, embeddings, retrieval logic, or prompts change.

Usage:
  uv run python -m eval.run_golden_set                  # retrieval + faithfulness
  uv run python -m eval.run_golden_set --skip-generation # retrieval only, no Claude calls
  uv run python -m eval.run_golden_set --fail-below 0.85 # exit nonzero if recall@k drops below this (for a CI gate later)
"""

import argparse
import json
from pathlib import Path

GOLDEN_SET_PATH = Path(__file__).parent / "golden_set.json"

REFUSAL_PHRASES = ("couldn't find", "cannot answer", "can't answer", "no information", "not contain")


def _chunk_key(item: dict) -> str:
    return f"{item['source_document']}::{item['locator']}"


def _evaluate_retrieval(question: dict, retrieved_metadatas: list[dict]) -> dict:
    expected_keys = {_chunk_key(e) for e in question["expected"]}
    retrieved_keys = [f"{m['source_document']}::{m['locator']}" for m in retrieved_metadatas]

    if not expected_keys:
        return {"recall": None, "precision": None, "mrr": None}

    hits = [key for key in retrieved_keys if key in expected_keys]
    recall = len(set(hits)) / len(expected_keys)
    precision = len(hits) / len(retrieved_keys) if retrieved_keys else 0.0

    mrr = 0.0
    for rank, key in enumerate(retrieved_keys, start=1):
        if key in expected_keys:
            mrr = 1.0 / rank
            break

    return {"recall": recall, "precision": precision, "mrr": mrr}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--fail-below", type=float, default=None, help="exit nonzero if mean recall@k drops below this")
    parser.add_argument("--skip-generation", action="store_true", help="retrieval-only, skips Claude calls")
    args = parser.parse_args()

    from policy_advisor.retrieval.hybrid_retriever import HybridRetriever

    retriever = HybridRetriever()
    chain = None
    if not args.skip_generation:
        from policy_advisor.generation.chain import RAGChain

        chain = RAGChain()

    questions = json.loads(GOLDEN_SET_PATH.read_text(encoding="utf-8"))

    retrieval_metrics = []
    faithfulness_results = []
    refusal_results = []

    for question in questions:
        retrieved = retriever.retrieve(
            question["question"],
            top_k=args.top_k,
            matter_id=question["matter_id"],
            jurisdiction=question["jurisdiction"],
        )
        retrieved_metadatas = [c.metadata for c in retrieved]
        metrics = _evaluate_retrieval(question, retrieved_metadatas)

        outcome = {"id": question["id"], "question": question["question"], **metrics}

        if chain is not None:
            result = chain.answer(question["question"], matter_id=question["matter_id"], jurisdiction=question["jurisdiction"])
            outcome["faithful"] = result.faithful
            if not question["expect_refusal"]:
                faithfulness_results.append(result.faithful)
            if question["expect_refusal"]:
                refused = any(phrase in result.answer.lower() for phrase in REFUSAL_PHRASES)
                refusal_results.append(refused)
                outcome["refused_as_expected"] = refused

        retrieval_metrics.append(outcome)
        print(json.dumps(outcome))

    scored = [m for m in retrieval_metrics if m["recall"] is not None]
    mean_recall = sum(m["recall"] for m in scored) / len(scored) if scored else None
    mean_precision = sum(m["precision"] for m in scored) / len(scored) if scored else None
    mean_mrr = sum(m["mrr"] for m in scored) / len(scored) if scored else None

    print("\n--- summary ---")
    print(f"questions: {len(questions)} (scored: {len(scored)}, refusal-expected: {len(questions) - len(scored)})")
    print(f"recall@{args.top_k}: {mean_recall}")
    print(f"precision@{args.top_k}: {mean_precision}")
    print(f"MRR@{args.top_k}: {mean_mrr}")
    if faithfulness_results:
        print(f"faithfulness pass rate: {sum(faithfulness_results) / len(faithfulness_results)}")
    if refusal_results:
        print(f"correct refusal rate: {sum(refusal_results) / len(refusal_results)}")

    if args.fail_below is not None and mean_recall is not None and mean_recall < args.fail_below:
        raise SystemExit(f"recall@{args.top_k} {mean_recall:.3f} is below threshold {args.fail_below}")


if __name__ == "__main__":
    main()
