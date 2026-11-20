"""GET /chat/conversations (plans/chat-history.md T5), with the in-memory fake."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from metanoia_ai.api.deps import get_list_chat_history
from metanoia_ai.api.main import app
from metanoia_ai.application.ports.chat_history_repository import ChatScope
from metanoia_ai.application.use_cases.list_chat_history import ListChatHistory
from metanoia_ai.config import get_settings
from tests.unit.api.test_auth import USER_ID, make_settings, make_token
from tests.unit.application.fakes import FakeChatHistoryRepository
from tests.unit.application.test_list_chat_history import T0, add_chat

HEADERS = {"Authorization": f"Bearer {make_token()}"}


@pytest.fixture
def repo():
    return FakeChatHistoryRepository()


@pytest.fixture
def client(repo):
    app.dependency_overrides[get_settings] = make_settings
    app.dependency_overrides[get_list_chat_history] = lambda: ListChatHistory(repo)
    yield TestClient(app)
    app.dependency_overrides.clear()


def get(client, **params):
    return client.get("/chat/conversations", params=params, headers=HEADERS)


def test_lists_chats_with_project_task_and_last_message(client, repo):
    add_chat(repo, "Tiệm bánh", ["Brief", "Ok"], user=USER_ID)
    add_chat(
        repo,
        "Quán cà phê",
        ["Chia nhỏ giúp mình"],
        user=USER_ID,
        start=T0 + timedelta(hours=1),
        scope=ChatScope.TASK,
    )
    response = get(client)
    assert response.status_code == 200
    body = response.json()
    assert body["has_more"] is False
    task_chat, project_chat = body["items"]
    assert task_chat["scope"] == "task"
    assert task_chat["task"]["name"] == "Làm logo"
    assert project_chat["scope"] == "project" and project_chat["task"] is None
    assert project_chat["project"]["name"] == "Tiệm bánh"
    assert project_chat["message_count"] == 2
    last = project_chat["last_message"]
    assert last == {
        "role": "assistant",
        "preview": "Ok",
        "created_at": "2026-11-20T09:01:00+07:00",  # the user's time zone
    }
    assert project_chat["match"] is None


def test_search_returns_the_match(client, repo):
    chat = add_chat(repo, "Tiệm bánh", ["Thêm trang blog nhé", "Ok"], user=USER_ID)
    add_chat(repo, "Khác", ["Logo"], user=USER_ID)
    body = get(client, q="BLOG").json()
    [item] = body["items"]
    assert item["match"]["message_id"] == str(chat.messages[0].message_id)
    assert item["match"]["snippet"] == "Thêm trang blog nhé"


def test_paging(client, repo):
    for hour in range(3):
        add_chat(
            repo, f"P{hour}", ["x"], user=USER_ID, start=T0 + timedelta(hours=hour)
        )
    body = get(client, limit=2).json()
    assert [i["project"]["name"] for i in body["items"]] == ["P2", "P1"]
    assert body["has_more"] is True
    assert get(client, limit=2, offset=2).json()["has_more"] is False


@pytest.mark.parametrize(
    "params", [{"q": "a" * 101}, {"limit": 0}, {"limit": 51}, {"offset": -1}]
)
def test_bad_parameters_are_422(client, params):
    assert get(client, **params).status_code == 422


def test_needs_a_token(client):
    assert client.get("/chat/conversations").status_code == 401


def test_does_not_hide_the_project_chat_endpoints(client):
    # /chat/conversations/{id} still belongs to the project chat router.
    paths = client.get("/openapi.json").json()["paths"]
    assert "get" in paths["/chat/conversations"]
    assert "get" in paths["/chat/conversations/{conversation_id}"]
