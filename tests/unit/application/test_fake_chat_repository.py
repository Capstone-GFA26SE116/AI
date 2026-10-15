"""The fake must behave like the real repository, or use case tests prove nothing."""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from metanoia_ai.application.ports.chat_repository import (
    ChatRepository,
    ChatRole,
    ChatTask,
    SavedDraft,
)
from metanoia_ai.domain.models import TaskType
from metanoia_ai.domain.task_breakdown import (
    Draft,
    DraftNumbers,
    Subtask,
)
from tests.unit.application.fakes import FakeChatRepository

USER = uuid4()
OTHER_USER = uuid4()
NOW = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)
DRAFT = SavedDraft(
    Draft((Subtask("A", 60, 1), Subtask("B", 90, 2))),
    DraftNumbers(120, 156, 150, False, None),
)


def make_task() -> ChatTask:
    return ChatTask(
        task_id=uuid4(),
        project_id=uuid4(),
        task_type=TaskType.FLEXIBLE,
        status="ToDo",
        title="Làm landing page",
        description=None,
        domain_id=uuid4(),
        domain_name="Coding",
        client_name=None,
        estimated_minutes=240,
        deadline=None,
        time_zone=ZoneInfo("Asia/Ho_Chi_Minh"),
        subtask_count=0,
    )


def run(coro):
    return asyncio.run(coro)


def setup():
    repo = FakeChatRepository(now=NOW)
    task = make_task()
    repo.add_task(USER, task)
    conversation = run(repo.create_conversation(USER, task))
    return repo, task, conversation


def test_fake_matches_the_port():
    repo: ChatRepository = FakeChatRepository(now=NOW)  # type check only
    assert repo is not None


def test_task_only_for_its_owner_and_lock_is_recorded():
    repo, task, _ = setup()
    assert run(repo.get_task(OTHER_USER, task.task_id)) is None
    assert run(repo.get_task(USER, task.task_id, lock=True)) == task
    assert repo.locked == [task.task_id]


def test_one_conversation_per_task_is_found_again():
    repo, task, conversation = setup()
    assert run(repo.find_conversation(USER, task.task_id)) == conversation
    assert run(repo.find_conversation(OTHER_USER, task.task_id)) is None


def test_messages_oldest_first_and_limit_keeps_the_newest():
    repo, _, conversation = setup()
    for n in range(5):
        run(repo.add_message(USER, conversation, ChatRole.USER, f"tin {n}"))
    messages = run(repo.list_messages(USER, conversation, limit=2))
    assert [m.content for m in messages] == ["tin 3", "tin 4"]
    assert messages[0].created_at < messages[1].created_at
    assert run(repo.list_messages(OTHER_USER, conversation)) == []


def test_latest_draft_and_only_ai_has_drafts():
    repo, _, conversation = setup()
    first = run(repo.add_message(USER, conversation, ChatRole.ASSISTANT, "1", DRAFT))
    second = run(repo.add_message(USER, conversation, ChatRole.ASSISTANT, "2", DRAFT))
    run(repo.add_message(USER, conversation, ChatRole.ASSISTANT, "câu hỏi"))
    assert run(repo.latest_draft(USER, conversation)) == second
    assert first != second
    with pytest.raises(ValueError):
        run(repo.add_message(USER, conversation, ChatRole.USER, "x", DRAFT))


def test_subtasks_change_the_count_and_apply_marks_once():
    repo, task, conversation = setup()
    message = run(
        repo.add_message(USER, conversation, ChatRole.ASSISTANT, "nháp", DRAFT)
    )
    saved = run(repo.add_subtasks(USER, task.task_id, DRAFT.draft.subtasks))
    run(repo.mark_applied(USER, conversation, message.message_id))

    assert [s.order_index for s in saved] == [1, 2]
    assert run(repo.get_task(USER, task.task_id)).subtask_count == 2
    assert repo.conversations[conversation].status == "Applied"
    assert run(repo.latest_draft(USER, conversation)).is_applied is True
    with pytest.raises(ValueError):
        run(repo.mark_applied(USER, conversation, message.message_id))


def test_k_bias():
    repo, task, _ = setup()
    assert run(repo.get_k_bias(USER, task.domain_id)) is None
    repo.k_bias[(USER, task.domain_id)] = Decimal("1.34")
    assert run(repo.get_k_bias(USER, task.domain_id)) == Decimal("1.34")


def test_transaction_rolls_back_on_error():
    repo, task, conversation = setup()

    async def save_then_fail():
        async with repo.transaction():
            await repo.add_message(USER, conversation, ChatRole.USER, "x")
            await repo.add_subtasks(USER, task.task_id, DRAFT.draft.subtasks)
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        run(save_then_fail())
    assert run(repo.list_messages(USER, conversation)) == []
    assert repo.subtasks == {}
