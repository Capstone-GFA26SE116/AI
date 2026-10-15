"""What the task chat (FR-08a) needs from storage. The database code implements it."""

from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from metanoia_ai.domain.models import TaskType
from metanoia_ai.domain.similar_tasks import SimilarSummary
from metanoia_ai.domain.task_breakdown import Draft, DraftNumbers, Subtask


class ChatRole(StrEnum):
    """Who wrote a message (DB `Sender`: User / AI)."""

    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True)
class ChatTask:
    """The task being split, with what the chat needs to know about it."""

    task_id: UUID
    project_id: UUID
    task_type: TaskType
    status: str  # Task.Status as in the DB: ToDo, Scheduled, InProgress, Done, Skipped
    title: str
    description: str | None
    domain_id: UUID
    domain_name: str
    client_name: str | None  # for masking only
    estimated_minutes: int | None  # None for Fixed tasks
    deadline: datetime | None
    time_zone: ZoneInfo  # the user's
    subtask_count: int


@dataclass(frozen=True)
class SavedDraft:
    """A draft as stored with its AI message, numbers included (plan #22)."""

    draft: Draft
    numbers: DraftNumbers
    similar: SimilarSummary | None = None  # FR-09 summary shown with the draft


@dataclass(frozen=True)
class ChatMessage:
    message_id: UUID
    role: ChatRole
    content: str  # user text as written / AI reply
    created_at: datetime
    draft: SavedDraft | None = None  # AI drafts only
    is_applied: bool = False


@dataclass(frozen=True)
class SavedSubtask:
    subtask_id: UUID
    title: str
    estimated_minutes: int
    order_index: int


class ChatRepository(Protocol):
    """Storage for task chats. Every call is limited to the given user."""

    def transaction(self) -> AbstractAsyncContextManager[object]:
        """Calls inside run in one DB transaction: all saved, or nothing."""
        ...

    async def get_task(
        self, user_id: UUID, task_id: UUID, lock: bool = False
    ) -> ChatTask | None:
        """The task, or None if missing or not this user's.

        lock=True: lock the task row until the transaction ends (plan #18, #28),
        so two requests for the same task run one after the other.
        """
        ...

    async def get_k_bias(self, user_id: UUID, domain_id: UUID) -> Decimal | None:
        """The user's K for this domain, or None when nothing is learned yet."""
        ...

    async def find_conversation(self, user_id: UUID, task_id: UUID) -> UUID | None:
        """The chat of this task (one per task, plan #2), or None."""
        ...

    async def create_conversation(self, user_id: UUID, task: ChatTask) -> UUID:
        """A new task chat (Scope Task, Status Open, title = task title, plan #27)."""
        ...

    async def list_messages(
        self, user_id: UUID, conversation_id: UUID, limit: int | None = None
    ) -> list[ChatMessage]:
        """Messages oldest first; with a limit, only the newest `limit` of them."""
        ...

    async def latest_draft(
        self, user_id: UUID, conversation_id: UUID
    ) -> ChatMessage | None:
        """The newest AI message that has a draft, or None."""
        ...

    async def add_message(
        self,
        user_id: UUID,
        conversation_id: UUID,
        role: ChatRole,
        content: str,
        draft: SavedDraft | None = None,
    ) -> ChatMessage: ...

    async def add_subtasks(
        self, user_id: UUID, task_id: UUID, subtasks: Sequence[Subtask]
    ) -> list[SavedSubtask]:
        """Write the subtasks (Status ToDo, OrderIndex as given)."""
        ...

    async def mark_applied(
        self, user_id: UUID, conversation_id: UUID, message_id: UUID
    ) -> None:
        """Message IsApplied = true and chat Status = Applied (plan #29)."""
        ...
