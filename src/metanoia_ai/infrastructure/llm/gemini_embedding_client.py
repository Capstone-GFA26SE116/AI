"""EmbeddingClient on Google Gemini (google-genai SDK). FR-19."""

from typing import Any

from google import genai
from google.genai import types
from pydantic import SecretStr

from metanoia_ai.application.ports.embedding_client import (
    EmbeddingError,
    EmbeddingResponse,
)
from metanoia_ai.infrastructure.llm.gemini_client import MIN_TIMEOUT_SECONDS

DEFAULT_TIMEOUT_SECONDS = MIN_TIMEOUT_SECONDS


def build_config(dimensions: int, timeout_seconds: float) -> types.EmbedContentConfig:
    """Ask for vectors of the configured size, and stop waiting after the timeout."""
    return types.EmbedContentConfig(
        output_dimensionality=dimensions,
        http_options=types.HttpOptions(timeout=int(timeout_seconds * 1000)),
    )


def to_response(response: types.EmbedContentResponse, model: str) -> EmbeddingResponse:
    """The single vector of the answer. No vector → EmbeddingError.

    Gemini only sometimes reports a token count; None lets the caller estimate (plan #10).
    """
    embedding = response.embeddings[0] if response.embeddings else None
    if embedding is None or not embedding.values:
        raise EmbeddingError("Gemini returned no embedding", model=model)
    statistics = embedding.statistics
    token_count = statistics.token_count if statistics else None
    return EmbeddingResponse(
        vector=list(embedding.values),
        model=model,
        input_tokens=round(token_count) if token_count is not None else None,
    )


class GeminiEmbeddingClient:
    """One instance per app. Holds the API key; never logs or shows it."""

    def __init__(
        self,
        api_key: SecretStr,
        model: str,
        dimensions: int,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        client: Any = None,  # tests pass a stand-in; real code builds genai.Client
    ) -> None:
        if timeout_seconds < MIN_TIMEOUT_SECONDS:
            raise ValueError(
                f"Gemini needs a timeout of at least {MIN_TIMEOUT_SECONDS} s"
            )
        self.model = model
        self.dimensions = dimensions
        self.timeout_seconds = timeout_seconds
        self.client = client or genai.Client(api_key=api_key.get_secret_value())

    async def embed(self, text: str) -> EmbeddingResponse:
        """Send the memory text as stored: it already has its `title: … | text: …` prefix (#26)."""
        try:
            response = await self.client.aio.models.embed_content(
                model=self.model,
                contents=text,
                config=build_config(self.dimensions, self.timeout_seconds),
            )
        except Exception as error:
            # Only the error type: provider messages could echo request details.
            raise EmbeddingError(
                f"Gemini embedding failed: {type(error).__name__}", model=self.model
            ) from error
        return to_response(response, self.model)
