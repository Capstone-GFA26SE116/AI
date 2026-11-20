"""The chat history use case (plans/chat-history.md T3)."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from metanoia_ai.application.ports.chat_history_repository import (
    ChatScope,
    ChatSummary,
)
from metanoia_ai.application.ports.chat_repository import ChatRole
from metanoia_ai.application.ports.project_chat_repository import ConversationStatus
from metanoia_ai.application.use_cases.list_chat_history import (
    InvalidHistoryRequestError,
    ListChatHistory,
    UserNotFoundError,
)
from tests.unit.application.fakes import (
    FakeChatHistoryRepository,
    FakeHistoryChat,
    FakeHistoryMessage,
)

USER = UUID(int=7)
OTHER = UUID(int=8)
T0 = datetime(2026, 11, 20, 2, 0, tzinfo=UTC)


def run(coro):
    return asyncio.run(coro)


def add_chat(repo, name, texts, *, user=USER, start=T0, scope=ChatScope.PROJECT):
    """A chat whose messages are one minute apart, user and AI in turn."""
    summary = ChatSummary(
        conversation_id=uuid4(),
        scope=scope,
        title=f"Chat {name}",
        status=ConversationStatus.OPEN,
        applied=False,
        project_id=uuid4(),
        project_name=name,
        task_id=uuid4() if scope is ChatScope.TASK else None,
        task_title="Làm logo" if scope is ChatScope.TASK else None,
        message_count=0,
        last_role=ChatRole.USER,
        last_content="",
        last_at=start,
    )
    chat = FakeHistoryChat(summary, user)
    for index, text in enumerate(texts):
        role = ChatRole.USER if index % 2 == 0 else ChatRole.ASSISTANT
        chat.messages.append(
            FakeHistoryMessage(uuid4(), role, text, start + timedelta(minutes=index))
        )
    repo.chats.append(chat)
    return chat


def history(repo, **kw):
    return run(ListChatHistory(repo).execute(USER, **kw))


def test_newest_chat_first_with_a_preview_of_the_last_message():
    repo = FakeChatHistoryRepository()
    add_chat(repo, "Tiệm bánh", ["Brief", "Ok\nmình hiểu"], start=T0)
    add_chat(repo, "Quán cà phê", ["Logo"], start=T0 + timedelta(hours=1))
    page = history(repo)
    assert [e.chat.project_name for e in page.entries] == ["Quán cà phê", "Tiệm bánh"]
    assert page.entries[1].preview == "Ok mình hiểu"
    assert page.entries[1].chat.last_role is ChatRole.ASSISTANT
    assert page.entries[1].snippet is None
    assert page.has_more is False
    assert str(page.time_zone) == "Asia/Ho_Chi_Minh"


def test_other_users_and_empty_chats_are_not_listed():
    repo = FakeChatHistoryRepository()
    add_chat(repo, "Của mình", ["Hi"])
    add_chat(repo, "Trống", [])
    add_chat(repo, "Người khác", ["Hi"], user=OTHER)
    assert [e.chat.project_name for e in history(repo).entries] == ["Của mình"]


def test_one_more_row_than_asked_means_there_is_a_next_page():
    repo = FakeChatHistoryRepository()
    for hour in range(3):
        add_chat(repo, f"P{hour}", ["Hi"], start=T0 + timedelta(hours=hour))
    page = history(repo, limit=2)
    assert len(page.entries) == 2 and page.has_more
    assert repo.calls[-1] == (None, 3, 0)
    last = history(repo, limit=2, offset=2)
    assert [e.chat.project_name for e in last.entries] == ["P0"]
    assert not last.has_more


def test_search_returns_the_matching_chat_with_a_snippet():
    repo = FakeChatHistoryRepository()
    add_chat(repo, "Tiệm bánh", ["Khách xin thêm trang BLOG 3 bài", "Ok"])
    add_chat(repo, "Quán cà phê", ["Logo màu nâu"])
    page = history(repo, query="  blog ")
    assert repo.calls[-1][0] == "blog"  # spaces tidied before the search
    [found] = page.entries
    assert found.chat.project_name == "Tiệm bánh"
    assert "BLOG" in found.snippet
    assert found.preview == "Ok"  # the last message, not the match


def test_a_name_match_has_no_snippet():
    repo = FakeChatHistoryRepository()
    add_chat(repo, "Quán cà phê", ["Logo"], scope=ChatScope.TASK)
    [found] = history(repo, query="logo").entries  # task title "Làm logo"
    assert found.chat.scope is ChatScope.TASK
    found_by_name = history(repo, query="cà phê").entries[0]
    assert found_by_name.snippet is None


def test_empty_search_lists_everything():
    repo = FakeChatHistoryRepository()
    add_chat(repo, "A", ["x"])
    assert len(history(repo, query="   ").entries) == 1
    assert repo.calls[-1][0] is None


@pytest.mark.parametrize(
    "kw",
    [
        {"limit": 0},
        {"limit": 51},
        {"offset": -1},
        {"offset": 1001},
        {"query": "a" * 101},
    ],
)
def test_bad_requests_are_refused(kw):
    repo = FakeChatHistoryRepository()
    with pytest.raises(InvalidHistoryRequestError):
        history(repo, **kw)
    assert repo.calls == []


def test_unknown_user():
    with pytest.raises(UserNotFoundError):
        history(FakeChatHistoryRepository(time_zone=None))
