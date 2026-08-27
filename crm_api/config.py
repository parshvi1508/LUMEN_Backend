from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    channel_hmac_secret: str
    channel_send_url: str
    # Auth: set supabase_jwks_url for ES256 (prod), or supabase_jwt_secret for HS256.
    supabase_jwt_secret: str | None = None
    supabase_jwks_url: str | None = None
    groq_api_key: str
    openrouter_api_key: str
    groq_model: str = "qwen/qwen3.6-27b"
    openrouter_model: str = "openai/gpt-oss-20b:free"
    app_name: str = "Lumen CRM API"
    cors_origins: str = "https://lumencrm-frontend.vercel.app"
    default_tenant_id: str | None = None
    ai_rate_limit_per_minute: int = 120
    receipt_rate_limit_per_minute: int = 600

    @field_validator("database_url")
    @classmethod
    def normalize_database_url(cls, v: str) -> str:
        if v.startswith("postgres://"):
            v = v.replace("postgres://", "postgresql://", 1)
        if v.startswith("postgresql://"):
            v = v.replace("postgresql://", "postgresql+asyncpg://", 1)
        if not v.startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL must be a postgresql connection string")
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
