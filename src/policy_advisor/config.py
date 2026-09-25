"""Centralized settings, loaded once from .env. Never log secret values directly."""

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
INDEX_DIR = DATA_DIR / "index"
MATTERS_DIR = INDEX_DIR / "matters"
AUTH_DIR = DATA_DIR / "auth"
PHASE1_DEMO_MATTER_ID = "phase1-demo"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    anthropic_api_key: SecretStr
    anthropic_model: str = "claude-sonnet-4-6"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    policy_agent_port: int = 8501
    log_level: str = "info"
    retrieval_top_k: int = 8
    # Two-band relevance gate, calibrated against eval/golden_set.json by
    # eval/calibrate_relevance_floor.py. Re-run it after changing
    # EMBEDDING_MODEL, the distance space, or chunking - all three move the
    # distributions these are drawn from.
    #
    # At or below CERTAIN: relevant, answer from the corpus.
    # Above MAX: irrelevant, retrieval returns nothing (which is what lets the
    #   official-sources fallback fire).
    # In between: too close to call on distance, so ask Claude. The measured
    #   margin between the hardest answerable question and the easiest
    #   unanswerable one is 0.0007, far too narrow for a single threshold to
    #   be anything but overfitting, which is why the middle band exists.
    #
    # Set CERTAIN >= MAX to collapse to a plain threshold with no LLM call, and
    # MAX to 2.0 (the largest possible cosine distance) to disable gating.
    retrieval_certain_distance: float = 0.21
    retrieval_max_distance: float = 0.32
    auth_cookie_key: SecretStr = SecretStr("dev-only-insecure-key-set-AUTH_COOKIE_KEY-in-.env")


@lru_cache
def get_settings() -> Settings:
    return Settings()


def mask_secret(value: str, keep: int = 4) -> str:
    """Show only the last `keep` characters, for safe inclusion in logs/startup banners."""
    if len(value) <= keep:
        return "*" * len(value)
    return "*" * (len(value) - keep) + value[-keep:]
