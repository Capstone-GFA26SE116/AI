"""Read-only data the voice assistant (FR-22) gets from the Backend. Only GET.

Every call passes on the user's own `Authorization` header, so the Backend checks
ownership itself. The schedule is not here: it is read from the DB like FR-12 (plan §5, D).
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol
from uuid import UUID


class BackendReadError(Exception):
    """The Backend failed, was too slow or answered something unexpected (#13)."""


class BackendNotFoundError(Exception):
    """No such item for this user (the Backend answered 404 or 403)."""


@dataclass(frozen=True)
class RunningFocus:
    """GET /api/focus/current."""

    task_title: str
    started_at: datetime
    elapsed_seconds: int
    is_overrun: bool  # the task already took longer than planned


@dataclass(frozen=True)
class ProjectSummary:
    """One item of GET /api/projects."""

    id: UUID
    name: str
    status: str  # Active / OnHold / Completed (Archived ones are not listed)


@dataclass(frozen=True)
class ProjectProgress:
    """GET /api/projects/{id}/progress."""

    name: str
    deadline: date | None
    total_tasks: int
    completed_tasks: int
    progress_percent: float


class BackendReader(Protocol):
    async def current_focus(self, auth_header: str) -> RunningFocus | None:
        """The running timer, or None when nothing is being timed."""
        ...

    async def projects(self, auth_header: str) -> list[ProjectSummary]:
        """The user's projects that are not archived."""
        ...

    async def project_progress(
        self, project_id: UUID, auth_header: str
    ) -> ProjectProgress:
        """Raises BackendNotFoundError when the project is not the user's."""
        ...
