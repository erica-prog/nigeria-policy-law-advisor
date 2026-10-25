"""Centralized settings, loaded once from .env. Never log secret values directly."""

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
INDEX_DIR = DATA_DIR / "index"
# Where the Chroma-era per-matter chunks.json/meta.json files lived. Read only
# by scripts/migrate_to_postgres.py now.
MATTERS_DIR = INDEX_DIR / "matters"
AUTH_DIR = DATA_DIR / "auth"
ORIGINALS_DIR = DATA_DIR / "originals"
PHASE1_DEMO_MATTER_ID = "phase1-demo"
# United States federal authority (docs/23 slice P2-B): public-domain texts
# fetched by scripts/fetch_us_corpus.py into data/us/ and seeded by build_index.
US_DATA_DIR = DATA_DIR / "us"
US_LIBRARY_MATTER_ID = "us-library"
# The shared, read-only authority collections every lawyer's question may
# widen into (ADR-001 decision 4). Nothing is ever uploaded to them.
LIBRARY_MATTER_IDS = frozenset({PHASE1_DEMO_MATTER_ID, US_LIBRARY_MATTER_ID})


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
    # Required only when ANTHROPIC_API_KEY is an organization key that is not
    # scoped to one workspace. Never attached to a user's own key.
    anthropic_workspace_id: str = ""
    anthropic_model: str = "claude-sonnet-4-6"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    # Postgres (Netlify DB, a Neon database): matters, chunks with their
    # embeddings, chat history, accounts and tenants. Use the unpooled
    # connection string (docs/16). Optional here so tools that never touch the
    # database still import; db.py raises a clear error if it's missing when needed.
    database_url: SecretStr | None = None
    # After connecting, the app switches to this role, which cannot bypass
    # row-level security (migration 0009; docs/31). Empty keeps the connecting
    # role, which bypasses the tenant policies: for emergencies only.
    database_app_role: str = "policy_advisor_app"
    # Where uploaded originals are kept (storage.py). A mounted volume in the
    # container; an S3-compatible store is the planned alternative.
    originals_dir: Path = ORIGINALS_DIR
    # Document jobs (api/jobs.py) are rows in document_jobs. True: the API
    # process runs the worker inline after each upload. False: run
    # `python -m policy_advisor.worker` alongside the API.
    inline_workers: bool = True
    worker_poll_seconds: float = 2.0
    log_level: str = "info"
    retrieval_top_k: int = 8
    # Keyword search backend per matter: up to this many chunks the retriever
    # builds an in-memory BM25 index from the matter's rows; above it, keyword
    # hits come from Postgres full-text search (migration 0004), which never
    # loads the matter into memory. 0 forces full-text search everywhere.
    keyword_index_max_chunks: int = 5000
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
    #
    # The two are NOT symmetric, and MAX is deliberately loose. The golden set
    # covers one matter of long, formal civil-procedure text; a lawyer's own
    # matter looks nothing like it. Measured against a two-sentence contract,
    # questions the document plainly answers sit at 0.32 ("how long does the
    # exclusivity clause run for", answered verbatim in the text) and 0.37
    # ("what is the exclusivity period"), while a genuinely unrelated question
    # sits at 0.57. A MAX fitted to the demo corpus therefore hard-rejects real
    # documents in real matters.
    #
    # So MAX only buys the cheap rejection of the obviously unrelated. The
    # adjudicator in relevance_check.py is the actual classifier, and being
    # wrong in the two directions costs very different things: auto-accepting a
    # weak chunk leads to a grounded prompt that still refuses when the passage
    # doesn't answer the question, while auto-rejecting hides the lawyer's own
    # document behind a web result they never asked for.
    retrieval_certain_distance: float = 0.21
    retrieval_max_distance: float = 0.50
    # Per-tier overrides of the two thresholds, as JSON in the environment:
    #   RETRIEVAL_GATE_OVERRIDES='{"authority": {"certain": 0.21, "max": 0.50}}'
    # A tier without an entry uses the two values above. Calibrate per tier with
    # eval/calibrate_relevance_floor.py against a golden set for that tier.
    retrieval_gate_overrides: dict[str, dict[str, float]] = {}
    # Cross-encoder that rescores the top fused candidates on one relevance
    # scale (retrieval/rerank.py). Empty disables reranking.
    rerank_enabled: bool = True
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    rerank_top_n: int = 24
    auth_cookie_key: SecretStr = SecretStr("dev-only-insecure-key-set-AUTH_COOKIE_KEY-in-.env")
    # Web API (docs/architecture/web-mvp.md)
    session_cookie_secure: bool = False
    max_upload_mb: int = 25
    # Per-chunk Claude translation at ingestion; off allows fully offline uploads.
    translate_on_ingest: bool = True
    # Bring-your-own-key (docs/17): web users normally think with their own
    # Claude key. Only when this is true may a user without a key fall back to
    # the server's ANTHROPIC_API_KEY.
    allow_shared_anthropic_key: bool = False
    # External sources as MCP tools (docs/26). Local connector servers live
    # under CONNECTORS_DIR and run with NODE_COMMAND; every tool call has a
    # timeout and a run has a call budget (docs/23 D5). Connector credentials
    # (REGULATIONS_GOV_API_KEY, COURTLISTENER_API_KEY, GOVINFO_API_KEY) are
    # read from the environment by the name the registry row gives.
    connectors_dir: Path = PROJECT_ROOT / "connectors"
    node_command: str = "node"
    connector_timeout_seconds: float = 45.0
    connector_result_max_chars: int = 12_000
    agent_max_tool_calls: int = 12
    # The research agent (docs/28). Empty AGENT_MODEL means ANTHROPIC_MODEL, so
    # one key and one bill; the per-turn output cap bounds a runaway answer.
    agent_model: str = ""
    agent_max_tokens: int = 16_000
    # Tracing and the cost ledger (docs/32). OTEL_EXPORTER: none | console |
    # otlp (endpoint from OTEL_EXPORTER_OTLP_ENDPOINT) | memory (tests).
    # MODEL_PRICES is JSON per model, per million tokens, e.g.
    #   {"claude-sonnet-4-6": {"input_per_million": 3.0, "output_per_million": 15.0}}
    # Empty leaves estimated_usd NULL in the ledger; token counts are always kept.
    otel_exporter: str = "none"
    # Bulk analysis (docs/33). Triage with the cheaper model, escalate flagged
    # documents to the escalation model (blank = ANTHROPIC_MODEL); jobs over
    # BULK_BATCH_THRESHOLD documents use the Batch API; a job stops at its
    # token ceiling and waits to be resumed; a record below the confidence
    # floor is flagged for escalation and review.
    bulk_triage_model: str = "claude-haiku-5-5"
    bulk_escalation_model: str = ""
    bulk_batch_threshold: int = 50
    bulk_default_ceiling_tokens: int = 2_000_000
    bulk_flag_confidence: float = 0.8
    bulk_max_document_chars: int = 120_000
    model_prices: dict[str, dict[str, float]] = {}

    def llm_configured(self) -> bool:
        return bool(self.anthropic_api_key.get_secret_value().strip())

    def auth_cookie_key_is_insecure_default(self) -> bool:
        return self.auth_cookie_key.get_secret_value().startswith("dev-only-insecure-key")


@lru_cache
def get_settings() -> Settings:
    return Settings()


def workspace_headers(settings: Settings, api_key: str | None) -> dict[str, str]:
    """Header Anthropic requires for an unscoped organization key.

    `api_key` is the override passed by the caller, before it is replaced with
    the server key. None means this call uses the server key. A different key
    belongs to the user, so the server's workspace id must not go with it.
    """
    workspace = settings.anthropic_workspace_id
    if not isinstance(workspace, str) or not workspace.strip():
        return {}
    server = settings.anthropic_api_key.get_secret_value()
    if not isinstance(server, str):
        return {}
    if api_key is not None and api_key != server:
        return {}
    return {"anthropic-workspace-id": workspace.strip()}


def mask_secret(value: str, keep: int = 4) -> str:
    """Show only the last `keep` characters, for safe inclusion in logs/startup banners."""
    if len(value) <= keep:
        return "*" * len(value)
    return "*" * (len(value) - keep) + value[-keep:]
