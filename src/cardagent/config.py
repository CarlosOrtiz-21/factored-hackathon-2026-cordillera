"""Application settings, loaded from environment variables and `.env`.

Secrets (API keys) never live in code: they come from the environment and are
wrapped in `SecretStr` so they don't leak into logs or reprs by accident.
"""

from datetime import date
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM: one model per agent (docs/ARCHITECTURE.md §5). Parameters per model are
    # handled by cardagent.llm.profiles; the all-Haiku ablation only changes these values.
    anthropic_api_key: SecretStr | None = None
    # Required only when the API key is not scoped to a workspace (the API says so with a 400).
    anthropic_workspace_id: str | None = None
    triage_model: str = "claude-haiku-4-5"
    executor_model: str = "claude-sonnet-5"
    rag_model: str = "claude-sonnet-5"
    critic_model: str = "claude-sonnet-5"
    prompt_version: str = "v1.2"  # agents/prompts.py; v1.0 kept for the before/after compare

    # Dataset source: read-only S3 bucket shared with hackathon participants.
    # Keys come from .env only; they must never reach the public repo.
    aws_access_key_id: SecretStr | None = None
    aws_secret_access_key: SecretStr | None = None
    aws_default_region: str = "us-east-2"
    s3_bucket: str = "factored-datathon-2026-s3-157725502942-us-east-2-an"
    s3_prefix: str = "data/"

    # Paths
    data_dir: Path = Path("data")
    raw_dir: Path = Path("data/raw")
    warehouse_path: Path = Path("data/warehouse.duckdb")
    bank_db_path: Path = Path("data/bank.db")

    # PII detection: minimum Presidio score for a span to be tokenized.
    pii_score_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    spacy_model: str = "es_core_news_md"

    # Safety limits (see docs/ARCHITECTURE.md §4)
    hitl_risk_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    max_react_iterations: int = Field(default=5, ge=1)
    max_critic_retries: int = Field(default=2, ge=0)
    approval_ttl_minutes: int = Field(default=10, ge=1)
    session_ttl_minutes: int = Field(default=15, ge=1)

    # API (step 4). Demo codes stand in for an identity provider's second factor: a
    # customer id alone never proves identity. Override them in .env.
    demo_login_code: SecretStr = SecretStr("246810")
    auditor_login_code: SecretStr = SecretStr("135790")
    frontend_origin: str = "http://localhost:3000"
    max_message_chars: int = Field(default=2000, ge=1)
    rate_limit_per_minute: int = Field(default=20, ge=1)
    checkpoint_path: Path = Path("data/checkpoints.db")
    vault_db_path: Path = Path("data/vault.db")
    vault_key: SecretStr | None = None  # else a key file is created at vault_key_path
    vault_key_path: Path = Path("data/vault.key")

    # The dataset is a static snapshot: "today" for transaction look-back windows.
    bank_as_of_date: date = date(2026, 6, 17)


@lru_cache
def get_settings() -> Settings:
    return Settings()
