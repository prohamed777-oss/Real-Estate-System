"""Application configuration.

All configuration comes from environment variables (12-factor).
Production secrets live in the deployment environment; local dev uses .env.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Core ---
    app_env: str = "development"  # development | test | production
    test_mode: bool = False  # enables WhatsApp simulator on production for QA
    app_name: str = "Real Estate Revenue OS"
    api_prefix: str = "/api/v1"
    cors_origins: list[str] = ["http://localhost:3000"]

    # --- Database (Supabase Postgres; pooler URL in production) ---
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/revenue_os"
    db_pool_size: int = 5
    db_max_overflow: int = 10

    # --- Auth (Supabase) ---
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_jwt_secret: str = ""  # legacy HS256 secret; JWKS preferred
    auth_dev_enabled: bool = True  # X-Dev-Email login — NEVER enable in production

    # --- Jobs / cron ---
    cron_secret: str = "dev-cron-secret"

    # --- Channels ---
    meta_webhook_verify_token: str = "dev-verify-token"

    # --- Encryption at rest for provider credentials ---
    secret_encryption_key: str = ""  # Fernet key or passphrase; empty = derived (dev)

    # --- V4 event transport (optional) ---
    nats_url: str = ""

    # --- A5: email delivery ---
    resend_api_key: str = ""

    # --- B2: error tracking ---
    sentry_dsn: str = ""

    # --- A4: storage bucket ---
    storage_bucket: str = "media"  # e.g. nats://nats.railway.internal:4222

    # --- AI (Model Gateway) ---
    ai_provider: str = "mock"  # mock | gemini | openai_compatible
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    gemini_embedding_model: str = "text-embedding-004"
    embedding_dimensions: int = 768
    openai_compatible_base_url: str = ""
    openai_compatible_api_key: str = ""
    openai_compatible_model: str = ""

    # --- Storage (Supabase Storage / S3-compatible) ---
    storage_bucket: str = "media"

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def jwks_url(self) -> str | None:
        if self.supabase_url:
            return f"{self.supabase_url}/auth/v1/.well-known/jwks.json"
        return None


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
