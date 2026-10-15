"""Service settings, read from environment variables and the .env file."""

from dataclasses import dataclass
from functools import lru_cache

from pydantic import Field, SecretStr, model_validator
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

    # env_ignore_empty: "NAME=" (as in .env.example) counts as not set.
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", env_ignore_empty=True
    )

    database_url: SecretStr

    jwt_secret: SecretStr
    jwt_issuer: str
    jwt_audience: str
    jwt_leeway_seconds: int = Field(default=300, ge=0)

    # The DB only accepts alpha in (0, 0.30] (KBiasUpdateLog_Alpha_check).
    kbias_alpha: float = Field(default=DEFAULT_ALPHA, gt=0, le=0.30)
    kbias_initial: float = Field(default=INITIAL_K_BIAS, ge=K_MIN, le=K_MAX)
    kbias_outlier_ratio: float = Field(default=DEFAULT_OUTLIER_RATIO, gt=1)

    # Schedule proposal (FR-10).
    schedule_window_days: int = Field(default=7, gt=0)
    schedule_min_chunk_minutes: int = Field(default=30, gt=0)
    schedule_round_minutes: int = Field(default=5, gt=0, le=60)
    schedule_top_productive_hours: int = Field(default=3, gt=0, le=24)
    quota_daily_max: int = Field(default=50, gt=0)

    # LLM. Optional at startup: only features that call the LLM need them,
    # and they get them through llm_config(), which fails if one is missing.
    llm_model: str | None = None
    llm_max_retries: int = Field(default=2, ge=0)
    gemini_api_key: SecretStr | None = None
    llm_input_price_per_1m: float | None = Field(default=None, ge=0)
    llm_output_price_per_1m: float | None = Field(default=None, ge=0)

    # Embedding + long-term memory (FR-19). Like the LLM: optional at startup,
    # required through embedding_config(). Uses the same GEMINI_API_KEY.
    embedding_model: str | None = None
    # The DB column is vector(1536); another size cannot be stored.
    embedding_dimensions: int = Field(default=1536, gt=0)
    embedding_price_per_1m: float | None = Field(default=None, ge=0)
    memory_text_max_chars: int = Field(default=1000, gt=0)

    # Similar tasks (FR-09): hybrid search + RRF.
    # Vector results below this cosine similarity are dropped as noise.
    similar_min_similarity: float = Field(default=0.5, ge=0, le=1)
    similar_rrf_k: int = Field(default=60, gt=0)
    # Rows taken from each branch (vector, full-text) before merging.
    similar_branch_limit: int = Field(default=20, ge=1, le=100)
    # Results returned when the caller gives no limit (API allows 1-10).
    similar_default_limit: int = Field(default=5, ge=1, le=10)
    # Wait at most this long for the query vector, then search text only (#14).
    similar_embed_timeout_seconds: float = Field(default=1.5, gt=0)

    # Task chat (FR-08a): split a big task into subtasks.
    chat_history_limit: int = Field(default=20, ge=1)  # messages sent to the LLM
    chat_message_max_chars: int = Field(default=2000, gt=0)
    breakdown_min_subtasks: int = Field(default=2, ge=1)
    breakdown_max_subtasks: int = Field(default=10, ge=1)
    # Minutes per subtask: AI drafts stay small; manual edits may go higher.
    breakdown_min_minutes: int = Field(default=5, ge=1)
    breakdown_max_minutes: int = Field(default=120, ge=1)
    breakdown_manual_max_minutes: int = Field(default=480, ge=1)
    # Warn when the subtask total differs from T_est × K by more than this share.
    breakdown_warning_ratio: float = Field(default=0.3, gt=0)
    # Wait per Gemini call in the chat (#31). FR-10 keeps 10 s; Gemini needs ≥ 10.
    chat_llm_timeout_seconds: float = Field(default=20, ge=10)

    @model_validator(mode="after")
    def check_breakdown_ranges(self) -> "Settings":
        if self.breakdown_min_subtasks > self.breakdown_max_subtasks:
            raise ValueError("BREAKDOWN_MIN_SUBTASKS > BREAKDOWN_MAX_SUBTASKS")
        if self.breakdown_min_minutes > self.breakdown_max_minutes:
            raise ValueError("BREAKDOWN_MIN_MINUTES > BREAKDOWN_MAX_MINUTES")
        if self.breakdown_max_minutes > self.breakdown_manual_max_minutes:
            raise ValueError("BREAKDOWN_MAX_MINUTES > BREAKDOWN_MANUAL_MAX_MINUTES")
        return self

    def embedding_config(self) -> "EmbeddingConfig":
        """The embedding settings, all present. Raises MissingLlmSettingError otherwise."""
        values = {
            "GEMINI_API_KEY": self.gemini_api_key,
            "EMBEDDING_MODEL": self.embedding_model,
            "EMBEDDING_PRICE_PER_1M": self.embedding_price_per_1m,
        }
        missing = [name for name, value in values.items() if value is None]
        if missing:
            # Only the names: never put a secret value in an error message.
            raise MissingLlmSettingError(", ".join(missing))
        return EmbeddingConfig(
            model=self.embedding_model,
            api_key=self.gemini_api_key,
            dimensions=self.embedding_dimensions,
            price_per_1m=self.embedding_price_per_1m,
        )

    def llm_config(self) -> "LlmConfig":
        """The LLM settings, all present. Raises MissingLlmSettingError otherwise."""
        values = {
            "LLM_MODEL": self.llm_model,
            "GEMINI_API_KEY": self.gemini_api_key,
            "LLM_INPUT_PRICE_PER_1M": self.llm_input_price_per_1m,
            "LLM_OUTPUT_PRICE_PER_1M": self.llm_output_price_per_1m,
        }
        missing = [name for name, value in values.items() if value is None]
        if missing:
            # Only the names: never put a secret value in an error message.
            raise MissingLlmSettingError(", ".join(missing))
        return LlmConfig(
            model=self.llm_model,
            api_key=self.gemini_api_key,
            max_retries=self.llm_max_retries,
            input_price_per_1m=self.llm_input_price_per_1m,
            output_price_per_1m=self.llm_output_price_per_1m,
        )


class MissingLlmSettingError(Exception):
    """An LLM feature was used but its settings are not in .env."""


@dataclass(frozen=True)
class LlmConfig:
    """Everything needed to call the LLM and log the cost of each call."""

    model: str
    api_key: SecretStr
    max_retries: int
    # Price per 1 million tokens (currency not agreed with the Backend yet).
    input_price_per_1m: float
    output_price_per_1m: float


@dataclass(frozen=True)
class EmbeddingConfig:
    """Everything needed to embed memory text and log the cost of each call."""

    model: str
    api_key: SecretStr
    dimensions: int
    # Price per 1 million input tokens (embeddings have no output tokens).
    price_per_1m: float


@lru_cache
def get_settings() -> Settings:
    """Return the settings, loaded once and reused."""
    return Settings()
