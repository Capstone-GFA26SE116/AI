"""FR-19 background jobs in deps.py: they must never raise (AC2, plan #19)."""

import asyncio
import logging
from types import SimpleNamespace
from uuid import uuid4

from pydantic import SecretStr

from metanoia_ai.api.deps import (
    embed_memory,
    embed_waiting_memories,
    get_embedding_client,
)
from metanoia_ai.config import Settings
from metanoia_ai.infrastructure.llm.gemini_embedding_client import (
    GeminiEmbeddingClient,
)
from tests.unit.api.test_auth import make_settings

FAKE_KEY = "fake-gemini-key"


def embedding_settings() -> Settings:
    return make_settings().model_copy(
        update={
            "gemini_api_key": SecretStr(FAKE_KEY),
            "embedding_model": "gemini-embedding-2",
            "embedding_price_per_1m": 0.15,
        }
    )


class Calls:
    """A session factory that must not be reached, or that breaks."""

    def __init__(self, error: Exception | None = None) -> None:
        self.count = 0
        self.error = error

    def __call__(self):
        self.count += 1
        raise self.error or AssertionError("the DB should not be used")


def test_missing_settings_leave_the_memory_pending(caplog):
    factory = Calls()
    state = SimpleNamespace(session_factory=factory)

    with caplog.at_level(logging.WARNING):
        asyncio.run(embed_memory(state, make_settings(), uuid4(), uuid4()))

    assert factory.count == 0
    assert "GEMINI_API_KEY" in caplog.text
    assert "stays Pending" in caplog.text


def test_missing_settings_skip_the_startup_scan(caplog):
    factory = Calls()
    state = SimpleNamespace(session_factory=factory)

    with caplog.at_level(logging.WARNING):
        asyncio.run(embed_waiting_memories(state, make_settings()))

    assert factory.count == 0
    assert "startup memory scan skipped" in caplog.text


def test_database_error_is_logged_not_raised(caplog):
    factory = Calls(ConnectionError("db down"))
    state = SimpleNamespace(session_factory=factory)

    asyncio.run(embed_memory(state, embedding_settings(), uuid4(), uuid4()))
    asyncio.run(embed_waiting_memories(state, embedding_settings()))

    assert factory.count == 2
    assert "Embedding memory" in caplog.text
    assert "Startup memory scan failed" in caplog.text


def test_embedding_client_is_made_once_without_showing_the_key():
    state = SimpleNamespace()
    first = get_embedding_client(state, embedding_settings())
    second = get_embedding_client(state, embedding_settings())

    assert first is second
    assert isinstance(first, GeminiEmbeddingClient)
    assert (first.model, first.dimensions) == ("gemini-embedding-2", 1536)
    assert FAKE_KEY not in repr(first)
