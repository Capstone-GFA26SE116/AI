"""FR-19: save (or refresh) the memory of a finished or skipped task.

Only writes the row as Pending; the vector is made later in the background.
"""

import logging
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from metanoia_ai.application.ports.memory_repository import (
    MemoryRepository,
    TaskForMemory,
)
from metanoia_ai.application.use_cases.record_task_completion import (
    TaskNotFoundError,
)
from metanoia_ai.domain.memory_text import MemoryOutcome, build_memory_draft
from metanoia_ai.domain.models import TaskType

logger = logging.getLogger(__name__)

# Task.Status values that get a memory (plan #3).
OUTCOME_BY_STATUS = {"Done": MemoryOutcome.DONE, "Skipped": MemoryOutcome.SKIPPED}


class MemoryStatus(StrEnum):
    """What happened to the memory. Sent in the API as `memory`."""

    QUEUED = "queued"  # saved, waiting to be embedded
    SKIPPED = "skipped"  # this task does not get a memory
    FAILED = "failed"  # saving failed; K_bias learning is not affected


@dataclass(frozen=True)
class SaveMemoryResult:
    status: MemoryStatus
    memory_id: UUID | None = None  # set when queued: the row to embed next


class SaveTaskMemory:
    """Write the task's memory text and numbers. No LLM call, no quota."""

    def __init__(
        self, repo: MemoryRepository, model: str | None, max_chars: int
    ) -> None:
        self.repo = repo
        self.model = model  # None when EMBEDDING_MODEL is not set yet (plan #19)
        self.max_chars = max_chars

    async def execute(self, user_id: UUID, task_id: UUID) -> SaveMemoryResult:
        """Missing task → TaskNotFoundError. Any other error → FAILED, never raised."""
        try:
            async with self.repo.transaction():
                return await self._save(user_id, task_id)
        except TaskNotFoundError:
            raise
        except Exception:
            logger.exception("Saving the memory of task %s failed", task_id)
            return SaveMemoryResult(MemoryStatus.FAILED)

    async def _save(self, user_id: UUID, task_id: UUID) -> SaveMemoryResult:
        task = await self.repo.get_task(user_id, task_id)
        if task is None:
            raise TaskNotFoundError(str(task_id))

        outcome = OUTCOME_BY_STATUS.get(task.status)
        if task.task_type is TaskType.FIXED or outcome is None:
            return SaveMemoryResult(MemoryStatus.SKIPPED)

        draft = build_memory_draft(
            title=task.title,
            domain_name=task.domain_name,
            outcome=outcome,
            estimated_minutes=self._estimate(task),
            sessions=task.sessions,
            client_name=task.client_name,
            max_chars=self.max_chars,
        )
        memory_id = await self.repo.upsert(user_id, task_id, draft, self.model)
        return SaveMemoryResult(MemoryStatus.QUEUED, memory_id)

    @staticmethod
    def _estimate(task: TaskForMemory) -> int:
        """T_est of a Flexible task. The DB requires it, so None means bad data."""
        if task.estimated_minutes is None:
            raise ValueError(f"Flexible task {task.task_id} has no estimate")
        return task.estimated_minutes
