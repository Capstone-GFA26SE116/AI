"""FR-19 background jobs in deps.py: they must never raise (AC2, plan #19)."""

import asyncio
import logging
from types import SimpleNamespace
from uuid import uuid4

from pydantic import SecretStr

from metanoia_ai.api.deps import (
    embed_memory,
    embed_waiting_memories,
    get_chat_llm_client,
    get_embedding_client,
    get_find_similar_tasks,
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


# --- FR-09 wiring ---


def test_similar_search_without_embedding_settings_uses_no_client(caplog):
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    with caplog.at_level(logging.WARNING):
        use_case = get_find_similar_tasks(request, None, make_settings())
    assert use_case.client is None  # text-only search (plan #6)
    assert "text only" in caplog.text


def test_similar_search_with_embedding_settings_has_a_client():
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    use_case = get_find_similar_tasks(request, None, embedding_settings())
    assert isinstance(use_case.client, GeminiEmbeddingClient)
    assert use_case.branch_limit == 20


# --- FR-08a wiring ---


def test_chat_uses_its_own_gemini_client_with_a_longer_wait():
    settings = make_settings().model_copy(
        update={
            "gemini_api_key": SecretStr(FAKE_KEY),
            "llm_model": "gemini-flash-lite",
            "llm_input_price_per_1m": 0.1,
            "llm_output_price_per_1m": 0.4,
        }
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    chat_client = get_chat_llm_client(request, settings)
    assert chat_client.timeout_seconds == 20  # plan #31
    assert get_chat_llm_client(request, settings) is chat_client  # made once
    assert not hasattr(request.app.state, "llm_client")  # FR-10 client untouched
