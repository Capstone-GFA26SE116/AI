"""Daily briefing (FR-12) on the shared Supabase Postgres. Read-only.

Every query is limited to one user: Task has no UserId, so it joins Project;
GoogleCalendarEvent joins GoogleConnection. All reads run in one READ ONLY
transaction, closed before the use case starts the similar task searches.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from metanoia_ai.application.ports.briefing_repository import (
    BriefingData,
    BriefingTask,
    DomainK,
)
from metanoia_ai.domain.briefing import (
    FixedEntry,
    GoogleEntry,
    OpenSubtask,
    SlotEntry,
    SlotStatus,
)
from metanoia_ai.domain.models import FocusSessionRecord, WorkingHours
from metanoia_ai.infrastructure.db.learning_repository import SOURCE_FROM_DB
from metanoia_ai.infrastructure.db.schedule_repository import (
    OPEN_SUBTASK,
    STATUS_FROM_DB,
)

SLOT_STATUS_FROM_DB = {
    "Proposed": SlotStatus.PROPOSED,
    "Approved": SlotStatus.APPROVED,
    "Done": SlotStatus.DONE,
}
CONNECTED = "Active"
LEARNED = "Learned"

# The Active plan: at most one per user (UX_UserSubscription_OneActivePerUser).
GET_USER = text("""
    SELECT u."TimeZone", u."WorkDayStart", u."WorkDayEnd", u."WorksOnWeekend",
           u."DefaultBufferMinutes", u."ProductiveBlocks",
           (SELECT sp."Price"
            FROM "UserSubscription" us
            JOIN "SubscriptionPlan" sp ON sp."Id" = us."PlanId"
            WHERE us."UserId" = u."Id" AND us."Status" = 'Active'
            ORDER BY us."StartAt" DESC
            LIMIT 1) AS "PlanPrice"
    FROM "User" u
    WHERE u."Id" = :user_id AND u."DeletedAt" IS NULL
""")

# Candidate tasks (plan §2 step 3): Flexible, open, project not closed.
OPEN_TASK_FILTER = """
    p."UserId" = :user_id
      AND t."TaskType" = 'Flexible'
      AND t."Status" IN ('ToDo', 'Scheduled', 'InProgress')
      AND p."Status" NOT IN ('Completed', 'Archived')
"""
OPEN_TASKS = f"""
    FROM "Task" t
    JOIN "Project" p ON p."Id" = t."ProjectId"
    WHERE {OPEN_TASK_FILTER}
"""

GET_TASKS = text(f"""
    SELECT t."Id", t."Title", p."Name" AS "ProjectName", t."DomainId",
           d."Name" AS "DomainName", t."Status", t."Deadline", t."EstimatedMinutes"
    FROM "Task" t
    JOIN "Project" p ON p."Id" = t."ProjectId"
    JOIN "Domain" d ON d."Id" = t."DomainId"
    WHERE {OPEN_TASK_FILTER}
""")

GET_SUBTASKS = text(f"""
    SELECT st."Id", st."TaskId", st."EstimatedMinutes", st."Status"
    FROM "SubTask" st
    WHERE st."TaskId" IN (SELECT t."Id" {OPEN_TASKS})
    ORDER BY st."TaskId", st."OrderIndex"
""")

GET_SESSIONS = text(f"""
    SELECT fs."TaskId", fs."SubTaskId", fs."StartedAt", fs."EndedAt",
           fs."CaptureMethod", fs."IsConfirmed", fs."RemainingMinutes"
    FROM "FocusSession" fs
    WHERE fs."TaskId" IN (SELECT t."Id" {OPEN_TASKS})
    ORDER BY fs."StartedAt"
""")

GET_K_BIAS = text("""
    SELECT "DomainId", "KBias", "SampleCount"
    FROM "UserDomainProfile"
    WHERE "UserId" = :user_id
""")

GET_HOURS = text("""
    SELECT "HourOfDay", "Score", "Source"
    FROM "UserCircadianProfile"
    WHERE "UserId" = :user_id
""")

# A subtask slot shows "Task: Subtask", like FR-10.
GET_SLOTS = text("""
    SELECT s."TaskId", s."StartTime", s."EndTime", s."Status",
           CASE WHEN st."Title" IS NULL THEN t."Title"
                ELSE t."Title" || ': ' || st."Title" END AS "Title"
    FROM "ScheduleSlot" s
    JOIN "Task" t ON t."Id" = s."TaskId"
    JOIN "Project" p ON p."Id" = t."ProjectId"
    LEFT JOIN "SubTask" st ON st."Id" = s."SubTaskId"
    WHERE p."UserId" = :user_id
      AND s."Status" IN ('Proposed', 'Approved', 'Done')
      AND s."EndTime" > :day_start
      AND s."StartTime" < :day_end
""")

GET_FIXED_TASKS = text("""
    SELECT t."Id", t."Title", t."FixedStart", t."FixedEnd", t."Status"
    FROM "Task" t
    JOIN "Project" p ON p."Id" = t."ProjectId"
    WHERE p."UserId" = :user_id
      AND t."TaskType" = 'Fixed'
      AND t."FixedEnd" > :day_start
      AND t."FixedStart" < :window_end
""")

# >= on EndTime: a zero-length event at the window start still counts.
GET_CALENDAR_EVENTS = text("""
    SELECT e."Title", e."StartTime", e."EndTime", e."IsAllDay", e."IsCancelled",
           e."IsCreatedByMetanoia"
    FROM "GoogleCalendarEvent" e
    JOIN "GoogleConnection" c ON c."Id" = e."GoogleConnectionId"
    WHERE c."UserId" = :user_id
      AND e."EndTime" >= :day_start
      AND e."StartTime" < :window_end
""")

GET_CONNECTION_STATUS = text("""
    SELECT "Status"
    FROM "GoogleConnection"
    WHERE "UserId" = :user_id
""")


class SqlBriefingRepository:
    """BriefingRepository on Postgres. One instance per session."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @asynccontextmanager
    async def _read_only(self) -> AsyncIterator[None]:
        if self.session.in_transaction():
            await self.session.commit()
        async with self.session.begin():
            await self.session.execute(text("SET TRANSACTION READ ONLY"))
            yield

    async def get_briefing_data(
        self,
        user_id: UUID,
        day_start: datetime,
        day_end: datetime,
        window_end: datetime,
    ) -> BriefingData | None:
        async with self._read_only():
            return await self._load(user_id, day_start, day_end, window_end)

    async def _load(
        self,
        user_id: UUID,
        day_start: datetime,
        day_end: datetime,
        window_end: datetime,
    ) -> BriefingData | None:
        user = {"user_id": user_id}
        execute = self.session.execute

        row = (await execute(GET_USER, user)).one_or_none()
        if row is None:
            return None
        hours = WorkingHours(
            time_zone=ZoneInfo(row.TimeZone),
            day_start=row.WorkDayStart,
            day_end=row.WorkDayEnd,
            works_on_weekend=row.WorksOnWeekend,
            buffer_minutes=row.DefaultBufferMinutes,
        )

        has_subtasks: set[UUID] = set()
        open_subtasks: dict[UUID, list[OpenSubtask]] = {}
        for s in await execute(GET_SUBTASKS, user):
            has_subtasks.add(s.TaskId)
            if s.Status == OPEN_SUBTASK:
                open_subtasks.setdefault(s.TaskId, []).append(
                    OpenSubtask(s.Id, s.EstimatedMinutes)
                )
        sessions: dict[UUID, list[FocusSessionRecord]] = {}
        for s in await execute(GET_SESSIONS, user):
            sessions.setdefault(s.TaskId, []).append(
                FocusSessionRecord(
                    started_at=s.StartedAt,
                    ended_at=s.EndedAt,
                    source=SOURCE_FROM_DB[s.CaptureMethod],
                    is_confirmed=s.IsConfirmed,
                    subtask_id=s.SubTaskId,
                    remaining_minutes=s.RemainingMinutes,
                )
            )
        tasks = tuple(
            BriefingTask(
                task_id=t.Id,
                title=t.Title,
                project_name=t.ProjectName,
                domain_id=t.DomainId,
                domain_name=t.DomainName,
                status=STATUS_FROM_DB[t.Status],
                deadline=t.Deadline,
                estimated_minutes=t.EstimatedMinutes,
                has_subtasks=t.Id in has_subtasks,
                open_subtasks=tuple(open_subtasks.get(t.Id, ())),
                sessions=tuple(sessions.get(t.Id, ())),
            )
            for t in await execute(GET_TASKS, user)
        )

        scores = {}
        learned = False
        for r in await execute(GET_HOURS, user):
            scores[r.HourOfDay] = r.Score
            learned = learned or r.Source == LEARNED

        today = {**user, "day_start": day_start, "day_end": day_end}
        window = {**user, "day_start": day_start, "window_end": window_end}
        connection = (await execute(GET_CONNECTION_STATUS, user)).scalar_one_or_none()
        return BriefingData(
            hours=hours,
            # No Active plan (bad data) runs like Free (#7).
            is_free_plan=row.PlanPrice is None or row.PlanPrice == 0,
            tasks=tasks,
            k_by_domain={
                r.DomainId: DomainK(r.KBias, r.SampleCount)
                for r in await execute(GET_K_BIAS, user)
            },
            productive_scores=scores,
            has_productive_answer=bool(row.ProductiveBlocks) or learned,
            slots=tuple(
                SlotEntry(
                    r.TaskId,
                    r.Title,
                    r.StartTime,
                    r.EndTime,
                    SLOT_STATUS_FROM_DB[r.Status],
                )
                for r in await execute(GET_SLOTS, today)
            ),
            fixed_tasks=tuple(
                FixedEntry(
                    r.Id, r.Title, r.FixedStart, r.FixedEnd, r.Status == "Skipped"
                )
                for r in await execute(GET_FIXED_TASKS, window)
            ),
            events=tuple(
                GoogleEntry(
                    r.Title,
                    r.StartTime,
                    r.EndTime,
                    r.IsAllDay,
                    r.IsCancelled,
                    r.IsCreatedByMetanoia,
                )
                for r in await execute(GET_CALENDAR_EVENTS, window)
            ),
            calendar_connected=connection == CONNECTED,
        )
