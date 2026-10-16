"""What the project chat (FR-08b-1) needs from storage. The database code implements it."""

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from metanoia_ai.application.ports.chat_repository import ChatRole
from metanoia_ai.domain.project_breakdown import ProjectDraft, ProjectNumbers

# Project.Status values that mean the project is closed (#28).
CLOSED_PROJECT_STATUSES = frozenset({"Completed", "Archived"})


class ConversationStatus(StrEnum):
    """ChatConversation.Status (DB: Open / Applied / Closed). Values are sent in the API."""

    OPEN = "open"
    APPLIED = "applied"
    CLOSED = "closed"


@dataclass(frozen=True)
class ChatProject:
    """The project being planned, with what the chat needs to know about it."""

    project_id: UUID
    name: str
    description: str | None
    deadline: date | None  # Project.Deadline is a date
    status: str  # as in the DB: Active, OnHold, Completed, Archived
    client_name: str | None  # for masking only
    time_zone: ZoneInfo  # the user's

    @property
    def is_closed(self) -> bool:
        return self.status in CLOSED_PROJECT_STATUSES


@dataclass(frozen=True)
class ProjectConversation:
    conversation_id: UUID
    project_id: UUID
    title: str
    status: ConversationStatus
    created_at: datetime


@dataclass(frozen=True)
class SavedProjectDraft:
    """A draft as stored with its AI message, numbers included (#8)."""

    draft: ProjectDraft
    numbers: ProjectNumbers


@dataclass(frozen=True)
class ProjectChatMessage:
    message_id: UUID
    role: ChatRole
    content: str  # user text as written / AI reply
    created_at: datetime
    draft: SavedProjectDraft | None = None  # AI drafts only
    is_applied: bool = False


@dataclass(frozen=True)
class ExistingWork:
    """Titles already in the project, newest first (#3, #9)."""

    milestone_titles: tuple[str, ...]
    task_titles: tuple[str, ...]


@dataclass(frozen=True)
class NewMilestone:
    title: str  # Milestone.Name
    due_date: date  # Milestone.DueDate
    order_index: int


@dataclass(frozen=True)
class NewTask:
    """A Flexible ToDo task to create at Apply (#10)."""

    milestone_id: UUID
    title: str
    domain_id: UUID
    deadline: datetime  # 23:59 of the milestone day, user's zone (#16)
    estimated_minutes: int
    ai_estimated_minutes: int | None  # the AI's number; None for user-added (#11)


class ProjectChatRepository(Protocol):
    """Storage for project chats. Every call is limited to the given user."""

    def transaction(self) -> AbstractAsyncContextManager[object]:
        """Calls inside run in one DB transaction: all saved, or nothing."""
        ...

    async def get_project(
        self, user_id: UUID, project_id: UUID, lock: bool = False
    ) -> ChatProject | None:
        """The project, or None if missing or not this user's.

        lock=True: lock the project row until the transaction ends (Apply, #22).
        """
        ...

    async def get_conversation(
        self, user_id: UUID, conversation_id: UUID, lock: bool = False
    ) -> ProjectConversation | None:
        """A project chat (Scope Project) of this user, or None.

        Task chats (FR-08a) are never returned. lock=True: lock the chat row.
        """
        ...

    async def list_conversations(
        self, user_id: UUID, project_id: UUID
    ) -> list[ProjectConversation]:
        """The project chats of this project, newest first."""
        ...

    async def create_conversation(
        self, user_id: UUID, project_id: UUID, title: str
    ) -> ProjectConversation:
        """A new project chat (Scope Project, Status Open)."""
        ...

    async def list_messages(
        self, user_id: UUID, conversation_id: UUID
    ) -> list[ProjectChatMessage]:
        """Every message of the chat, oldest first."""
        ...

    async def latest_draft(
        self, user_id: UUID, conversation_id: UUID
    ) -> ProjectChatMessage | None:
        """The newest AI message that has a draft, or None."""
        ...

    async def add_message(
        self,
        user_id: UUID,
        conversation_id: UUID,
        role: ChatRole,
        content: str,
        draft: SavedProjectDraft | None = None,
    ) -> ProjectChatMessage: ...

    async def active_domains(self) -> dict[UUID, str]:
        """Domains with IsActive, id → name. Domain is shared by every user."""
        ...

    async def get_k_biases(self, user_id: UUID) -> dict[UUID, Decimal]:
        """The user's learned K per domain (domains not learned yet are missing)."""
        ...

    async def existing_work(self, user_id: UUID, project_id: UUID) -> ExistingWork:
        """Every milestone and task title of the project, newest first."""
        ...

    async def next_milestone_order(self, user_id: UUID, project_id: UUID) -> int:
        """max(Milestone.OrderIndex) + 1 for the project, 1 when it has none (#27)."""
        ...

    async def add_milestone(
        self, user_id: UUID, project_id: UUID, milestone: NewMilestone
    ) -> UUID: ...

    async def add_task(
        self, user_id: UUID, project_id: UUID, task: NewTask
    ) -> UUID: ...

    async def mark_applied(
        self, user_id: UUID, conversation_id: UUID, message_id: UUID
    ) -> None:
        """Message IsApplied = true and chat Status = Applied (#1)."""
        ...
