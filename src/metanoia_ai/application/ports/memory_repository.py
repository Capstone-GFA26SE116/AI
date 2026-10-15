"""What long-term memory (FR-19) needs from storage. The database code implements it."""

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from metanoia_ai.domain.memory_text import MemoryDraft, MemorySession
from metanoia_ai.domain.models import TaskType


@dataclass(frozen=True)
class TaskForMemory:
    """The parts of a task needed to write its memory."""

    task_id: UUID
    task_type: TaskType
    status: str  # Task.Status as in the DB: ToDo, Scheduled, InProgress, Done, Skipped
    title: str
    domain_name: str
    estimated_minutes: int | None  # None for Fixed tasks
    client_name: str | None  # None: the project has no client
    sessions: tuple[MemorySession, ...]


@dataclass(frozen=True)
class PendingMemory:
    """A memory row waiting for its vector."""

    memory_id: UUID
    user_id: UUID
    content: str


class MemoryRepository(Protocol):
    """Storage for task memories. Every call is limited to the given user,
    except `list_waiting` (startup scan, plan #20)."""

    def transaction(self) -> AbstractAsyncContextManager[object]:
        """Calls inside run in one short DB transaction: all saved, or nothing."""
        ...

    async def get_task(self, user_id: UUID, task_id: UUID) -> TaskForMemory | None:
        """The task with its sessions, or None if missing or not this user's."""
        ...

    async def upsert(
        self, user_id: UUID, task_id: UUID, draft: MemoryDraft, model: str | None
    ) -> UUID:
        """Create the task's memory, or replace the text of the existing one.

        Either way: status Pending, old vector removed. Returns the memory id.
        """
        ...

    async def get_pending(self, user_id: UUID, memory_id: UUID) -> PendingMemory | None:
        """The memory if it still needs a vector (Pending or Failed), else None."""
        ...

    async def list_waiting(self, limit: int) -> list[tuple[UUID, UUID]]:
        """(memory id, user id) of Pending/Failed rows, oldest first. All users."""
        ...

    async def save_embedding(
        self,
        user_id: UUID,
        memory_id: UUID,
        content: str,
        vector: list[float],
        model: str,
    ) -> bool:
        """Store the vector and set Done, only if the text is still `content`.

        False: the text changed while embedding, so nothing was saved (plan #15).
        """
        ...

    async def mark_failed(self, user_id: UUID, memory_id: UUID, content: str) -> bool:
        """Set Failed, only if the text is still `content`. False otherwise."""
        ...
