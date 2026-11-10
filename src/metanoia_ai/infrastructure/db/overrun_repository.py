"""Overrun proposal (FR-16b) reads on the shared Supabase Postgres. Only SELECT.

Every query is limited to one user (Task has no UserId, so it joins Project).
User hours, Fixed tasks and Google events use the FR-10 queries.
"""

from datetime import datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from metanoia_ai.application.ports.overrun_repository import OverrunData, SlotRow
from metanoia_ai.domain.free_time import CalendarEvent, FixedTaskTime
from metanoia_ai.domain.models import WorkingHours
from metanoia_ai.domain.overrun_plan import SlotState
from metanoia_ai.infrastructure.db.schedule_repository import (
    GET_CALENDAR_EVENTS,
    GET_FIXED_TASKS,
    GET_USER,
)

STATE_FROM_DB = {
    "Proposed": SlotState.PROPOSED,
    "Approved": SlotState.APPROVED,
    "Due": SlotState.DUE,
    "InProgress": SlotState.IN_PROGRESS,
    "Overrun": SlotState.OVERRUN,
    "Done": SlotState.DONE,
    "Skipped": SlotState.SKIPPED,
    "Missed": SlotState.MISSED,
    "Postponed": SlotState.POSTPONED,
    "Cancelled": SlotState.CANCELLED,
}
FIXED_TASK = "Fixed"

_SLOT_COLUMNS = """
    SELECT s."Id", s."StartTime", s."EndTime", s."Status",
           t."Title", t."TaskType", t."Deadline",
           EXISTS (
               SELECT 1 FROM "FocusSession" fs WHERE fs."ScheduleSlotId" = s."Id"
           ) AS "Started"
    FROM "ScheduleSlot" s
    JOIN "Task" t ON t."Id" = s."TaskId"
    JOIN "Project" p ON p."Id" = t."ProjectId"
    WHERE p."UserId" = :user_id
"""

GET_SLOT = text(
    _SLOT_COLUMNS
    + """
      AND s."Id" = :slot_id
"""
)

GET_SLOTS = text(
    _SLOT_COLUMNS
    + """
      AND s."EndTime" > :window_start
      AND s."StartTime" < :window_end
"""
)


def slot_row(r: Any) -> SlotRow:
    is_fixed = r.TaskType == FIXED_TASK
    return SlotRow(
        slot_id=r.Id,
        title=r.Title,
        start=r.StartTime,
        end=r.EndTime,
        state=STATE_FROM_DB[r.Status],
        is_fixed=is_fixed,
        deadline=None if is_fixed else r.Deadline,
        started=r.Started,
    )


class SqlOverrunRepository:
    """OverrunRepository on Postgres. One instance per request (one session)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_overrun_data(
        self,
        user_id: UUID,
        slot_id: UUID,
        window_start: datetime,
        window_end: datetime,
    ) -> OverrunData | None:
        user = {"user_id": user_id}
        window = {**user, "window_start": window_start, "window_end": window_end}
        execute = self.session.execute

        u = (await execute(GET_USER, user)).one_or_none()
        if u is None:
            return None
        late = (await execute(GET_SLOT, {**user, "slot_id": slot_id})).one_or_none()
        return OverrunData(
            hours=WorkingHours(
                time_zone=ZoneInfo(u.TimeZone),
                day_start=u.WorkDayStart,
                day_end=u.WorkDayEnd,
                works_on_weekend=u.WorksOnWeekend,
                buffer_minutes=u.DefaultBufferMinutes,
            ),
            late=None if late is None else slot_row(late),
            slots=tuple(slot_row(r) for r in await execute(GET_SLOTS, window)),
            fixed_tasks=tuple(
                FixedTaskTime(r.FixedStart, r.FixedEnd, r.Status == "Skipped")
                for r in await execute(GET_FIXED_TASKS, window)
            ),
            calendar_events=tuple(
                CalendarEvent(
                    r.StartTime,
                    r.EndTime,
                    r.IsAllDay,
                    r.IsCancelled,
                    r.IsCreatedByMetanoia,
                )
                for r in await execute(GET_CALENDAR_EVENTS, window)
            ),
        )
