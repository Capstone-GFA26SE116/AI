"""FR-22d reads on the shared Supabase Postgres. Only SELECT.

Every query is limited to one user (Task has no UserId, so it joins Project;
a group slot carries its UserId). Same slot rules as FR-16b (`overrun_repository.py`).
"""

from datetime import datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import TextClause, text
from sqlalchemy.ext.asyncio import AsyncSession

from metanoia_ai.application.ports.reshuffle_repository import (
    CalendarData,
    CalendarSlot,
    FixedTask,
    GoogleEvent,
)
from metanoia_ai.domain.models import WorkingHours
from metanoia_ai.infrastructure.db.overrun_repository import FIXED_TASK, STATE_FROM_DB
from metanoia_ai.infrastructure.db.schedule_repository import (
    GET_USER,
    GROUP_SLOT_OF_USER,
)

FINISHED_TASK = ("Done", "Skipped")

_PERSONAL_SLOTS = """
    SELECT s."Id", s."StartTime", s."EndTime", s."Status",
           t."Title", t."TaskType", t."Deadline", t."Status" AS "TaskStatus",
           s."TaskId", s."SubTaskId",
           EXISTS (
               SELECT 1 FROM "FocusSession" fs WHERE fs."ScheduleSlotId" = s."Id"
           ) AS "Started",
           FALSE AS "IsGroup"
    FROM "ScheduleSlot" s
    JOIN "Task" t ON t."Id" = s."TaskId"
    JOIN "Project" p ON p."Id" = t."ProjectId"
    WHERE p."UserId" = :user_id
"""

_GROUP_SLOTS = (
    """
    SELECT s."Id", s."StartTime", s."EndTime", s."Status",
           gt."Title", CAST(NULL AS varchar) AS "TaskType", gt."Deadline",
           CAST(NULL AS varchar) AS "TaskStatus",
           CAST(NULL AS uuid) AS "TaskId", CAST(NULL AS uuid) AS "SubTaskId",
           EXISTS (
               SELECT 1 FROM "GroupFocusSession" gf WHERE gf."ScheduleSlotId" = s."Id"
           ) AS "Started",
           TRUE AS "IsGroup"
    FROM "ScheduleSlot" s
    JOIN "GroupTask" gt ON gt."Id" = s."GroupTaskId"
    WHERE """
    + GROUP_SLOT_OF_USER
)


def _both_kinds(condition: str) -> TextClause:
    return text(
        _PERSONAL_SLOTS + condition + "    UNION ALL" + _GROUP_SLOTS + condition
    )


GET_SLOT = _both_kinds("""
      AND s."Id" = :slot_id
""")

GET_SLOTS = _both_kinds("""
      AND s."EndTime" > :window_start
      AND s."StartTime" < :window_end
""")

GET_FIXED_TASKS = text("""
    SELECT t."Id", t."Title", t."FixedStart", t."FixedEnd", t."Status",
           p."ClientId" IS NOT NULL AS "WithClient"
    FROM "Task" t
    JOIN "Project" p ON p."Id" = t."ProjectId"
    WHERE p."UserId" = :user_id
      AND t."TaskType" = 'Fixed'
      AND t."FixedEnd" > :window_start
      AND t."FixedStart" < :window_end
""")

GET_EVENTS = text("""
    SELECT e."Title", e."StartTime", e."EndTime", e."IsAllDay", e."IsCancelled",
           e."IsCreatedByMetanoia"
    FROM "GoogleCalendarEvent" e
    JOIN "GoogleConnection" c ON c."Id" = e."GoogleConnectionId"
    WHERE c."UserId" = :user_id
      AND e."EndTime" > :window_start
      AND e."StartTime" < :window_end
""")

# Plan #7: where the app puts the urgent task by default.
GET_DEFAULT_PROJECT = text("""
    SELECT "Id" FROM "Project"
    WHERE "UserId" = :user_id AND "IsDefault"
    ORDER BY "CreatedAt"
    LIMIT 1
""")

GET_RECENT_DOMAIN = text("""
    SELECT t."DomainId"
    FROM "Task" t
    JOIN "Project" p ON p."Id" = t."ProjectId"
    WHERE p."UserId" = :user_id
    ORDER BY t."CreatedAt" DESC
    LIMIT 1
""")


def slot_row(r: Any) -> CalendarSlot:
    is_fixed = r.TaskType == FIXED_TASK
    return CalendarSlot(
        slot_id=r.Id,
        title=r.Title,
        start=r.StartTime,
        end=r.EndTime,
        state=STATE_FROM_DB[r.Status],
        is_fixed=is_fixed,
        deadline=None if is_fixed else r.Deadline,
        started=r.Started,
        is_group=r.IsGroup,
        task_id=r.TaskId,
        subtask_id=r.SubTaskId,
        task_finished=r.TaskStatus in FINISHED_TASK,
    )


class SqlReshuffleRepository:
    """ReshuffleRepository on Postgres. One instance per request (one session)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_calendar(
        self,
        user_id: UUID,
        window_start: datetime,
        window_end: datetime,
        slot_id: UUID | None = None,
    ) -> CalendarData | None:
        user = {"user_id": user_id}
        window = {**user, "window_start": window_start, "window_end": window_end}
        execute = self.session.execute

        u = (await execute(GET_USER, user)).one_or_none()
        if u is None:
            return None
        target = None
        if slot_id is not None:
            found = (
                await execute(GET_SLOT, {**user, "slot_id": slot_id})
            ).one_or_none()
            target = None if found is None else slot_row(found)
        return CalendarData(
            hours=WorkingHours(
                time_zone=ZoneInfo(u.TimeZone),
                day_start=u.WorkDayStart,
                day_end=u.WorkDayEnd,
                works_on_weekend=u.WorksOnWeekend,
                buffer_minutes=u.DefaultBufferMinutes,
            ),
            slots=tuple(slot_row(r) for r in await execute(GET_SLOTS, window)),
            fixed_tasks=tuple(
                FixedTask(
                    r.Id,
                    r.Title,
                    r.FixedStart,
                    r.FixedEnd,
                    r.Status == "Skipped",
                    r.WithClient,
                )
                for r in await execute(GET_FIXED_TASKS, window)
            ),
            events=tuple(
                GoogleEvent(
                    r.Title or "",
                    r.StartTime,
                    r.EndTime,
                    r.IsAllDay,
                    r.IsCancelled,
                    r.IsCreatedByMetanoia,
                )
                for r in await execute(GET_EVENTS, window)
            ),
            target=target,
            default_project_id=(
                await execute(GET_DEFAULT_PROJECT, user)
            ).scalar_one_or_none(),
            recent_domain_id=(
                await execute(GET_RECENT_DOMAIN, user)
            ).scalar_one_or_none(),
        )
