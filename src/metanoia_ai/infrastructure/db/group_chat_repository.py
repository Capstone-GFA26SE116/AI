"""FR-24: group plan chats in ChatConversation (Scope 'Group') + ChatMessage.

Needs the Backend change in plans/FR-24.sql (ChatConversation.GroupId, Scope 'Group').
The group tables are read only.
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from metanoia_ai.application.ports.group_chat_repository import (
    ChatGroup,
    GroupChatMessage,
    GroupConversation,
    NewGroupMessage,
)
from metanoia_ai.application.ports.project_chat_repository import ExistingWork
from metanoia_ai.domain.group_plan import TaskAssignment
from metanoia_ai.infrastructure.db.chat_repository import ROLE_FROM_DB, ROLE_TO_DB
from metanoia_ai.infrastructure.db.group_assignee_repository import (
    ROLE_FROM_DB as GROUP_ROLE,
)
from metanoia_ai.infrastructure.db.project_chat_repository import (
    ACTIVE_DOMAINS,
    PROPOSAL_VERSION,
    STATUS_FROM_DB,
    draft_to_json,
    notes_to_json,
    proposal_from_json,
)

GET_GROUP = text("""
    SELECT g."Id", g."Name", g."Description", g."Deadline", g."Status",
           m."Role" AS "CallerRole", u."TimeZone"
    FROM "WorkGroup" g
    JOIN "User" u ON u."Id" = :caller_id
    LEFT JOIN "GroupMember" m ON m."GroupId" = g."Id" AND m."UserId" = :caller_id
    WHERE g."Id" = :group_id
""")

CONVERSATION_COLUMNS = """
    SELECT cv."Id", cv."GroupId", cv."Title", cv."Status", cv."CreatedAt"
    FROM "ChatConversation" cv
    WHERE cv."Scope" = 'Group'
"""
GET_CONVERSATION = text(CONVERSATION_COLUMNS + ' AND cv."Id" = :conversation_id')
LIST_CONVERSATIONS = text(
    CONVERSATION_COLUMNS
    + ' AND cv."GroupId" = :group_id ORDER BY cv."CreatedAt" DESC, cv."Id" DESC'
)
CREATE_CONVERSATION = text("""
    INSERT INTO "ChatConversation" ("GroupId", "ProjectId", "TaskId", "Scope",
                                    "Title", "Status")
    VALUES (:group_id, NULL, NULL, 'Group', :title, 'Open')
    RETURNING "Id", "GroupId", "Title", "Status", "CreatedAt"
""")

LIST_MESSAGES = text("""
    SELECT m."Id", m."Sender", m."Content", m."ProposalJson"::text AS "ProposalJson",
           m."IsApplied", m."CreatedAt"
    FROM "ChatMessage" m
    JOIN "ChatConversation" cv ON cv."Id" = m."ConversationId"
    WHERE m."ConversationId" = :conversation_id AND cv."Scope" = 'Group'
    ORDER BY m."CreatedAt", m."Id"
""")
# clock_timestamp(): the user message and the AI reply must not tie on CreatedAt.
ADD_MESSAGE = text("""
    INSERT INTO "ChatMessage" ("ConversationId", "Sender", "Content",
                               "ProposalJson", "CreatedAt")
    SELECT cv."Id", :sender, :content,
           CAST(CAST(:proposal AS text) AS jsonb), clock_timestamp()
    FROM "ChatConversation" cv
    WHERE cv."Id" = :conversation_id AND cv."Scope" = 'Group'
    RETURNING "Id", "IsApplied", "CreatedAt"
""")
# Only an AI draft of this chat, and only once.
MARK_APPLIED = text("""
    UPDATE "ChatMessage" m
    SET "IsApplied" = true,
        "ProposalJson" = jsonb_set(
            m."ProposalJson", '{applied_at}', to_jsonb(clock_timestamp()))
    FROM "ChatConversation" cv
    WHERE m."Id" = :message_id AND m."ConversationId" = :conversation_id
      AND cv."Id" = m."ConversationId" AND cv."Scope" = 'Group'
      AND m."ProposalJson"->>'version' = :version
      AND m."ProposalJson"->>'applied_at' IS NULL
    RETURNING m."Id"
""")

GROUP_MILESTONES = text("""
    SELECT "Name" FROM "GroupMilestone" WHERE "GroupId" = :group_id
    ORDER BY "OrderIndex"
""")
GROUP_TASKS = text("""
    SELECT "Title" FROM "GroupTask"
    WHERE "GroupId" = :group_id AND "Status" <> 'Skipped'
    ORDER BY "CreatedAt" DESC, "Id"
""")


def proposal_json(message: NewGroupMessage) -> str | None:
    """A draft (with its member suggestions) or a checklist, as ChatMessage.ProposalJson."""
    if message.draft is not None and message.notes is not None:
        raise ValueError("a message has a draft or a checklist, not both")
    if message.notes is not None:
        return notes_to_json(message.notes)
    if message.draft is None:
        return None
    data = json.loads(draft_to_json(message.draft))
    data["assignments"] = [
        {
            "task_key": a.task_key,
            "user_id": str(a.user_id) if a.user_id else None,
            "fits": a.fits,
            "reason": a.reason,
        }
        for a in message.assignments
    ]
    return json.dumps(data, ensure_ascii=False)


def assignments_from_json(raw: str | None) -> tuple[TaskAssignment, ...]:
    if not raw:
        return ()
    items: list[dict[str, Any]] = json.loads(raw).get("assignments", [])
    return tuple(
        TaskAssignment(
            task_key=i["task_key"],
            user_id=UUID(i["user_id"]) if i["user_id"] else None,
            fits=i["fits"],
            reason=i["reason"],
        )
        for i in items
    )


def to_group(row: Row) -> ChatGroup:
    return ChatGroup(
        group_id=row.Id,
        name=row.Name,
        description=row.Description,
        deadline=row.Deadline,
        archived=row.Status != "Active",
        caller_role=GROUP_ROLE.get(row.CallerRole) if row.CallerRole else None,
        time_zone=ZoneInfo(row.TimeZone),
    )


def to_conversation(row: Row) -> GroupConversation:
    return GroupConversation(
        conversation_id=row.Id,
        group_id=row.GroupId,
        title=row.Title,
        status=STATUS_FROM_DB[row.Status],
        created_at=row.CreatedAt,
    )


def to_message(row: Row) -> GroupChatMessage:
    draft, notes = proposal_from_json(row.ProposalJson)
    applied = row.IsApplied or (
        bool(row.ProposalJson) and "applied_at" in json.loads(row.ProposalJson)
    )
    return GroupChatMessage(
        message_id=row.Id,
        role=ROLE_FROM_DB[row.Sender],
        content=row.Content,
        created_at=row.CreatedAt,
        draft=draft,
        assignments=assignments_from_json(row.ProposalJson),
        notes=notes,
        is_applied=applied,
    )


class SqlGroupChatRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        if self.session.in_transaction():
            await self.session.commit()
        async with self.session.begin():
            yield

    async def get_group(self, group_id: UUID, caller_id: UUID) -> ChatGroup | None:
        params = {"group_id": group_id, "caller_id": caller_id}
        row = (await self.session.execute(GET_GROUP, params)).one_or_none()
        return None if row is None else to_group(row)

    async def get_conversation(self, conversation_id: UUID) -> GroupConversation | None:
        params = {"conversation_id": conversation_id}
        row = (await self.session.execute(GET_CONVERSATION, params)).one_or_none()
        return None if row is None else to_conversation(row)

    async def list_conversations(self, group_id: UUID) -> list[GroupConversation]:
        rows = await self.session.execute(LIST_CONVERSATIONS, {"group_id": group_id})
        return [to_conversation(r) for r in rows]

    async def create_conversation(
        self, group_id: UUID, title: str
    ) -> GroupConversation:
        params = {"group_id": group_id, "title": title}
        row = (await self.session.execute(CREATE_CONVERSATION, params)).one()
        return to_conversation(row)

    async def list_messages(self, conversation_id: UUID) -> list[GroupChatMessage]:
        params = {"conversation_id": conversation_id}
        return [
            to_message(r) for r in await self.session.execute(LIST_MESSAGES, params)
        ]

    async def add_message(
        self, conversation_id: UUID, message: NewGroupMessage
    ) -> GroupChatMessage:
        params = {
            "conversation_id": conversation_id,
            "sender": ROLE_TO_DB[message.role],
            "content": message.content,
            "proposal": proposal_json(message),
        }
        row = (await self.session.execute(ADD_MESSAGE, params)).one()
        return GroupChatMessage(
            row.Id,
            message.role,
            message.content,
            row.CreatedAt,
            message.draft,
            message.assignments,
            message.notes,
            row.IsApplied,
        )

    async def mark_applied(self, conversation_id: UUID, message_id: UUID) -> bool:
        params = {
            "conversation_id": conversation_id,
            "message_id": message_id,
            "version": PROPOSAL_VERSION,
        }
        row = (await self.session.execute(MARK_APPLIED, params)).one_or_none()
        return row is not None

    async def active_domains(self) -> dict[UUID, str]:
        return {r.Id: r.Name for r in await self.session.execute(ACTIVE_DOMAINS)}

    async def existing_work(self, group_id: UUID) -> ExistingWork:
        params = {"group_id": group_id}
        milestones = await self.session.execute(GROUP_MILESTONES, params)
        tasks = await self.session.execute(GROUP_TASKS, params)
        return ExistingWork(
            tuple(r.Name for r in milestones), tuple(r.Title for r in tasks)
        )
