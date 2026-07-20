from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    app_environment: str = "development"
    database_url: str = "sqlite:///./home_media.db"
    redis_url: str = "redis://redis:6379/0"
    internal_api_token: str = "change-me-before-production"
    telegram_bot_token: str | None = None
    telegram_allowed_user_ids: list[int] = Field(default_factory=list)
    llm_provider: str = "openai"
    openai_api_key: str | None = None
    automation_provider: str = "mock"
    automation_webhook_token: str = "change-me-before-production"
    jellyfin_url: str = "http://jellyfin:8096"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        enable_decoding=False,
    )

    @field_validator("telegram_allowed_user_ids", mode="before")
    @classmethod
    def parse_allowed_users(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
