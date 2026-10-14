"""Schedule proposals (FR-10) on the shared Supabase Postgres (tables owned by the Backend).

Every query is limited to one user: Task has no UserId, so it joins Project;
GoogleCalendarEvent joins GoogleConnection.
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from metanoia_ai.application.ports.schedule_repository import (
    NewProposal,
    PlanningData,
    SubTaskToSchedule,
    TaskStatus,
    TaskToSchedule,
)
from metanoia_ai.domain.free_time import CalendarEvent, FixedTaskTime
from metanoia_ai.domain.models import FocusSessionRecord, TimeRange, WorkingHours
from metanoia_ai.infrastructure.db.learning_repository import SOURCE_FROM_DB

STATUS_FROM_DB = {
    "ToDo": TaskStatus.TODO,
    "Scheduled": TaskStatus.SCHEDULED,
    "InProgress": TaskStatus.IN_PROGRESS,
    "Done": TaskStatus.DONE,
    "Skipped": TaskStatus.SKIPPED,
}
OPEN_SUBTASK = "ToDo"
# Google connection states that mean "events may be out of date".
UNSYNCED_CONNECTIONS = {"Expired", "Revoked"}

GET_USER = text("""
    SELECT "TimeZone", "WorkDayStart", "WorkDayEnd", "WorksOnWeekend",
           "DefaultBufferMinutes"
    FROM "User"
    WHERE "Id" = :user_id AND "DeletedAt" IS NULL
""")

# Flexible tasks that are not finished. The use case decides which to schedule.
GET_TASKS = text("""
    SELECT t."Id", t."Title", t."DomainId", t."Status", t."Deadline",
           t."EstimatedMinutes",
           EXISTS (
               SELECT 1 FROM "ScheduleSlot" s
               WHERE s."TaskId" = t."Id" AND s."Status" = 'Approved'
           ) AS "HasApprovedSlot"
    FROM "Task" t
    JOIN "Project" p ON p."Id" = t."ProjectId"
    WHERE p."UserId" = :user_id
      AND t."TaskType" = 'Flexible'
      AND t."Status" NOT IN ('Done', 'Skipped')
""")

GET_SUBTASKS = text("""
    SELECT st."Id", st."TaskId", st."Title", st."EstimatedMinutes",
           st."OrderIndex", st."Status"
    FROM "SubTask" st
    JOIN "Task" t ON t."Id" = st."TaskId"
    JOIN "Project" p ON p."Id" = t."ProjectId"
    WHERE p."UserId" = :user_id
      AND t."TaskType" = 'Flexible'
      AND t."Status" NOT IN ('Done', 'Skipped')
    ORDER BY st."TaskId", st."OrderIndex"
""")

GET_SESSIONS = text("""
    SELECT fs."TaskId", fs."SubTaskId", fs."StartedAt", fs."EndedAt",
           fs."CaptureMethod", fs."IsConfirmed", fs."RemainingMinutes"
    FROM "FocusSession" fs
    JOIN "Task" t ON t."Id" = fs."TaskId"
    JOIN "Project" p ON p."Id" = t."ProjectId"
    WHERE p."UserId" = :user_id
      AND t."TaskType" = 'Flexible'
      AND t."Status" NOT IN ('Done', 'Skipped')
    ORDER BY fs."StartedAt"
""")

GET_K_BIAS = text("""
    SELECT "DomainId", "KBias"
    FROM "UserDomainProfile"
    WHERE "UserId" = :user_id
""")

GET_PRODUCTIVE_HOURS = text("""
    SELECT "HourOfDay", "Score"
    FROM "UserCircadianProfile"
    WHERE "UserId" = :user_id
""")

GET_FIXED_TASKS = text("""
    SELECT t."FixedStart", t."FixedEnd", t."Status"
    FROM "Task" t
    JOIN "Project" p ON p."Id" = t."ProjectId"
    WHERE p."UserId" = :user_id
      AND t."TaskType" = 'Fixed'
      AND t."FixedEnd" > :window_start
      AND t."FixedStart" < :window_end
""")

GET_CALENDAR_EVENTS = text("""
    SELECT e."StartTime", e."EndTime", e."IsAllDay", e."IsCancelled",
           e."IsCreatedByMetanoia"
    FROM "GoogleCalendarEvent" e
    JOIN "GoogleConnection" c ON c."Id" = e."GoogleConnectionId"
    WHERE c."UserId" = :user_id
      AND e."EndTime" > :window_start
      AND e."StartTime" < :window_end
""")

GET_CONNECTION_STATUS = text("""
    SELECT "Status"
    FROM "GoogleConnection"
    WHERE "UserId" = :user_id
""")

GET_APPROVED_SLOTS = text("""
    SELECT s."StartTime", s."EndTime"
    FROM "ScheduleSlot" s
    JOIN "Task" t ON t."Id" = s."TaskId"
    JOIN "Project" p ON p."Id" = t."ProjectId"
    WHERE p."UserId" = :user_id
      AND s."Status" = 'Approved'
      AND s."EndTime" > :window_start
      AND s."StartTime" < :window_end
""")

# Blind-test proposals (FR-13) are never touched here.
CANCEL_OLD_SLOTS = text("""
    UPDATE "ScheduleSlot" s
    SET "Status" = 'Cancelled'
    FROM "ScheduleProposal" sp
    WHERE s."ScheduleProposalId" = sp."Id"
      AND sp."UserId" = :user_id
      AND sp."Status" = 'Pending'
      AND sp."BlindTestSessionId" IS NULL
      AND s."Status" = 'Proposed'
""")

SUPERSEDE_OLD_PROPOSALS = text("""
    UPDATE "ScheduleProposal"
    SET "Status" = 'Superseded'
    WHERE "UserId" = :user_id
      AND "Status" = 'Pending'
      AND "BlindTestSessionId" IS NULL
""")

INSERT_PROPOSAL = text("""
    INSERT INTO "ScheduleProposal" (
        "Id", "UserId", "Level", "Status", "IsFeasible", "MissingMinutes",
        "Explanation", "Warnings"
    ) VALUES (
        :proposal_id, :user_id, 3, 'Pending', :is_feasible, :missing_minutes,
        :explanation, CAST(:warnings AS jsonb)
    )
""")

INSERT_SLOT = text("""
    INSERT INTO "ScheduleSlot" (
        "Id", "TaskId", "SubTaskId", "ScheduleProposalId", "StartTime", "EndTime",
        "ProposedMinutes", "KBiasUsed", "Explanation", "Status"
    ) VALUES (
        :slot_id, :task_id, :subtask_id, :proposal_id, :start, :end,
        :proposed_minutes, :k_bias_used, :explanation, 'Proposed'
    )
""")


class SqlScheduleRepository:
    """ScheduleRepository on Postgres. One instance per request (one session)."""

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

    async def get_planning_data(
        self, user_id: UUID, window_start: datetime, window_end: datetime
    ) -> PlanningData:
        user = {"user_id": user_id}
        window = {**user, "window_start": window_start, "window_end": window_end}
        execute = self.session.execute

        row = (await execute(GET_USER, user)).one()
        hours = WorkingHours(
            time_zone=ZoneInfo(row.TimeZone),
            day_start=row.WorkDayStart,
            day_end=row.WorkDayEnd,
            works_on_weekend=row.WorksOnWeekend,
            buffer_minutes=row.DefaultBufferMinutes,
        )

        subtasks: dict[UUID, list[SubTaskToSchedule]] = {}
        for s in await execute(GET_SUBTASKS, user):
            subtasks.setdefault(s.TaskId, []).append(
                SubTaskToSchedule(
                    subtask_id=s.Id,
                    title=s.Title,
                    estimated_minutes=s.EstimatedMinutes,
                    order_index=s.OrderIndex,
                    is_open=s.Status == OPEN_SUBTASK,
                )
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
            TaskToSchedule(
                task_id=t.Id,
                title=t.Title,
                domain_id=t.DomainId,
                status=STATUS_FROM_DB[t.Status],
                deadline=t.Deadline,
                estimated_minutes=t.EstimatedMinutes,
                has_approved_slot=t.HasApprovedSlot,
                subtasks=tuple(subtasks.get(t.Id, ())),
                sessions=tuple(sessions.get(t.Id, ())),
            )
            for t in await execute(GET_TASKS, user)
        )

        connection = (await execute(GET_CONNECTION_STATUS, user)).scalar_one_or_none()
        return PlanningData(
            hours=hours,
            tasks=tasks,
            k_by_domain={r.DomainId: r.KBias for r in await execute(GET_K_BIAS, user)},
            productive_scores={
                r.HourOfDay: r.Score for r in await execute(GET_PRODUCTIVE_HOURS, user)
            },
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
            calendar_synced=connection not in UNSYNCED_CONNECTIONS,
            approved_slots=tuple(
                TimeRange(r.StartTime, r.EndTime)
                for r in await execute(GET_APPROVED_SLOTS, window)
            ),
        )

    async def save_proposal(self, user_id: UUID, proposal: NewProposal) -> None:
        user = {"user_id": user_id}
        await self.session.execute(CANCEL_OLD_SLOTS, user)
        await self.session.execute(SUPERSEDE_OLD_PROPOSALS, user)
        await self.session.execute(
            INSERT_PROPOSAL,
            {
                **user,
                "proposal_id": proposal.proposal_id,
                "is_feasible": proposal.is_feasible,
                "missing_minutes": proposal.missing_minutes,
                "explanation": proposal.explanation,
                "warnings": warnings_json(proposal),
            },
        )
        if proposal.slots:
            await self.session.execute(
                INSERT_SLOT,
                [
                    {
                        "slot_id": s.slot_id,
                        "task_id": s.task_id,
                        "subtask_id": s.subtask_id,
                        "proposal_id": proposal.proposal_id,
                        "start": s.start,
                        "end": s.end,
                        "proposed_minutes": s.proposed_minutes,
                        "k_bias_used": s.k_bias_used,
                        "explanation": s.explanation,
                    }
                    for s in proposal.slots
                ],
            )


def warnings_json(proposal: NewProposal) -> str:
    """ScheduleProposal.Warnings: a JSON array (the DB rejects anything else)."""
    return json.dumps(
        [
            {
                "task_id": str(w.warning.task_id) if w.warning.task_id else None,
                "type": w.warning.type.value,
                "needed_minutes": w.warning.needed_minutes,
                "free_minutes": w.warning.free_minutes,
                "message": w.message,
            }
            for w in proposal.warnings
        ],
        ensure_ascii=False,
    )
