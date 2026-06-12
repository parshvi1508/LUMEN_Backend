from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class ChannelSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    crm_receipts_url: str = "http://localhost:8000/api/v1/receipts"
    channel_hmac_secret: str
    jitter_min_seconds: float = 0.5
    jitter_max_seconds: float = 30.0
    duplicate_probability: float = 0.1
    reorder_probability: float = 0.15


@lru_cache
def get_settings() -> ChannelSettings:
    return ChannelSettings()
