"""Voice assistant (FR-22) reads on the shared Supabase Postgres. Read-only.

Every query is limited to one user. The schedule reuses the FR-12 statements,
so the "Today" screen and the assistant read the same rows.
"""

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from metanoia_ai.application.ports.assistant_repository import ScheduleRows
from metanoia_ai.domain.briefing import FixedEntry, GoogleEntry, SlotEntry
from metanoia_ai.domain.name_matching import NamedItem
from metanoia_ai.infrastructure.db.briefing_repository import (
    GET_CALENDAR_EVENTS,
    GET_FIXED_TASKS,
    GET_SLOTS,
    SLOT_STATUS_FROM_DB,
)

GET_TIME_ZONE = text("""
    SELECT "TimeZone"
    FROM "User"
    WHERE "Id" = :user_id AND "DeletedAt" IS NULL
""")

GET_OPEN_TASKS = text("""
    SELECT t."Id", t."Title"
    FROM "Task" t
    JOIN "Project" p ON p."Id" = t."ProjectId"
    WHERE p."UserId" = :user_id
      AND t."TaskType" = 'Flexible'
      AND t."Status" IN ('ToDo', 'Scheduled', 'InProgress')
    ORDER BY t."Title", t."Id"
""")

GET_PROJECTS = text("""
    SELECT p."Id", p."Name"
    FROM "Project" p
    WHERE p."UserId" = :user_id AND p."Status" <> 'Archived'
    ORDER BY p."Name", p."Id"
""")


class SqlAssistantRepository:
    """AssistantRepository on Postgres. One instance per session."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def transaction(self) -> AbstractAsyncContextManager[object]:
        if self.session.in_transaction():
            # A read left open by autobegin: close it so begin() can start fresh.
            return _after_commit(self.session)
        return self.session.begin()

    @asynccontextmanager
    async def _read_only(self) -> AsyncIterator[None]:
        if self.session.in_transaction():
            await self.session.commit()
        async with self.session.begin():
            await self.session.execute(text("SET TRANSACTION READ ONLY"))
            yield

    async def get_time_zone(self, user_id: UUID) -> ZoneInfo | None:
        async with self._read_only():
            row = (
                await self.session.execute(GET_TIME_ZONE, {"user_id": user_id})
            ).one_or_none()
        return None if row is None else ZoneInfo(row.TimeZone)

    async def _names(self, statement, user_id: UUID) -> list[NamedItem]:
        async with self._read_only():
            rows = await self.session.execute(statement, {"user_id": user_id})
            return [NamedItem(r[0], r[1]) for r in rows]

    async def open_tasks(self, user_id: UUID) -> list[NamedItem]:
        return await self._names(GET_OPEN_TASKS, user_id)

    async def projects(self, user_id: UUID) -> list[NamedItem]:
        return await self._names(GET_PROJECTS, user_id)

    async def schedule(
        self, user_id: UUID, start: datetime, end: datetime
    ) -> ScheduleRows:
        # The FR-12 statements name the range day_start / day_end / window_end.
        slot_params = {"user_id": user_id, "day_start": start, "day_end": end}
        params = {"user_id": user_id, "day_start": start, "window_end": end}
        async with self._read_only():
            execute = self.session.execute
            return ScheduleRows(
                slots=tuple(
                    SlotEntry(
                        r.TaskId,
                        r.Title,
                        r.StartTime,
                        r.EndTime,
                        SLOT_STATUS_FROM_DB[r.Status],
                    )
                    for r in await execute(GET_SLOTS, slot_params)
                ),
                fixed_tasks=tuple(
                    FixedEntry(
                        r.Id, r.Title, r.FixedStart, r.FixedEnd, r.Status == "Skipped"
                    )
                    for r in await execute(GET_FIXED_TASKS, params)
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
                    for r in await execute(GET_CALENDAR_EVENTS, params)
                ),
            )


@asynccontextmanager
async def _after_commit(session: AsyncSession) -> AsyncIterator[None]:
    await session.commit()
    async with session.begin():
        yield
