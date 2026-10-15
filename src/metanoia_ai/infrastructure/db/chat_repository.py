"""Task chat storage (FR-08a) on the shared Supabase Postgres.

Table and column names are quoted: Postgres treats "TaskId" and taskid as different.
`ChatConversation` and `ChatMessage` have no UserId: every query reaches the
owner through Project.UserId.
"""

import json
from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from metanoia_ai.application.ports.chat_repository import (
    ChatMessage,
    ChatRole,
    ChatTask,
    SavedDraft,
    SavedSubtask,
)
from metanoia_ai.domain.similar_tasks import SimilarSummary
from metanoia_ai.domain.task_breakdown import (
    MAX_TITLE_CHARS,
    Draft,
    DraftNumbers,
    Subtask,
)
from metanoia_ai.infrastructure.db.learning_repository import TASK_TYPE_FROM_DB

# API role <-> DB ChatMessage.Sender (CHECK: User / AI).
ROLE_TO_DB = {ChatRole.USER: "User", ChatRole.ASSISTANT: "AI"}
ROLE_FROM_DB = {name: role for role, name in ROLE_TO_DB.items()}
PROPOSAL_VERSION = 1  # bump when the ProposalJson shape changes

# Lock first, read after: a count inside the locking statement would use the
# snapshot taken before waiting for the lock and miss subtasks just written.
LOCK_TASK = text("""
    SELECT t."Id"
    FROM "Task" t
    JOIN "Project" p ON p."Id" = t."ProjectId"
    WHERE t."Id" = :task_id AND p."UserId" = :user_id
    FOR UPDATE OF t
""")

GET_TASK = text("""
    SELECT t."Id", t."ProjectId", t."TaskType", t."Status", t."Title",
           t."Description", t."DomainId", d."Name" AS "DomainName",
           c."Name" AS "ClientName", t."EstimatedMinutes", t."Deadline",
           u."TimeZone",
           (SELECT count(*) FROM "SubTask" s WHERE s."TaskId" = t."Id")
               AS "SubtaskCount"
    FROM "Task" t
    JOIN "Project" p ON p."Id" = t."ProjectId"
    JOIN "User" u ON u."Id" = p."UserId"
    JOIN "Domain" d ON d."Id" = t."DomainId"
    LEFT JOIN "Client" c ON c."Id" = p."ClientId"
    WHERE t."Id" = :task_id AND p."UserId" = :user_id
""")

GET_K_BIAS = text("""
    SELECT "KBias" FROM "UserDomainProfile"
    WHERE "UserId" = :user_id AND "DomainId" = :domain_id
""")

# One chat per task (plan #2); the oldest wins if the DB ever has two.
FIND_CONVERSATION = text("""
    SELECT cv."Id"
    FROM "ChatConversation" cv
    JOIN "Project" p ON p."Id" = cv."ProjectId"
    WHERE cv."TaskId" = :task_id AND cv."Scope" = 'Task' AND p."UserId" = :user_id
    ORDER BY cv."CreatedAt", cv."Id"
    LIMIT 1
""")

CREATE_CONVERSATION = text("""
    INSERT INTO "ChatConversation" ("ProjectId", "TaskId", "Scope", "Title", "Status")
    SELECT p."Id", t."Id", 'Task', :title, 'Open'
    FROM "Task" t
    JOIN "Project" p ON p."Id" = t."ProjectId"
    WHERE t."Id" = :task_id AND p."UserId" = :user_id
    RETURNING "Id"
""")

MESSAGE_COLUMNS = """
    m."Id", m."Sender", m."Content", m."ProposalJson"::text AS "ProposalJson",
    m."IsApplied", m."CreatedAt"
"""
OWN_CONVERSATION = """
    FROM "ChatMessage" m
    JOIN "ChatConversation" cv ON cv."Id" = m."ConversationId"
    JOIN "Project" p ON p."Id" = cv."ProjectId"
    WHERE m."ConversationId" = :conversation_id AND p."UserId" = :user_id
"""

LIST_MESSAGES = text(f"""
    SELECT {MESSAGE_COLUMNS} {OWN_CONVERSATION}
    ORDER BY m."CreatedAt", m."Id"
""")

# The newest `limit`, returned oldest first.
LIST_NEWEST_MESSAGES = text(f"""
    SELECT * FROM (
        SELECT {MESSAGE_COLUMNS} {OWN_CONVERSATION}
        ORDER BY m."CreatedAt" DESC, m."Id" DESC
        LIMIT :limit
    ) newest
    ORDER BY "CreatedAt", "Id"
""")

LATEST_DRAFT = text(f"""
    SELECT {MESSAGE_COLUMNS} {OWN_CONVERSATION}
      AND m."ProposalJson" IS NOT NULL
    ORDER BY m."CreatedAt" DESC, m."Id" DESC
    LIMIT 1
""")

# clock_timestamp(), not the default now(): now() is the same for every row of
# a transaction, so the user message and the AI reply would tie on CreatedAt.
ADD_MESSAGE = text("""
    INSERT INTO "ChatMessage" ("ConversationId", "Sender", "Content",
                               "ProposalJson", "CreatedAt")
    SELECT cv."Id", :sender, :content,
           CAST(CAST(:proposal AS text) AS jsonb), clock_timestamp()
    FROM "ChatConversation" cv
    JOIN "Project" p ON p."Id" = cv."ProjectId"
    WHERE cv."Id" = :conversation_id AND p."UserId" = :user_id
    RETURNING "Id", "IsApplied", "CreatedAt"
""")

ADD_SUBTASK = text("""
    INSERT INTO "SubTask" ("TaskId", "Title", "EstimatedMinutes", "OrderIndex", "Status")
    SELECT t."Id", :title, :minutes, :order_index, 'ToDo'
    FROM "Task" t
    JOIN "Project" p ON p."Id" = t."ProjectId"
    WHERE t."Id" = :task_id AND p."UserId" = :user_id
    RETURNING "Id"
""")

MARK_MESSAGE_APPLIED = text("""
    UPDATE "ChatMessage" m
    SET "IsApplied" = true
    FROM "ChatConversation" cv
    JOIN "Project" p ON p."Id" = cv."ProjectId"
    WHERE m."Id" = :message_id AND m."ConversationId" = :conversation_id
      AND cv."Id" = m."ConversationId" AND p."UserId" = :user_id
    RETURNING m."Id"
""")

MARK_CONVERSATION_APPLIED = text("""
    UPDATE "ChatConversation" cv
    SET "Status" = 'Applied'
    FROM "Project" p
    WHERE cv."Id" = :conversation_id AND p."Id" = cv."ProjectId"
      AND p."UserId" = :user_id
""")


class NotOwnedError(LookupError):
    """A write matched no row of this user. Means a bug upstream: never ignore it."""


# --- ProposalJson <-> SavedDraft (T26) ---


def draft_to_json(saved: SavedDraft) -> str:
    numbers = saved.numbers
    similar = saved.similar
    data: dict[str, Any] = {
        "version": PROPOSAL_VERSION,
        "subtasks": [
            {
                "title": s.title,
                "estimated_minutes": s.estimated_minutes,
                "order_index": s.order_index,
                "reason": s.reason,
            }
            for s in saved.draft.subtasks
        ],
        "numbers": {
            "your_estimate_minutes": numbers.your_estimate_minutes,
            "adjusted_minutes": numbers.adjusted_minutes,
            "subtask_total_minutes": numbers.subtask_total_minutes,
            "adjusted_is_hint": numbers.adjusted_is_hint,
            "warning": numbers.warning,
        },
        "similar": None
        if similar is None
        else {
            "count": similar.count,
            "done_count": similar.done_count,
            "average_ratio": similar.average_ratio,
        },
    }
    return json.dumps(data, ensure_ascii=False)


def draft_from_json(raw: str) -> SavedDraft:
    data = json.loads(raw)
    numbers = data["numbers"]
    similar = data.get("similar")
    return SavedDraft(
        draft=Draft(
            tuple(
                Subtask(
                    title=s["title"],
                    estimated_minutes=s["estimated_minutes"],
                    order_index=s["order_index"],
                    reason=s.get("reason"),
                )
                for s in data["subtasks"]
            )
        ),
        numbers=DraftNumbers(
            your_estimate_minutes=numbers["your_estimate_minutes"],
            adjusted_minutes=numbers["adjusted_minutes"],
            subtask_total_minutes=numbers["subtask_total_minutes"],
            adjusted_is_hint=numbers["adjusted_is_hint"],
            warning=numbers.get("warning"),
        ),
        similar=None
        if similar is None
        else SimilarSummary(
            similar["count"], similar["done_count"], similar["average_ratio"]
        ),
    )


def to_message(row: Row) -> ChatMessage:
    return ChatMessage(
        message_id=row.Id,
        role=ROLE_FROM_DB[row.Sender],
        content=row.Content,
        created_at=row.CreatedAt,
        draft=draft_from_json(row.ProposalJson) if row.ProposalJson else None,
        is_applied=row.IsApplied,
    )


class SqlChatRepository:
    """ChatRepository on Postgres. One instance per request (one session)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def transaction(self) -> AbstractAsyncContextManager[object]:
        return self.session.begin()

    async def get_task(
        self, user_id: UUID, task_id: UUID, lock: bool = False
    ) -> ChatTask | None:
        params = {"task_id": task_id, "user_id": user_id}
        if lock:
            locked = await self.session.execute(LOCK_TASK, params)
            if locked.one_or_none() is None:
                return None
        row = (await self.session.execute(GET_TASK, params)).one_or_none()
        if row is None:
            return None
        return ChatTask(
            task_id=row.Id,
            project_id=row.ProjectId,
            task_type=TASK_TYPE_FROM_DB[row.TaskType],
            status=row.Status,
            title=row.Title,
            description=row.Description,
            domain_id=row.DomainId,
            domain_name=row.DomainName,
            client_name=row.ClientName,
            estimated_minutes=row.EstimatedMinutes,
            deadline=row.Deadline,
            time_zone=ZoneInfo(row.TimeZone),
            subtask_count=row.SubtaskCount,
        )

    async def get_k_bias(self, user_id: UUID, domain_id: UUID) -> Decimal | None:
        params = {"user_id": user_id, "domain_id": domain_id}
        return (await self.session.execute(GET_K_BIAS, params)).scalar_one_or_none()

    async def find_conversation(self, user_id: UUID, task_id: UUID) -> UUID | None:
        params = {"task_id": task_id, "user_id": user_id}
        result = await self.session.execute(FIND_CONVERSATION, params)
        return result.scalar_one_or_none()

    async def create_conversation(self, user_id: UUID, task: ChatTask) -> UUID:
        params = {
            "task_id": task.task_id,
            "user_id": user_id,
            "title": task.title[:MAX_TITLE_CHARS],
        }
        result = await self.session.execute(CREATE_CONVERSATION, params)
        conversation_id = result.scalar_one_or_none()
        if conversation_id is None:
            raise NotOwnedError(str(task.task_id))
        return conversation_id

    async def list_messages(
        self, user_id: UUID, conversation_id: UUID, limit: int | None = None
    ) -> list[ChatMessage]:
        params: dict[str, Any] = {
            "conversation_id": conversation_id,
            "user_id": user_id,
        }
        if limit is None:
            rows = await self.session.execute(LIST_MESSAGES, params)
        else:
            rows = await self.session.execute(
                LIST_NEWEST_MESSAGES, params | {"limit": limit}
            )
        return [to_message(row) for row in rows]

    async def latest_draft(
        self, user_id: UUID, conversation_id: UUID
    ) -> ChatMessage | None:
        params = {"conversation_id": conversation_id, "user_id": user_id}
        row = (await self.session.execute(LATEST_DRAFT, params)).one_or_none()
        return to_message(row) if row else None

    async def add_message(
        self,
        user_id: UUID,
        conversation_id: UUID,
        role: ChatRole,
        content: str,
        draft: SavedDraft | None = None,
    ) -> ChatMessage:
        params = {
            "conversation_id": conversation_id,
            "user_id": user_id,
            "sender": ROLE_TO_DB[role],
            "content": content,
            "proposal": draft_to_json(draft) if draft else None,
        }
        row = (await self.session.execute(ADD_MESSAGE, params)).one_or_none()
        if row is None:
            raise NotOwnedError(str(conversation_id))
        return ChatMessage(row.Id, role, content, row.CreatedAt, draft, row.IsApplied)

    async def add_subtasks(
        self, user_id: UUID, task_id: UUID, subtasks: Sequence[Subtask]
    ) -> list[SavedSubtask]:
        saved = []
        for subtask in subtasks:
            params = {
                "task_id": task_id,
                "user_id": user_id,
                "title": subtask.title,
                "minutes": subtask.estimated_minutes,
                "order_index": subtask.order_index,
            }
            result = await self.session.execute(ADD_SUBTASK, params)
            subtask_id = result.scalar_one_or_none()
            if subtask_id is None:
                raise NotOwnedError(str(task_id))
            saved.append(
                SavedSubtask(
                    subtask_id,
                    subtask.title,
                    subtask.estimated_minutes,
                    subtask.order_index,
                )
            )
        return saved

    async def mark_applied(
        self, user_id: UUID, conversation_id: UUID, message_id: UUID
    ) -> None:
        params = {
            "conversation_id": conversation_id,
            "user_id": user_id,
            "message_id": message_id,
        }
        result = await self.session.execute(MARK_MESSAGE_APPLIED, params)
        if result.scalar_one_or_none() is None:
            raise NotOwnedError(str(message_id))
        await self.session.execute(MARK_CONVERSATION_APPLIED, params)
