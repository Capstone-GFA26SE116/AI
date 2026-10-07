import pytest
from pydantic import ValidationError

from metanoia_ai.config import Settings

JWT_VARS = {
    "JWT_SECRET": "test-secret-at-least-32-bytes-long!!",
    "JWT_ISSUER": "METANOIA",
    "JWT_AUDIENCE": "METANOIA.Client",
}


@pytest.fixture
def jwt_env(monkeypatch):
    """Set the JWT variables and clear the optional one."""
    for name, value in JWT_VARS.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("JWT_LEEWAY_SECONDS", raising=False)
    return monkeypatch


def load() -> Settings:
    # _env_file=None: never read the real .env in tests.
    return Settings(_env_file=None)


def test_reads_values_from_env(jwt_env):
    settings = load()
    assert settings.jwt_secret.get_secret_value() == JWT_VARS["JWT_SECRET"]
    assert settings.jwt_issuer == "METANOIA"
    assert settings.jwt_audience == "METANOIA.Client"


def test_leeway_defaults_to_300(jwt_env):
    assert load().jwt_leeway_seconds == 300


def test_leeway_can_be_changed(jwt_env):
    jwt_env.setenv("JWT_LEEWAY_SECONDS", "60")
    assert load().jwt_leeway_seconds == 60


def test_negative_leeway_is_rejected(jwt_env):
    jwt_env.setenv("JWT_LEEWAY_SECONDS", "-1")
    with pytest.raises(ValidationError):
        load()


def test_missing_secret_fails(jwt_env):
    jwt_env.delenv("JWT_SECRET")
    with pytest.raises(ValidationError):
        load()


def test_secret_is_hidden_when_printed(jwt_env):
    settings = load()
    assert JWT_VARS["JWT_SECRET"] not in repr(settings)
    assert JWT_VARS["JWT_SECRET"] not in str(settings)
