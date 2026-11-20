"""SqlProjectChatRepository conversions (T25). The SQL itself is checked with EXPLAIN (step 10)."""

import asyncio
import json
from datetime import UTC, date, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest

from metanoia_ai.application.ports.project_chat_repository import (
    AttachmentSource,
    ChatRole,
    ConversationStatus,
    DocumentSourceType,
    ProjectChatRepository,
    SavedProjectDraft,
)
from metanoia_ai.domain.project_breakdown import (
    DomainNumbers,
    DraftMilestone,
    DraftTask,
    ProjectDraft,
    ProjectNumbers,
)
from metanoia_ai.domain.project_discussion import DiscussionNotes
from metanoia_ai.infrastructure.db import project_chat_repository as sql
from metanoia_ai.infrastructure.db.project_chat_repository import (
    SOURCE_TO_DB,
    STATUS_FROM_DB,
    SqlProjectChatRepository,
    draft_from_json,
    draft_to_json,
    notes_to_json,
    proposal_from_json,
    to_attachment,
    to_conversation,
    to_document,
    to_message,
    to_project,
)

DESIGN = UUID(int=1)
NOW = datetime(2026, 10, 16, 9, 0, tzinfo=UTC)

SAVED = SavedProjectDraft(
    draft=ProjectDraft(
        milestones=(
            DraftMilestone(
                "m1",
                "Thiết kế",
                date(2026, 10, 22),
                (DraftTask("m1-t1", "Wireframe", DESIGN, "Design", 240, "1 dòng"),),
            ),
            DraftMilestone(
                None,
                "Thêm tay",
                None,
                (DraftTask(None, "Logo", DESIGN, "Design", 60, None),),
            ),
        ),
        reminders=("Họp chốt",),
        warnings=('Đã bỏ "API": dự án đã có việc này.',),
    ),
    numbers=ProjectNumbers(
        by_domain=(DomainNumbers("Design", 300, 390),),
        total_estimated_minutes=300,
        total_adjusted_minutes=390,
        days_left=None,
        adjusted_is_hint=True,
        warning=None,
    ),
)


# --- ProposalJson ---


def test_t25_draft_round_trip():
    assert draft_from_json(draft_to_json(SAVED)) == SAVED


def test_proposal_json_shape_matches_the_plan():
    data = json.loads(draft_to_json(SAVED))
    assert data["version"] == "project_v1"
    task = data["milestones"][0]["tasks"][0]
    assert task == {
        "key": "m1-t1",
        "title": "Wireframe",
        "domain": {"id": str(DESIGN), "name": "Design"},
        "estimated_minutes": 240,
        "reason": "1 dòng",
    }
    assert data["milestones"][0]["deadline"] == "2026-10-22"
    assert data["numbers"]["by_domain"] == [
        {"domain": "Design", "estimated_minutes": 300, "adjusted_minutes": 390}
    ]
    assert "Thiết kế" in draft_to_json(SAVED)  # not escaped to \\u...


@pytest.mark.parametrize("version", [1, None, "project_v2"])
def test_other_draft_versions_are_refused(version):
    data = json.loads(draft_to_json(SAVED)) | {"version": version}
    with pytest.raises(ValueError):
        draft_from_json(json.dumps(data))


# --- rows ---


def test_t25_row_becomes_project():
    row = SimpleNamespace(
        Id=uuid4(),
        Name="Landing",
        Description=None,
        Deadline=date(2026, 10, 30),
        Status="OnHold",
        ClientName="ACME",
        TimeZone="Asia/Ho_Chi_Minh",
        AllowAiDocumentAccess=True,
    )
    project = to_project(row)
    assert project.allow_ai_documents is True
    assert project.time_zone == ZoneInfo("Asia/Ho_Chi_Minh")
    assert project.is_closed is False
    assert (project.client_name, project.deadline) == ("ACME", date(2026, 10, 30))


def test_every_db_conversation_status_is_known():
    # DB CHECK: Open / Applied / Closed.
    assert set(STATUS_FROM_DB) == {"Open", "Applied", "Closed"}
    assert STATUS_FROM_DB["Applied"] is ConversationStatus.APPLIED


def test_row_becomes_conversation():
    row = SimpleNamespace(
        Id=uuid4(),
        ProjectId=uuid4(),
        Title="Brief 2026-10-16",
        Status="Open",
        CreatedAt=NOW,
    )
    assert to_conversation(row).status is ConversationStatus.OPEN


def test_row_becomes_message_with_draft():
    row = SimpleNamespace(
        Id=uuid4(),
        Sender="AI",
        Content="Mình chia 2 giai đoạn.",
        ProposalJson=draft_to_json(SAVED),
        IsApplied=False,
        CreatedAt=NOW,
    )
    message = to_message(row)
    assert message.role is ChatRole.ASSISTANT
    assert message.draft == SAVED


def test_user_row_has_no_draft():
    row = SimpleNamespace(
        Id=uuid4(),
        Sender="User",
        Content="Brief",
        ProposalJson=None,
        IsApplied=False,
        CreatedAt=NOW,
    )
    assert (to_message(row).role, to_message(row).draft) == (ChatRole.USER, None)


# --- the SQL text ---

CHAT_QUERIES = [
    "GET_CONVERSATION",
    "LOCK_CONVERSATION",
    "LIST_CONVERSATIONS",
    "LIST_MESSAGES",
    "LATEST_DRAFT",
    "ADD_MESSAGE",
    "MARK_MESSAGE_APPLIED",
    "MARK_CONVERSATION_APPLIED",
    "ADD_ATTACHMENT",
    "LIST_ATTACHMENTS",
]
OWNED_QUERIES = [
    *CHAT_QUERIES,
    "GET_DOCUMENT",
    "GET_PROJECT",
    "LOCK_PROJECT",
    "CREATE_CONVERSATION",
    "GET_K_BIASES",
    "MILESTONE_TITLES",
    "TASK_TITLES",
    "NEXT_MILESTONE_ORDER",
    "ADD_MILESTONE",
    "ADD_TASK",
]


@pytest.mark.parametrize("name", CHAT_QUERIES)
def test_chat_queries_never_reach_task_chats(name):
    assert "\"Scope\" = 'Project'" in getattr(sql, name).text


@pytest.mark.parametrize("name", OWNED_QUERIES)
def test_queries_are_filtered_by_the_owner(name):
    assert ":user_id" in getattr(sql, name).text


def test_locks_take_only_their_own_row():
    assert "FOR UPDATE OF p" in sql.LOCK_PROJECT.text
    assert "FOR UPDATE OF cv" in sql.LOCK_CONVERSATION.text


def test_new_task_is_flexible_todo_with_the_ai_number():
    query = sql.ADD_TASK.text
    assert "'Flexible'" in query and "'ToDo'" in query
    assert '"AiEstimatedMinutes"' in query
    assert 'm."ProjectId" = p."Id"' in query  # milestone of this project only


def test_repository_matches_the_port():
    repo: ProjectChatRepository = SqlProjectChatRepository(session=None)  # type only
    assert repo is not None


# --- FR-08b-2: documents and attachments ---


@pytest.mark.parametrize(
    ("db", "source_type"),
    [("File", DocumentSourceType.FILE), ("Link", DocumentSourceType.LINK)],
)
def test_row_becomes_document(db, source_type):
    row = SimpleNamespace(
        Id=uuid4(),
        ProjectId=uuid4(),
        Name="Brief",
        SourceType=db,
        StoragePath="p/a/brief.pdf" if db == "File" else None,
        SizeBytes=None,  # the column allows NULL
    )
    document = to_document(row)
    assert document.source_type is source_type
    assert document.size_bytes is None


def test_attachment_sources_match_the_db_check():
    # DB CHECK: Source IN ('Hub', 'Upload').
    assert set(SOURCE_TO_DB.values()) == {"Hub", "Upload"}


@pytest.mark.parametrize("source", list(AttachmentSource))
def test_row_becomes_attachment(source):
    row = SimpleNamespace(
        Id=uuid4(),
        MessageId=uuid4(),
        Source=SOURCE_TO_DB[source],
        DocumentId=None,
        Name="brief.pdf",
        ExtractedText="Text",
        IsTruncated=True,
        CreatedAt=NOW,
    )
    attachment = to_attachment(row)
    assert (attachment.source, attachment.is_truncated) == (source, True)


def test_attachments_only_go_with_user_messages():
    assert "m.\"Sender\" = 'User'" in sql.ADD_ATTACHMENT.text


def test_project_query_reads_the_document_switch():
    assert '"AllowAiDocumentAccess"' in sql.GET_PROJECT.text


# --- FR-08b-3: discussion checklists share ProposalJson (T6) ---

NOTES = DiscussionNotes(
    ("Landing page 3 phần",), ("Hạn bàn giao",), ("Thiếu logo",), False
)


def test_notes_round_trip_and_have_their_own_version():
    raw = notes_to_json(NOTES)
    assert json.loads(raw)["version"] == "project_discuss_v1"
    assert proposal_from_json(raw) == (None, NOTES)


def test_notes_without_questions_still_read():
    data = json.loads(notes_to_json(NOTES))
    del data["questions"]
    assert proposal_from_json(json.dumps(data)) == (None, NOTES)


def test_draft_json_still_reads_as_a_draft():
    assert proposal_from_json(draft_to_json(SAVED)) == (SAVED, None)


def test_empty_proposal_reads_as_nothing():
    assert proposal_from_json(None) == (None, None)


def test_row_becomes_message_with_notes_and_no_draft():
    row = SimpleNamespace(
        Id=uuid4(),
        Sender="AI",
        Content="Mình đã nắm phần chính.",
        ProposalJson=notes_to_json(NOTES),
        IsApplied=False,
        CreatedAt=NOW,
    )
    message = to_message(row)
    assert message.draft is None
    assert message.notes == NOTES


def test_latest_draft_only_matches_the_draft_version():
    sql_text = " ".join(str(sql.LATEST_DRAFT).split())
    assert "\"ProposalJson\"->>'version' = 'project_v1'" in sql_text


class AddMessageSession:
    """Records the insert and returns a saved row."""

    def __init__(self):
        self.params = None

    async def execute(self, statement, params):
        self.params = params
        row = SimpleNamespace(Id=uuid4(), IsApplied=False, CreatedAt=NOW)
        return SimpleNamespace(one_or_none=lambda: row)


def test_add_message_saves_notes_in_proposal_json():
    session = AddMessageSession()
    repo = sql.SqlProjectChatRepository(session)
    message = asyncio.run(
        repo.add_message(uuid4(), uuid4(), ChatRole.ASSISTANT, "Ok", notes=NOTES)
    )
    assert json.loads(session.params["proposal"])["version"] == "project_discuss_v1"
    assert message.notes == NOTES and message.draft is None


def test_add_message_refuses_a_draft_and_notes_together():
    repo = sql.SqlProjectChatRepository(AddMessageSession())
    with pytest.raises(ValueError):
        asyncio.run(
            repo.add_message(uuid4(), uuid4(), ChatRole.ASSISTANT, "Ok", SAVED, NOTES)
        )


# --- FR-08b-4: several applied drafts per chat (T6) ---


def test_mark_applied_records_the_time_in_the_draft_not_is_applied():
    sql_text = " ".join(str(sql.MARK_MESSAGE_APPLIED).split())
    assert "jsonb_set" in sql_text and "applied_at" in sql_text
    assert '"IsApplied" = true' not in sql_text  # the unique index allows one per chat
    assert 'm."ProposalJson" IS NOT NULL' in sql_text


def test_applied_comes_from_the_flag_or_the_draft_time():
    applied_json = json.dumps({**json.loads(draft_to_json(SAVED)), "applied_at": "x"})

    def row(proposal, flag):
        return SimpleNamespace(
            Id=uuid4(),
            Sender="AI",
            Content="Ok",
            ProposalJson=proposal,
            IsApplied=flag,
            CreatedAt=NOW,
        )

    assert to_message(row(applied_json, False)).is_applied  # FR-08b-4
    assert to_message(row(draft_to_json(SAVED), True)).is_applied  # older rows
    assert not to_message(row(draft_to_json(SAVED), False)).is_applied
    assert to_message(row(applied_json, False)).draft == SAVED  # still reads
