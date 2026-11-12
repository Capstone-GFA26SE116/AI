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
    session_id: UUID | None = None  # FR-22c: to stop it
    slot_id: UUID | None = None  # FR-22c: to add time to it (FR-16b)


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


@dataclass(frozen=True)
class PendingSession:
    """One item of GET /api/focus/pending-confirmation: a timer stopped by the system."""

    task_title: str
    system_minutes: int  # what the timer recorded, before the user confirms


@dataclass(frozen=True)
class UnreadNotice:
    """One item of GET /api/notifications?unreadOnly=true (newest first)."""

    title: str


@dataclass(frozen=True)
class PendingProposal:
    """GET /api/schedule/proposals/pending."""

    slot_starts: tuple[datetime, ...]  # timezone-aware, in time order
    is_feasible: bool  # False: not every deadline can be met
    missing_minutes: int | None
    proposal_id: UUID | None = None  # FR-22c: to approve it


@dataclass(frozen=True)
class MilestoneSummary:
    """One item of GET /api/projects/{id}/milestones."""

    name: str
    due_date: date | None
    status: str  # Planned / InProgress / Delivered / InRevision / Approved


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

    async def pending_confirmations(self, auth_header: str) -> list[PendingSession]:
        """Timer sessions the system stopped, waiting for the user to confirm."""
        ...

    async def unread_notifications(self, auth_header: str) -> list[UnreadNotice]:
        """Unread notifications, newest first."""
        ...

    async def pending_proposal(self, auth_header: str) -> PendingProposal | None:
        """The schedule proposal waiting for approval, or None."""
        ...

    async def milestones(
        self, project_id: UUID, auth_header: str
    ) -> list[MilestoneSummary]:
        """Raises BackendNotFoundError when the project is not the user's."""
        ...
