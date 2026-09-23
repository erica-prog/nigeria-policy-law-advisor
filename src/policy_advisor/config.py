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
    auth_cookie_key: SecretStr = SecretStr("dev-only-insecure-key-set-AUTH_COOKIE_KEY-in-.env")


@lru_cache
def get_settings() -> Settings:
    return Settings()


def mask_secret(value: str, keep: int = 4) -> str:
    """Show only the last `keep` characters, for safe inclusion in logs/startup banners."""
    if len(value) <= keep:
        return "*" * len(value)
    return "*" * (len(value) - keep) + value[-keep:]
