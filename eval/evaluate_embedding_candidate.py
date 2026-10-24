"""Evaluates a candidate embedding model against the golden set's recall@k,
entirely in memory, without touching the production database.
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
from pathlib import Path

import numpy as np

GOLDEN_SET_PATH = Path(__file__).parent / "golden_set.json"


def _chunk_key(item: dict) -> str:
    return f"{item['source_document']}::{item['locator']}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("model_name")
    parser.add_argument("--top-k", type=int, default=8)
    args = parser.parse_args()

    from langchain_huggingface import HuggingFaceEmbeddings

    from policy_advisor.config import DATA_DIR
    from policy_advisor.ingestion.chunk import chunk_corpus
    from policy_advisor.retrieval.vector_store import VectorIndex, chunk_to_record

    print(f"Loading candidate model: {args.model_name} ...")
    # Normalized for the same reason as production (ingestion/embed.py): the
    # search computes cosine distance as 1 - dot, which only holds on unit
    # vectors.
    embedding_fn = HuggingFaceEmbeddings(
        model_name=args.model_name, encode_kwargs={"normalize_embeddings": True}
    )

    chunks = chunk_corpus(DATA_DIR)
    print(f"Chunked {len(chunks)} chunks from the Phase 1 demo corpus.")

    index = VectorIndex.from_records(
        [chunk_to_record(c) for c in chunks],
        np.asarray(embedding_fn.embed_documents([c.text for c in chunks]), dtype=np.float32),
    )

    questions = json.loads(GOLDEN_SET_PATH.read_text(encoding="utf-8"))
    recalls = []
    for question in questions:
        expected_keys = {_chunk_key(e) for e in question["expected"]}
        if not expected_keys:
            continue
        query_vector = np.asarray(embedding_fn.embed_query(question["question"]), dtype=np.float32)
        results = index.search(query_vector, k=args.top_k, jurisdiction=question.get("jurisdiction"))
        retrieved_keys = [f"{meta['source_document']}::{meta['locator']}" for meta, _, _ in results]
        hits = [k for k in retrieved_keys if k in expected_keys]
        recall = len(set(hits)) / len(expected_keys)
        recalls.append(recall)
        print(f"  {question['id']}: recall@{args.top_k} = {recall:.2f}")

    mean_recall = sum(recalls) / len(recalls) if recalls else None
    print(f"\nCandidate: {args.model_name}")
    print(f"Vector-only recall@{args.top_k} on golden set: {mean_recall}")
    print("Compare against Phase 1's English-only baseline (BAAI/bge-small-en-v1.5) before adopting.")


if __name__ == "__main__":
    main()
