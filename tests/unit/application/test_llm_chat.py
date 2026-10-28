"""The helpers shared by the task chat and the project chat (FR-08b-1 #26)."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from metanoia_ai.application.llm_chat import ChatQuota, CheckedLLMCaller
from metanoia_ai.application.ports.llm_client import LLMError
from metanoia_ai.application.ports.quota_repository import AIFeature, Subscription
from metanoia_ai.application.reason_writer import BadAnswerError, LLMCall
from metanoia_ai.application.use_cases.propose_schedule import (
    NoSubscriptionError,
    QuotaExceededError,
)
from tests.unit.application.fakes import FakeLLMClient, FakeQuotaRepository

NOW = datetime(2026, 10, 16, 9, 0, tzinfo=UTC)
SCHEMA = {"type": "object"}


def number(text: str) -> int:
    """A tiny check: the answer must be a whole number."""
    if not text.isdigit():
        raise BadAnswerError("not a number")
    return int(text)


class Clock:
    """Each read moves time forward by `step` seconds."""

    def __init__(self, step: float) -> None:
        self.now = 0.0
        self.step = step

    def __call__(self) -> float:
        self.now += self.step
        return self.now


def caller(llm, max_retries=1, budget=40.0, step=0.1) -> CheckedLLMCaller:
    return CheckedLLMCaller(
        llm,
        max_retries=max_retries,
        input_price_per_1m=0.1,
        output_price_per_1m=0.4,
        retry_budget_seconds=budget,
        clock=Clock(step),
    )


def run(c: CheckedLLMCaller, retry_hint=None):
    return asyncio.run(c.call("PROMPT", SCHEMA, number, retry_hint))


# --- CheckedLLMCaller ---


def test_good_first_answer():
    llm = FakeLLMClient("42")
    result = run(caller(llm))
    assert result.value == 42
    assert [c.succeeded for c in result.calls] == [True]
    assert llm.schemas == [SCHEMA]


def test_bad_then_good_is_retried_with_the_same_prompt_by_default():
    llm = FakeLLMClient("nope", "7")
    result = run(caller(llm))
    assert result.value == 7
    assert [c.succeeded for c in result.calls] == [False, True]
    assert llm.prompts == ["PROMPT", "PROMPT"]  # FR-08a behaviour


def test_retry_hint_is_added_after_a_bad_answer():
    llm = FakeLLMClient("nope", "7")
    run(caller(llm), retry_hint=lambda reason: f"Fix this: {reason}.")
    assert llm.prompts == ["PROMPT", "PROMPT\n\nFix this: not a number."]


def test_retry_hint_is_not_added_after_a_failed_call():
    llm = FakeLLMClient(LLMError("timeout", model="m"), "7")
    run(caller(llm), retry_hint=lambda reason: "HINT")
    assert llm.prompts == ["PROMPT", "PROMPT"]


def test_every_try_bad_gives_no_value():
    llm = FakeLLMClient("a", "b", "c")
    result = run(caller(llm, max_retries=1))
    assert result.value is None
    assert len(result.calls) == 2  # 1 try + 1 retry


def test_no_retry_after_the_time_budget():
    llm = FakeLLMClient("a", "7")
    result = run(caller(llm, budget=1.0, step=2.0))  # first try took too long
    assert result.value is None
    assert len(llm.prompts) == 1


def test_failed_call_is_logged_with_its_model_and_no_output():
    llm = FakeLLMClient(LLMError("down", model="gemini-x", input_tokens=10), "7")
    result = run(caller(llm))
    failed = result.calls[0]
    assert (failed.model, failed.input_tokens, failed.output_tokens) == (
        "gemini-x",
        10,
        0,
    )
    assert failed.succeeded is False


def test_cost_of_a_call():
    result = run(caller(FakeLLMClient("1")))
    # 100 in × 0.1 + 50 out × 0.4 per 1M tokens.
    assert result.calls[0].cost * 1_000_000 == 30


# --- ChatQuota ---


def quota(used=0, limit=50, add=True):
    repo = FakeQuotaRepository(now=NOW)
    user = uuid4()
    if add:
        plan = Subscription(uuid4(), price=0, ai_call_limit=limit, ai_calls_used=used)
        repo.add_subscription(user, plan, NOW - timedelta(days=1))
    return repo, user, ChatQuota(repo, now=lambda: NOW)


def test_quota_check_returns_the_plan():
    _, user, q = quota()
    assert asyncio.run(q.check(user)).ai_calls_used == 0


def test_no_plan():
    _, user, q = quota(add=False)
    with pytest.raises(NoSubscriptionError):
        asyncio.run(q.check(user))


def test_t3_plan_limit_reached():
    _, user, q = quota(used=50, limit=50)
    with pytest.raises(QuotaExceededError, match="plan limit reached"):
        asyncio.run(q.check(user))


def test_t2_no_daily_limit_while_the_plan_has_calls_left():
    _, user, q = quota(used=60, limit=300)
    subscription = asyncio.run(q.check(user))
    calls = [LLMCall("m", 1, 1, 0, 5, True)] * 60
    asyncio.run(q.log(subscription, calls, charged=True))  # 60 charged today
    assert asyncio.run(q.check(user)) == subscription


def test_log_charges_only_the_good_call():
    repo, user, q = quota()
    subscription = asyncio.run(q.check(user))
    calls = [LLMCall("m", 1, 0, 0, 5, False), LLMCall("m", 1, 1, 0, 5, True)]

    asyncio.run(q.log(subscription, calls, charged=True))

    assert [entry.quota_charged for _, _, entry in repo.logs] == [False, True]
    assert all(entry.feature is AIFeature.CHAT for _, _, entry in repo.logs)


def test_log_without_charge_marks_nothing_charged():
    repo, user, q = quota()
    subscription = asyncio.run(q.check(user))
    asyncio.run(q.log(subscription, [LLMCall("m", 1, 1, 0, 5, True)], charged=False))
    assert [entry.quota_charged for _, _, entry in repo.logs] == [False]
