"""FR-08a: show the chat of a task (messages, newest draft, applied or not). No LLM."""

from dataclasses import dataclass, field
from uuid import UUID
from zoneinfo import ZoneInfo

from metanoia_ai.application.ports.chat_repository import ChatMessage, ChatRepository
from metanoia_ai.application.use_cases.record_task_completion import (
    TaskNotFoundError,
)


@dataclass(frozen=True)
class TaskChatView:
    task_id: UUID
    time_zone: ZoneInfo  # the user's, for created_at in the answer
    messages: list[ChatMessage] = field(default_factory=list)  # oldest first
    latest_draft: ChatMessage | None = None
    applied: bool = False  # a draft of this chat was applied (IsApplied, #26)


class GetTaskChat:
    def __init__(self, repo: ChatRepository) -> None:
        self.repo = repo

    async def execute(self, user_id: UUID, task_id: UUID) -> TaskChatView:
        """Missing or other user's task → TaskNotFoundError. No chat yet → empty."""
        async with self.repo.transaction():
            task = await self.repo.get_task(user_id, task_id)
            if task is None:
                raise TaskNotFoundError(str(task_id))
            conversation_id = await self.repo.find_conversation(user_id, task_id)
            if conversation_id is None:
                return TaskChatView(task_id, task.time_zone)
            messages = await self.repo.list_messages(user_id, conversation_id)
        drafts = [m for m in messages if m.draft is not None]
        return TaskChatView(
            task_id=task_id,
            time_zone=task.time_zone,
            messages=messages,
            latest_draft=drafts[-1] if drafts else None,
            applied=any(m.is_applied for m in messages),
        )
