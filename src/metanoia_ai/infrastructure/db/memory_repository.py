"""Long-term memory storage (FR-19) on the shared Supabase Postgres.

Table and column names are quoted: Postgres treats "TaskId" and taskid as different.
`TaskExecutionMemory` has no UserId: every query reaches the owner via Task → Project.
"""

import math
from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from metanoia_ai.application.ports.memory_repository import (
    PendingMemory,
    TaskForMemory,
)
from metanoia_ai.application.use_cases.record_task_completion import (
    TaskNotFoundError,
)
from metanoia_ai.domain.memory_text import (
    MemoryDraft,
    MemoryOutcome,
    MemorySession,
    StopReason,
)
from metanoia_ai.infrastructure.db.learning_repository import TASK_TYPE_FROM_DB

# Domain names <-> the strings allowed by the DB CHECK constraints.
OUTCOME_TO_DB = {
    MemoryOutcome.DONE: "Completed",  # plan #23
    MemoryOutcome.SKIPPED: "NotDone",
}
STOP_REASON_FROM_DB = {
    "Completed": StopReason.COMPLETED,
    "Interrupted": StopReason.INTERRUPTED,
    "Blocked": StopReason.BLOCKED,
    "Other": StopReason.OTHER,
}

GET_TASK = text("""
    SELECT t."Id", t."TaskType", t."Status", t."Title", t."EstimatedMinutes",
           d."Name" AS "DomainName", c."Name" AS "ClientName"
    FROM "Task" t
    JOIN "Project" p ON p."Id" = t."ProjectId"
    JOIN "Domain" d ON d."Id" = t."DomainId"
    LEFT JOIN "Client" c ON c."Id" = p."ClientId"
    WHERE t."Id" = :task_id AND p."UserId" = :user_id
""")

# Sessions of the task and of its subtasks (both carry TaskId).
GET_SESSIONS = text("""
    SELECT "StartedAt", "EndedAt", "StopReason", "Note"
    FROM "FocusSession"
    WHERE "TaskId" = :task_id
    ORDER BY "StartedAt"
""")

# Insert only for the owner's task. One row per task (UNIQUE "TaskId", plan #22):
# a second call replaces the text and drops the old vector.
UPSERT = text("""
    INSERT INTO "TaskExecutionMemory" (
        "TaskId", "Content", "Outcome", "EstimatedMinutes", "ActualMinutes",
        "Embedding", "EmbeddingStatus", "EmbeddingModel"
    )
    SELECT t."Id", :content, :outcome, :estimated_minutes, :actual_minutes,
           NULL, 'Pending', :model
    FROM "Task" t
    JOIN "Project" p ON p."Id" = t."ProjectId"
    WHERE t."Id" = :task_id AND p."UserId" = :user_id
    ON CONFLICT ("TaskId") DO UPDATE SET
        "Content" = EXCLUDED."Content",
        "Outcome" = EXCLUDED."Outcome",
        "EstimatedMinutes" = EXCLUDED."EstimatedMinutes",
        "ActualMinutes" = EXCLUDED."ActualMinutes",
        "Embedding" = NULL,
        "EmbeddingStatus" = 'Pending',
        "EmbeddingModel" = EXCLUDED."EmbeddingModel"
    RETURNING "Id"
""")

GET_PENDING = text("""
    SELECT m."Id", p."UserId", m."Content"
    FROM "TaskExecutionMemory" m
    JOIN "Task" t ON t."Id" = m."TaskId"
    JOIN "Project" p ON p."Id" = t."ProjectId"
    WHERE m."Id" = :memory_id AND p."UserId" = :user_id
      AND m."EmbeddingStatus" <> 'Done'
""")

# Startup scan: the only query across users (plan #20). IDs only.
LIST_WAITING = text("""
    SELECT m."Id", p."UserId"
    FROM "TaskExecutionMemory" m
    JOIN "Task" t ON t."Id" = m."TaskId"
    JOIN "Project" p ON p."Id" = t."ProjectId"
    WHERE m."EmbeddingStatus" <> 'Done'
    ORDER BY m."CreatedAt", m."Id"
    LIMIT :limit
""")

# The vector goes in as text, then Postgres turns it into a pgvector value.
# "Content" = :content: never store the vector of an older text (plan #15).
SAVE_EMBEDDING = text("""
    UPDATE "TaskExecutionMemory" m
    SET "Embedding" = CAST(CAST(:vector AS text) AS vector),
        "EmbeddingStatus" = 'Done',
        "EmbeddingModel" = :model
    FROM "Task" t
    JOIN "Project" p ON p."Id" = t."ProjectId"
    WHERE m."Id" = :memory_id AND t."Id" = m."TaskId" AND p."UserId" = :user_id
      AND m."Content" = :content
    RETURNING m."Id"
""")

MARK_FAILED = text("""
    UPDATE "TaskExecutionMemory" m
    SET "EmbeddingStatus" = 'Failed'
    FROM "Task" t
    JOIN "Project" p ON p."Id" = t."ProjectId"
    WHERE m."Id" = :memory_id AND t."Id" = m."TaskId" AND p."UserId" = :user_id
      AND m."Content" = :content
    RETURNING m."Id"
""")


def to_pgvector(vector: Sequence[float]) -> str:
    """pgvector text format: "[0.1,0.2,0.3]". Rejects NaN and infinity (pgvector does too)."""
    numbers = [float(value) for value in vector]
    if not numbers:
        raise ValueError("A vector needs at least one number")
    if not all(math.isfinite(number) for number in numbers):
        raise ValueError("A vector cannot contain NaN or infinity")
    return "[" + ",".join(repr(number) for number in numbers) + "]"


class SqlMemoryRepository:
    """MemoryRepository on Postgres. One instance per session."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def transaction(self) -> AbstractAsyncContextManager[object]:
        return self.session.begin()

    async def get_task(self, user_id: UUID, task_id: UUID) -> TaskForMemory | None:
        params = {"task_id": task_id, "user_id": user_id}
        row = (await self.session.execute(GET_TASK, params)).one_or_none()
        if row is None:
            return None
        sessions = await self.session.execute(GET_SESSIONS, {"task_id": task_id})
        return TaskForMemory(
            task_id=row.Id,
            task_type=TASK_TYPE_FROM_DB[row.TaskType],
            status=row.Status,
            title=row.Title,
            domain_name=row.DomainName,
            estimated_minutes=row.EstimatedMinutes,
            client_name=row.ClientName,
            sessions=tuple(
                MemorySession(
                    started_at=s.StartedAt,
                    ended_at=s.EndedAt,
                    stop_reason=STOP_REASON_FROM_DB.get(s.StopReason),
                    note=s.Note,
                )
                for s in sessions
            ),
        )

    async def upsert(
        self, user_id: UUID, task_id: UUID, draft: MemoryDraft, model: str | None
    ) -> UUID:
        memory_id = (
            await self.session.execute(
                UPSERT,
                {
                    "task_id": task_id,
                    "user_id": user_id,
                    "content": draft.content,
                    "outcome": OUTCOME_TO_DB[draft.outcome],
                    "estimated_minutes": draft.estimated_minutes,
                    "actual_minutes": draft.actual_minutes,
                    "model": model,
                },
            )
        ).scalar_one_or_none()
        if memory_id is None:  # not this user's task: nothing was written
            raise TaskNotFoundError(str(task_id))
        return memory_id

    async def get_pending(self, user_id: UUID, memory_id: UUID) -> PendingMemory | None:
        params = {"memory_id": memory_id, "user_id": user_id}
        row = (await self.session.execute(GET_PENDING, params)).one_or_none()
        if row is None:
            return None
        return PendingMemory(memory_id=row.Id, user_id=row.UserId, content=row.Content)

    async def list_waiting(self, limit: int) -> list[tuple[UUID, UUID]]:
        rows = await self.session.execute(LIST_WAITING, {"limit": limit})
        return [(row.Id, row.UserId) for row in rows]

    async def save_embedding(
        self,
        user_id: UUID,
        memory_id: UUID,
        content: str,
        vector: list[float],
        model: str,
    ) -> bool:
        params = {
            "memory_id": memory_id,
            "user_id": user_id,
            "content": content,
            "vector": to_pgvector(vector),
            "model": model,
        }
        result = await self.session.execute(SAVE_EMBEDDING, params)
        return result.scalar_one_or_none() is not None

    async def mark_failed(self, user_id: UUID, memory_id: UUID, content: str) -> bool:
        params = {"memory_id": memory_id, "user_id": user_id, "content": content}
        result = await self.session.execute(MARK_FAILED, params)
        return result.scalar_one_or_none() is not None
