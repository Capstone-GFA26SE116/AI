"""What the daily briefing (FR-12) needs from storage. Read-only."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from metanoia_ai.application.ports.schedule_repository import TaskStatus
from metanoia_ai.domain.briefing import FixedEntry, GoogleEntry, OpenSubtask, SlotEntry
from metanoia_ai.domain.models import FocusSessionRecord, WorkingHours


@dataclass(frozen=True)
class BriefingTask:
    """An open Flexible task: ToDo / Scheduled / InProgress, project not closed."""

    task_id: UUID
    title: str
    project_name: str
    domain_id: UUID
    domain_name: str
    status: TaskStatus
    # Required by the DB for Flexible tasks; None only for bad data (skipped).
    deadline: datetime | None
    estimated_minutes: int | None
    has_subtasks: bool
    open_subtasks: tuple[OpenSubtask, ...]
    sessions: tuple[FocusSessionRecord, ...]


@dataclass(frozen=True)
class DomainK:
    """UserDomainProfile of one domain."""

    k_bias: Decimal
    sample_count: int


@dataclass(frozen=True)
class BriefingData:
    """Everything the briefing reads, loaded once per request."""

    hours: WorkingHours
    is_free_plan: bool  # no Active plan counts as Free (#7)
    tasks: tuple[BriefingTask, ...]
    k_by_domain: dict[UUID, DomainK]  # missing domain → no profile yet
    productive_scores: dict[int, Decimal]  # hour of day → score
    # ProductiveBlocks answered, or at least one Learned hour (#13).
    has_productive_answer: bool
    slots: tuple[SlotEntry, ...]  # today's Proposed / Approved / Done slots
    # From the start of today to the end of the risk window (today's list + busy time).
    fixed_tasks: tuple[FixedEntry, ...]
    events: tuple[GoogleEntry, ...]
    calendar_connected: bool  # a GoogleConnection with Status Active


class BriefingRepository(Protocol):
    """Every read is limited to the given user."""

    async def get_briefing_data(
        self,
        user_id: UUID,
        day_start: datetime,
        day_end: datetime,
        window_end: datetime,
    ) -> BriefingData | None:
        """None when the user does not exist (or is deleted).

        Slots: those overlapping [day_start, day_end). Fixed tasks and Google
        events: those overlapping [day_start, window_end).
        """
        ...
