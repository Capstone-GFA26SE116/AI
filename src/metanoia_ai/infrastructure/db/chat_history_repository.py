"""ChatHistoryRepository on Postgres (plans/chat-history.md). Read only."""

from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from metanoia_ai.application.ports.chat_history_repository import (
    ChatMatch,
    ChatScope,
    ChatSummary,
)
from metanoia_ai.infrastructure.db.chat_repository import ROLE_FROM_DB
from metanoia_ai.infrastructure.db.project_chat_repository import STATUS_FROM_DB

SCOPE_FROM_DB = {"Project": ChatScope.PROJECT, "Task": ChatScope.TASK}

GET_TIME_ZONE = text('SELECT "TimeZone" FROM "User" WHERE "Id" = :user_id')

# Every chat of the user (owner = Project.UserId), its last message and counts.
# A chat with no message has no "last" row and drops out (AC1).
# Applied: the old flag (task chats) or applied_at in the draft (FR-08b-4).
_CHATS = """
    WITH chats AS (
        SELECT cv."Id", cv."Scope", cv."Title", cv."Status", cv."ProjectId",
               p."Name" AS "ProjectName", cv."TaskId", t."Title" AS "TaskTitle"
        FROM "ChatConversation" cv
        JOIN "Project" p ON p."Id" = cv."ProjectId"
        LEFT JOIN "Task" t ON t."Id" = cv."TaskId"
        WHERE p."UserId" = :user_id
    )
    SELECT c.*, last."Sender" AS "LastSender", last."Content" AS "LastContent",
           last."CreatedAt" AS "LastAt", stats."Count", stats."Applied",
           {match_columns}
    FROM chats c
    JOIN LATERAL (
        SELECT m."Sender", m."Content", m."CreatedAt" FROM "ChatMessage" m
        WHERE m."ConversationId" = c."Id"
        ORDER BY m."CreatedAt" DESC, m."Id" DESC LIMIT 1
    ) last ON true
    JOIN LATERAL (
        SELECT count(*) AS "Count",
               coalesce(bool_or(m."IsApplied"
                   OR m."ProposalJson" ->> 'applied_at' IS NOT NULL), false) AS "Applied"
        FROM "ChatMessage" m WHERE m."ConversationId" = c."Id"
    ) stats ON true
    {match_join}
    ORDER BY last."CreatedAt" DESC, c."Id" DESC
    LIMIT :limit OFFSET :offset
"""

LIST_CHATS = text(
    _CHATS.format(
        match_columns='NULL AS "MatchId", NULL AS "MatchContent", NULL AS "MatchAt"',
        match_join="",
    )
)

# The newest message containing the text; or the text is in a name (AC4).
SEARCH_CHATS = text(
    _CHATS.format(
        match_columns=(
            'hit."Id" AS "MatchId", hit."Content" AS "MatchContent", '
            'hit."CreatedAt" AS "MatchAt"'
        ),
        match_join="""
    LEFT JOIN LATERAL (
        SELECT m."Id", m."Content", m."CreatedAt" FROM "ChatMessage" m
        WHERE m."ConversationId" = c."Id" AND m."Content" ILIKE :pattern
        ORDER BY m."CreatedAt" DESC, m."Id" DESC LIMIT 1
    ) hit ON true
    WHERE hit."Id" IS NOT NULL OR c."ProjectName" ILIKE :pattern
       OR c."TaskTitle" ILIKE :pattern OR c."Title" ILIKE :pattern""",
    )
)


def like_pattern(query: str) -> str:
    """`%query%` where % _ and \\ typed by the user are plain characters (ILIKE escape \\)."""
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def to_summary(row: Any) -> ChatSummary:
    match = None
    if row.MatchId is not None:
        match = ChatMatch(row.MatchId, row.MatchContent, row.MatchAt)
    return ChatSummary(
        conversation_id=row.Id,
        scope=SCOPE_FROM_DB[row.Scope],
        title=row.Title,
        status=STATUS_FROM_DB[row.Status],
        applied=bool(row.Applied),
        project_id=row.ProjectId,
        project_name=row.ProjectName,
        task_id=row.TaskId,
        task_title=row.TaskTitle,
        message_count=row.Count,
        last_role=ROLE_FROM_DB[row.LastSender],
        last_content=row.LastContent,
        last_at=row.LastAt,
        match=match,
    )


class SqlChatHistoryRepository:
    """One instance per request (one session)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def time_zone(self, user_id: UUID) -> ZoneInfo | None:
        result = await self.session.execute(GET_TIME_ZONE, {"user_id": user_id})
        name = result.scalar_one_or_none()
        return ZoneInfo(name) if name else None

    async def search(
        self, user_id: UUID, query: str | None, limit: int, offset: int
    ) -> list[ChatSummary]:
        params: dict[str, Any] = {"user_id": user_id, "limit": limit, "offset": offset}
        statement = LIST_CHATS
        if query:
            statement = SEARCH_CHATS
            params["pattern"] = like_pattern(query)
        rows = (await self.session.execute(statement, params)).all()
        return [to_summary(row) for row in rows]
