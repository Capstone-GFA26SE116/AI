"""FR-19: turn waiting memory texts into vectors. Runs in the background.

Each DB step is a short transaction; the embedding call happens outside any
transaction, so no DB connection is held while waiting for Gemini (plan #17).
"""

import asyncio
import logging
import math
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from metanoia_ai.application.ports.embedding_client import (
    EmbeddingClient,
    EmbeddingError,
    EmbeddingResponse,
)
from metanoia_ai.application.ports.memory_repository import (
    MemoryRepository,
    PendingMemory,
)
from metanoia_ai.application.ports.quota_repository import (
    AIFeature,
    QuotaRepository,
    UsageLogEntry,
)

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3  # plan #16: 3 calls in total
WAIT_SECONDS = (1.0, 2.0)  # between call 1→2 and 2→3
STARTUP_SCAN_LIMIT = 100
CHARS_PER_TOKEN = 4  # plan #10: estimate when the API gives no token count
TOKENS_PER_PRICE_UNIT = Decimal(1_000_000)


class EmbedOutcome(StrEnum):
    DONE = "done"  # vector saved
    FAILED = "failed"  # 3 calls failed; row marked Failed
    TEXT_CHANGED = "text_changed"  # a newer text arrived meanwhile; nothing saved
    NOT_NEEDED = "not_needed"  # row missing, not this user's, or already Done


@dataclass(frozen=True)
class Attempt:
    """One embedding call, for AIUsageLog."""

    model: str
    input_tokens: int
    cost: Decimal
    latency_ms: int
    succeeded: bool


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


class EmbedPendingMemories:
    """Embed one memory (after a request) or the waiting ones (at startup)."""

    def __init__(
        self,
        memory_repo: MemoryRepository,
        quota_repo: QuotaRepository,
        client: EmbeddingClient,
        dimensions: int,
        price_per_1m: float,
        now: Callable[[], datetime],
        clock: Callable[[], float] = time.perf_counter,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.memory_repo = memory_repo
        self.quota_repo = quota_repo  # shares the memory repo's session
        self.client = client
        self.dimensions = dimensions
        self.price = Decimal(str(price_per_1m))
        self.now = now
        self.clock = clock
        self.sleep = sleep

    async def embed_waiting(
        self, limit: int = STARTUP_SCAN_LIMIT
    ) -> list[EmbedOutcome]:
        """Startup scan: Pending and Failed rows of all users, oldest first (plan #20)."""
        async with self.memory_repo.transaction():
            waiting = await self.memory_repo.list_waiting(limit)
        return [
            await self.embed_one(user_id, memory_id) for memory_id, user_id in waiting
        ]

    async def embed_one(self, user_id: UUID, memory_id: UUID) -> EmbedOutcome:
        async with self.memory_repo.transaction():
            pending = await self.memory_repo.get_pending(user_id, memory_id)
            subscription = await self.quota_repo.get_subscription(user_id, self.now())
        if pending is None:
            return EmbedOutcome.NOT_NEEDED

        response, attempts = await self._call_with_retries(pending)

        async with self.memory_repo.transaction():
            if response is not None:
                saved = await self.memory_repo.save_embedding(
                    user_id, memory_id, pending.content, response.vector, response.model
                )
                outcome = EmbedOutcome.DONE if saved else EmbedOutcome.TEXT_CHANGED
            else:
                marked = await self.memory_repo.mark_failed(
                    user_id, memory_id, pending.content
                )
                outcome = EmbedOutcome.FAILED if marked else EmbedOutcome.TEXT_CHANGED
            if subscription is None:
                # Plan #7: AIUsageLog needs a plan; still embed, only log here.
                logger.warning(
                    "User %s has no Active plan: AIUsageLog skipped", user_id
                )
            else:
                for attempt in attempts:
                    await self.quota_repo.log_usage(
                        self._usage(subscription.subscription_id, attempt)
                    )
        return outcome

    async def _call_with_retries(
        self, pending: PendingMemory
    ) -> tuple[EmbeddingResponse | None, Sequence[Attempt]]:
        attempts: list[Attempt] = []
        for number in range(1, MAX_ATTEMPTS + 1):
            started = self.clock()
            try:
                response = await self.client.embed(pending.content)
            except EmbeddingError as error:
                logger.warning("Embedding call %d failed: %s", number, error)
                attempts.append(self._attempt(self.client.model, 0, started, False))
            else:
                tokens = response.input_tokens or estimate_tokens(pending.content)
                ok = len(response.vector) == self.dimensions
                attempts.append(self._attempt(response.model, tokens, started, ok))
                if ok:
                    return response, attempts
                logger.warning(
                    "Embedding call %d gave %d dimensions, expected %d",
                    number,
                    len(response.vector),
                    self.dimensions,
                )
            if number < MAX_ATTEMPTS:
                await self.sleep(WAIT_SECONDS[number - 1])
        return None, attempts

    def _attempt(
        self, model: str, input_tokens: int, started: float, succeeded: bool
    ) -> Attempt:
        return Attempt(
            model=model,
            input_tokens=input_tokens,
            cost=input_tokens * self.price / TOKENS_PER_PRICE_UNIT,
            latency_ms=max(0, round((self.clock() - started) * 1000)),
            succeeded=succeeded,
        )

    @staticmethod
    def _usage(subscription_id: UUID, attempt: Attempt) -> UsageLogEntry:
        return UsageLogEntry(
            subscription_id=subscription_id,
            feature=AIFeature.EMBEDDING,
            model=attempt.model,
            input_tokens=attempt.input_tokens,
            output_tokens=0,
            cost=attempt.cost,
            latency_ms=attempt.latency_ms,
            succeeded=attempt.succeeded,
            quota_charged=False,  # plan #6: memory never uses the user's quota
        )
