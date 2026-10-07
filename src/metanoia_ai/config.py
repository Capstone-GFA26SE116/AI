"""Service settings, read from environment variables and the .env file."""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All config values. Missing required values fail at startup."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    jwt_secret: SecretStr
    jwt_issuer: str
    jwt_audience: str
    jwt_leeway_seconds: int = Field(default=300, ge=0)


@lru_cache
def get_settings() -> Settings:
    """Return the settings, loaded once and reused."""
    return Settings()
