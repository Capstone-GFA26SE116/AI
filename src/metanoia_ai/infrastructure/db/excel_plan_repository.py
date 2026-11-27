"""Persist structured imports using the existing project and planning writes."""

from uuid import UUID

from sqlalchemy import bindparam, text

from metanoia_ai.application.ports.project_import_repository import NewProject
from metanoia_ai.infrastructure.db.chat_repository import NotOwnedError
from metanoia_ai.infrastructure.db.project_chat_repository import (
    SqlProjectChatRepository,
)
from metanoia_ai.infrastructure.db.project_import_repository import (
    SqlProjectImportRepository,
)

EXISTING_PROJECTS = text("""
    SELECT "Id" FROM "Project" WHERE "UserId" = :user_id AND "Id" IN :ids
""").bindparams(bindparam("ids", expanding=True))

INSERT_PLAN_PROJECT = text("""
    INSERT INTO "Project" ("Id", "UserId", "Name", "Description", "StartDate", "Deadline",
                           "Status", "IsDefault", "AllowAiDocumentAccess")
    SELECT :id, u."Id", :name, :description, :start_date, :deadline, 'Active', false, false
    FROM "User" u
    WHERE u."Id" = :user_id
    RETURNING "Id"
""")


class SqlExcelPlanRepository(SqlProjectImportRepository, SqlProjectChatRepository):
    """All operations share the import repository's transaction and session."""

    async def existing_projects(self, user_id: UUID, ids: list[UUID]) -> set[UUID]:
        result = await self.session.execute(
            EXISTING_PROJECTS, {"user_id": user_id, "ids": ids}
        )
        return set(result.scalars())

    async def create_plan_project(
        self, user_id: UUID, project_id: UUID, project: NewProject
    ) -> None:
        result = await self.session.execute(
            INSERT_PLAN_PROJECT,
            {
                "id": project_id,
                "user_id": user_id,
                "name": project.name,
                "description": project.description,
                "start_date": project.start_date,
                "deadline": project.deadline,
            },
        )
        if result.scalar_one_or_none() is None:
            raise NotOwnedError(str(user_id))
