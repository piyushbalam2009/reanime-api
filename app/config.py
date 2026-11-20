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
    source_allowed_hosts: str = "reanime.cz,www.reanime.cz,flixcloud.cc,.flixcloud.cc"
    source_timeout_seconds: float = 20.0
    source_access_mode: str = "direct"
    flaresolverr_url: str | None = None
    stream_proxy_base_url: str | None = None
    proxy_signing_secret: str | None = None
    proxy_token_ttl_seconds: int = 14400

    @property
    def allowed_hosts(self) -> set[str]:
        return {host.strip().lower() for host in self.source_allowed_hosts.split(",") if host.strip()}

    @property
    def cors(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
