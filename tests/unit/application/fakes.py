"""In-memory stand-ins for the database, used by application tests."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from metanoia_ai.application.ports.learning_repository import (
    DomainProfile,
    KBiasLogEntry,
    TaskAlreadyLearnedError,
    TaskForLearning,
)


class FakeLearningRepository:
    """Behaves like the real repository, but keeps everything in dicts."""

    def __init__(self) -> None:
        # task id -> (owner id, task)
        self.tasks: dict[UUID, tuple[UUID, TaskForLearning]] = {}
        # (user id, domain id) -> profile
        self.profiles: dict[tuple[UUID, UUID], DomainProfile] = {}
        # task id -> log entry
        self.logs: dict[UUID, KBiasLogEntry] = {}
        # task id -> owner id of its log (the real DB finds it via the profile)
        self.log_owners: dict[UUID, UUID] = {}
        self.locked: list[tuple[UUID, UUID]] = []

    def add_task(self, owner_id: UUID, task: TaskForLearning) -> None:
        self.tasks[task.task_id] = (owner_id, task)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        # Roll back on error, like a real transaction.
        saved = (dict(self.profiles), dict(self.logs), dict(self.log_owners))
        try:
            yield
        except BaseException:
            self.profiles, self.logs, self.log_owners = saved
            raise

    async def get_task(self, user_id: UUID, task_id: UUID) -> TaskForLearning | None:
        owner_and_task = self.tasks.get(task_id)
        if owner_and_task is None or owner_and_task[0] != user_id:
            return None
        return owner_and_task[1]

    async def get_log(self, user_id: UUID, task_id: UUID) -> KBiasLogEntry | None:
        if self.log_owners.get(task_id) != user_id:
            return None
        return self.logs.get(task_id)

    async def lock_profile(
        self, user_id: UUID, domain_id: UUID, initial_k: float
    ) -> DomainProfile:
        self.locked.append((user_id, domain_id))
        return self.profiles.setdefault(
            (user_id, domain_id), DomainProfile(k_bias=initial_k, sample_count=0)
        )

    async def save(self, user_id: UUID, domain_id: UUID, entry: KBiasLogEntry) -> None:
        if entry.task_id in self.logs:
            raise TaskAlreadyLearnedError(str(entry.task_id))
        self.profiles[(user_id, domain_id)] = DomainProfile(
            k_bias=entry.new_k, sample_count=entry.sample_index
        )
        self.logs[entry.task_id] = entry
        self.log_owners[entry.task_id] = user_id
