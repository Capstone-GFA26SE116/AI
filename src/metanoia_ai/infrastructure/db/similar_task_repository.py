"""Similar task search (FR-09) on the shared Supabase Postgres. Read-only.

Table and column names are quoted: Postgres treats "TaskId" and taskid as different.
Full-text uses the 'simple' config of the GIN index, written out in every query:
the database default is 'english', which would skip the index (plan #19).
"""

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from metanoia_ai.application.ports.similar_task_repository import (
    QueryTask,
    VectorMatch,
)
from metanoia_ai.domain.similar_tasks import WORD, PastTask, SimilarOutcome
from metanoia_ai.infrastructure.db.learning_repository import TASK_TYPE_FROM_DB
from metanoia_ai.infrastructure.db.memory_repository import to_pgvector

# DB Outcome → answer. "Partial" is never written by the AI (FR-19 #23); if the
# Backend ever writes it, it is shown like "skipped" (no ratio).
OUTCOME_FROM_DB = {
    "Completed": SimilarOutcome.DONE,
    "NotDone": SimilarOutcome.SKIPPED,
    "Partial": SimilarOutcome.SKIPPED,
}

GET_TASK = text("""
    SELECT t."Id", t."TaskType", t."Title", d."Name" AS "DomainName",
           c."Name" AS "ClientName", u."TimeZone"
    FROM "Task" t
    JOIN "Project" p ON p."Id" = t."ProjectId"
    JOIN "User" u ON u."Id" = p."UserId"
    JOIN "Domain" d ON d."Id" = t."DomainId"
    LEFT JOIN "Client" c ON c."Id" = p."ClientId"
    WHERE t."Id" = :task_id AND p."UserId" = :user_id
""")

# pgvector 0.8: keep scanning the HNSW index until enough rows pass the user
# filter (plan #12). Only for this transaction.
ITERATIVE_SCAN = text("SET LOCAL hnsw.iterative_scan = relaxed_order")

# <=> is cosine distance; similarity = 1 − distance.
VECTOR_SEARCH = text("""
    SELECT m."TaskId", t."Title", d."Name" AS "DomainName", m."Outcome",
           m."EstimatedMinutes", m."ActualMinutes", t."CompletedAt",
           1 - (m."Embedding" <=> CAST(CAST(:vector AS text) AS vector)) AS "Similarity"
    FROM "TaskExecutionMemory" m
    JOIN "Task" t ON t."Id" = m."TaskId"
    JOIN "Project" p ON p."Id" = t."ProjectId"
    JOIN "Domain" d ON d."Id" = t."DomainId"
    WHERE p."UserId" = :user_id
      AND m."TaskId" <> :exclude_task_id
      AND m."EmbeddingStatus" = 'Done'
    ORDER BY m."Embedding" <=> CAST(CAST(:vector AS text) AS vector)
    LIMIT :limit
""")

# Any status: memories still waiting for a vector are found here (plan #13).
TEXT_SEARCH = text("""
    SELECT m."TaskId", t."Title", d."Name" AS "DomainName", m."Outcome",
           m."EstimatedMinutes", m."ActualMinutes", t."CompletedAt",
           ts_rank_cd(to_tsvector('simple', m."Content"),
                      to_tsquery('simple', :terms)) AS "Rank"
    FROM "TaskExecutionMemory" m
    JOIN "Task" t ON t."Id" = m."TaskId"
    JOIN "Project" p ON p."Id" = t."ProjectId"
    JOIN "Domain" d ON d."Id" = t."DomainId"
    WHERE p."UserId" = :user_id
      AND m."TaskId" <> :exclude_task_id
      AND to_tsvector('simple', m."Content") @@ to_tsquery('simple', :terms)
    ORDER BY "Rank" DESC, t."CompletedAt" DESC NULLS LAST, m."TaskId"
    LIMIT :limit
""")


def clean_tsquery(terms: str) -> str | None:
    """Keep only plain words joined by " | ", so to_tsquery can never fail.

    The domain already builds clean terms; this guards the SQL boundary.
    """
    words = [w for w in (part.strip() for part in terms.split("|")) if w]
    safe = [w for w in words if WORD.fullmatch(w)]
    return " | ".join(safe) or None


def to_past_task(row: Row) -> PastTask:
    return PastTask(
        task_id=row.TaskId,
        title=row.Title,
        domain_name=row.DomainName,
        outcome=OUTCOME_FROM_DB[row.Outcome],
        estimated_minutes=row.EstimatedMinutes,
        actual_minutes=row.ActualMinutes,
        finished_at=row.CompletedAt,
    )


class SqlSimilarTaskRepository:
    """SimilarTaskRepository on Postgres. One instance per session."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @asynccontextmanager
    async def read_only(self) -> AsyncIterator[None]:
        async with self.session.begin():
            await self.session.execute(text("SET TRANSACTION READ ONLY"))
            yield

    def transaction(self) -> AbstractAsyncContextManager[object]:
        return self.session.begin()

    async def get_task(self, user_id: UUID, task_id: UUID) -> QueryTask | None:
        params = {"task_id": task_id, "user_id": user_id}
        row = (await self.session.execute(GET_TASK, params)).one_or_none()
        if row is None:
            return None
        return QueryTask(
            task_id=row.Id,
            task_type=TASK_TYPE_FROM_DB[row.TaskType],
            title=row.Title,
            domain_name=row.DomainName,
            client_name=row.ClientName,
            time_zone=ZoneInfo(row.TimeZone),
        )

    async def vector_search(
        self, user_id: UUID, exclude_task_id: UUID, vector: list[float], limit: int
    ) -> list[VectorMatch]:
        await self.session.execute(ITERATIVE_SCAN)
        params = {
            "user_id": user_id,
            "exclude_task_id": exclude_task_id,
            "vector": to_pgvector(vector),
            "limit": limit,
        }
        rows = await self.session.execute(VECTOR_SEARCH, params)
        matches = [VectorMatch(to_past_task(r), float(r.Similarity)) for r in rows]
        # relaxed_order may return rows slightly out of order: sort again.
        return sorted(matches, key=lambda m: m.similarity, reverse=True)

    async def text_search(
        self, user_id: UUID, exclude_task_id: UUID, terms: str, limit: int
    ) -> list[PastTask]:
        query = clean_tsquery(terms)
        if query is None:
            return []
        params = {
            "user_id": user_id,
            "exclude_task_id": exclude_task_id,
            "terms": query,
            "limit": limit,
        }
        rows = await self.session.execute(TEXT_SEARCH, params)
        return [to_past_task(r) for r in rows]
