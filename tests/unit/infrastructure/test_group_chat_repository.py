"""SqlGroupChatRepository: SQL shape, ProposalJson with member suggestions (T8)."""

import asyncio
import json
from datetime import UTC, date, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest

from metanoia_ai.application.ports.chat_repository import ChatRole
from metanoia_ai.application.ports.group_chat_repository import (
    GroupChatRepository,
    NewGroupMessage,
)
from metanoia_ai.application.ports.project_chat_repository import SavedProjectDraft
from metanoia_ai.domain.assignee_ranking import GroupRole
from metanoia_ai.domain.group_plan import TaskAssignment
from metanoia_ai.domain.project_breakdown import (
    DomainNumbers,
    DraftMilestone,
    DraftTask,
    ProjectDraft,
    ProjectNumbers,
)
from metanoia_ai.domain.project_discussion import DiscussionNotes
from metanoia_ai.infrastructure.db.group_chat_repository import (
    ACTIVE_DOMAINS,
    ADD_MESSAGE,
    CREATE_CONVERSATION,
    GET_CONVERSATION,
    GET_GROUP,
    GROUP_MILESTONES,
    GROUP_TASKS,
    LIST_CONVERSATIONS,
    LIST_MESSAGES,
    MARK_APPLIED,
    SqlGroupChatRepository,
    proposal_json,
    to_group,
    to_message,
)

GROUP, CHAT, MSG = UUID(int=1), UUID(int=2), UUID(int=3)
A = UUID(int=21)
CODING = UUID(int=50)
WHEN = datetime(2026, 11, 23, 8, tzinfo=UTC)

SAVED = SavedProjectDraft(
    draft=ProjectDraft(
        milestones=(
            DraftMilestone(
                "m1",
                "Lập trình",
                date(2026, 11, 27),
                (DraftTask("m1-t1", "Dựng trang", CODING, "Coding", 120),),
            ),
        )
    ),
    numbers=ProjectNumbers(
        (DomainNumbers("Coding", 120, 156),), 120, 156, 4, False, None
    ),
)
ASSIGNED = (TaskAssignment("m1-t1", A, True, "Còn 9 giờ rảnh."),)


class Result:
    def __init__(self, rows):
        self.rows = rows

    def __iter__(self):
        return iter(self.rows)

    def one_or_none(self):
        return self.rows[0] if self.rows else None

    def one(self):
        return self.rows[0]


class RecordingSession:
    def __init__(self, rows_by_statement=None):
        self.rows = rows_by_statement or {}
        self.calls = []

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params))
        return Result(self.rows.get(str(statement), []))


def message_row(proposal, applied=False):
    return SimpleNamespace(
        Id=MSG,
        Sender="AI",
        Content="Bản nháp",
        ProposalJson=proposal,
        IsApplied=applied,
        CreatedAt=WHEN,
    )


def test_repository_matches_the_port():
    repo: GroupChatRepository = SqlGroupChatRepository(RecordingSession())
    assert repo is not None


def test_sql_only_touches_group_chats():
    for statement in (GET_CONVERSATION, LIST_CONVERSATIONS, LIST_MESSAGES, ADD_MESSAGE):
        assert "\"Scope\" = 'Group'" in str(statement)
    create = str(CREATE_CONVERSATION)
    assert "'Group'" in create and ":group_id" in create and "NULL, NULL" in create
    mark = str(MARK_APPLIED)
    assert "applied_at' IS NULL" in mark and ":version" in mark
    assert "'Skipped'" in str(GROUP_TASKS)
    assert 'LEFT JOIN "GroupMember"' in str(GET_GROUP)


def test_draft_with_assignments_round_trips():
    raw = proposal_json(NewGroupMessage(ChatRole.ASSISTANT, "x", SAVED, ASSIGNED))
    assert json.loads(raw)["assignments"][0]["user_id"] == str(A)
    message = to_message(message_row(raw))
    assert message.draft == SAVED and message.assignments == ASSIGNED
    assert not message.is_applied
    applied = json.loads(raw) | {"applied_at": "2026-11-23T08:00:00"}
    assert to_message(message_row(json.dumps(applied))).is_applied


def test_checklist_and_plain_messages():
    notes = DiscussionNotes(("a",), ("b",), ("c",), True, ("d",))
    raw = proposal_json(NewGroupMessage(ChatRole.ASSISTANT, "x", notes=notes))
    message = to_message(message_row(raw))
    assert message.notes == notes and message.draft is None
    assert message.assignments == ()
    assert proposal_json(NewGroupMessage(ChatRole.USER, "hi")) is None
    with pytest.raises(ValueError):
        proposal_json(NewGroupMessage(ChatRole.ASSISTANT, "x", SAVED, (), notes))


def test_group_row_and_caller_role():
    row = SimpleNamespace(
        Id=GROUP,
        Name="Nhóm",
        Description=None,
        Deadline=date(2026, 11, 30),
        Status="Archived",
        CallerRole="Leader",
        TimeZone="Asia/Ho_Chi_Minh",
    )
    group = to_group(row)
    assert group.archived and group.caller_role is GroupRole.LEADER
    assert (
        to_group(SimpleNamespace(**{**vars(row), "CallerRole": None})).caller_role
        is None
    )


def test_add_message_and_mark_applied_send_the_right_params():
    added = SimpleNamespace(Id=MSG, IsApplied=False, CreatedAt=WHEN)
    session = RecordingSession({str(ADD_MESSAGE): [added], str(MARK_APPLIED): []})
    repo = SqlGroupChatRepository(session)
    saved = asyncio.run(
        repo.add_message(
            CHAT, NewGroupMessage(ChatRole.ASSISTANT, "x", SAVED, ASSIGNED)
        )
    )
    assert saved.message_id == MSG and saved.assignments == ASSIGNED
    params = session.calls[0][1]
    assert params["sender"] == "AI" and "assignments" in params["proposal"]
    assert asyncio.run(repo.mark_applied(CHAT, MSG)) is False  # no row updated
    assert session.calls[1][1]["version"] == "project_v1"


def test_reads_convert_rows():
    conversation = SimpleNamespace(
        Id=CHAT, GroupId=GROUP, Title="Kế hoạch", Status="Open", CreatedAt=WHEN
    )
    group = SimpleNamespace(
        Id=GROUP,
        Name="Nhóm",
        Description="d",
        Deadline=None,
        Status="Active",
        CallerRole="Member",
        TimeZone="Asia/Ho_Chi_Minh",
    )
    session = RecordingSession(
        {
            str(GET_GROUP): [group],
            str(GET_CONVERSATION): [conversation],
            str(LIST_CONVERSATIONS): [conversation],
            str(CREATE_CONVERSATION): [conversation],
            str(LIST_MESSAGES): [message_row(None)],
            str(ACTIVE_DOMAINS): [SimpleNamespace(Id=CODING, Name="Coding")],
            str(GROUP_MILESTONES): [SimpleNamespace(Name="M1")],
            str(GROUP_TASKS): [SimpleNamespace(Title="T1")],
        }
    )
    repo = SqlGroupChatRepository(session)
    run = asyncio.run
    assert run(repo.get_group(GROUP, A)).caller_role is GroupRole.MEMBER
    assert run(repo.get_conversation(CHAT)).title == "Kế hoạch"
    assert [c.conversation_id for c in run(repo.list_conversations(GROUP))] == [CHAT]
    assert run(repo.create_conversation(GROUP, "Kế hoạch")).group_id == GROUP
    (message,) = run(repo.list_messages(CHAT))
    assert message.draft is None and message.notes is None
    assert run(repo.active_domains()) == {CODING: "Coding"}
    work = run(repo.existing_work(GROUP))
    assert work.milestone_titles == ("M1",) and work.task_titles == ("T1",)
    empty = SqlGroupChatRepository(RecordingSession())
    assert run(empty.get_group(GROUP, A)) is None
    assert run(empty.get_conversation(CHAT)) is None
