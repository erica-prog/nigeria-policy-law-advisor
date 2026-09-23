"""HuggingFace embeddings wrapper. Model name is config-driven (`EMBEDDING_MODEL`)
so swapping/evaluating candidates doesn't require a code change (docs/03)."""

from langchain_huggingface import HuggingFaceEmbeddings

from policy_advisor.config import get_settings


def get_embedding_function() -> HuggingFaceEmbeddings:
    settings = get_settings()
    return HuggingFaceEmbeddings(model_name=settings.embedding_model)
