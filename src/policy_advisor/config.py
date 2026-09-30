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
        # An exported but empty variable (`export ANTHROPIC_API_KEY=`) must not
        # shadow the value in .env; otherwise /api/health reports the model as
        # unconfigured even though the file is correct.
        env_ignore_empty=True,
        extra="ignore",
    )

    # Empty means "not configured": the web API starts, reports it on /api/health
    # and refuses ask/analyze with a clear error instead of crashing at import.
    anthropic_api_key: SecretStr = SecretStr("")
    anthropic_model: str = "claude-sonnet-4-6"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    policy_agent_port: int = 8501
    log_level: str = "info"
    retrieval_top_k: int = 8
    auth_cookie_key: SecretStr = SecretStr("dev-only-insecure-key-set-AUTH_COOKIE_KEY-in-.env")
    # Web API (docs/architecture/web-mvp.md)
    session_cookie_secure: bool = False
    max_upload_mb: int = 25
    # Per-chunk Claude translation at ingestion; off allows fully offline uploads.
    translate_on_ingest: bool = True
    # Bring-your-own-key (docs/17): web users normally think with their own
    # Claude key. Only when this is true may a user without a key fall back to
    # the server's ANTHROPIC_API_KEY. The Streamlit prototype is unaffected.
    allow_shared_anthropic_key: bool = False

    def llm_configured(self) -> bool:
        return bool(self.anthropic_api_key.get_secret_value().strip())

    def auth_cookie_key_is_insecure_default(self) -> bool:
        return self.auth_cookie_key.get_secret_value().startswith("dev-only-insecure-key")


@lru_cache
def get_settings() -> Settings:
    return Settings()


def mask_secret(value: str, keep: int = 4) -> str:
    """Show only the last `keep` characters, for safe inclusion in logs/startup banners."""
    if len(value) <= keep:
        return "*" * len(value)
    return "*" * (len(value) - keep) + value[-keep:]
