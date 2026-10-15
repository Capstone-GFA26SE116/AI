"""What the similar task search (FR-09) needs from storage. The database code implements it."""

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from metanoia_ai.domain.models import TaskType
from metanoia_ai.domain.similar_tasks import PastTask


@dataclass(frozen=True)
class QueryTask:
    """The task the user asks about."""

    task_id: UUID
    task_type: TaskType
    title: str
    domain_name: str
    client_name: str | None  # None: the project has no client
    time_zone: ZoneInfo  # the user's, for finished_at in the answer


@dataclass(frozen=True)
class VectorMatch:
    """A vector search result and its cosine similarity (1 − cosine distance)."""

    task: PastTask
    similarity: float


class SimilarTaskRepository(Protocol):
    """Reads past task memories. Every call is limited to the given user and
    never returns the task being asked about."""

    def read_only(self) -> AbstractAsyncContextManager[object]:
        """Calls inside run in one read-only DB transaction."""
        ...

    def transaction(self) -> AbstractAsyncContextManager[object]:
        """A short write transaction (for AIUsageLog through the quota repository)."""
        ...

    async def get_task(self, user_id: UUID, task_id: UUID) -> QueryTask | None:
        """The task, or None if missing or not this user's."""
        ...

    async def vector_search(
        self, user_id: UUID, exclude_task_id: UUID, vector: list[float], limit: int
    ) -> list[VectorMatch]:
        """Embedded (Done) memories closest to the vector, most similar first."""
        ...

    async def text_search(
        self, user_id: UUID, exclude_task_id: UUID, terms: str, limit: int
    ) -> list[PastTask]:
        """Memories whose text matches any of the terms ("a | b"), best first.

        Includes memories still waiting for a vector (Pending/Failed, plan #13).
        """
        ...
