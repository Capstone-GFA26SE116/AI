"""FR-08b-1: show one project chat with its messages and its newest draft. No LLM."""

from dataclasses import dataclass
from uuid import UUID
from zoneinfo import ZoneInfo

from metanoia_ai.application.ports.project_chat_repository import (
    ConversationStatus,
    ProjectChatMessage,
    ProjectChatRepository,
    ProjectConversation,
)
from metanoia_ai.application.use_cases.start_project_chat import (
    ConversationNotFoundError,
)


@dataclass(frozen=True)
class ProjectChatView:
    conversation: ProjectConversation
    messages: list[ProjectChatMessage]  # oldest first
    latest_draft: ProjectChatMessage | None  # the one Apply accepts
    time_zone: ZoneInfo  # the user's, for created_at in the answer

    @property
    def applied(self) -> bool:
        return self.conversation.status is ConversationStatus.APPLIED


class GetProjectChat:
    def __init__(self, repo: ProjectChatRepository) -> None:
        self.repo = repo

    async def execute(self, user_id: UUID, conversation_id: UUID) -> ProjectChatView:
        """Also works for an applied chat or a closed project (read only)."""
        async with self.repo.transaction():
            conversation = await self.repo.get_conversation(user_id, conversation_id)
            if conversation is None:
                raise ConversationNotFoundError(str(conversation_id))
            project = await self.repo.get_project(user_id, conversation.project_id)
            if project is None:
                raise ConversationNotFoundError(str(conversation_id))
            messages = await self.repo.list_messages(user_id, conversation_id)
            latest = await self.repo.latest_draft(user_id, conversation_id)
        return ProjectChatView(conversation, messages, latest, project.time_zone)
