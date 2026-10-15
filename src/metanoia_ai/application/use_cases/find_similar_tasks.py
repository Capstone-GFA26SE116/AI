"""FR-09: find the user's past tasks most similar to a task (hybrid search + RRF).

The user is waiting, so Gemini gets one try with a short time limit (plan #6, #14).
Without a vector the search still runs on text only.
"""

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID
from zoneinfo import ZoneInfo

from metanoia_ai.application.ports.embedding_client import (
    EmbeddingClient,
    EmbeddingError,
)
from metanoia_ai.application.ports.quota_repository import (
    AIFeature,
    QuotaRepository,
    UsageLogEntry,
)
from metanoia_ai.application.ports.similar_task_repository import (
    QueryTask,
    SimilarTaskRepository,
)
from metanoia_ai.application.use_cases.embed_pending_memories import (
    TOKENS_PER_PRICE_UNIT,
    estimate_tokens,
)
from metanoia_ai.application.use_cases.record_task_completion import (
    TaskNotFoundError,
)
from metanoia_ai.domain.models import TaskType
from metanoia_ai.domain.similar_tasks import (
    PastTask,
    SimilarSummary,
    SimilarTask,
    build_search_query,
    rrf_merge,
    search_terms,
    summarize_similar,
)

logger = logging.getLogger(__name__)


class SearchMode(StrEnum):
    HYBRID = "hybrid"  # vector + text
    TEXT_ONLY = "text_only"  # Gemini failed, timed out or is not configured
    NONE = "none"  # Fixed task: nothing searched (plan #7)


@dataclass(frozen=True)
class SimilarTasksResult:
    task_id: UUID
    search_mode: SearchMode
    time_zone: ZoneInfo  # the user's, for finished_at in the answer
    items: list[SimilarTask] = field(default_factory=list)
    summary: SimilarSummary = field(default_factory=lambda: SimilarSummary(0, 0, None))


class FindSimilarTasks:
    """No quota is used; the embedding call is logged in AIUsageLog (plan #5)."""

    def __init__(
        self,
        repo: SimilarTaskRepository,
        quota_repo: QuotaRepository,
        client: EmbeddingClient | None,  # None: embedding not configured
        dimensions: int,
        price_per_1m: float | None,
        min_similarity: float,
        rrf_k: int,
        branch_limit: int,
        embed_timeout_seconds: float,
        now: Callable[[], datetime],
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.repo = repo
        self.quota_repo = quota_repo  # shares the repo's session
        self.client = client
        self.dimensions = dimensions
        self.price = Decimal(str(price_per_1m or 0))
        self.min_similarity = min_similarity
        self.rrf_k = rrf_k
        self.branch_limit = branch_limit
        self.embed_timeout_seconds = embed_timeout_seconds
        self.now = now
        self.clock = clock

    async def execute(
        self, user_id: UUID, task_id: UUID, limit: int
    ) -> SimilarTasksResult:
        """Missing or other user's task → TaskNotFoundError. Gemini errors never raise."""
        async with self.repo.read_only():
            task = await self.repo.get_task(user_id, task_id)
        if task is None:
            raise TaskNotFoundError(str(task_id))
        if task.task_type is TaskType.FIXED:
            return SimilarTasksResult(task_id, SearchMode.NONE, task.time_zone)

        query = build_search_query(task.title, task.domain_name, task.client_name)
        vector = await self._embed(user_id, query)
        vector_found, text_found = await self._search(user_id, task, vector)

        items = rrf_merge(vector_found, text_found, k=self.rrf_k, limit=limit)
        mode = SearchMode.HYBRID if vector is not None else SearchMode.TEXT_ONLY
        return SimilarTasksResult(
            task_id, mode, task.time_zone, items, summarize_similar(items)
        )

    async def _embed(self, user_id: UUID, query: str) -> list[float] | None:
        """One call, outside any transaction. None when there is no usable vector."""
        if self.client is None:
            return None
        started = self.clock()
        vector: list[float] | None = None
        try:
            response = await asyncio.wait_for(
                self.client.embed(query), timeout=self.embed_timeout_seconds
            )
        except (EmbeddingError, TimeoutError) as error:
            logger.warning("Search embedding failed: %s", type(error).__name__)
            model, tokens = self.client.model, 0
        else:
            model = response.model
            tokens = response.input_tokens or estimate_tokens(query)
            if len(response.vector) == self.dimensions:
                vector = response.vector
            else:  # plan #15: same as a failed call
                logger.warning(
                    "Search embedding gave %d dimensions, expected %d",
                    len(response.vector),
                    self.dimensions,
                )
        latency_ms = max(0, round((self.clock() - started) * 1000))
        await self._log_usage(user_id, model, tokens, latency_ms, vector is not None)
        return vector

    async def _log_usage(
        self, user_id: UUID, model: str, tokens: int, latency_ms: int, succeeded: bool
    ) -> None:
        async with self.repo.transaction():
            subscription = await self.quota_repo.get_subscription(user_id, self.now())
            if subscription is None:
                # Plan #5: AIUsageLog needs a plan; the search still runs.
                logger.warning(
                    "User %s has no Active plan: AIUsageLog skipped", user_id
                )
                return
            await self.quota_repo.log_usage(
                UsageLogEntry(
                    subscription_id=subscription.subscription_id,
                    feature=AIFeature.EMBEDDING,
                    model=model,
                    input_tokens=tokens,
                    output_tokens=0,
                    cost=tokens * self.price / TOKENS_PER_PRICE_UNIT,
                    latency_ms=latency_ms,
                    succeeded=succeeded,
                    quota_charged=False,
                )
            )

    async def _search(
        self, user_id: UUID, task: QueryTask, vector: list[float] | None
    ) -> tuple[list[PastTask], list[PastTask]]:
        """Both branches in one read-only transaction. A branch with no input is skipped."""
        terms = search_terms(task.title, task.domain_name, task.client_name)
        vector_found: list[PastTask] = []
        text_found: list[PastTask] = []
        async with self.repo.read_only():
            if vector is not None:
                matches = await self.repo.vector_search(
                    user_id, task.task_id, vector, self.branch_limit
                )
                # Plan #4: drop weak matches before RRF.
                vector_found = [
                    m.task for m in matches if m.similarity >= self.min_similarity
                ]
            if terms is not None:
                text_found = await self.repo.text_search(
                    user_id, task.task_id, terms, self.branch_limit
                )
        return vector_found, text_found
