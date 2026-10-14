"""What the application needs from an LLM provider. GeminiClient implements it."""

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class LLMResponse:
    """Raw answer of one call. The caller checks the text against its schema."""

    text: str
    model: str
    input_tokens: int
    output_tokens: int


class LLMError(Exception):
    """The call failed (network, timeout, provider error). Never contains the API key."""

    def __init__(self, message: str, model: str = "", input_tokens: int = 0) -> None:
        super().__init__(message)
        self.model = model
        self.input_tokens = input_tokens


class LLMClient(Protocol):
    async def generate_json(
        self, prompt: str, response_schema: dict[str, Any]
    ) -> LLMResponse:
        """Ask for a JSON answer that follows `response_schema` (JSON Schema).

        Raises LLMError when no answer comes back.
        """
        ...
