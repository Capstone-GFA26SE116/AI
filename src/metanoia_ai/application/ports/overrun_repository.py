"""What the overrun proposal (FR-16b) reads from storage. It writes nothing."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from metanoia_ai.domain.free_time import CalendarEvent, FixedTaskTime
from metanoia_ai.domain.models import WorkingHours
from metanoia_ai.domain.overrun_plan import SlotState


@dataclass(frozen=True)
class SlotRow:
    """A slot of the user with what decides whether it may run over or move."""

    slot_id: UUID
    title: str  # the task title
    start: datetime
    end: datetime
    state: SlotState
    is_fixed: bool  # the task is Fixed
    deadline: datetime | None  # the task deadline (Flexible tasks only)
    started: bool  # a timer was started on this slot


@dataclass(frozen=True)
class OverrunData:
    hours: WorkingHours
    late: SlotRow | None  # None: no such slot for this user
    slots: tuple[SlotRow, ...]  # the user's slots that take time in the window
    fixed_tasks: tuple[FixedTaskTime, ...]
    calendar_events: tuple[CalendarEvent, ...]


class OverrunRepository(Protocol):
    """Reads limited to one user. No write method on purpose (AC5)."""

    async def get_overrun_data(
        self,
        user_id: UUID,
        slot_id: UUID,
        window_start: datetime,
        window_end: datetime,
    ) -> OverrunData | None:
        """None when the user does not exist."""
        ...
