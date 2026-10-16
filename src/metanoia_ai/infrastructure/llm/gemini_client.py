"""LLMClient on Google Gemini (google-genai SDK)."""

import logging
import re
from typing import Any

from google import genai
from google.genai import types
from pydantic import SecretStr

from metanoia_ai.application.ports.llm_client import LLMError, LLMResponse

logger = logging.getLogger(__name__)

# Gemini rejects a request deadline under 10 s (400 INVALID_ARGUMENT).
MIN_TIMEOUT_SECONDS = 10.0
DEFAULT_TIMEOUT_SECONDS = MIN_TIMEOUT_SECONDS
# Google status names look like INVALID_ARGUMENT, PERMISSION_DENIED.
STATUS_NAME = re.compile(r"^[A-Z_]{1,40}$")


def describe_error(error: Exception) -> str:
    """Error type + HTTP code + Google status, e.g. "ClientError 400 INVALID_ARGUMENT".

    Never the message: provider messages can echo the request or the key.
    """
    parts = [type(error).__name__]
    code = getattr(error, "code", None)
    if isinstance(code, int):
        parts.append(str(code))
    status = getattr(error, "status", None)
    if isinstance(status, str) and STATUS_NAME.match(status):
        parts.append(status)
    return " ".join(parts)


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
        if timeout_seconds < MIN_TIMEOUT_SECONDS:
            raise ValueError(
                f"Gemini needs a timeout of at least {MIN_TIMEOUT_SECONDS} s"
            )
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
            reason = describe_error(error)
            logger.warning("Gemini call failed (model %s): %s", self.model, reason)
            raise LLMError(f"Gemini call failed: {reason}", model=self.model) from error
        return to_response(response, self.model)
