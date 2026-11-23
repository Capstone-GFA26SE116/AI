"""What FR-23 (suggest who should do a group task) needs from storage.

Read only: the Work Group tables belong to the Backend (Backend PR #15).
"""

from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from metanoia_ai.domain.assignee_ranking import GroupRole
from metanoia_ai.domain.models import TimeRange


class GroupTaskStatus(StrEnum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class GroupTaskInfo:
    """The group task and the caller's place in its group."""

    task_id: UUID
    group_id: UUID
    group_archived: bool
    caller_role: GroupRole | None  # None: the caller is not a member
    title: str
    status: GroupTaskStatus
    deadline: datetime | None
    estimate_minutes: int | None  # the task's estimate, else its open subtasks'
    fixed_time: TimeRange | None
    assignee_id: UUID | None


@dataclass(frozen=True)
class GroupMember:
    user_id: UUID
    role: GroupRole


@dataclass(frozen=True)
class GroupWork:
    """A member's open group work in all their groups (without the asked task)."""

    fixed_times: tuple[TimeRange, ...]  # group tasks with a fixed time
    open_minutes: int  # estimates of open work due in the window, without a fixed time


class GroupAssigneeRepository(Protocol):
    def transaction(self) -> AbstractAsyncContextManager[None]:
        """The quota writes (charge + AIUsageLog) run in one DB transaction."""
        ...

    async def get_task(self, task_id: UUID, caller_id: UUID) -> GroupTaskInfo | None:
        """The group task, or None when it does not exist."""
        ...

    async def get_members(self, group_id: UUID, limit: int) -> list[GroupMember]:
        """Leaders and Members of the group (no Viewers, no deleted accounts)."""
        ...

    async def get_group_work(
        self,
        user_ids: Sequence[UUID],
        window_start: datetime,
        window_end: datetime,
        exclude_task_id: UUID,
    ) -> dict[UUID, GroupWork]:
        """Open group work given to each user, in every group; missing user = none."""
        ...
