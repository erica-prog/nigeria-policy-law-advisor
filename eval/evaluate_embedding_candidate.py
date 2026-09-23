"""Evaluates a candidate embedding model against the golden set's recall@k,
in a throwaway Chroma directory, without touching the production index.
CLAUDE-2.md capability 3 requires this before adopting a multilingual model:
a swap that helps cross-language retrieval but regresses English recall
relative to Phase 1's BAAI/bge-small-en-v1.5 baseline is not a clean win.

Vector-only (BM25 doesn't depend on the embedding model, so it's identical
across candidates and not worth re-testing here).

Usage:
  uv run python -m eval.evaluate_embedding_candidate intfloat/multilingual-e5-small
"""

import argparse
import json
import shutil
import tempfile
from pathlib import Path

GOLDEN_SET_PATH = Path(__file__).parent / "golden_set.json"


def _chunk_key(item: dict) -> str:
    return f"{item['source_document']}::{item['locator']}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("model_name")
    parser.add_argument("--top-k", type=int, default=8)
    args = parser.parse_args()

    from langchain_chroma import Chroma
    from langchain_huggingface import HuggingFaceEmbeddings

    from policy_advisor.config import DATA_DIR
    from policy_advisor.ingestion.chunk import chunk_corpus
    from policy_advisor.retrieval.vector_store import chunk_to_document

    print(f"Loading candidate model: {args.model_name} ...")
    embedding_fn = HuggingFaceEmbeddings(model_name=args.model_name)

    chunks = chunk_corpus(DATA_DIR)
    print(f"Chunked {len(chunks)} chunks from the Phase 1 demo corpus.")

    scratch_dir = Path(tempfile.mkdtemp(prefix="embedding_eval_"))
    try:
        vector_store = Chroma(
            collection_name="candidate_eval",
            embedding_function=embedding_fn,
            persist_directory=str(scratch_dir),
        )
        vector_store.add_documents(
            documents=[chunk_to_document(c) for c in chunks],
            ids=[c.chunk_id for c in chunks],
        )

        questions = json.loads(GOLDEN_SET_PATH.read_text(encoding="utf-8"))
        recalls = []
        for question in questions:
            expected_keys = {_chunk_key(e) for e in question["expected"]}
            if not expected_keys:
                continue
            filter_ = {"matter_id": question["matter_id"]}
            if question.get("jurisdiction"):
                filter_ = {"$and": [filter_, {"jurisdiction": question["jurisdiction"]}]}
            results = vector_store.similarity_search(question["question"], k=args.top_k, filter=filter_)
            retrieved_keys = [f"{d.metadata['source_document']}::{d.metadata['locator']}" for d in results]
            hits = [k for k in retrieved_keys if k in expected_keys]
            recall = len(set(hits)) / len(expected_keys)
            recalls.append(recall)
            print(f"  {question['id']}: recall@{args.top_k} = {recall:.2f}")

        mean_recall = sum(recalls) / len(recalls) if recalls else None
        print(f"\nCandidate: {args.model_name}")
        print(f"Vector-only recall@{args.top_k} on golden set: {mean_recall}")
        print("Compare against Phase 1's English-only baseline (BAAI/bge-small-en-v1.5) before adopting.")
    finally:
        shutil.rmtree(scratch_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
