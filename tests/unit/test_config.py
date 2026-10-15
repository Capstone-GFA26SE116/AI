import pytest
from pydantic import ValidationError

from metanoia_ai.config import MissingLlmSettingError, Settings

REQUIRED_VARS = {
    "DATABASE_URL": "postgresql://user:fake-password@localhost:5432/test",
    "JWT_SECRET": "test-secret-at-least-32-bytes-long!!",
    "JWT_ISSUER": "METANOIA",
    "JWT_AUDIENCE": "METANOIA.Client",
}
LLM_VARS = {
    "LLM_MODEL": "fake-flash-lite",
    "GEMINI_API_KEY": "fake-gemini-key",
    "LLM_INPUT_PRICE_PER_1M": "0.1",
    "LLM_OUTPUT_PRICE_PER_1M": "0.4",
}
SIMILAR_VARS = [
    "SIMILAR_MIN_SIMILARITY",
    "SIMILAR_RRF_K",
    "SIMILAR_BRANCH_LIMIT",
    "SIMILAR_DEFAULT_LIMIT",
    "SIMILAR_EMBED_TIMEOUT_SECONDS",
]
CHAT_VARS = [
    "CHAT_HISTORY_LIMIT",
    "CHAT_MESSAGE_MAX_CHARS",
    "BREAKDOWN_MIN_SUBTASKS",
    "BREAKDOWN_MAX_SUBTASKS",
    "BREAKDOWN_MIN_MINUTES",
    "BREAKDOWN_MAX_MINUTES",
    "BREAKDOWN_MANUAL_MAX_MINUTES",
    "BREAKDOWN_WARNING_RATIO",
]
OPTIONAL_VARS = [
    "JWT_LEEWAY_SECONDS",
    "KBIAS_ALPHA",
    "KBIAS_INITIAL",
    "KBIAS_OUTLIER_RATIO",
    "SCHEDULE_WINDOW_DAYS",
    "SCHEDULE_MIN_CHUNK_MINUTES",
    "SCHEDULE_ROUND_MINUTES",
    "SCHEDULE_TOP_PRODUCTIVE_HOURS",
    "QUOTA_DAILY_MAX",
    "LLM_MAX_RETRIES",
    *LLM_VARS,
    "EMBEDDING_MODEL",
    "EMBEDDING_DIMENSIONS",
    "EMBEDDING_PRICE_PER_1M",
    "MEMORY_TEXT_MAX_CHARS",
    *SIMILAR_VARS,
    *CHAT_VARS,
]
EMBEDDING_VARS = {
    "GEMINI_API_KEY": "fake-gemini-key",
    "EMBEDDING_MODEL": "fake-embedding",
    "EMBEDDING_PRICE_PER_1M": "0.15",
}


@pytest.fixture
def env(monkeypatch):
    """Set the required variables and clear the optional ones."""
    for name, value in REQUIRED_VARS.items():
        monkeypatch.setenv(name, value)
    for name in OPTIONAL_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def load() -> Settings:
    # _env_file=None: never read the real .env in tests.
    return Settings(_env_file=None)


def test_reads_values_from_env(env):
    settings = load()
    assert settings.database_url.get_secret_value() == REQUIRED_VARS["DATABASE_URL"]
    assert settings.jwt_secret.get_secret_value() == REQUIRED_VARS["JWT_SECRET"]
    assert settings.jwt_issuer == "METANOIA"
    assert settings.jwt_audience == "METANOIA.Client"


def test_defaults(env):
    settings = load()
    assert settings.jwt_leeway_seconds == 300
    assert settings.kbias_alpha == 0.2
    assert settings.kbias_initial == 1.3
    assert settings.kbias_outlier_ratio == 5.0
    assert settings.schedule_window_days == 7
    assert settings.schedule_min_chunk_minutes == 30
    assert settings.schedule_round_minutes == 5
    assert settings.schedule_top_productive_hours == 3
    assert settings.quota_daily_max == 50
    assert settings.llm_max_retries == 2


def test_starts_without_llm_settings(env):
    settings = load()
    assert settings.llm_model is None
    assert settings.gemini_api_key is None


def test_empty_value_counts_as_not_set(env):
    # Copying .env.example as-is must not stop the app.
    for name in LLM_VARS:
        env.setenv(name, "")
    settings = load()
    assert settings.llm_input_price_per_1m is None


def test_llm_config_when_all_set(env):
    for name, value in LLM_VARS.items():
        env.setenv(name, value)
    config = load().llm_config()
    assert config.model == "fake-flash-lite"
    assert config.api_key.get_secret_value() == "fake-gemini-key"
    assert config.max_retries == 2
    assert config.input_price_per_1m == 0.1
    assert config.output_price_per_1m == 0.4


@pytest.mark.parametrize("name", list(LLM_VARS))
def test_llm_config_fails_when_one_is_missing(env, name):
    for other, value in LLM_VARS.items():
        if other != name:
            env.setenv(other, value)
    settings = load()  # the app still starts
    with pytest.raises(MissingLlmSettingError, match=name):
        settings.llm_config()


def test_llm_error_does_not_show_secret(env):
    env.setenv("GEMINI_API_KEY", "fake-gemini-key")
    with pytest.raises(MissingLlmSettingError) as error:
        load().llm_config()
    assert "fake-gemini-key" not in str(error.value)


def test_values_can_be_changed(env):
    env.setenv("JWT_LEEWAY_SECONDS", "60")
    env.setenv("KBIAS_ALPHA", "0.3")
    env.setenv("KBIAS_INITIAL", "1.25")
    env.setenv("KBIAS_OUTLIER_RATIO", "4")
    settings = load()
    assert settings.jwt_leeway_seconds == 60
    assert settings.kbias_alpha == 0.3
    assert settings.kbias_initial == 1.25
    assert settings.kbias_outlier_ratio == 4


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("JWT_LEEWAY_SECONDS", "-1"),
        ("KBIAS_ALPHA", "0"),
        ("KBIAS_ALPHA", "0.31"),  # the DB rejects alpha > 0.30
        ("KBIAS_INITIAL", "0.4"),
        ("KBIAS_INITIAL", "3.1"),
        ("KBIAS_OUTLIER_RATIO", "1"),
        ("SCHEDULE_WINDOW_DAYS", "0"),
        ("SCHEDULE_MIN_CHUNK_MINUTES", "0"),
        ("SCHEDULE_ROUND_MINUTES", "0"),
        ("SCHEDULE_ROUND_MINUTES", "61"),
        ("SCHEDULE_TOP_PRODUCTIVE_HOURS", "0"),
        ("SCHEDULE_TOP_PRODUCTIVE_HOURS", "25"),
        ("QUOTA_DAILY_MAX", "0"),
        ("LLM_MAX_RETRIES", "-1"),
        ("LLM_INPUT_PRICE_PER_1M", "-0.1"),
        ("LLM_OUTPUT_PRICE_PER_1M", "-0.1"),
    ],
)
def test_bad_values_are_rejected(env, name, value):
    env.setenv(name, value)
    with pytest.raises(ValidationError):
        load()


@pytest.mark.parametrize("name", list(REQUIRED_VARS))
def test_missing_required_value_fails(env, name):
    env.delenv(name)
    with pytest.raises(ValidationError):
        load()


def test_secrets_are_hidden_when_printed(env):
    env.setenv("GEMINI_API_KEY", "fake-gemini-key")
    settings = load()
    for secret in ["fake-password", REQUIRED_VARS["JWT_SECRET"], "fake-gemini-key"]:
        assert secret not in repr(settings)
        assert secret not in str(settings)


# --- embedding (FR-19) ---


def test_embedding_defaults(env):
    settings = load()
    assert settings.embedding_model is None
    assert settings.embedding_price_per_1m is None
    assert settings.embedding_dimensions == 1536
    assert settings.memory_text_max_chars == 1000


def test_embedding_config_when_all_set(env):
    for name, value in EMBEDDING_VARS.items():
        env.setenv(name, value)
    config = load().embedding_config()
    assert config.model == "fake-embedding"
    assert config.api_key.get_secret_value() == "fake-gemini-key"
    assert config.dimensions == 1536
    assert config.price_per_1m == 0.15


def test_embedding_config_does_not_need_llm_settings(env):
    # Only the shared key: no LLM_MODEL, no LLM prices.
    for name, value in EMBEDDING_VARS.items():
        env.setenv(name, value)
    assert load().embedding_config().model == "fake-embedding"


@pytest.mark.parametrize("name", list(EMBEDDING_VARS))
def test_embedding_config_fails_when_one_is_missing(env, name):
    for other, value in EMBEDDING_VARS.items():
        if other != name:
            env.setenv(other, value)
    settings = load()  # the app still starts
    with pytest.raises(MissingLlmSettingError, match=name):
        settings.embedding_config()


def test_embedding_error_does_not_show_secret(env):
    env.setenv("GEMINI_API_KEY", "fake-gemini-key")
    with pytest.raises(MissingLlmSettingError) as error:
        load().embedding_config()
    assert "fake-gemini-key" not in str(error.value)


def test_embedding_values_can_be_changed(env):
    env.setenv("EMBEDDING_DIMENSIONS", "768")
    env.setenv("MEMORY_TEXT_MAX_CHARS", "500")
    settings = load()
    assert settings.embedding_dimensions == 768
    assert settings.memory_text_max_chars == 500


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("EMBEDDING_DIMENSIONS", "0"),
        ("EMBEDDING_PRICE_PER_1M", "-0.1"),
        ("MEMORY_TEXT_MAX_CHARS", "0"),
    ],
)
def test_bad_embedding_values_are_rejected(env, name, value):
    env.setenv(name, value)
    with pytest.raises(ValidationError):
        load()


# --- similar tasks (FR-09) ---


def test_similar_defaults(env):
    settings = load()
    assert settings.similar_min_similarity == 0.5
    assert settings.similar_rrf_k == 60
    assert settings.similar_branch_limit == 20
    assert settings.similar_default_limit == 5
    assert settings.similar_embed_timeout_seconds == 1.5


def test_similar_values_can_be_changed(env):
    env.setenv("SIMILAR_MIN_SIMILARITY", "0.65")
    env.setenv("SIMILAR_RRF_K", "30")
    env.setenv("SIMILAR_BRANCH_LIMIT", "50")
    env.setenv("SIMILAR_DEFAULT_LIMIT", "10")
    env.setenv("SIMILAR_EMBED_TIMEOUT_SECONDS", "2")
    settings = load()
    assert settings.similar_min_similarity == 0.65
    assert settings.similar_rrf_k == 30
    assert settings.similar_branch_limit == 50
    assert settings.similar_default_limit == 10
    assert settings.similar_embed_timeout_seconds == 2


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("SIMILAR_MIN_SIMILARITY", "-0.1"),
        ("SIMILAR_MIN_SIMILARITY", "1.1"),
        ("SIMILAR_RRF_K", "0"),
        ("SIMILAR_BRANCH_LIMIT", "0"),
        ("SIMILAR_BRANCH_LIMIT", "101"),
        ("SIMILAR_DEFAULT_LIMIT", "0"),
        ("SIMILAR_DEFAULT_LIMIT", "11"),
        ("SIMILAR_EMBED_TIMEOUT_SECONDS", "0"),
    ],
)
def test_bad_similar_values_are_rejected(env, name, value):
    env.setenv(name, value)
    with pytest.raises(ValidationError):
        load()


# --- task chat (FR-08a) ---


def test_chat_defaults(env):
    settings = load()
    assert settings.chat_history_limit == 20
    assert settings.chat_message_max_chars == 2000
    assert (settings.breakdown_min_subtasks, settings.breakdown_max_subtasks) == (2, 10)
    assert (settings.breakdown_min_minutes, settings.breakdown_max_minutes) == (5, 120)
    assert settings.breakdown_manual_max_minutes == 480
    assert settings.breakdown_warning_ratio == 0.3


def test_chat_values_can_be_changed(env):
    env.setenv("CHAT_HISTORY_LIMIT", "10")
    env.setenv("BREAKDOWN_MAX_SUBTASKS", "8")
    env.setenv("BREAKDOWN_WARNING_RATIO", "0.5")
    settings = load()
    assert settings.chat_history_limit == 10
    assert settings.breakdown_max_subtasks == 8
    assert settings.breakdown_warning_ratio == 0.5


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("CHAT_HISTORY_LIMIT", "0"),
        ("CHAT_MESSAGE_MAX_CHARS", "0"),
        ("BREAKDOWN_MIN_SUBTASKS", "0"),
        ("BREAKDOWN_MIN_MINUTES", "0"),
        ("BREAKDOWN_WARNING_RATIO", "0"),
    ],
)
def test_bad_chat_values_are_rejected(env, name, value):
    env.setenv(name, value)
    with pytest.raises(ValidationError):
        load()


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({"BREAKDOWN_MIN_SUBTASKS": "11"}, id="min-subtasks-over-max"),
        pytest.param({"BREAKDOWN_MIN_MINUTES": "121"}, id="min-minutes-over-max"),
        pytest.param({"BREAKDOWN_MAX_MINUTES": "500"}, id="ai-max-over-manual-max"),
    ],
)
def test_breakdown_ranges_must_make_sense(env, changes):
    for name, value in changes.items():
        env.setenv(name, value)
    with pytest.raises(ValidationError):
        load()
