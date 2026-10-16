"""GeminiClient data conversion only. No network: the SDK client is a stand-in."""

import asyncio
import logging
from types import SimpleNamespace

import pytest
from google.genai import errors, types
from pydantic import SecretStr

from metanoia_ai.application.ports.llm_client import LLMError
from metanoia_ai.application.reason_writer import ReasonsAnswer
from metanoia_ai.infrastructure.llm.gemini_client import (
    GeminiClient,
    build_config,
    describe_error,
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
    config = build_config(SCHEMA, timeout_seconds=10)
    assert config.response_mime_type == "application/json"
    assert config.response_json_schema == SCHEMA
    assert config.http_options.timeout == 10_000  # milliseconds


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
    client = GeminiClient(FAKE_KEY, "gemini-flash-lite", timeout_seconds=12, client=sdk)
    response = asyncio.run(client.generate_json("the prompt", SCHEMA))
    assert response.input_tokens == 120
    (call,) = sdk.calls
    assert call["model"] == "gemini-flash-lite"
    assert call["contents"] == "the prompt"
    assert call["config"].http_options.timeout == 12_000


def test_default_timeout_is_the_gemini_minimum():
    client = GeminiClient(FAKE_KEY, "m", client=StandInSDK(gemini_answer()))
    assert client.timeout_seconds == 10


def test_timeout_under_10_seconds_is_rejected():
    # Gemini answers 400 "Minimum allowed deadline is 10s" (found in step 15).
    with pytest.raises(ValueError):
        GeminiClient(
            FAKE_KEY, "m", timeout_seconds=8, client=StandInSDK(gemini_answer())
        )


def test_sdk_error_becomes_llm_error_without_details():
    secret_detail = "request failed for key fake-gemini-key"
    sdk = StandInSDK(TimeoutError(secret_detail))
    client = GeminiClient(FAKE_KEY, "gemini-flash-lite", client=sdk)
    with pytest.raises(LLMError) as error:
        asyncio.run(client.generate_json("p", SCHEMA))
    assert "TimeoutError" in str(error.value)
    assert "fake-gemini-key" not in str(error.value)
    assert error.value.model == "gemini-flash-lite"


def google_error(code: int = 400, status: str = "INVALID_ARGUMENT") -> errors.APIError:
    """What the SDK raises when Google rejects a call; the message echoes the key."""
    body = {
        "error": {
            "code": code,
            "message": "API key not valid: fake-gemini-key",
            "status": status,
        }
    }
    return errors.ClientError(code, body)


def test_google_error_is_logged_with_code_and_status_only(caplog):
    sdk = StandInSDK(google_error())
    client = GeminiClient(FAKE_KEY, "gemini-flash-lite", client=sdk)
    with caplog.at_level(logging.WARNING), pytest.raises(LLMError) as error:
        asyncio.run(client.generate_json("p", SCHEMA))
    assert "ClientError 400 INVALID_ARGUMENT" in caplog.text
    assert "gemini-flash-lite" in caplog.text
    assert "ClientError 400 INVALID_ARGUMENT" in str(error.value)
    for leaked in ("fake-gemini-key", "API key not valid"):
        assert leaked not in caplog.text
        assert leaked not in str(error.value)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (google_error(429, "RESOURCE_EXHAUSTED"), "ClientError 429 RESOURCE_EXHAUSTED"),
        (TimeoutError("fake-gemini-key"), "TimeoutError"),
        (google_error(400, "not a status: fake-gemini-key"), "ClientError 400"),
    ],
)
def test_describe_error(error, expected):
    assert describe_error(error) == expected


def test_real_sdk_client_is_built_without_network():
    client = GeminiClient(FAKE_KEY, "gemini-flash-lite")
    assert client.client is not None
    assert "fake-gemini-key" not in repr(client)
    assert "fake-gemini-key" not in str(vars(client).get("model"))
