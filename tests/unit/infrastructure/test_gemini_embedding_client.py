"""GeminiEmbeddingClient data conversion only. No network: the SDK client is a stand-in."""

import asyncio
from types import SimpleNamespace

import pytest
from google.genai import types
from pydantic import SecretStr

from metanoia_ai.application.ports.embedding_client import (
    EmbeddingClient,
    EmbeddingError,
)
from metanoia_ai.infrastructure.llm.gemini_embedding_client import (
    GeminiEmbeddingClient,
    build_config,
    to_response,
)

FAKE_KEY = SecretStr("fake-gemini-key")
TEXT = "title: Landing page cho [client] | text: Coding. Done."


def gemini_answer(
    values: list[float] | None = (0.1, 0.2, 0.3),
    token_count: float | None = None,
    with_embedding: bool = True,
) -> types.EmbedContentResponse:
    statistics = (
        types.ContentEmbeddingStatistics(token_count=token_count)
        if token_count is not None
        else None
    )
    embeddings = (
        [
            types.ContentEmbedding(
                values=list(values) if values is not None else None,
                statistics=statistics,
            )
        ]
        if with_embedding
        else None
    )
    return types.EmbedContentResponse(embeddings=embeddings)


class StandInSDK:
    """Looks like genai.Client for `client.aio.models.embed_content`."""

    def __init__(self, result: types.EmbedContentResponse | Exception) -> None:
        self.result = result
        self.calls: list[dict] = []
        self.aio = SimpleNamespace(models=SimpleNamespace(embed_content=self._call))

    async def _call(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def make_client(sdk: StandInSDK, **kwargs) -> GeminiEmbeddingClient:
    return GeminiEmbeddingClient(
        FAKE_KEY, "gemini-embedding-2", dimensions=1536, client=sdk, **kwargs
    )


def test_client_matches_the_port():
    client: EmbeddingClient = make_client(StandInSDK(gemini_answer()))
    assert client.model == "gemini-embedding-2"


def test_config_asks_for_the_configured_dimensions_and_timeout():
    config = build_config(dimensions=1536, timeout_seconds=10)
    assert config.output_dimensionality == 1536
    assert config.http_options.timeout == 10_000  # milliseconds


def test_embed_sends_model_text_as_stored_and_config():
    sdk = StandInSDK(gemini_answer())
    response = asyncio.run(make_client(sdk, timeout_seconds=12).embed(TEXT))

    assert response.vector == [0.1, 0.2, 0.3]
    assert response.model == "gemini-embedding-2"
    (call,) = sdk.calls
    assert call["model"] == "gemini-embedding-2"
    assert call["contents"] == TEXT  # no second prefix (#26)
    assert call["config"].output_dimensionality == 1536
    assert call["config"].http_options.timeout == 12_000


def test_token_count_is_read_when_given():
    response = to_response(gemini_answer(token_count=17.0), model="m")
    assert response.input_tokens == 17


def test_missing_token_count_gives_none():
    # The use case then estimates from the text length (plan #10).
    assert to_response(gemini_answer(), model="m").input_tokens is None


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param(gemini_answer(with_embedding=False), id="no-embeddings"),
        pytest.param(gemini_answer(values=None), id="no-values"),
        pytest.param(gemini_answer(values=[]), id="empty-values"),
    ],
)
def test_answer_without_vector_is_an_error(answer):
    with pytest.raises(EmbeddingError) as error:
        to_response(answer, model="gemini-embedding-2")
    assert error.value.model == "gemini-embedding-2"


def test_sdk_error_becomes_embedding_error_without_details():
    secret_detail = "request failed for key fake-gemini-key"
    sdk = StandInSDK(TimeoutError(secret_detail))
    with pytest.raises(EmbeddingError) as error:
        asyncio.run(make_client(sdk).embed(TEXT))
    assert "TimeoutError" in str(error.value)
    assert "fake-gemini-key" not in str(error.value)
    assert error.value.model == "gemini-embedding-2"


def test_timeout_under_10_seconds_is_rejected():
    with pytest.raises(ValueError):
        make_client(StandInSDK(gemini_answer()), timeout_seconds=8)


def test_real_sdk_client_is_built_without_network():
    client = GeminiEmbeddingClient(FAKE_KEY, "gemini-embedding-2", dimensions=1536)
    assert client.client is not None
    assert "fake-gemini-key" not in repr(client)
