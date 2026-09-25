"""HuggingFace embeddings wrapper. Model name is config-driven (`EMBEDDING_MODEL`)
so swapping/evaluating candidates doesn't require a code change (docs/03)."""

from langchain_huggingface import HuggingFaceEmbeddings

from policy_advisor.config import get_settings


def get_embedding_function() -> HuggingFaceEmbeddings:
    settings = get_settings()
    # BGE models are trained for cosine similarity and their model card
    # instructs normalizing embeddings before comparing them. Without this the
    # vectors are unnormalized, so Chroma's distances have no bounded or
    # comparable scale - which the relevance floor in hybrid_retriever.py
    # relies on to tell "no relevant match" from "the least bad match".
    return HuggingFaceEmbeddings(
        model_name=settings.embedding_model,
        encode_kwargs={"normalize_embeddings": True},
    )
