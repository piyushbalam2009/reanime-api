from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    app_name: str = "ReAnime API"
    app_env: str = "development"
    log_level: str = "INFO"
    cors_origins: str = "*"
    redis_url: str | None = None
    source_base_url: str = "https://reanime.cz"
    source_timeout_seconds: float = 20.0
    source_access_mode: str = "direct"
    flixcloud_flaresolverr_url: str = "http://127.0.0.1:8191"
    flixcloud_flaresolverr_timeout_seconds: int = 60000

    @property
    def cors(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
