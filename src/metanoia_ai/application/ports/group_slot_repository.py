"""FR-25: what the AI reads about one Work Group task for the caller. Read only."""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol
from uuid import UUID

from metanoia_ai.domain.group_slot_plan import GroupPart


@dataclass(frozen=True)
class GroupSlotTask:
    task_id: UUID
    title: str
    is_member: bool  # the caller is in the group (any role)
    group_archived: bool
    is_closed: bool  # Done / Skipped
    has_fixed_time: bool  # FixedStart set by the Leader
    deadline: datetime | None  # GroupTask.Deadline
    parts: tuple[GroupPart, ...]  # the task itself, or its subtasks
    group_deadline: date | None = None  # WorkGroup.Deadline (a date)


class GroupSlotRepository(Protocol):
    async def get_task(self, task_id: UUID, caller_id: UUID) -> GroupSlotTask | None:
        """None when the task does not exist."""
        ...
