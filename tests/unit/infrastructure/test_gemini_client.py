"""GeminiClient data conversion only. No network: the SDK client is a stand-in."""

import asyncio
from types import SimpleNamespace

import pytest
from google.genai import types
from pydantic import SecretStr

from metanoia_ai.application.ports.llm_client import LLMError
from metanoia_ai.application.reason_writer import ReasonsAnswer
from metanoia_ai.infrastructure.llm.gemini_client import (
    GeminiClient,
    build_config,
    to_response,
)

FAKE_KEY = SecretStr("fake-gemini-key")
SCHEMA = ReasonsAnswer.model_json_schema()


def gemini_answer(
    text: str | None = '{"reasons": []}',
    prompt_tokens: int | None = 120,
    output_tokens: int | None = 40,
    thinking_tokens: int | None = None,
    model_version: str | None = "gemini-test-001",
    with_usage: bool = True,
) -> types.GenerateContentResponse:
    parts = [types.Part(text=text)] if text is not None else []
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=parts))],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=prompt_tokens,
            candidates_token_count=output_tokens,
            thoughts_token_count=thinking_tokens,
        )
        if with_usage
        else None,
        model_version=model_version,
    )


class StandInSDK:
    """Looks like genai.Client for `client.aio.models.generate_content`."""

    def __init__(self, result: types.GenerateContentResponse | Exception) -> None:
        self.result = result
        self.calls: list[dict] = []
        self.aio = SimpleNamespace(models=SimpleNamespace(generate_content=self._call))

    async def _call(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_config_asks_for_json_with_schema_and_timeout():
    config = build_config(SCHEMA, timeout_seconds=8)
    assert config.response_mime_type == "application/json"
    assert config.response_json_schema == SCHEMA
    assert config.http_options.timeout == 8000  # milliseconds


def test_to_response_reads_text_and_tokens():
    response = to_response(gemini_answer(), model="configured-model")
    assert response.text == '{"reasons": []}'
    assert (response.input_tokens, response.output_tokens) == (120, 40)
    assert response.model == "gemini-test-001"  # the exact version Gemini used


def test_thinking_tokens_count_as_output():
    response = to_response(gemini_answer(thinking_tokens=25), model="m")
    assert response.output_tokens == 65


def test_missing_usage_and_version_fall_back():
    response = to_response(
        gemini_answer(with_usage=False, model_version=None), model="configured-model"
    )
    assert (response.input_tokens, response.output_tokens) == (0, 0)
    assert response.model == "configured-model"


def test_empty_answer_gives_empty_text():
    assert to_response(gemini_answer(text=None), model="m").text == ""


def test_generate_json_sends_model_prompt_and_config():
    sdk = StandInSDK(gemini_answer())
    client = GeminiClient(FAKE_KEY, "gemini-flash-lite", timeout_seconds=5, client=sdk)
    response = asyncio.run(client.generate_json("the prompt", SCHEMA))
    assert response.input_tokens == 120
    (call,) = sdk.calls
    assert call["model"] == "gemini-flash-lite"
    assert call["contents"] == "the prompt"
    assert call["config"].http_options.timeout == 5000


def test_sdk_error_becomes_llm_error_without_details():
    secret_detail = "request failed for key fake-gemini-key"
    sdk = StandInSDK(TimeoutError(secret_detail))
    client = GeminiClient(FAKE_KEY, "gemini-flash-lite", client=sdk)
    with pytest.raises(LLMError) as error:
        asyncio.run(client.generate_json("p", SCHEMA))
    assert "TimeoutError" in str(error.value)
    assert "fake-gemini-key" not in str(error.value)
    assert error.value.model == "gemini-flash-lite"


def test_real_sdk_client_is_built_without_network():
    client = GeminiClient(FAKE_KEY, "gemini-flash-lite")
    assert client.client is not None
    assert "fake-gemini-key" not in repr(client)
    assert "fake-gemini-key" not in str(vars(client).get("model"))
