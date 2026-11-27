"""Persistence required to confirm an imported plan."""

from contextlib import AbstractAsyncContextManager
from typing import Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from metanoia_ai.application.ports.project_chat_repository import NewMilestone, NewTask
from metanoia_ai.application.ports.project_import_repository import (
    NewProject,
    ProjectAllowance,
)


class ExcelPlanRepository(Protocol):
    def transaction(self) -> AbstractAsyncContextManager[object]: ...

    async def time_zone(self, user_id: UUID) -> ZoneInfo | None: ...

    async def allowance(
        self, user_id: UUID, lock: bool = False
    ) -> ProjectAllowance: ...

    async def active_domains(self) -> dict[UUID, str]: ...

    async def existing_projects(self, user_id: UUID, ids: list[UUID]) -> set[UUID]: ...

    async def create_plan_project(
        self, user_id: UUID, project_id: UUID, project: NewProject
    ) -> None: ...

    async def add_milestone(
        self, user_id: UUID, project_id: UUID, milestone: NewMilestone
    ) -> UUID: ...

    async def add_task(
        self, user_id: UUID, project_id: UUID, task: NewTask
    ) -> UUID: ...
