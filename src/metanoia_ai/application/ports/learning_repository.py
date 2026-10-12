"""What K_bias learning (FR-17) needs from storage. The database code implements it."""

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from metanoia_ai.domain.models import (
    ActualTimeSource,
    ConfidenceTier,
    FocusSessionRecord,
    TaskType,
)


@dataclass(frozen=True)
class TaskForLearning:
    """The parts of a task needed to learn from it."""

    task_id: UUID
    domain_id: UUID
    task_type: TaskType
    is_done: bool
    estimated_minutes: int | None  # None for Fixed tasks
    sessions: tuple[FocusSessionRecord, ...]


@dataclass(frozen=True)
class DomainProfile:
    """Current K_bias of one user for one work domain."""

    k_bias: float
    sample_count: int


@dataclass(frozen=True)
class KBiasLogEntry:
    """One K_bias update, kept forever so any value can be recomputed."""

    task_id: UUID
    old_k: float
    new_k: float
    alpha: float
    estimated_minutes: int
    actual_minutes: int
    observed_ratio: float
    source: ActualTimeSource
    confidence: ConfidenceTier
    sample_index: int


class TaskAlreadyLearnedError(Exception):
    """A log row for this task already exists (it was learned before)."""


class LearningRepository(Protocol):
    """Storage for K_bias learning. Every read is limited to the given user."""

    def transaction(self) -> AbstractAsyncContextManager[None]:
        """All calls inside run in one DB transaction: all saved, or nothing."""
        ...

    async def get_task(self, user_id: UUID, task_id: UUID) -> TaskForLearning | None:
        """The task with its work sessions, or None if missing or not this user's."""
        ...

    async def get_log(self, user_id: UUID, task_id: UUID) -> KBiasLogEntry | None:
        """The earlier learning result for this user's task, if any."""
        ...

    async def lock_profile(
        self, user_id: UUID, domain_id: UUID, initial_k: float
    ) -> DomainProfile:
        """Get the profile (created with `initial_k` if missing) and lock it.

        The lock lasts until the transaction ends, so two requests for the
        same user and domain run one after the other.
        """
        ...

    async def save(self, user_id: UUID, domain_id: UUID, entry: KBiasLogEntry) -> None:
        """Update the profile (K = new_k, count = sample_index) and add the log.

        Raises TaskAlreadyLearnedError if the task already has a log row.
        """
        ...
