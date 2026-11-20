"""ProjectImportRepository on Postgres (FR-08c). INSERT only into "Project"."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from metanoia_ai.application.ports.chat_repository import ChatRole
from metanoia_ai.application.ports.project_import_repository import (
    NewProject,
    ProjectAllowance,
)
from metanoia_ai.infrastructure.db.chat_repository import ROLE_TO_DB, NotOwnedError
from metanoia_ai.infrastructure.db.project_chat_repository import (
    ADD_MESSAGE,
    CREATE_CONVERSATION,
)

GET_TIME_ZONE = text('SELECT "TimeZone" FROM "User" WHERE "Id" = :user_id')
LOCK_USER = text('SELECT "Id" FROM "User" WHERE "Id" = :user_id FOR UPDATE')

# Same rule as the Backend (PlanLimitService.EnsureCanCreateProjectAsync):
# the plan of the Active subscription; no plan or NULL limit = no limit.
GET_PROJECT_LIMIT = text("""
    SELECT sp."ProjectLimit"
    FROM "UserSubscription" us
    JOIN "SubscriptionPlan" sp ON sp."Id" = us."PlanId"
    WHERE us."UserId" = :user_id AND us."Status" = 'Active'
    LIMIT 1
""")
# ProjectRepository.CountCountableAsync: not the default project, not Archived.
COUNT_COUNTABLE_PROJECTS = text("""
    SELECT count(*) FROM "Project"
    WHERE "UserId" = :user_id AND NOT "IsDefault" AND "Status" <> 'Archived'
""")

# The only write to "Project" in this service (CLAUDE.md §8): a new, active,
# non-default project without a client, AI document access off.
INSERT_PROJECT = text("""
    INSERT INTO "Project" ("UserId", "Name", "Description", "StartDate", "Deadline",
                           "Status", "IsDefault", "AllowAiDocumentAccess")
    SELECT u."Id", :name, :description, :start_date, :deadline, 'Active', false, false
    FROM "User" u
    WHERE u."Id" = :user_id
    RETURNING "Id"
""")


class SqlProjectImportRepository:
    """One instance per request (one session)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        # Reads before this point opened a transaction automatically; end it.
        if self.session.in_transaction():
            await self.session.commit()
        async with self.session.begin():
            yield

    async def time_zone(self, user_id: UUID) -> ZoneInfo | None:
        result = await self.session.execute(GET_TIME_ZONE, {"user_id": user_id})
        name = result.scalar_one_or_none()
        return ZoneInfo(name) if name else None

    async def allowance(self, user_id: UUID, lock: bool = False) -> ProjectAllowance:
        params = {"user_id": user_id}
        if lock:
            await self.session.execute(LOCK_USER, params)
        limit = (
            await self.session.execute(GET_PROJECT_LIMIT, params)
        ).scalar_one_or_none()
        used = (
            await self.session.execute(COUNT_COUNTABLE_PROJECTS, params)
        ).scalar_one()
        return ProjectAllowance(limit, used)

    async def create_project(self, user_id: UUID, project: NewProject) -> UUID:
        params = {
            "user_id": user_id,
            "name": project.name,
            "description": project.description,
            "start_date": project.start_date,
            "deadline": project.deadline,
        }
        project_id = (
            await self.session.execute(INSERT_PROJECT, params)
        ).scalar_one_or_none()
        if project_id is None:
            raise NotOwnedError(str(user_id))
        return project_id

    async def create_chat(
        self, user_id: UUID, project_id: UUID, title: str, first_message: str
    ) -> UUID:
        params = {"project_id": project_id, "user_id": user_id, "title": title}
        row = (await self.session.execute(CREATE_CONVERSATION, params)).one_or_none()
        if row is None:
            raise NotOwnedError(str(project_id))
        message = {
            "conversation_id": row.Id,
            "user_id": user_id,
            "sender": ROLE_TO_DB[ChatRole.USER],
            "content": first_message,
            "proposal": None,
        }
        if (await self.session.execute(ADD_MESSAGE, message)).one_or_none() is None:
            raise NotOwnedError(str(row.Id))
        return row.Id
