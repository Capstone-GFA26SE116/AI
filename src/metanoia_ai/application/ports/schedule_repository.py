"""What the schedule proposal (FR-10) needs from storage. The database code implements it."""

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from metanoia_ai.domain.free_time import CalendarEvent, FixedTaskTime
from metanoia_ai.domain.models import (
    FocusSessionRecord,
    ScheduleWarning,
    TimeRange,
    WorkingHours,
)


class TaskStatus(StrEnum):
    TODO = "todo"
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class SubTaskToSchedule:
    subtask_id: UUID
    title: str
    estimated_minutes: int
    order_index: int
    is_open: bool  # False when Done or Skipped


@dataclass(frozen=True)
class TaskToSchedule:
    """A Flexible task of the user, with what is needed to size and place it."""

    task_id: UUID
    title: str
    domain_id: UUID
    status: TaskStatus
    deadline: datetime
    estimated_minutes: int
    has_approved_slot: bool
    subtasks: tuple[SubTaskToSchedule, ...]
    sessions: tuple[FocusSessionRecord, ...]


@dataclass(frozen=True)
class PlanningData:
    """Everything the planner reads, loaded once per request."""

    hours: WorkingHours
    tasks: tuple[TaskToSchedule, ...]  # Flexible, not Done/Skipped
    k_by_domain: dict[UUID, Decimal]  # missing domain → initial K
    productive_scores: dict[int, Decimal]  # hour of day → score
    fixed_tasks: tuple[FixedTaskTime, ...]
    calendar_events: tuple[CalendarEvent, ...]
    calendar_synced: bool  # False when the Google connection is Expired/Revoked
    approved_slots: tuple[TimeRange, ...]


@dataclass(frozen=True)
class ProposalWarning:
    """A warning and its text in the user's language, stored in ScheduleProposal.Warnings."""

    warning: ScheduleWarning
    message: str


@dataclass(frozen=True)
class NewSlot:
    slot_id: UUID
    task_id: UUID
    subtask_id: UUID | None
    start: datetime
    end: datetime
    proposed_minutes: int
    k_bias_used: Decimal | None
    explanation: str | None


@dataclass(frozen=True)
class NewProposal:
    proposal_id: UUID
    is_feasible: bool
    missing_minutes: int | None  # required when not feasible
    explanation: str
    warnings: tuple[ProposalWarning, ...]
    slots: tuple[NewSlot, ...]


class ScheduleRepository(Protocol):
    """Storage for schedule proposals. Every read is limited to the given user."""

    def transaction(self) -> AbstractAsyncContextManager[None]:
        """All writes inside (also the quota ones) run in one DB transaction."""
        ...

    async def get_planning_data(
        self, user_id: UUID, window_start: datetime, window_end: datetime
    ) -> PlanningData:
        """User settings, tasks and busy time that overlap the window."""
        ...

    async def save_proposal(self, user_id: UUID, proposal: NewProposal) -> None:
        """Replace the user's Pending proposal with this one.

        Old Pending proposals → Superseded, their Proposed slots → Cancelled;
        then the new proposal (Pending) and its slots (Proposed) are inserted.
        """
        ...
