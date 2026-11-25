"""FR-25 reads on the shared Supabase Postgres. Only SELECT.

The task row says whether the caller is in its group; the subtasks and the caller's
booked slots are read only for a member, and the slots only of the caller.
"""

from collections.abc import Iterable
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from metanoia_ai.application.ports.group_slot_repository import GroupSlotTask
from metanoia_ai.domain.group_slot_plan import GroupPart
from metanoia_ai.infrastructure.db.schedule_repository import ON_CALENDAR_SLOTS

OPEN = {"ToDo", "InProgress"}
CLOSED = {"Done", "Skipped"}
ARCHIVED = "Archived"

GET_TASK = text("""
    SELECT t."Id", t."Title", t."Status", t."AssigneeId", t."EstimatedMinutes",
           t."FixedStart", t."Deadline", g."Deadline" AS "GroupDeadline",
           g."Status" AS "GroupStatus",
           EXISTS (
               SELECT 1 FROM "GroupMember" m
               WHERE m."GroupId" = t."GroupId" AND m."UserId" = :caller_id
           ) AS "IsMember"
    FROM "GroupTask" t
    JOIN "WorkGroup" g ON g."Id" = t."GroupId"
    WHERE t."Id" = :task_id
""")

GET_SUBTASKS = text("""
    SELECT s."Id", s."Title", s."AssigneeId", s."Status", s."EstimatedMinutes",
           s."OrderIndex"
    FROM "GroupSubTask" s
    WHERE s."GroupTaskId" = :task_id
    ORDER BY s."OrderIndex", s."Id"
""")

# Minutes the caller already has on the calendar (or done) per subtask (NULL: the task).
GET_BOOKED = text(
    """
    SELECT s."GroupSubTaskId",
           CAST(SUM(EXTRACT(EPOCH FROM s."EndTime" - s."StartTime")) / 60 AS integer)
               AS "Minutes"
    FROM "ScheduleSlot" s
    WHERE s."GroupTaskId" = :task_id
      AND s."UserId" = :caller_id
      AND s."Status" IN """
    + ON_CALENDAR_SLOTS[:-1]
    + """, 'Done')
    GROUP BY s."GroupSubTaskId"
"""
)


def task_parts(
    task: Any, subtasks: Iterable[Any], booked: dict[UUID | None, int]
) -> tuple[GroupPart, ...]:
    """The task itself when it has no subtasks, else one part per subtask."""
    subs = list(subtasks)
    if not subs:
        return (
            GroupPart(
                None,
                task.Title,
                task.AssigneeId,
                task.Status in OPEN,
                task.EstimatedMinutes,
                booked.get(None, 0),
            ),
        )
    return tuple(
        GroupPart(
            s.Id,
            f"{task.Title}: {s.Title}",
            s.AssigneeId or task.AssigneeId,  # like Backend EffectiveAssigneeId
            s.Status in OPEN,
            s.EstimatedMinutes,
            booked.get(s.Id, 0),
            s.OrderIndex,
        )
        for s in subs
    )


class SqlGroupSlotRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_task(self, task_id: UUID, caller_id: UUID) -> GroupSlotTask | None:
        params = {"task_id": task_id, "caller_id": caller_id}
        execute = self.session.execute
        t = (await execute(GET_TASK, params)).one_or_none()
        if t is None:
            return None
        parts: tuple[GroupPart, ...] = ()
        if t.IsMember:
            subtasks = (await execute(GET_SUBTASKS, {"task_id": task_id})).all()
            booked = {
                r.GroupSubTaskId: r.Minutes for r in await execute(GET_BOOKED, params)
            }
            parts = task_parts(t, subtasks, booked)
        return GroupSlotTask(
            task_id=t.Id,
            title=t.Title,
            is_member=t.IsMember,
            group_archived=t.GroupStatus == ARCHIVED,
            is_closed=t.Status in CLOSED,
            has_fixed_time=t.FixedStart is not None,
            deadline=t.Deadline,
            parts=parts,
            group_deadline=t.GroupDeadline,
        )
