"""FR-08c step 2: create the projects the user kept, each with a project chat
whose first message is its part of the file. No AI call, no quota.

All or nothing: over the plan's project limit → nothing is created.
"""

from collections.abc import Callable, Sequence
from datetime import datetime
from uuid import UUID

from metanoia_ai.application.ports.project_import_repository import (
    CreatedProject,
    NewProject,
    ProjectImportRepository,
)
from metanoia_ai.application.use_cases.list_chat_history import UserNotFoundError
from metanoia_ai.domain.models import Language
from metanoia_ai.domain.project_import import (
    ImportInput,
    check_import,
    first_message,
    projects_left,
)

CHAT_TITLE = {Language.VI: "Từ file {file}", Language.EN: "From {file}"}


class ProjectLimitError(Exception):
    """The plan does not allow this many more projects (→ 409 project_limit)."""

    def __init__(self, limit: int, used: int, wanted: int) -> None:
        super().__init__(f"limit {limit}, used {used}, wanted {wanted}")
        self.limit = limit
        self.used = used
        self.wanted = wanted


class ImportProjects:
    def __init__(
        self, repo: ProjectImportRepository, *, now: Callable[[], datetime]
    ) -> None:
        self.repo = repo
        self.now = now

    async def execute(
        self,
        user_id: UUID,
        file_name: str,
        items: Sequence[ImportInput],
        language: Language,
    ) -> list[CreatedProject]:
        """BadDraftError (422), ProjectLimitError (409)."""
        zone = await self.repo.time_zone(user_id)
        if zone is None:
            raise UserNotFoundError(str(user_id))
        today = self.now().astimezone(zone).date()
        projects = check_import(items, today)
        name = (" ".join(file_name.split()) or "file")[:100]

        created = []
        async with self.repo.transaction():
            allowance = await self.repo.allowance(user_id, lock=True)
            left = projects_left(allowance.limit, allowance.used)
            if left is not None and len(projects) > left:
                assert allowance.limit is not None
                raise ProjectLimitError(allowance.limit, allowance.used, len(projects))
            for project in projects:
                project_id = await self.repo.create_project(
                    user_id,
                    NewProject(
                        project.name, project.description, today, project.deadline
                    ),
                )
                conversation_id = await self.repo.create_chat(
                    user_id,
                    project_id,
                    CHAT_TITLE[language].format(file=name)[:200],
                    first_message(name, project, language.value),
                )
                created.append(
                    CreatedProject(project_id, conversation_id, project.name)
                )
        return created
