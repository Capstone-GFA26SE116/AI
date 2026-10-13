"""LLMClient on Google Gemini (google-genai SDK)."""

from typing import Any

from google import genai
from google.genai import types
from pydantic import SecretStr

from metanoia_ai.application.ports.llm_client import LLMError, LLMResponse

DEFAULT_TIMEOUT_SECONDS = 8.0


def build_config(
    response_schema: dict[str, Any], timeout_seconds: float
) -> types.GenerateContentConfig:
    """Ask for JSON that follows the schema, and stop waiting after the timeout."""
    return types.GenerateContentConfig(
        response_mime_type="application/json",
        response_json_schema=response_schema,
        http_options=types.HttpOptions(timeout=int(timeout_seconds * 1000)),
    )


def to_response(response: types.GenerateContentResponse, model: str) -> LLMResponse:
    """Text and token counts of a Gemini answer.

    Thinking tokens are billed as output, so they are added to the output count.
    An empty or blocked answer gives empty text; the caller treats it as a bad answer.
    """
    usage = response.usage_metadata
    input_tokens = (usage.prompt_token_count or 0) if usage else 0
    output_tokens = (
        (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)
        if usage
        else 0
    )
    return LLMResponse(
        text=response.text or "",
        model=response.model_version or model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


class GeminiClient:
    """One instance per app. Holds the API key; never logs or shows it."""

    def __init__(
        self,
        api_key: SecretStr,
        model: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        client: Any = None,  # tests pass a stand-in; real code builds genai.Client
    ) -> None:
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.client = client or genai.Client(api_key=api_key.get_secret_value())

    async def generate_json(
        self, prompt: str, response_schema: dict[str, Any]
    ) -> LLMResponse:
        try:
            response = await self.client.aio.models.generate_content(
                model=self.model,
                contents=prompt,
                config=build_config(response_schema, self.timeout_seconds),
            )
        except Exception as error:
            # Only the error type: provider messages could echo request details.
            raise LLMError(
                f"Gemini call failed: {type(error).__name__}", model=self.model
            ) from error
        return to_response(response, self.model)
