"""Project chat storage (FR-08b-1, FR-08b-2) on the shared Supabase Postgres.

Table and column names are quoted: Postgres treats "ProjectId" and projectid as
different. `ChatConversation`, `ChatMessage`, `Milestone` and `Task` have no
UserId: every query reaches the owner through Project.UserId. Every chat query
also checks Scope = 'Project', so a task chat (FR-08a) is never found here.
`ProjectDocument` and `ChatAttachment` (FR-08b-2) reach the owner the same way.
"""

import json
from contextlib import AbstractAsyncContextManager
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from metanoia_ai.application.ports.project_chat_repository import (
    AttachmentSource,
    ChatAttachment,
    ChatProject,
    ChatRole,
    ConversationStatus,
    DocumentSourceType,
    ExistingWork,
    NewAttachment,
    NewMilestone,
    NewTask,
    ProjectChatMessage,
    ProjectConversation,
    ProjectDocument,
    SavedProjectDraft,
)
from metanoia_ai.domain.project_breakdown import (
    DomainNumbers,
    DraftMilestone,
    DraftTask,
    ProjectDraft,
    ProjectNumbers,
)
from metanoia_ai.infrastructure.db.chat_repository import (
    ROLE_FROM_DB,
    ROLE_TO_DB,
    NotOwnedError,
)

PROPOSAL_VERSION = "project_v1"  # #31: not the task chat's 1
STATUS_FROM_DB = {
    "Open": ConversationStatus.OPEN,
    "Applied": ConversationStatus.APPLIED,
    "Closed": ConversationStatus.CLOSED,
}
DOCUMENT_TYPE_FROM_DB = {
    "File": DocumentSourceType.FILE,
    "Link": DocumentSourceType.LINK,
}
SOURCE_TO_DB = {AttachmentSource.HUB: "Hub", AttachmentSource.UPLOAD: "Upload"}
SOURCE_FROM_DB = {db: source for source, db in SOURCE_TO_DB.items()}

PROJECT_COLUMNS = """
    SELECT p."Id", p."Name", p."Description", p."Deadline", p."Status",
           p."AllowAiDocumentAccess", c."Name" AS "ClientName", u."TimeZone"
    FROM "Project" p
    JOIN "User" u ON u."Id" = p."UserId"
    LEFT JOIN "Client" c ON c."Id" = p."ClientId"
    WHERE p."Id" = :project_id AND p."UserId" = :user_id
"""
GET_PROJECT = text(PROJECT_COLUMNS)
# FOR UPDATE OF p: lock only the project row, not the user or client rows.
LOCK_PROJECT = text(PROJECT_COLUMNS + " FOR UPDATE OF p")

CONVERSATION_COLUMNS = """
    SELECT cv."Id", cv."ProjectId", cv."Title", cv."Status", cv."CreatedAt"
    FROM "ChatConversation" cv
    JOIN "Project" p ON p."Id" = cv."ProjectId"
    WHERE cv."Scope" = 'Project' AND p."UserId" = :user_id
"""
GET_CONVERSATION = text(CONVERSATION_COLUMNS + ' AND cv."Id" = :conversation_id')
LOCK_CONVERSATION = text(
    CONVERSATION_COLUMNS + ' AND cv."Id" = :conversation_id FOR UPDATE OF cv'
)
LIST_CONVERSATIONS = text(
    CONVERSATION_COLUMNS
    + ' AND cv."ProjectId" = :project_id ORDER BY cv."CreatedAt" DESC, cv."Id" DESC'
)

CREATE_CONVERSATION = text("""
    INSERT INTO "ChatConversation" ("ProjectId", "TaskId", "Scope", "Title", "Status")
    SELECT p."Id", NULL, 'Project', :title, 'Open'
    FROM "Project" p
    WHERE p."Id" = :project_id AND p."UserId" = :user_id
    RETURNING "Id", "ProjectId", "Title", "Status", "CreatedAt"
""")

MESSAGE_COLUMNS = """
    SELECT m."Id", m."Sender", m."Content", m."ProposalJson"::text AS "ProposalJson",
           m."IsApplied", m."CreatedAt"
    FROM "ChatMessage" m
    JOIN "ChatConversation" cv ON cv."Id" = m."ConversationId"
    JOIN "Project" p ON p."Id" = cv."ProjectId"
    WHERE m."ConversationId" = :conversation_id AND cv."Scope" = 'Project'
      AND p."UserId" = :user_id
"""
LIST_MESSAGES = text(MESSAGE_COLUMNS + ' ORDER BY m."CreatedAt", m."Id"')
LATEST_DRAFT = text(
    MESSAGE_COLUMNS
    + ' AND m."ProposalJson" IS NOT NULL ORDER BY m."CreatedAt" DESC, m."Id" DESC LIMIT 1'
)

# clock_timestamp(), not the default now(): now() is the same for every row of
# a transaction, so the user message and the AI reply would tie on CreatedAt.
ADD_MESSAGE = text("""
    INSERT INTO "ChatMessage" ("ConversationId", "Sender", "Content",
                               "ProposalJson", "CreatedAt")
    SELECT cv."Id", :sender, :content,
           CAST(CAST(:proposal AS text) AS jsonb), clock_timestamp()
    FROM "ChatConversation" cv
    JOIN "Project" p ON p."Id" = cv."ProjectId"
    WHERE cv."Id" = :conversation_id AND cv."Scope" = 'Project'
      AND p."UserId" = :user_id
    RETURNING "Id", "IsApplied", "CreatedAt"
""")

# --- FR-08b-2: library documents and chat attachments ---

GET_DOCUMENT = text("""
    SELECT d."Id", d."ProjectId", d."Name", d."SourceType", d."StoragePath",
           d."SizeBytes"
    FROM "ProjectDocument" d
    JOIN "Project" p ON p."Id" = d."ProjectId"
    WHERE d."Id" = :document_id AND p."UserId" = :user_id
""")

# Only for a user message of this user's project chat; clock_timestamp() keeps
# the attachments of one message in the order they were added.
ADD_ATTACHMENT = text("""
    INSERT INTO "ChatAttachment" ("MessageId", "DocumentId", "Source", "Name",
                                  "ExtractedText", "IsTruncated", "CreatedAt")
    SELECT m."Id", CAST(:document_id AS uuid), :source, :name, :extracted_text,
           :is_truncated, clock_timestamp()
    FROM "ChatMessage" m
    JOIN "ChatConversation" cv ON cv."Id" = m."ConversationId"
    JOIN "Project" p ON p."Id" = cv."ProjectId"
    WHERE m."Id" = :message_id AND m."Sender" = 'User' AND cv."Scope" = 'Project'
      AND p."UserId" = :user_id
    RETURNING "Id", "CreatedAt"
""")

LIST_ATTACHMENTS = text("""
    SELECT a."Id", a."MessageId", a."Source", a."DocumentId", a."Name",
           a."ExtractedText", a."IsTruncated", a."CreatedAt"
    FROM "ChatAttachment" a
    JOIN "ChatMessage" m ON m."Id" = a."MessageId"
    JOIN "ChatConversation" cv ON cv."Id" = m."ConversationId"
    JOIN "Project" p ON p."Id" = cv."ProjectId"
    WHERE m."ConversationId" = :conversation_id AND cv."Scope" = 'Project'
      AND p."UserId" = :user_id
    ORDER BY a."CreatedAt", a."Id"
""")

ACTIVE_DOMAINS = text("""
    SELECT "Id", "Name" FROM "Domain" WHERE "IsActive" ORDER BY "Name", "Id"
""")

GET_K_BIASES = text("""
    SELECT "DomainId", "KBias" FROM "UserDomainProfile" WHERE "UserId" = :user_id
""")

# Milestone has no CreatedAt: the highest OrderIndex is the newest.
MILESTONE_TITLES = text("""
    SELECT m."Name"
    FROM "Milestone" m
    JOIN "Project" p ON p."Id" = m."ProjectId"
    WHERE m."ProjectId" = :project_id AND p."UserId" = :user_id
    ORDER BY m."OrderIndex" DESC
""")

TASK_TITLES = text("""
    SELECT t."Title"
    FROM "Task" t
    JOIN "Project" p ON p."Id" = t."ProjectId"
    WHERE t."ProjectId" = :project_id AND p."UserId" = :user_id
    ORDER BY t."CreatedAt" DESC, t."Id" DESC
""")

NEXT_MILESTONE_ORDER = text("""
    SELECT COALESCE(MAX(m."OrderIndex"), 0) + 1
    FROM "Milestone" m
    JOIN "Project" p ON p."Id" = m."ProjectId"
    WHERE m."ProjectId" = :project_id AND p."UserId" = :user_id
""")

# Status stays the DB default (Planned, #27).
ADD_MILESTONE = text("""
    INSERT INTO "Milestone" ("ProjectId", "Name", "OrderIndex", "DueDate")
    SELECT p."Id", :name, :order_index, :due_date
    FROM "Project" p
    WHERE p."Id" = :project_id AND p."UserId" = :user_id
    RETURNING "Id"
""")

# The milestone must be one of this project's (never another user's).
ADD_TASK = text("""
    INSERT INTO "Task" ("ProjectId", "MilestoneId", "DomainId", "Title", "TaskType",
                        "Status", "Deadline", "EstimatedMinutes", "AiEstimatedMinutes")
    SELECT p."Id", m."Id", :domain_id, :title, 'Flexible',
           'ToDo', :deadline, :estimated_minutes, :ai_estimated_minutes
    FROM "Project" p
    JOIN "Milestone" m ON m."ProjectId" = p."Id"
    WHERE p."Id" = :project_id AND p."UserId" = :user_id AND m."Id" = :milestone_id
    RETURNING "Id"
""")

MARK_MESSAGE_APPLIED = text("""
    UPDATE "ChatMessage" m
    SET "IsApplied" = true
    FROM "ChatConversation" cv
    JOIN "Project" p ON p."Id" = cv."ProjectId"
    WHERE m."Id" = :message_id AND m."ConversationId" = :conversation_id
      AND cv."Id" = m."ConversationId" AND cv."Scope" = 'Project'
      AND p."UserId" = :user_id
    RETURNING m."Id"
""")

MARK_CONVERSATION_APPLIED = text("""
    UPDATE "ChatConversation" cv
    SET "Status" = 'Applied'
    FROM "Project" p
    WHERE cv."Id" = :conversation_id AND cv."Scope" = 'Project'
      AND p."Id" = cv."ProjectId" AND p."UserId" = :user_id
""")


# --- ProposalJson <-> SavedProjectDraft (T25) ---


def _iso(day: date | None) -> str | None:
    return day.isoformat() if day else None


def _day(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def draft_to_json(saved: SavedProjectDraft) -> str:
    draft, numbers = saved.draft, saved.numbers
    data: dict[str, Any] = {
        "version": PROPOSAL_VERSION,
        "milestones": [
            {
                "key": m.key,
                "title": m.title,
                "deadline": _iso(m.deadline),
                "tasks": [
                    {
                        "key": t.key,
                        "title": t.title,
                        "domain": {"id": str(t.domain_id), "name": t.domain_name},
                        "estimated_minutes": t.estimated_minutes,
                        "reason": t.reason,
                    }
                    for t in m.tasks
                ],
            }
            for m in draft.milestones
        ],
        "reminders": list(draft.reminders),
        "warnings": list(draft.warnings),
        "numbers": {
            "by_domain": [
                {
                    "domain": d.domain,
                    "estimated_minutes": d.estimated_minutes,
                    "adjusted_minutes": d.adjusted_minutes,
                }
                for d in numbers.by_domain
            ],
            "total_estimated_minutes": numbers.total_estimated_minutes,
            "total_adjusted_minutes": numbers.total_adjusted_minutes,
            "days_left": numbers.days_left,
            "adjusted_is_hint": numbers.adjusted_is_hint,
            "warning": numbers.warning,
        },
    }
    return json.dumps(data, ensure_ascii=False)


def draft_from_json(raw: str) -> SavedProjectDraft:
    data = json.loads(raw)
    if data.get("version") != PROPOSAL_VERSION:
        # A task chat draft (FR-08a) or an unknown shape: never guess (#31).
        raise ValueError(f"not a project draft: version {data.get('version')!r}")
    numbers = data["numbers"]
    return SavedProjectDraft(
        draft=ProjectDraft(
            milestones=tuple(
                DraftMilestone(
                    key=m["key"],
                    title=m["title"],
                    deadline=_day(m["deadline"]),
                    tasks=tuple(
                        DraftTask(
                            key=t["key"],
                            title=t["title"],
                            domain_id=UUID(t["domain"]["id"]),
                            domain_name=t["domain"]["name"],
                            estimated_minutes=t["estimated_minutes"],
                            reason=t.get("reason"),
                        )
                        for t in m["tasks"]
                    ),
                )
                for m in data["milestones"]
            ),
            reminders=tuple(data.get("reminders", [])),
            warnings=tuple(data.get("warnings", [])),
        ),
        numbers=ProjectNumbers(
            by_domain=tuple(
                DomainNumbers(
                    d["domain"], d["estimated_minutes"], d["adjusted_minutes"]
                )
                for d in numbers["by_domain"]
            ),
            total_estimated_minutes=numbers["total_estimated_minutes"],
            total_adjusted_minutes=numbers["total_adjusted_minutes"],
            days_left=numbers["days_left"],
            adjusted_is_hint=numbers["adjusted_is_hint"],
            warning=numbers.get("warning"),
        ),
    )


def to_project(row: Row) -> ChatProject:
    return ChatProject(
        project_id=row.Id,
        name=row.Name,
        description=row.Description,
        deadline=row.Deadline,
        status=row.Status,
        client_name=row.ClientName,
        time_zone=ZoneInfo(row.TimeZone),
        allow_ai_documents=row.AllowAiDocumentAccess,
    )


def to_document(row: Row) -> ProjectDocument:
    return ProjectDocument(
        document_id=row.Id,
        project_id=row.ProjectId,
        name=row.Name,
        source_type=DOCUMENT_TYPE_FROM_DB[row.SourceType],
        storage_path=row.StoragePath,
        size_bytes=row.SizeBytes,
    )


def to_attachment(row: Row) -> ChatAttachment:
    return ChatAttachment(
        attachment_id=row.Id,
        message_id=row.MessageId,
        source=SOURCE_FROM_DB[row.Source],
        document_id=row.DocumentId,
        name=row.Name,
        extracted_text=row.ExtractedText,
        is_truncated=row.IsTruncated,
        created_at=row.CreatedAt,
    )


def to_conversation(row: Row) -> ProjectConversation:
    return ProjectConversation(
        conversation_id=row.Id,
        project_id=row.ProjectId,
        title=row.Title,
        status=STATUS_FROM_DB[row.Status],
        created_at=row.CreatedAt,
    )


def to_message(row: Row) -> ProjectChatMessage:
    return ProjectChatMessage(
        message_id=row.Id,
        role=ROLE_FROM_DB[row.Sender],
        content=row.Content,
        created_at=row.CreatedAt,
        draft=draft_from_json(row.ProposalJson) if row.ProposalJson else None,
        is_applied=row.IsApplied,
    )


class SqlProjectChatRepository:
    """ProjectChatRepository on Postgres. One instance per request (one session)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def transaction(self) -> AbstractAsyncContextManager[object]:
        return self.session.begin()

    async def get_project(
        self, user_id: UUID, project_id: UUID, lock: bool = False
    ) -> ChatProject | None:
        params = {"project_id": project_id, "user_id": user_id}
        query = LOCK_PROJECT if lock else GET_PROJECT
        row = (await self.session.execute(query, params)).one_or_none()
        return to_project(row) if row else None

    async def get_conversation(
        self, user_id: UUID, conversation_id: UUID, lock: bool = False
    ) -> ProjectConversation | None:
        params = {"conversation_id": conversation_id, "user_id": user_id}
        query = LOCK_CONVERSATION if lock else GET_CONVERSATION
        row = (await self.session.execute(query, params)).one_or_none()
        return to_conversation(row) if row else None

    async def list_conversations(
        self, user_id: UUID, project_id: UUID
    ) -> list[ProjectConversation]:
        params = {"project_id": project_id, "user_id": user_id}
        rows = await self.session.execute(LIST_CONVERSATIONS, params)
        return [to_conversation(row) for row in rows]

    async def create_conversation(
        self, user_id: UUID, project_id: UUID, title: str
    ) -> ProjectConversation:
        params = {"project_id": project_id, "user_id": user_id, "title": title}
        row = (await self.session.execute(CREATE_CONVERSATION, params)).one_or_none()
        if row is None:
            raise NotOwnedError(str(project_id))
        return to_conversation(row)

    async def list_messages(
        self, user_id: UUID, conversation_id: UUID
    ) -> list[ProjectChatMessage]:
        params = {"conversation_id": conversation_id, "user_id": user_id}
        rows = await self.session.execute(LIST_MESSAGES, params)
        return [to_message(row) for row in rows]

    async def latest_draft(
        self, user_id: UUID, conversation_id: UUID
    ) -> ProjectChatMessage | None:
        params = {"conversation_id": conversation_id, "user_id": user_id}
        row = (await self.session.execute(LATEST_DRAFT, params)).one_or_none()
        return to_message(row) if row else None

    async def add_message(
        self,
        user_id: UUID,
        conversation_id: UUID,
        role: ChatRole,
        content: str,
        draft: SavedProjectDraft | None = None,
    ) -> ProjectChatMessage:
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
        return ProjectChatMessage(
            row.Id, role, content, row.CreatedAt, draft, row.IsApplied
        )

    async def get_document(
        self, user_id: UUID, document_id: UUID
    ) -> ProjectDocument | None:
        params = {"document_id": document_id, "user_id": user_id}
        row = (await self.session.execute(GET_DOCUMENT, params)).one_or_none()
        return to_document(row) if row else None

    async def add_attachment(
        self, user_id: UUID, message_id: UUID, attachment: NewAttachment
    ) -> ChatAttachment:
        params = {
            "message_id": message_id,
            "user_id": user_id,
            "document_id": attachment.document_id,
            "source": SOURCE_TO_DB[attachment.source],
            "name": attachment.name,
            "extracted_text": attachment.extracted_text,
            "is_truncated": attachment.is_truncated,
        }
        row = (await self.session.execute(ADD_ATTACHMENT, params)).one_or_none()
        if row is None:
            raise NotOwnedError(str(message_id))
        return ChatAttachment(
            attachment_id=row.Id,
            message_id=message_id,
            source=attachment.source,
            document_id=attachment.document_id,
            name=attachment.name,
            extracted_text=attachment.extracted_text,
            is_truncated=attachment.is_truncated,
            created_at=row.CreatedAt,
        )

    async def list_attachments(
        self, user_id: UUID, conversation_id: UUID
    ) -> list[ChatAttachment]:
        params = {"conversation_id": conversation_id, "user_id": user_id}
        rows = await self.session.execute(LIST_ATTACHMENTS, params)
        return [to_attachment(row) for row in rows]

    async def active_domains(self) -> dict[UUID, str]:
        rows = await self.session.execute(ACTIVE_DOMAINS)
        return {row.Id: row.Name for row in rows}

    async def get_k_biases(self, user_id: UUID) -> dict[UUID, Decimal]:
        rows = await self.session.execute(GET_K_BIASES, {"user_id": user_id})
        return {row.DomainId: row.KBias for row in rows}

    async def existing_work(self, user_id: UUID, project_id: UUID) -> ExistingWork:
        params = {"project_id": project_id, "user_id": user_id}
        milestones = await self.session.execute(MILESTONE_TITLES, params)
        tasks = await self.session.execute(TASK_TITLES, params)
        return ExistingWork(
            milestone_titles=tuple(milestones.scalars()),
            task_titles=tuple(tasks.scalars()),
        )

    async def next_milestone_order(self, user_id: UUID, project_id: UUID) -> int:
        params = {"project_id": project_id, "user_id": user_id}
        return (await self.session.execute(NEXT_MILESTONE_ORDER, params)).scalar_one()

    async def add_milestone(
        self, user_id: UUID, project_id: UUID, milestone: NewMilestone
    ) -> UUID:
        params = {
            "project_id": project_id,
            "user_id": user_id,
            "name": milestone.title,
            "order_index": milestone.order_index,
            "due_date": milestone.due_date,
        }
        milestone_id = (
            await self.session.execute(ADD_MILESTONE, params)
        ).scalar_one_or_none()
        if milestone_id is None:
            raise NotOwnedError(str(project_id))
        return milestone_id

    async def add_task(self, user_id: UUID, project_id: UUID, task: NewTask) -> UUID:
        params = {
            "project_id": project_id,
            "user_id": user_id,
            "milestone_id": task.milestone_id,
            "domain_id": task.domain_id,
            "title": task.title,
            "deadline": task.deadline,
            "estimated_minutes": task.estimated_minutes,
            "ai_estimated_minutes": task.ai_estimated_minutes,
        }
        task_id = (await self.session.execute(ADD_TASK, params)).scalar_one_or_none()
        if task_id is None:
            raise NotOwnedError(str(project_id))
        return task_id

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
