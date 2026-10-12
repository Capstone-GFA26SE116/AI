import pytest
from pydantic import ValidationError

from metanoia_ai.config import Settings

REQUIRED_VARS = {
    "DATABASE_URL": "postgresql://user:fake-password@localhost:5432/test",
    "JWT_SECRET": "test-secret-at-least-32-bytes-long!!",
    "JWT_ISSUER": "METANOIA",
    "JWT_AUDIENCE": "METANOIA.Client",
}
OPTIONAL_VARS = [
    "JWT_LEEWAY_SECONDS",
    "KBIAS_ALPHA",
    "KBIAS_INITIAL",
    "KBIAS_OUTLIER_RATIO",
]


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
    settings = load()
    for secret in ["fake-password", REQUIRED_VARS["JWT_SECRET"]]:
        assert secret not in repr(settings)
        assert secret not in str(settings)
