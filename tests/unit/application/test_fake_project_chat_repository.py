"""The project chat fake behaves like the real DB where the use cases rely on it."""

import asyncio
from datetime import UTC, date, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from metanoia_ai.application.ports.project_chat_repository import (
    ChatProject,
    ChatRole,
    ConversationStatus,
    NewMilestone,
    NewTask,
    ProjectChatRepository,
)
from tests.unit.application.fakes import FakeProjectChatRepository

NOW = datetime(2026, 10, 16, 9, 0, tzinfo=UTC)
SAIGON = ZoneInfo("Asia/Saigon")
USER, OTHER = uuid4(), uuid4()
DESIGN = uuid4()


def project(status="Active") -> ChatProject:
    return ChatProject(
        uuid4(), "Landing", None, date(2026, 10, 30), status, None, SAIGON
    )


def setup():
    repo = FakeProjectChatRepository(now=NOW)
    p = project()
    repo.add_project(USER, p)
    repo.add_domain(DESIGN, "Design")
    return repo, p


def run(coro):
    return asyncio.run(coro)


def test_fake_matches_the_port():
    repo: ProjectChatRepository = FakeProjectChatRepository(now=NOW)
    assert repo is not None


@pytest.mark.parametrize(
    ("status", "closed"),
    [
        ("Active", False),
        ("OnHold", False),
        ("Completed", True),
        ("Archived", True),
    ],
)
def test_closed_project_statuses(status, closed):
    assert project(status).is_closed is closed


def test_other_users_project_and_chat_are_not_found():
    repo, p = setup()
    chat = run(repo.create_conversation(USER, p.project_id, "Brief"))
    assert run(repo.get_project(OTHER, p.project_id)) is None
    assert run(repo.get_conversation(OTHER, chat.conversation_id)) is None
    assert run(repo.list_conversations(OTHER, p.project_id)) == []


def test_task_chat_is_not_a_project_chat():
    repo, p = setup()
    chat = run(repo.create_conversation(USER, p.project_id, "Brief"))
    repo.task_chat_ids.add(chat.conversation_id)
    assert run(repo.get_conversation(USER, chat.conversation_id)) is None


def test_conversations_newest_first_and_open():
    repo, p = setup()
    first = run(repo.create_conversation(USER, p.project_id, "Brief 1"))
    second = run(repo.create_conversation(USER, p.project_id, "Brief 2"))
    assert run(repo.list_conversations(USER, p.project_id)) == [second, first]
    assert first.status is ConversationStatus.OPEN


def test_only_ai_messages_have_a_draft():
    repo, p = setup()
    chat = run(repo.create_conversation(USER, p.project_id, "Brief"))
    with pytest.raises(ValueError):
        run(
            repo.add_message(
                USER, chat.conversation_id, ChatRole.USER, "x", draft=object()
            )
        )


def test_rollback_on_error():
    repo, p = setup()

    async def fail():
        async with repo.transaction():
            await repo.create_conversation(USER, p.project_id, "Brief")
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        run(fail())
    assert repo.chats == {}


def test_milestone_order_continues_after_existing_ones():
    repo, p = setup()
    repo.add_old_milestone(p.project_id, 1, "Cũ 1")
    repo.add_old_milestone(p.project_id, 2, "Cũ 2")
    assert run(repo.next_milestone_order(USER, p.project_id)) == 3
    with pytest.raises(ValueError):  # unique (ProjectId, OrderIndex)
        run(
            repo.add_milestone(
                USER, p.project_id, NewMilestone("Mới", date(2026, 10, 30), 2)
            )
        )


def test_existing_work_is_newest_first_and_includes_new_rows():
    repo, p = setup()
    repo.add_old_task(p.project_id, "Task cũ")
    m = run(
        repo.add_milestone(USER, p.project_id, NewMilestone("M", date(2026, 10, 30), 1))
    )
    deadline = datetime(2026, 10, 30, 23, 59, tzinfo=SAIGON)
    run(
        repo.add_task(
            USER, p.project_id, NewTask(m, "Task mới", DESIGN, deadline, 60, 60)
        )
    )

    work = run(repo.existing_work(USER, p.project_id))

    assert work.task_titles == ("Task mới", "Task cũ")
    assert work.milestone_titles == ("M",)


def test_inactive_domain_is_left_out():
    repo, _ = setup()
    off = uuid4()
    repo.add_domain(off, "Old", is_active=False)
    assert run(repo.active_domains()) == {DESIGN: "Design"}


def test_one_applied_draft_per_chat():
    repo, p = setup()
    chat = run(repo.create_conversation(USER, p.project_id, "Brief"))
    message = run(
        repo.add_message(USER, chat.conversation_id, ChatRole.ASSISTANT, "ok")
    )
    run(repo.mark_applied(USER, chat.conversation_id, message.message_id))
    applied = run(repo.get_conversation(USER, chat.conversation_id))
    assert applied.status is ConversationStatus.APPLIED
    with pytest.raises(ValueError):
        run(repo.mark_applied(USER, chat.conversation_id, message.message_id))
