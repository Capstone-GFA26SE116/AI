"""FR-08b-1: open a new project chat, and list the chats of a project.

No LLM, no quota. A project can have many chats; each is applied at most once (#1).
"""

from collections.abc import Callable
from datetime import date, datetime
from uuid import UUID

from metanoia_ai.application.ports.project_chat_repository import (
    ChatProject,
    ProjectChatRepository,
    ProjectConversation,
)


class ProjectNotFoundError(Exception):
    """The project does not exist, or it belongs to another user. → 404."""


class ProjectClosedError(Exception):
    """The project is Completed or Archived (#28). → 409 project_closed."""


class ConversationNotFoundError(Exception):
    """No project chat with this id for this user (task chats included). → 404."""


class ConversationAppliedError(Exception):
    """The chat was already applied: it can only be read (#1). → 409 already_applied."""


def chat_title(day: date) -> str:
    """ChatConversation.Title of a new chat (#2): no LLM, no quota."""
    return f"Brief {day.isoformat()}"


async def open_project(
    repo: ProjectChatRepository, user_id: UUID, project_id: UUID, lock: bool = False
) -> ChatProject:
    """The user's project, not closed. Otherwise ProjectNotFoundError / ProjectClosedError."""
    project = await repo.get_project(user_id, project_id, lock=lock)
    if project is None:
        raise ProjectNotFoundError(str(project_id))
    if project.is_closed:
        raise ProjectClosedError(str(project_id))
    return project


class StartProjectChat:
    def __init__(
        self, repo: ProjectChatRepository, now: Callable[[], datetime]
    ) -> None:
        self.repo = repo
        self.now = now

    async def execute(self, user_id: UUID, project_id: UUID) -> ProjectConversation:
        async with self.repo.transaction():
            project = await open_project(self.repo, user_id, project_id)
            today = self.now().astimezone(project.time_zone).date()
            return await self.repo.create_conversation(
                user_id, project_id, chat_title(today)
            )


class ListProjectChats:
    def __init__(self, repo: ProjectChatRepository) -> None:
        self.repo = repo

    async def execute(
        self, user_id: UUID, project_id: UUID
    ) -> list[ProjectConversation]:
        """Newest first. A closed project can still be read."""
        async with self.repo.transaction():
            if await self.repo.get_project(user_id, project_id) is None:
                raise ProjectNotFoundError(str(project_id))
            return await self.repo.list_conversations(user_id, project_id)
