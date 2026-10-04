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
    llm_provider: str = "gemini"
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-flash-latest"
    gemini_google_search_enabled: bool = False
    media_market_country: str = "US"
    chat_history_limit: int = 20
    automation_provider: str = "mock"
    automation_webhook_token: str = "change-me-before-production"
    jellyfin_url: str = "http://jellyfin:8096"
    jellyfin_api_key: str | None = None
    radarr_url: str = "http://radarr:7878"
    radarr_api_key: str | None = None
    sonarr_url: str = "http://sonarr:8989"
    sonarr_api_key: str | None = None
    qbittorrent_url: str = "http://gluetun:8080"
    qbittorrent_username: str | None = None
    qbittorrent_password: str | None = None
    media_root_folder: str = "/data/library"
    radarr_root_folder: str = "/data/library/movies"
    sonarr_root_folder: str = "/data/library/tv"
    # The stock Arr profiles include this compact, 720p-only profile. Keep the
    # choice explicit: selecting the first profile can silently select "Any".
    radarr_quality_profile: str = "HD-720p"
    sonarr_quality_profile: str = "HD-720p"
    chat_reader_database_url: str | None = None
    chat_sql_timeout_ms: int = 3000
    chat_sql_row_limit: int = 25

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
