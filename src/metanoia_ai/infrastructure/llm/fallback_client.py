"""Gemini first; when it fails (quota, 503, timeout), the second provider. Plan LLM-fallback."""

import logging
from typing import Any

from metanoia_ai.application.ports.llm_client import LLMClient, LLMError, LLMResponse

logger = logging.getLogger(__name__)


class FallbackLLMClient:
    """Implements LLMClient. The callers keep their own retries and answer checks."""

    def __init__(self, primary: LLMClient, secondary: LLMClient) -> None:
        self.primary = primary
        self.secondary = secondary

    async def generate_json(
        self, prompt: str, response_schema: dict[str, Any]
    ) -> LLMResponse:
        try:
            return await self.primary.generate_json(prompt, response_schema)
        except LLMError as first:
            logger.warning("Primary LLM failed, using the fallback: %s", first)
            try:
                return await self.secondary.generate_json(prompt, response_schema)
            except LLMError as second:
                raise LLMError(
                    f"{first} / fallback: {second}", model=second.model or first.model
                ) from second
