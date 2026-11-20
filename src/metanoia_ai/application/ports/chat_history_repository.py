"""Port: read the user's chats for the history page (plans/chat-history.md). Read only."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from metanoia_ai.application.ports.chat_repository import ChatRole
from metanoia_ai.application.ports.project_chat_repository import ConversationStatus


class ChatScope(StrEnum):
    """ChatConversation.Scope (DB: Project / Task). Values are sent in the API."""

    PROJECT = "project"  # FR-08b: plan a project
    TASK = "task"  # FR-08a: split one task


@dataclass(frozen=True)
class ChatMatch:
    """The newest message of the chat that contains the search text."""

    message_id: UUID
    content: str
    created_at: datetime


@dataclass(frozen=True)
class ChatSummary:
    """One chat as the history list shows it."""

    conversation_id: UUID
    scope: ChatScope
    title: str
    status: ConversationStatus
    applied: bool  # a draft of this chat was applied at least once
    project_id: UUID
    project_name: str
    task_id: UUID | None  # task chats only
    task_title: str | None
    message_count: int
    last_role: ChatRole
    last_content: str
    last_at: datetime
    match: ChatMatch | None = None  # only when searching and a message matched


class ChatHistoryRepository(Protocol):
    async def time_zone(self, user_id: UUID) -> ZoneInfo | None:
        """The user's time zone; None if the user does not exist."""
        ...

    async def search(
        self, user_id: UUID, query: str | None, limit: int, offset: int
    ) -> list[ChatSummary]:
        """The user's chats that have messages, newest last message first.

        With `query`: only chats whose messages, project name, task title or chat
        title contain it (any case), each with its newest matching message.
        """
        ...
