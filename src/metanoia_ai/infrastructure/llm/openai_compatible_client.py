"""A second LLM provider with an OpenAI-style chat API (Groq by default). Plan LLM-fallback.

Only `httpx`, already used by the service: no new library. Asks for one JSON object
(`response_format = json_object`) and puts the JSON Schema in the system message; the
callers check the answer against their Pydantic schema as they do for Gemini.
The API key is never logged or put in an error.
"""

import json
import logging
from typing import Any

import httpx
from pydantic import SecretStr

from metanoia_ai.application.ports.llm_client import LLMError, LLMResponse

logger = logging.getLogger(__name__)

SYSTEM = "Answer with one JSON object only, no other text. It must follow this JSON Schema:\n"


class OpenAICompatibleClient:
    """One instance per app."""

    def __init__(
        self,
        base_url: str,
        api_key: SecretStr,
        model: str,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,  # tests only
    ) -> None:
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.api_key = api_key
        self.model = model
        self.timeout = timeout_seconds
        self.transport = transport

    async def generate_json(
        self, prompt: str, response_schema: dict[str, Any]
    ) -> LLMResponse:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM + json.dumps(response_schema)},
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
        }
        headers = {"Authorization": f"Bearer {self.api_key.get_secret_value()}"}
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout, transport=self.transport
            ) as client:
                response = await client.post(self.url, json=body, headers=headers)
        except httpx.HTTPError as error:
            reason = type(error).__name__
            logger.warning(
                "Fallback LLM call failed (model %s): %s", self.model, reason
            )
            raise LLMError(
                f"Fallback LLM call failed: {reason}", model=self.model
            ) from error
        if response.status_code != 200:
            logger.warning(
                "Fallback LLM answered HTTP %d (model %s)",
                response.status_code,
                self.model,
            )
            raise LLMError(
                f"Fallback LLM answered HTTP {response.status_code}", model=self.model
            )
        try:
            data = response.json()
            text = data["choices"][0]["message"]["content"] or ""
            usage = data.get("usage") or {}
            return LLMResponse(
                text=text,
                model=str(data.get("model") or self.model),
                input_tokens=int(usage.get("prompt_tokens", 0)),
                output_tokens=int(usage.get("completion_tokens", 0)),
            )
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise LLMError(
                "Fallback LLM sent an unexpected answer", model=self.model
            ) from error
