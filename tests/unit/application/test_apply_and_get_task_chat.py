import asyncio
from datetime import UTC, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from metanoia_ai.application.ports.chat_repository import (
    ChatRole,
    ChatTask,
    SavedDraft,
)
from metanoia_ai.application.use_cases.apply_task_breakdown import (
    ApplyTaskBreakdown,
    MessageNotFoundError,
    NotLatestDraftError,
)
from metanoia_ai.application.use_cases.get_task_chat import GetTaskChat
from metanoia_ai.application.use_cases.record_task_completion import (
    TaskNotFoundError,
)
from metanoia_ai.application.use_cases.send_task_chat_message import (
    TaskNotSplittableError,
)
from metanoia_ai.domain.models import TaskType
from metanoia_ai.domain.task_breakdown import (
    BadDraftError,
    Draft,
    DraftLimits,
    DraftNumbers,
    Subtask,
    SubtaskInput,
)
from tests.unit.application.fakes import FakeChatRepository

USER = uuid4()
OTHER_USER = uuid4()
NOW = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)
SAIGON = ZoneInfo("Asia/Ho_Chi_Minh")
MANUAL = DraftLimits(min_subtasks=1, max_subtasks=10, min_minutes=1, max_minutes=480)


def saved_draft(*minutes) -> SavedDraft:
    draft = Draft(tuple(Subtask(f"Bước {n}", m, n) for n, m in enumerate(minutes, 1)))
    return SavedDraft(draft, DraftNumbers(240, 312, draft.total_minutes, False, None))


def make_task(**changes) -> ChatTask:
    values = {
        "task_id": uuid4(),
        "project_id": uuid4(),
        "task_type": TaskType.FLEXIBLE,
        "status": "ToDo",
        "title": "Làm landing page",
        "description": None,
        "domain_id": uuid4(),
        "domain_name": "Coding",
        "client_name": None,
        "estimated_minutes": 240,
        "deadline": None,
        "time_zone": SAIGON,
        "subtask_count": 0,
    }
    return ChatTask(**(values | changes))


class Setup:
    """A chat with: user msg, old draft, user msg, newest draft."""

    def __init__(self, task=None) -> None:
        self.repo = FakeChatRepository(now=NOW)
        self.task = task or make_task()
        self.repo.add_task(USER, self.task)
        self.chat = self.run(self.repo.create_conversation(USER, self.task))
        add = self.repo.add_message
        self.first_user = self.run(add(USER, self.chat, ChatRole.USER, "chia giúp"))
        self.old = self.run(
            add(
                USER,
                self.chat,
                ChatRole.ASSISTANT,
                "4 bước",
                saved_draft(60, 60, 60, 60),
            )
        )
        self.run(add(USER, self.chat, ChatRole.USER, "bớt 1 bước"))
        self.latest = self.run(
            add(USER, self.chat, ChatRole.ASSISTANT, "3 bước", saved_draft(90, 90, 60))
        )
        self.apply_case = ApplyTaskBreakdown(self.repo, MANUAL)

    @staticmethod
    def run(coro):
        return asyncio.run(coro)

    def apply(self, message_id=None, edited=None, user_id=USER, task_id=None):
        return self.run(
            self.apply_case.execute(
                user_id,
                task_id or self.task.task_id,
                message_id or self.latest.message_id,
                edited,
            )
        )

    def saved(self):
        return self.repo.subtasks.get(self.task.task_id, [])


# --- T19: apply the newest draft ---


def test_t19_newest_draft_is_written_in_order():
    s = Setup()

    result = s.apply()

    assert [(x.title, x.estimated_minutes, x.order_index) for x in result.subtasks] == [
        ("Bước 1", 90, 1),
        ("Bước 2", 90, 2),
        ("Bước 3", 60, 3),
    ]
    assert result.subtasks == s.saved()
    assert s.repo.locked == [s.task.task_id]
    assert s.repo.conversations[s.chat].status == "Applied"
    view = s.run(GetTaskChat(s.repo).execute(USER, s.task.task_id))
    assert view.applied is True
    assert view.latest_draft.is_applied is True


# --- T20: wrong message ---


def test_t20_old_draft_is_refused():
    s = Setup()
    with pytest.raises(NotLatestDraftError):
        s.apply(s.old.message_id)
    assert s.saved() == []


def test_t20_user_message_is_refused():
    s = Setup()
    with pytest.raises(NotLatestDraftError):
        s.apply(s.first_user.message_id)


def test_t20_message_of_another_task_is_not_found():
    s = Setup()
    other = Setup()
    with pytest.raises(MessageNotFoundError):
        s.apply(other.latest.message_id)
    with pytest.raises(MessageNotFoundError):
        s.apply(uuid4())


def test_t20_other_users_task_is_not_found():
    s = Setup()
    with pytest.raises(TaskNotFoundError):
        s.apply(user_id=OTHER_USER)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"task_type": TaskType.FIXED, "estimated_minutes": None}, "fixed"),
        ({"status": "Done"}, "finished"),
    ],
)
def test_task_that_cannot_be_split_is_refused(changes, reason):
    s = Setup(make_task(**changes))
    with pytest.raises(TaskNotSplittableError) as error:
        s.apply()
    assert error.value.reason == reason


# --- T21: edited by hand ---


def test_t21_valid_edit_is_written_instead_of_the_draft():
    s = Setup()
    edited = [SubtaskInput("  Làm hết một lần ", 300)]

    result = s.apply(edited=edited)

    assert [(x.title, x.estimated_minutes, x.order_index) for x in result.subtasks] == [
        ("Làm hết một lần", 300, 1)
    ]


@pytest.mark.parametrize(
    "edited",
    [
        pytest.param([], id="empty"),
        pytest.param([SubtaskInput("A", 481)], id="481-minutes"),
        pytest.param([SubtaskInput("A", 0)], id="0-minutes"),
        pytest.param([SubtaskInput("A", 30), SubtaskInput("a", 30)], id="same-title"),
    ],
)
def test_t21_invalid_edit_is_refused_and_nothing_written(edited):
    s = Setup()
    with pytest.raises(BadDraftError):
        s.apply(edited=edited)
    assert s.saved() == []
    assert s.repo.locked == []  # refused before touching the DB


# --- T22: apply twice ---


def test_t22_second_apply_is_refused():
    s = Setup()
    s.apply()
    with pytest.raises(TaskNotSplittableError) as error:
        s.apply()
    assert error.value.reason == "has_subtasks"
    assert len(s.saved()) == 3


# --- GetTaskChat ---


def test_get_without_chat_is_empty():
    repo = FakeChatRepository(now=NOW)
    task = make_task()
    repo.add_task(USER, task)
    view = asyncio.run(GetTaskChat(repo).execute(USER, task.task_id))
    assert (view.messages, view.latest_draft, view.applied) == ([], None, False)


def test_get_shows_messages_in_order_and_the_newest_draft():
    s = Setup()
    view = s.run(GetTaskChat(s.repo).execute(USER, s.task.task_id))
    assert [m.content for m in view.messages] == [
        "chia giúp",
        "4 bước",
        "bớt 1 bước",
        "3 bước",
    ]
    assert view.latest_draft == s.latest
    assert view.applied is False
    assert view.time_zone == SAIGON


def test_get_other_users_task_is_not_found():
    s = Setup()
    with pytest.raises(TaskNotFoundError):
        s.run(GetTaskChat(s.repo).execute(OTHER_USER, s.task.task_id))
