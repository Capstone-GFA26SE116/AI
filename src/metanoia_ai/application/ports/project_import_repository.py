"""Port: create projects from an imported file (FR-08c). The only place the AI writes `Project`."""

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import date
from typing import Protocol
from uuid import UUID
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class ProjectAllowance:
    """The plan's project limit and how many projects count against it (like the Backend)."""

    limit: int | None  # SubscriptionPlan.ProjectLimit; None = no limit
    used: int  # not default, not Archived


@dataclass(frozen=True)
class NewProject:
    name: str
    description: str | None
    start_date: date  # today, the user's time zone
    deadline: date | None


@dataclass(frozen=True)
class CreatedProject:
    project_id: UUID
    conversation_id: UUID
    name: str


class ProjectImportRepository(Protocol):
    def transaction(self) -> AbstractAsyncContextManager[object]: ...

    async def time_zone(self, user_id: UUID) -> ZoneInfo | None:
        """The user's time zone; None if the user does not exist."""
        ...

    async def allowance(self, user_id: UUID, lock: bool = False) -> ProjectAllowance:
        """lock=True (inside the creating transaction): lock the user's row first,
        so two imports at once cannot both pass the limit."""
        ...

    async def create_project(self, user_id: UUID, project: NewProject) -> UUID:
        """INSERT only: Active, not default, no client, document access off."""
        ...

    async def create_chat(
        self, user_id: UUID, project_id: UUID, title: str, first_message: str
    ) -> UUID:
        """A project chat (Scope Project, Open) with one user message. Returns the chat id."""
        ...
