import base64
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from metanoia_ai.api.auth import AuthError, decode_user_id, get_current_user_id
from metanoia_ai.config import Settings, get_settings

# Fake key, only for tests. Looks like the real one (base64-like characters).
SECRET = "test+only/key0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOP"
USER_ID = UUID("3f2a6c1e-8b4d-4e2a-9c7f-1a2b3c4d5e6f")


def make_settings(secret: str = SECRET) -> Settings:
    return Settings(
        _env_file=None,
        database_url="postgresql://user:fake@localhost:5432/test",
        jwt_secret=secret,
        jwt_issuer="METANOIA",
        jwt_audience="METANOIA.Client",
        jwt_leeway_seconds=300,
        backend_base_url="https://backend.example.test",
    )


SETTINGS = make_settings()


def make_token(key: str | bytes = SECRET, algorithm: str = "HS256", **changes) -> str:
    """Build a token like the Backend does; `changes` override or remove (None) claims."""
    claims = {
        "sub": str(USER_ID),
        "email": "user@example.com",
        "iss": "METANOIA",
        "aud": "METANOIA.Client",
        "exp": datetime.now(UTC) + timedelta(minutes=60),
        "jti": "abc",
    }
    claims.update(changes)
    claims = {name: value for name, value in claims.items() if value is not None}
    return jwt.encode(claims, key, algorithm=algorithm)


def minutes_ago(minutes: int) -> datetime:
    return datetime.now(UTC) - timedelta(minutes=minutes)


# --- valid tokens ---


def test_valid_token_returns_user_id():
    assert decode_user_id(make_token(), SETTINGS) == USER_ID


def test_expired_within_leeway_is_accepted():
    token = make_token(exp=minutes_ago(2))
    assert decode_user_id(token, SETTINGS) == USER_ID


# --- invalid tokens ---


@pytest.mark.parametrize(
    "token",
    [
        pytest.param(make_token(exp=minutes_ago(10)), id="expired"),
        pytest.param(make_token(key="another-key-" * 6), id="wrong-key"),
        pytest.param(make_token(iss="SOMEONE-ELSE"), id="wrong-issuer"),
        pytest.param(make_token(aud="Other.Client"), id="wrong-audience"),
        pytest.param(make_token(sub=None), id="missing-sub"),
        pytest.param(make_token(exp=None), id="missing-exp"),
        pytest.param(make_token(iss=None), id="missing-issuer"),
        pytest.param(make_token(aud=None), id="missing-audience"),
        pytest.param(make_token(sub="not-a-guid"), id="sub-not-guid"),
        pytest.param(make_token(algorithm="HS512"), id="other-algorithm"),
        pytest.param(make_token(key=None, algorithm="none"), id="alg-none"),
        pytest.param("not.a.token", id="garbage"),
        pytest.param("", id="empty"),
    ],
)
def test_invalid_token_raises(token):
    with pytest.raises(AuthError):
        decode_user_id(token, SETTINGS)


def test_key_is_utf8_not_base64():
    # Backend uses Encoding.UTF8.GetBytes(key). A token signed with the
    # base64-decoded bytes of the same string must be rejected.
    secret = base64.b64encode(b"x" * 48).decode()
    settings = make_settings(secret)

    assert decode_user_id(make_token(key=secret), settings) == USER_ID
    with pytest.raises(AuthError):
        decode_user_id(make_token(key=base64.b64decode(secret)), settings)


# --- get_current_user_id (FastAPI dependency) ---


@pytest.fixture
def client() -> TestClient:
    """A tiny app with one protected route, using the test settings."""
    app = FastAPI()

    @app.get("/protected")
    def protected(user_id: Annotated[UUID, Depends(get_current_user_id)]):
        return {"user_id": str(user_id)}

    app.dependency_overrides[get_settings] = make_settings
    return TestClient(app)


def assert_401(response) -> None:
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json() == {"detail": "Invalid or missing token"}


def test_valid_bearer_token_returns_user_id(client):
    response = client.get(
        "/protected", headers={"Authorization": f"Bearer {make_token()}"}
    )
    assert response.status_code == 200
    assert response.json() == {"user_id": str(USER_ID)}


def test_missing_header_returns_401(client):
    assert_401(client.get("/protected"))


@pytest.mark.parametrize(
    "header",
    [
        pytest.param(f"Basic {make_token()}", id="wrong-scheme"),
        pytest.param(make_token(), id="no-scheme"),
        pytest.param("Bearer ", id="empty-token"),
        pytest.param(f"Bearer {make_token(exp=minutes_ago(10))}", id="expired"),
        pytest.param(f"Bearer {make_token(key='another-key-' * 6)}", id="wrong-key"),
    ],
)
def test_bad_header_returns_401(client, header):
    assert_401(client.get("/protected", headers={"Authorization": header}))
