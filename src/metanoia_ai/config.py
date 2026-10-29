"""Service settings, read from environment variables and the .env file."""

from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

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

    # Project chat (FR-08b-1): a client brief → milestones + Flexible tasks.
    chat_project_message_max_chars: int = Field(default=8000, gt=0)
    # The first message (the brief) is always sent, plus this many recent ones (#18).
    chat_project_history_limit: int = Field(default=10, ge=1)
    chat_project_prompt_max_chars: int = Field(default=30000, gt=0)
    # Newest existing milestone/task titles sent so the AI does not repeat them.
    chat_project_existing_titles_limit: int = Field(default=50, ge=0)
    project_max_milestones: int = Field(default=8, ge=1)
    project_max_tasks_per_milestone: int = Field(default=15, ge=1)
    project_max_tasks: int = Field(default=40, ge=1)
    # Minutes per task: AI drafts 15-480; manual edits at Apply 5-960 (#21).
    project_task_min_minutes: int = Field(default=15, ge=1)
    project_task_max_minutes: int = Field(default=480, ge=1)
    project_manual_min_minutes: int = Field(default=5, ge=1)
    project_manual_max_minutes: int = Field(default=960, ge=1)
    # Milestone.Name is varchar(150) in the DB (#27).
    project_milestone_title_max_chars: int = Field(default=150, ge=1, le=150)
    # Work hours per day for the rough overload warning (#20).
    chat_daily_work_hours: float = Field(default=6, gt=0, le=24)
    # Output can be up to 40 tasks, so it waits longer than the task chat (#23).
    chat_project_llm_timeout_seconds: float = Field(default=30, ge=10)
    # No retry once this much time has passed since the first try (#24).
    chat_project_retry_budget_seconds: float = Field(default=40, gt=0)
    chat_project_max_retries: int = Field(default=1, ge=0, le=2)  # #29

    # Project chat documents (FR-08b-2). Files come from the Backend's download
    # endpoint, called with the user's own token (B2).
    backend_base_url: str = Field(pattern=r"^https?://\S+$")
    backend_download_timeout_seconds: float = Field(default=15, gt=0)
    chat_document_max_files: int = Field(default=3, ge=1, le=10)  # hub + upload
    # NFR-26; ProjectDocument.SizeBytes has the same 20 MB check in the DB.
    chat_document_max_file_bytes: int = Field(
        default=20 * 1024 * 1024, gt=0, le=20 * 1024 * 1024
    )
    # ≈ 20,000 tokens at 4 characters per token (CLAUDE.md §7, #4, #16).
    chat_document_max_chars: int = Field(default=80_000, gt=0, le=200_000)
    chat_document_max_pdf_pages: int = Field(default=100, ge=1)  # #12

    # Productive hours (FR-18). Scores fit UserCircadianProfile.Score: [0, 1].
    onboarding_selected_score: float = Field(default=0.8, ge=0, le=1)
    onboarding_unselected_score: float = Field(default=0.3, ge=0, le=1)
    # How many sessions the onboarding answer is worth in the learned score.
    productive_prior_weight: float = Field(default=5, gt=0)
    # A session stopped for "Other" counts as good only if at least this long.
    productive_min_session_minutes: int = Field(default=25, gt=0)
    productive_hours_window_days: int = Field(default=90, gt=0)
    # Plan names (SubscriptionPlan.Name) that learn from timer data (#16).
    # In .env: comma-separated, e.g. "Cao cấp,Doanh nghiệp".
    productive_learning_plans: Annotated[tuple[str, ...], NoDecode] = ("Cao cấp",)

    # Daily "Today" briefing (FR-12). No LLM; only FR-09 embeddings.
    briefing_top_count: int = Field(default=3, ge=1, le=10)
    # Deadlines further away count as safe (#3).
    briefing_risk_window_days: int = Field(default=14, gt=0)
    briefing_at_risk_limit: int = Field(default=5, ge=1)
    # "You usually need X% more time" only for a learned K (#7). K must be > 1.
    briefing_bias_min_k: float = Field(default=1.1, gt=1, le=K_MAX)
    briefing_bias_min_samples: int = Field(default=3, ge=1)
    # Similar task warning (#11): enough results and a high average ratio.
    briefing_similar_min_count: int = Field(default=2, ge=1)
    briefing_similar_min_ratio: float = Field(default=1.3, gt=1)
    # Total wait for all similar task searches together (#12).
    briefing_similar_timeout_seconds: float = Field(default=3, gt=0)
    briefing_productive_top_hours: int = Field(default=3, ge=1, le=24)

    # Personal analytics (FR-20). No LLM, no quota.
    # Bias sentence (#3): fewer samples → "not enough data"; from "confident" on,
    # no "still learning" note. Thresholds from the K-SiP study.
    analytics_bias_min_samples: int = Field(default=5, ge=1)
    analytics_bias_confident_samples: int = Field(default=10, ge=1)
    # |K - 1| below this → "fairly accurate" (#4).
    analytics_bias_neutral_band: float = Field(default=0.10, ge=0, lt=1)
    # Project insight: at least this many tasks and this % off the plan (§2 #10).
    analytics_project_insight_min_percent: int = Field(default=20, ge=1)
    analytics_project_insight_min_tasks: int = Field(default=3, ge=1)
    analytics_insight_limit: int = Field(default=3, ge=1)
    # Largest `limit` of GET /analytics/breakdown?by=task.
    analytics_task_limit_max: int = Field(default=200, ge=1)

    @field_validator("productive_learning_plans", mode="before")
    @classmethod
    def split_plan_names(cls, value: object) -> object:
        if isinstance(value, str):
            value = tuple(name.strip() for name in value.split(","))
        if isinstance(value, tuple | list) and not all(value):
            raise ValueError("PRODUCTIVE_LEARNING_PLANS has an empty name")
        return value

    @model_validator(mode="after")
    def check_onboarding_scores(self) -> "Settings":
        # A chosen block must rank above the others (AC2).
        if self.onboarding_selected_score <= self.onboarding_unselected_score:
            raise ValueError(
                "ONBOARDING_SELECTED_SCORE must be > ONBOARDING_UNSELECTED_SCORE"
            )
        return self

    @model_validator(mode="after")
    def check_analytics_samples(self) -> "Settings":
        if self.analytics_bias_min_samples > self.analytics_bias_confident_samples:
            raise ValueError(
                "ANALYTICS_BIAS_MIN_SAMPLES > ANALYTICS_BIAS_CONFIDENT_SAMPLES"
            )
        return self

    @model_validator(mode="after")
    def check_breakdown_ranges(self) -> "Settings":
        if self.breakdown_min_subtasks > self.breakdown_max_subtasks:
            raise ValueError("BREAKDOWN_MIN_SUBTASKS > BREAKDOWN_MAX_SUBTASKS")
        if self.breakdown_min_minutes > self.breakdown_max_minutes:
            raise ValueError("BREAKDOWN_MIN_MINUTES > BREAKDOWN_MAX_MINUTES")
        if self.breakdown_max_minutes > self.breakdown_manual_max_minutes:
            raise ValueError("BREAKDOWN_MAX_MINUTES > BREAKDOWN_MANUAL_MAX_MINUTES")
        return self

    @model_validator(mode="after")
    def check_project_ranges(self) -> "Settings":
        if self.project_max_tasks_per_milestone > self.project_max_tasks:
            raise ValueError("PROJECT_MAX_TASKS_PER_MILESTONE > PROJECT_MAX_TASKS")
        if self.project_task_min_minutes > self.project_task_max_minutes:
            raise ValueError("PROJECT_TASK_MIN_MINUTES > PROJECT_TASK_MAX_MINUTES")
        if self.project_manual_min_minutes > self.project_manual_max_minutes:
            raise ValueError("PROJECT_MANUAL_MIN_MINUTES > PROJECT_MANUAL_MAX_MINUTES")
        # Whatever the AI may propose, the user may keep at Apply.
        if self.project_manual_min_minutes > self.project_task_min_minutes:
            raise ValueError("PROJECT_MANUAL_MIN_MINUTES > PROJECT_TASK_MIN_MINUTES")
        if self.project_task_max_minutes > self.project_manual_max_minutes:
            raise ValueError("PROJECT_TASK_MAX_MINUTES > PROJECT_MANUAL_MAX_MINUTES")
        # The brief alone must fit in the prompt.
        if self.chat_project_message_max_chars > self.chat_project_prompt_max_chars:
            raise ValueError(
                "CHAT_PROJECT_MESSAGE_MAX_CHARS > CHAT_PROJECT_PROMPT_MAX_CHARS"
            )
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
