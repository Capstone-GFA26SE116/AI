"""What the group plan chat (FR-24) needs from storage.

Chats live in ChatConversation (Scope 'Group', GroupId) + ChatMessage; the group
tables are read only (the app creates milestones / tasks through the Backend).
"""

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from metanoia_ai.application.ports.chat_repository import ChatRole
from metanoia_ai.application.ports.project_chat_repository import (
    ConversationStatus,
    ExistingWork,
    SavedProjectDraft,
)
from metanoia_ai.domain.assignee_ranking import GroupRole
from metanoia_ai.domain.group_plan import TaskAssignment
from metanoia_ai.domain.project_discussion import DiscussionNotes


@dataclass(frozen=True)
class ChatGroup:
    """The group and the caller's place in it."""

    group_id: UUID
    name: str
    description: str | None
    deadline: date | None
    archived: bool
    caller_role: GroupRole | None  # None: not a member
    time_zone: ZoneInfo  # the caller's


@dataclass(frozen=True)
class GroupConversation:
    conversation_id: UUID
    group_id: UUID
    title: str
    status: ConversationStatus
    created_at: datetime


@dataclass(frozen=True)
class GroupChatMessage:
    message_id: UUID
    role: ChatRole
    content: str
    created_at: datetime
    draft: SavedProjectDraft | None = None  # AI plan answers only
    assignments: tuple[TaskAssignment, ...] = ()  # one per draft task
    notes: DiscussionNotes | None = None  # AI discussion answers only
    is_applied: bool = False


@dataclass(frozen=True)
class NewGroupMessage:
    role: ChatRole
    content: str
    draft: SavedProjectDraft | None = None
    assignments: tuple[TaskAssignment, ...] = ()
    notes: DiscussionNotes | None = None


class GroupChatRepository(Protocol):
    def transaction(self) -> AbstractAsyncContextManager[None]:
        """Writes (messages + quota) run in one DB transaction."""
        ...

    async def get_group(self, group_id: UUID, caller_id: UUID) -> ChatGroup | None:
        """The group, or None when it does not exist."""
        ...

    async def get_conversation(self, conversation_id: UUID) -> GroupConversation | None:
        """A group chat (Scope 'Group'), or None."""
        ...

    async def list_conversations(self, group_id: UUID) -> list[GroupConversation]:
        """The group's chats, newest first."""
        ...

    async def create_conversation(
        self, group_id: UUID, title: str
    ) -> GroupConversation: ...

    async def list_messages(self, conversation_id: UUID) -> list[GroupChatMessage]:
        """Oldest first."""
        ...

    async def add_message(
        self, conversation_id: UUID, message: NewGroupMessage
    ) -> GroupChatMessage: ...

    async def mark_applied(self, conversation_id: UUID, message_id: UUID) -> bool:
        """Mark an AI draft as applied. False: not a draft of this chat, or done already."""
        ...

    async def active_domains(self) -> dict[UUID, str]:
        """Active work categories: id → name."""
        ...

    async def existing_work(self, group_id: UUID) -> ExistingWork:
        """Milestone and task titles already in the group (not Skipped)."""
        ...
