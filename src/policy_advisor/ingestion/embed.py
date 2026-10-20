"""HuggingFace embeddings wrapper. Model name is config-driven (`EMBEDDING_MODEL`)
so swapping/evaluating candidates doesn't require a code change (docs/03)."""

from functools import lru_cache

import numpy as np
from langchain_huggingface import HuggingFaceEmbeddings

from policy_advisor.config import get_settings


@lru_cache
def get_embedding_function() -> HuggingFaceEmbeddings:
    """Loaded once per process: the model takes seconds to load and 1.5-2 GB of
    memory, and every retriever, ingest and chain shares the same one."""
    settings = get_settings()
    # BGE models are trained for cosine similarity and their model card
    # instructs normalizing embeddings before comparing them. On unit vectors a
    # dot product *is* the cosine similarity, which is what lets vector search
    # compute distance as 1 - dot, and what gives the relevance gate in
    # hybrid_retriever.py a bounded, comparable scale to test.
    return HuggingFaceEmbeddings(
        model_name=settings.embedding_model,
        encode_kwargs={"normalize_embeddings": True},
    )


def embed_texts(texts: list[str]) -> np.ndarray:
    """Rows of float32 unit vectors, one per text, in input order."""
    if not texts:
        return np.zeros((0, 0), dtype=np.float32)
    return np.asarray(get_embedding_function().embed_documents(texts), dtype=np.float32)


def embed_query(text: str) -> np.ndarray:
    return np.asarray(get_embedding_function().embed_query(text), dtype=np.float32)
