"""Shared by the task chat (FR-08a) and the project chat (FR-08b-1, plan #26).

- CheckedLLMCaller: one LLM call, checked, retried within a time budget.
- ChatQuota: the quota check before the call and the AIUsageLog rows after it.
"""

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from metanoia_ai.application.ports.llm_client import LLMClient, LLMError
from metanoia_ai.application.ports.quota_repository import (
    AIFeature,
    QuotaRepository,
    Subscription,
    UsageLogEntry,
)
from metanoia_ai.application.reason_writer import (
    DEFAULT_RETRY_BUDGET_SECONDS,
    TOKENS_PER_PRICE_UNIT,
    BadAnswerError,
    LLMCall,
)
from metanoia_ai.application.use_cases.propose_schedule import (
    NoSubscriptionError,
    QuotaExceededError,
)


@dataclass(frozen=True)
class CheckedResult[T]:
    value: T | None  # None: every try failed
    calls: tuple[LLMCall, ...]  # one AIUsageLog row each


class CheckedLLMCaller:
    """Call the LLM until `check` accepts the answer (FR-08a #10, FR-08b-1 #24).

    A bad answer or a failed call is retried up to `max_retries` times, but only
    while less than `retry_budget_seconds` has passed since the first try.
    """

    def __init__(
        self,
        llm: LLMClient,
        *,
        max_retries: int,
        input_price_per_1m: float,
        output_price_per_1m: float,
        retry_budget_seconds: float = DEFAULT_RETRY_BUDGET_SECONDS,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.llm = llm
        self.max_retries = max_retries
        self.retry_budget = retry_budget_seconds
        self.input_price = Decimal(str(input_price_per_1m))
        self.output_price = Decimal(str(output_price_per_1m))
        self.clock = clock

    async def call[T](
        self,
        prompt: str,
        schema: dict[str, Any],
        check: Callable[[str], T],
        retry_hint: Callable[[str], str] | None = None,
    ) -> CheckedResult[T]:
        """`check` turns the LLM text into a value or raises BadAnswerError.

        retry_hint (FR-08b-1 #30): after a bad answer, the next try sends the
        prompt plus retry_hint(reason). Without it the same prompt is sent again.
        """
        calls: list[LLMCall] = []
        next_prompt = prompt
        first_start: float | None = None
        for _ in range(1 + self.max_retries):
            started = self.clock()
            if first_start is None:
                first_start = started
            elif started - first_start >= self.retry_budget:
                break  # too slow already: give up rather than make the user wait
            try:
                response = await self.llm.generate_json(next_prompt, schema)
            except LLMError as error:
                calls.append(
                    self._call(error.model, error.input_tokens, 0, started, False)
                )
                continue
            try:
                value: T | None = check(response.text)
            except BadAnswerError as error:
                value = None
                if retry_hint is not None:
                    next_prompt = f"{prompt}\n\n{retry_hint(str(error))}"
            calls.append(
                self._call(
                    response.model,
                    response.input_tokens,
                    response.output_tokens,
                    started,
                    value is not None,
                )
            )
            if value is not None:
                return CheckedResult(value=value, calls=tuple(calls))
        return CheckedResult(value=None, calls=tuple(calls))

    def _call(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        started: float,
        succeeded: bool,
    ) -> LLMCall:
        cost = (
            input_tokens * self.input_price + output_tokens * self.output_price
        ) / TOKENS_PER_PRICE_UNIT
        return LLMCall(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=cost,
            latency_ms=max(0, round((self.clock() - started) * 1000)),
            succeeded=succeeded,
        )


class ChatQuota:
    """Quota for chat messages: check before the LLM, log every call after it."""

    def __init__(
        self,
        repo: QuotaRepository,
        *,
        now: Callable[[], datetime],
    ) -> None:
        self.repo = repo
        self.now = now

    async def check(self, user_id: UUID) -> Subscription:
        """The active plan with calls left. No plan → NoSubscriptionError;
        plan limit reached → QuotaExceededError. Nothing is charged."""
        subscription = await self.repo.get_subscription(user_id, self.now())
        if subscription is None:
            raise NoSubscriptionError(str(user_id))
        if not subscription.has_calls_left:
            raise QuotaExceededError("plan limit reached")
        return subscription

    async def log(
        self, subscription: Subscription, calls: Sequence[LLMCall], charged: bool
    ) -> None:
        """One AIUsageLog row per LLM call; only the good call is charged."""
        for call in calls:
            await self.repo.log_usage(
                UsageLogEntry(
                    subscription_id=subscription.subscription_id,
                    feature=AIFeature.CHAT,
                    model=call.model,
                    input_tokens=call.input_tokens,
                    output_tokens=call.output_tokens,
                    cost=call.cost,
                    latency_ms=call.latency_ms,
                    succeeded=call.succeeded,
                    quota_charged=charged and call.succeeded,
                )
            )
