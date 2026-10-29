"""Personal analytics (FR-20) on the shared Supabase Postgres. Read-only.

Every query is limited to one user: Task has no UserId, so it joins Project;
KBiasUpdateLog joins UserDomainProfile. Sessions of Done tasks come back as one
JSON array per task (no N+1). Overview: 5 queries; breakdown: 2 (plan #12).
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from metanoia_ai.application.ports.analytics_repository import (
    AnalyticsDomainProfile,
    AnalyticsUser,
)
from metanoia_ai.domain.analytics import (
    DeadlineTask,
    DoneTask,
    KUpdate,
    Period,
    TaskStatus,
)
from metanoia_ai.domain.models import FocusSessionRecord
from metanoia_ai.infrastructure.db.learning_repository import (
    SOURCE_FROM_DB,
    TASK_TYPE_FROM_DB,
)

STATUS_FROM_DB = {
    "ToDo": TaskStatus.TODO,
    "Scheduled": TaskStatus.SCHEDULED,
    "InProgress": TaskStatus.IN_PROGRESS,
    "Done": TaskStatus.DONE,
    "Skipped": TaskStatus.SKIPPED,
}

# :project_id may be NULL → only check it when given.
GET_USER = text("""
    SELECT u."TimeZone",
           CASE WHEN CAST(:project_id AS uuid) IS NULL THEN NULL
                ELSE EXISTS (SELECT 1 FROM "Project" p
                             WHERE p."Id" = CAST(:project_id AS uuid)
                               AND p."UserId" = u."Id")
           END AS "ProjectFound"
    FROM "User" u
    WHERE u."Id" = :user_id AND u."DeletedAt" IS NULL
""")

# :start is NULL for the "all" range.
IN_PERIOD = """{column} <= :end
      AND (CAST(:start AS timestamptz) IS NULL OR {column} >= CAST(:start AS timestamptz))"""

# Completion (#6): Flexible tasks with a deadline in the period. Archived projects count.
GET_DEADLINE_TASKS = text(f"""
    SELECT t."TaskType", t."Status", t."Deadline", t."CompletedAt"
    FROM "Task" t
    JOIN "Project" p ON p."Id" = t."ProjectId"
    WHERE p."UserId" = :user_id
      AND t."TaskType" = 'Flexible'
      AND {IN_PERIOD.format(column='t."Deadline"')}
""")

GET_PROFILES = text("""
    SELECT udp."Id", udp."DomainId", d."Name" AS "DomainName", udp."KBias", udp."SampleCount"
    FROM "UserDomainProfile" udp
    JOIN "Domain" d ON d."Id" = udp."DomainId"
    WHERE udp."UserId" = :user_id
    ORDER BY d."Name", udp."Id"
""")

GET_K_UPDATES = text(f"""
    SELECT l."UserDomainProfileId", l."CreatedAt", l."NewKBias"
    FROM "KBiasUpdateLog" l
    JOIN "UserDomainProfile" udp ON udp."Id" = l."UserDomainProfileId"
    WHERE udp."UserId" = :user_id
      AND {IN_PERIOD.format(column='l."CreatedAt"')}
    ORDER BY l."CreatedAt", l."SampleIndex"
""")

# Planned vs actual (#9): Flexible Done tasks completed in the period, with all sessions.
GET_DONE_TASKS = text(f"""
    SELECT t."Id", t."Title", t."ProjectId", p."Name" AS "ProjectName",
           c."Id" AS "ClientId", c."Name" AS "ClientName", d."Name" AS "DomainName",
           t."EstimatedMinutes", t."AiEstimatedMinutes", t."CompletedAt",
           CAST(COALESCE(
               (SELECT json_agg(json_build_object(
                           'started_at', fs."StartedAt",
                           'ended_at', fs."EndedAt",
                           'source', fs."CaptureMethod",
                           'confirmed', fs."IsConfirmed")
                       ORDER BY fs."StartedAt")
                FROM "FocusSession" fs
                WHERE fs."TaskId" = t."Id"),
               '[]') AS text) AS "Sessions"
    FROM "Task" t
    JOIN "Project" p ON p."Id" = t."ProjectId"
    JOIN "Domain" d ON d."Id" = t."DomainId"
    LEFT JOIN "Client" c ON c."Id" = p."ClientId" AND c."UserId" = p."UserId"
    WHERE p."UserId" = :user_id
      AND t."TaskType" = 'Flexible'
      AND t."Status" = 'Done'
      AND {IN_PERIOD.format(column='t."CompletedAt"')}
      AND (CAST(:project_id AS uuid) IS NULL OR t."ProjectId" = CAST(:project_id AS uuid))
""")


def _period(period: Period) -> dict[str, datetime | None]:
    return {"start": period.start, "end": period.end}


def parse_sessions(raw: str) -> tuple[FocusSessionRecord, ...]:
    """The JSON array of GET_DONE_TASKS → session records."""
    return tuple(
        FocusSessionRecord(
            started_at=datetime.fromisoformat(s["started_at"]),
            ended_at=None
            if s["ended_at"] is None
            else datetime.fromisoformat(s["ended_at"]),
            source=SOURCE_FROM_DB[s["source"]],
            is_confirmed=s["confirmed"],
        )
        for s in json.loads(raw)
    )


class SqlAnalyticsRepository:
    """AnalyticsRepository on Postgres. One instance per session."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @asynccontextmanager
    async def _read_only(self) -> AsyncIterator[None]:
        if self.session.in_transaction():
            await self.session.commit()
        async with self.session.begin():
            await self.session.execute(text("SET TRANSACTION READ ONLY"))
            yield

    async def get_user(
        self, user_id: UUID, project_id: UUID | None = None
    ) -> AnalyticsUser | None:
        async with self._read_only():
            params = {"user_id": user_id, "project_id": project_id}
            row = (await self.session.execute(GET_USER, params)).one_or_none()
        if row is None:
            return None
        return AnalyticsUser(ZoneInfo(row.TimeZone), row.ProjectFound)

    async def get_deadline_tasks(
        self, user_id: UUID, period: Period
    ) -> list[DeadlineTask]:
        async with self._read_only():
            rows = await self.session.execute(
                GET_DEADLINE_TASKS, {"user_id": user_id, **_period(period)}
            )
            return [
                DeadlineTask(
                    task_type=TASK_TYPE_FROM_DB[r.TaskType],
                    status=STATUS_FROM_DB[r.Status],
                    deadline=r.Deadline,
                    completed_at=r.CompletedAt,
                )
                for r in rows
            ]

    async def get_domain_profiles(
        self, user_id: UUID, period: Period
    ) -> list[AnalyticsDomainProfile]:
        async with self._read_only():
            profiles = (
                await self.session.execute(GET_PROFILES, {"user_id": user_id})
            ).all()
            updates: dict[UUID, list[KUpdate]] = {}
            params = {"user_id": user_id, **_period(period)}
            for u in await self.session.execute(GET_K_UPDATES, params):
                updates.setdefault(u.UserDomainProfileId, []).append(
                    KUpdate(u.CreatedAt, u.NewKBias)
                )
        return [
            AnalyticsDomainProfile(
                domain_id=p.DomainId,
                domain_name=p.DomainName,
                k_bias=p.KBias,
                sample_count=p.SampleCount,
                updates=tuple(updates.get(p.Id, ())),
            )
            for p in profiles
        ]

    async def get_done_tasks(
        self, user_id: UUID, period: Period, project_id: UUID | None = None
    ) -> list[DoneTask]:
        params = {"user_id": user_id, "project_id": project_id, **_period(period)}
        async with self._read_only():
            rows = (await self.session.execute(GET_DONE_TASKS, params)).all()
        return [
            DoneTask(
                task_id=r.Id,
                title=r.Title,
                project_id=r.ProjectId,
                project_name=r.ProjectName,
                client_id=r.ClientId,
                client_name=r.ClientName,
                domain_name=r.DomainName,
                estimated_minutes=r.EstimatedMinutes,
                ai_estimated_minutes=r.AiEstimatedMinutes,
                completed_at=r.CompletedAt,
                sessions=parse_sessions(r.Sessions),
            )
            for r in rows
        ]
