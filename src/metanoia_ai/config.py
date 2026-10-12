"""Service settings, read from environment variables and the .env file."""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from metanoia_ai.domain.kbias import (
    DEFAULT_ALPHA,
    DEFAULT_OUTLIER_RATIO,
    INITIAL_K_BIAS,
    K_MAX,
    K_MIN,
)


class Settings(BaseSettings):
    """All config values. Missing required values fail at startup."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: SecretStr

    jwt_secret: SecretStr
    jwt_issuer: str
    jwt_audience: str
    jwt_leeway_seconds: int = Field(default=300, ge=0)

    # The DB only accepts alpha in (0, 0.30] (KBiasUpdateLog_Alpha_check).
    kbias_alpha: float = Field(default=DEFAULT_ALPHA, gt=0, le=0.30)
    kbias_initial: float = Field(default=INITIAL_K_BIAS, ge=K_MIN, le=K_MAX)
    kbias_outlier_ratio: float = Field(default=DEFAULT_OUTLIER_RATIO, gt=1)


@lru_cache
def get_settings() -> Settings:
    """Return the settings, loaded once and reused."""
    return Settings()
