"""Engine setup only. These tests never open a connection to a database."""

import asyncio

import pytest

from metanoia_ai.infrastructure.db.engine import (
    create_engine,
    create_session_factory,
    to_async_url,
)
from tests.unit.api.test_auth import make_settings

FAKE_PASSWORD = "fake-p%40ss"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            f"postgresql://u:{FAKE_PASSWORD}@host:5432/db",
            f"postgresql+asyncpg://u:{FAKE_PASSWORD}@host:5432/db",
        ),
        (
            "postgres://u:p@host/db",
            "postgresql+asyncpg://u:p@host/db",
        ),
        (
            "postgresql+asyncpg://u:p@host/db",
            "postgresql+asyncpg://u:p@host/db",
        ),
    ],
)
def test_to_async_url(url, expected):
    assert to_async_url(url) == expected


def test_bad_url_error_does_not_leak_the_password():
    with pytest.raises(ValueError) as error:
        to_async_url(f"mysql://u:{FAKE_PASSWORD}@host/db")
    assert FAKE_PASSWORD not in str(error.value)


def test_create_engine_uses_asyncpg_without_connecting():
    engine = create_engine(make_settings())
    try:
        assert engine.dialect.driver == "asyncpg"
        assert engine.url.host == "localhost"
    finally:
        asyncio.run(engine.dispose())


def test_session_factory_is_bound_to_the_engine():
    engine = create_engine(make_settings())
    try:
        factory = create_session_factory(engine)
        assert factory.kw["bind"] is engine
    finally:
        asyncio.run(engine.dispose())
