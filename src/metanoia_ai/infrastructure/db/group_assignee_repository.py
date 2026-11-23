"""FR-23: read the Work Group tables (Backend PR #15). Read only; no schema change."""

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from metanoia_ai.application.ports.group_assignee_repository import (
    GroupMember,
    GroupTaskInfo,
    GroupTaskStatus,
    GroupWork,
)
from metanoia_ai.domain.assignee_ranking import GroupRole
from metanoia_ai.domain.models import TimeRange

ROLE_FROM_DB = {
    "Leader": GroupRole.LEADER,
    "Member": GroupRole.MEMBER,
    "Viewer": GroupRole.VIEWER,
}
STATUS_FROM_DB = {
    "ToDo": GroupTaskStatus.TODO,
    "InProgress": GroupTaskStatus.IN_PROGRESS,
    "Done": GroupTaskStatus.DONE,
    "Skipped": GroupTaskStatus.SKIPPED,
}

# The task, its group and the caller's role (NULL when not a member).
GET_TASK = text("""
    SELECT t."Id", t."GroupId", g."Status" AS "GroupStatus", m."Role" AS "CallerRole",
           t."Title", t."Status", t."Deadline", t."EstimatedMinutes",
           t."FixedStart", t."FixedEnd", t."AssigneeId",
           (SELECT SUM(s."EstimatedMinutes") FROM "GroupSubTask" s
             WHERE s."GroupTaskId" = t."Id"
               AND s."Status" IN ('ToDo', 'InProgress')) AS "OpenSubTaskMinutes"
    FROM "GroupTask" t
    JOIN "WorkGroup" g ON g."Id" = t."GroupId"
    LEFT JOIN "GroupMember" m ON m."GroupId" = t."GroupId" AND m."UserId" = :caller_id
    WHERE t."Id" = :task_id
""")

# Leaders and Members whose account is not deleted, Leaders first.
GET_MEMBERS = text("""
    SELECT m."UserId", m."Role"
    FROM "GroupMember" m
    JOIN "User" u ON u."Id" = m."UserId"
    WHERE m."GroupId" = :group_id
      AND m."Role" IN ('Leader', 'Member')
      AND u."DeletedAt" IS NULL
    ORDER BY CASE m."Role" WHEN 'Leader' THEN 0 ELSE 1 END, m."JoinedAt", m."UserId"
    LIMIT :limit
""")

# Open group work given to the users in Active groups: tasks without subtasks,
# and open subtasks (their task's deadline and fixed time).
GET_GROUP_WORK = text("""
    SELECT t."AssigneeId" AS "UserId", t."Id" AS "TaskId",
           t."EstimatedMinutes" AS "Minutes", t."Deadline", t."FixedStart", t."FixedEnd"
    FROM "GroupTask" t
    JOIN "WorkGroup" g ON g."Id" = t."GroupId"
    WHERE t."AssigneeId" = ANY(CAST(:user_ids AS uuid[]))
      AND t."Id" <> :exclude_task_id
      AND g."Status" = 'Active'
      AND t."Status" IN ('ToDo', 'InProgress')
      AND NOT EXISTS (SELECT 1 FROM "GroupSubTask" s WHERE s."GroupTaskId" = t."Id")
    UNION ALL
    SELECT s."AssigneeId", t."Id",
           s."EstimatedMinutes", t."Deadline", t."FixedStart", t."FixedEnd"
    FROM "GroupSubTask" s
    JOIN "GroupTask" t ON t."Id" = s."GroupTaskId"
    JOIN "WorkGroup" g ON g."Id" = t."GroupId"
    WHERE s."AssigneeId" = ANY(CAST(:user_ids AS uuid[]))
      AND t."Id" <> :exclude_task_id
      AND g."Status" = 'Active'
      AND t."Status" IN ('ToDo', 'InProgress')
      AND s."Status" IN ('ToDo', 'InProgress')
""")


class SqlGroupAssigneeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        # Reads before this point opened a transaction automatically; end it
        # so the writes run in a fresh one that commits or rolls back as a whole.
        if self.session.in_transaction():
            await self.session.commit()
        async with self.session.begin():
            yield

    async def get_task(self, task_id: UUID, caller_id: UUID) -> GroupTaskInfo | None:
        params = {"task_id": task_id, "caller_id": caller_id}
        row = (await self.session.execute(GET_TASK, params)).one_or_none()
        return None if row is None else task_from_row(row)

    async def get_members(self, group_id: UUID, limit: int) -> list[GroupMember]:
        params = {"group_id": group_id, "limit": limit}
        rows = await self.session.execute(GET_MEMBERS, params)
        return [GroupMember(r.UserId, ROLE_FROM_DB[r.Role]) for r in rows]

    async def get_group_work(
        self,
        user_ids: Sequence[UUID],
        window_start: datetime,
        window_end: datetime,
        exclude_task_id: UUID,
    ) -> dict[UUID, GroupWork]:
        if not user_ids:
            return {}
        params = {"user_ids": list(user_ids), "exclude_task_id": exclude_task_id}
        rows = await self.session.execute(GET_GROUP_WORK, params)
        return work_from_rows(rows, window_start, window_end)


def task_from_row(row: Any) -> GroupTaskInfo:
    estimate = row.EstimatedMinutes
    if estimate is None and row.OpenSubTaskMinutes:
        estimate = int(row.OpenSubTaskMinutes)
    fixed = None
    if row.FixedStart is not None and row.FixedEnd is not None:
        fixed = TimeRange(row.FixedStart, row.FixedEnd)
    return GroupTaskInfo(
        task_id=row.Id,
        group_id=row.GroupId,
        group_archived=row.GroupStatus != "Active",
        caller_role=ROLE_FROM_DB.get(row.CallerRole) if row.CallerRole else None,
        title=row.Title,
        status=STATUS_FROM_DB[row.Status],
        deadline=row.Deadline,
        estimate_minutes=estimate,
        fixed_time=fixed,
        assignee_id=row.AssigneeId,
    )


def work_from_rows(
    rows: Any, window_start: datetime, window_end: datetime
) -> dict[UUID, GroupWork]:
    """Fixed times in the window (once per user and task) and open minutes due in it.

    Work with a fixed time is busy time, so its minutes are not counted twice.
    Work without a deadline, or due after the window, is not counted.
    """
    fixed: dict[UUID, dict[UUID, TimeRange]] = {}
    minutes: dict[UUID, int] = {}
    for r in rows:
        if r.FixedStart is not None and r.FixedEnd is not None:
            if r.FixedStart < window_end and window_start < r.FixedEnd:
                fixed.setdefault(r.UserId, {})[r.TaskId] = TimeRange(
                    r.FixedStart, r.FixedEnd
                )
            continue
        if r.Deadline is not None and r.Deadline <= window_end and r.Minutes:
            minutes[r.UserId] = minutes.get(r.UserId, 0) + r.Minutes
    users = set(fixed) | set(minutes)
    return {
        u: GroupWork(
            fixed_times=tuple(sorted(fixed.get(u, {}).values(), key=lambda t: t.start)),
            open_minutes=minutes.get(u, 0),
        )
        for u in users
    }
