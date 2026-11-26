"""What FR-22d (urgent block, postpone, catch up) reads from storage. It writes nothing."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from metanoia_ai.domain.models import WorkingHours
from metanoia_ai.domain.overrun_plan import SlotState


@dataclass(frozen=True)
class CalendarSlot:
    """A slot of the user (personal or Work Group) with what decides whether it moves."""

    slot_id: UUID
    title: str  # the task title ("Task: Subtask" is not needed here)
    start: datetime
    end: datetime
    state: SlotState
    is_fixed: bool  # the task is Fixed
    deadline: datetime | None  # Flexible tasks only
    started: bool  # a timer was started on this slot
    is_group: bool = False
    task_id: UUID | None = None
    subtask_id: UUID | None = None
    task_finished: bool = False  # the task is Done / Skipped


@dataclass(frozen=True)
class FixedTask:
    task_id: UUID
    title: str
    start: datetime
    end: datetime
    is_skipped: bool
    with_client: bool  # its project has a client (plan #4)


@dataclass(frozen=True)
class GoogleEvent:
    title: str
    start: datetime
    end: datetime
    is_all_day: bool
    is_cancelled: bool
    is_created_by_metanoia: bool


@dataclass(frozen=True)
class CalendarData:
    hours: WorkingHours
    slots: tuple[CalendarSlot, ...]  # in the window
    fixed_tasks: tuple[FixedTask, ...]
    events: tuple[GoogleEvent, ...]
    target: CalendarSlot | None = None  # the asked slot (postpone), wherever it is
    default_project_id: UUID | None = None  # plan #7
    recent_domain_id: UUID | None = None  # plan #7


class ReshuffleRepository(Protocol):
    """Reads limited to one user. No write method on purpose (AC6)."""

    async def get_calendar(
        self,
        user_id: UUID,
        window_start: datetime,
        window_end: datetime,
        slot_id: UUID | None = None,
    ) -> CalendarData | None:
        """None when the user does not exist."""
        ...
