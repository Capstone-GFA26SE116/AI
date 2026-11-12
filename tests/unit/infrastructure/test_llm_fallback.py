"""The second LLM provider and the Gemini → fallback switch. No network: httpx.MockTransport."""

import asyncio
import json
import logging

import httpx
import pytest
from pydantic import SecretStr

from metanoia_ai.api.deps import with_fallback
from metanoia_ai.application.ports.llm_client import LLMError, LLMResponse
from metanoia_ai.config import FallbackLlmConfig
from metanoia_ai.infrastructure.llm.fallback_client import FallbackLLMClient
from metanoia_ai.infrastructure.llm.openai_compatible_client import (
    OpenAICompatibleClient,
)

KEY = "fake-groq-key"
SCHEMA = {"type": "object", "properties": {"reasons": {"type": "array"}}}


def client(handler) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        "https://llm.test/v1/",
        SecretStr(KEY),
        "llama-test",
        timeout_seconds=10,
        transport=httpx.MockTransport(handler),
    )


def answer(content: str | None = '{"reasons": []}') -> dict:
    return {
        "model": "llama-test-0001",
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 120, "completion_tokens": 30},
    }


def test_sends_json_request_and_reads_answer():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=answer())

    result = asyncio.run(client(handler).generate_json("Write reasons.", SCHEMA))

    assert result == LLMResponse('{"reasons": []}', "llama-test-0001", 120, 30)
    request = seen[0]
    assert str(request.url) == "https://llm.test/v1/chat/completions"
    assert request.headers["Authorization"] == f"Bearer {KEY}"
    body = json.loads(request.content)
    assert body["model"] == "llama-test"
    assert body["response_format"] == {"type": "json_object"}
    assert json.dumps(SCHEMA) in body["messages"][0]["content"]
    assert body["messages"][1] == {"role": "user", "content": "Write reasons."}


def test_missing_usage_counts_zero():
    body = answer()
    del body["usage"]
    result = asyncio.run(
        client(lambda _: httpx.Response(200, json=body)).generate_json("x", SCHEMA)
    )
    assert (result.input_tokens, result.output_tokens) == (0, 0)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(429, json={"error": {"message": f"bad key {KEY}"}}),
        httpx.Response(500, text="oops"),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"choices": []}),
    ],
)
def test_failures_become_llm_error_without_the_key(response, caplog):
    caplog.set_level(logging.DEBUG)
    with pytest.raises(LLMError) as error:
        asyncio.run(client(lambda _: response).generate_json("x", SCHEMA))
    assert error.value.model == "llama-test"
    assert KEY not in str(error.value)
    assert KEY not in caplog.text


def test_network_error_becomes_llm_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow", request=request)

    with pytest.raises(LLMError, match="ConnectTimeout"):
        asyncio.run(client(handler).generate_json("x", SCHEMA))


class Stub:
    def __init__(self, result: LLMResponse | LLMError) -> None:
        self.result = result
        self.calls = 0

    async def generate_json(self, prompt, response_schema) -> LLMResponse:
        self.calls += 1
        if isinstance(self.result, LLMError):
            raise self.result
        return self.result


GEMINI = LLMResponse("{}", "gemini-test", 10, 5)
GROQ = LLMResponse("{}", "llama-test", 10, 5)


def test_uses_primary_when_it_works():
    secondary = Stub(GROQ)
    result = asyncio.run(
        FallbackLLMClient(Stub(GEMINI), secondary).generate_json("x", {})
    )
    assert result.model == "gemini-test"
    assert secondary.calls == 0


def test_switches_when_primary_fails():
    result = asyncio.run(
        FallbackLLMClient(
            Stub(LLMError("Gemini call failed: 429", model="gemini-test")), Stub(GROQ)
        ).generate_json("x", {})
    )
    assert result.model == "llama-test"  # AIUsageLog shows which model answered


def test_both_fail_gives_one_llm_error():
    with pytest.raises(LLMError) as error:
        asyncio.run(
            FallbackLLMClient(
                Stub(LLMError("Gemini call failed: 429", model="gemini-test")),
                Stub(LLMError("Fallback LLM answered HTTP 503", model="llama-test")),
            ).generate_json("x", {})
        )
    assert "429" in str(error.value) and "503" in str(error.value)
    assert error.value.model == "llama-test"


class FakeSettings:
    def __init__(self, fallback: FallbackLlmConfig | None) -> None:
        self.fallback = fallback

    def fallback_llm(self) -> FallbackLlmConfig | None:
        return self.fallback


def test_with_fallback_off_keeps_gemini_alone():
    primary = Stub(GEMINI)
    assert with_fallback(FakeSettings(None), primary) is primary


def test_with_fallback_on_wraps_gemini():
    config = FallbackLlmConfig("https://llm.test/v1", "llama-test", SecretStr(KEY))
    primary = Stub(GEMINI)
    wrapped = with_fallback(FakeSettings(config), primary, 20)
    assert isinstance(wrapped, FallbackLLMClient)
    assert wrapped.primary is primary
    assert wrapped.secondary.model == "llama-test"
    assert wrapped.secondary.timeout == 20
