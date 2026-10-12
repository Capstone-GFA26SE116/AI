import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from metanoia_ai.api.main import app
from metanoia_ai.config import get_settings
from tests.unit.api.test_auth import USER_ID, make_settings, make_token


@pytest.fixture
def client():
    """Client without startup (no `with`), using the test settings."""
    app.dependency_overrides[get_settings] = make_settings
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def clean_settings(monkeypatch, tmp_path):
    """Run in an empty folder (no real .env) with a fresh settings cache."""
    monkeypatch.chdir(tmp_path)
    for name in ["DATABASE_URL", "JWT_SECRET", "JWT_ISSUER", "JWT_AUDIENCE"]:
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()


def test_health_needs_no_token(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_me_without_token_returns_401(client):
    assert client.get("/me").status_code == 401


def test_me_with_valid_token_returns_user_id(client):
    response = client.get("/me", headers={"Authorization": f"Bearer {make_token()}"})
    assert response.status_code == 200
    assert response.json() == {"user_id": str(USER_ID)}


def test_startup_succeeds_with_settings(clean_settings):
    clean_settings.setenv("DATABASE_URL", "postgresql://user:fake@localhost:5432/test")
    clean_settings.setenv("JWT_SECRET", "x" * 64)
    clean_settings.setenv("JWT_ISSUER", "METANOIA")
    clean_settings.setenv("JWT_AUDIENCE", "METANOIA.Client")
    with TestClient(app) as started:
        assert started.get("/health").status_code == 200


def test_startup_fails_without_secret(clean_settings):
    with pytest.raises(ValidationError), TestClient(app):
        pass
