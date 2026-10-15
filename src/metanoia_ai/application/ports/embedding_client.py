"""What the application needs from an embedding provider (FR-19)."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class EmbeddingResponse:
    """One vector and what the call used."""

    vector: list[float]
    model: str
    input_tokens: int | None  # None: the provider did not say (caller estimates)


class EmbeddingError(Exception):
    """The call failed (network, timeout, provider error). Never contains the API key."""

    def __init__(self, message: str, model: str = "") -> None:
        super().__init__(message)
        self.model = model


class EmbeddingClient(Protocol):
    model: str

    async def embed(self, text: str) -> EmbeddingResponse:
        """Embed the memory text as given. Raises EmbeddingError when no vector comes back."""
        ...
