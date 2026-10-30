"""What the voice assistant (FR-22) reads from the DB itself. Read-only.

Names to match spoken words against (#6), and the schedule of a time range,
read like FR-12 (plan §5, D) so both screens tell the same schedule.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from metanoia_ai.domain.briefing import FixedEntry, GoogleEntry, SlotEntry
from metanoia_ai.domain.name_matching import NamedItem


@dataclass(frozen=True)
class ScheduleRows:
    """Slots (all states), Fixed tasks and Google events overlapping the range."""

    slots: tuple[SlotEntry, ...]
    fixed_tasks: tuple[FixedEntry, ...]
    events: tuple[GoogleEntry, ...]


class AssistantRepository(Protocol):
    """Every read is limited to the given user (Task through Project.UserId)."""

    async def get_time_zone(self, user_id: UUID) -> ZoneInfo | None:
        """None when the user does not exist (or is deleted)."""
        ...

    async def open_tasks(self, user_id: UUID) -> list[NamedItem]:
        """Flexible tasks still to do (ToDo / Scheduled / InProgress)."""
        ...

    async def projects(self, user_id: UUID) -> list[NamedItem]:
        """Projects that are not archived."""
        ...

    async def schedule(
        self, user_id: UUID, start: datetime, end: datetime
    ) -> ScheduleRows:
        """Everything that overlaps [start, end)."""
        ...
