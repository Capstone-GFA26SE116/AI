"""The task chat endpoints, with fake repositories and a fake LLM (no DB, no Gemini)."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from metanoia_ai.api.deps import (
    get_apply_task_breakdown,
    get_send_task_chat_message,
    get_task_chat,
    manual_draft_limits,
)
from metanoia_ai.api.main import app
from metanoia_ai.application.chat_writer import ChatWriter
from metanoia_ai.application.ports.quota_repository import Subscription
from metanoia_ai.application.use_cases.apply_task_breakdown import ApplyTaskBreakdown
from metanoia_ai.application.use_cases.get_task_chat import GetTaskChat
from metanoia_ai.application.use_cases.send_task_chat_message import (
    SendTaskChatMessage,
)
from metanoia_ai.config import get_settings
from metanoia_ai.domain.models import TaskType
from metanoia_ai.domain.task_breakdown import DraftLimits
from tests.unit.api.test_auth import USER_ID, make_settings, make_token
from tests.unit.application.fakes import (
    FakeChatRepository,
    FakeLLMClient,
    FakeQuotaRepository,
)
from tests.unit.application.test_send_task_chat_message import (
    DRAFT,
    QUESTION,
    StubSimilar,
    answer,
    make_task,
)

NOW = datetime(2026, 10, 15, 2, 0, tzinfo=UTC)  # 09:00 in Saigon
AI_LIMITS = DraftLimits(2, 10, 5, 120)


class World:
    def __init__(self) -> None:
        self.repo = FakeChatRepository(now=NOW)
        self.quota = FakeQuotaRepository(now=NOW)
        self.subscription = Subscription(uuid4(), 50_000, 300, 7)
        self.quota.add_subscription(USER_ID, self.subscription, start_at=NOW)
        self.task = make_task()
        self.repo.add_task(USER_ID, self.task)
        self.llm = FakeLLMClient()

    def send_case(self) -> SendTaskChatMessage:
        return SendTaskChatMessage(
            self.repo,
            self.quota,
            ChatWriter(
                self.llm,
                limits=AI_LIMITS,
                max_retries=2,
                input_price_per_1m=0.1,
                output_price_per_1m=0.4,
            ),
            StubSimilar(),
            initial_k=Decimal("1.3"),
            history_limit=20,
            max_message_chars=2000,
            similar_limit=5,
            warning_ratio=0.3,
            now=lambda: NOW,
        )


@pytest.fixture
def world():
    return World()


@pytest.fixture
def client(world):
    """App without startup (no real DB), using the fakes."""
    app.dependency_overrides[get_settings] = make_settings
    app.dependency_overrides[get_send_task_chat_message] = world.send_case
    app.dependency_overrides[get_task_chat] = lambda: GetTaskChat(world.repo)
    app.dependency_overrides[get_apply_task_breakdown] = lambda: ApplyTaskBreakdown(
        world.repo, manual_draft_limits(make_settings())
    )
    yield TestClient(app)
    app.dependency_overrides.clear()


HEADERS = {"Authorization": f"Bearer {make_token()}"}


def send(client, world, message="Chia nhỏ giúp mình", task_id=None, headers=HEADERS):
    url = f"/chat/tasks/{task_id or world.task.task_id}/messages"
    return client.post(url, json={"message": message}, headers=headers)


def apply(client, world, body, task_id=None):
    url = f"/chat/tasks/{task_id or world.task.task_id}/apply"
    return client.post(url, json=body, headers=HEADERS)


# --- T23: POST messages ---


def test_t23_draft_turn_matches_the_plan(client, world):
    world.llm.answers = [DRAFT]

    response = send(client, world)

    assert response.status_code == 200
    body = response.json()
    assert body["task_id"] == str(world.task.task_id)
    user, ai = body["user_message"], body["ai_message"]
    assert (user["role"], user["kind"], user["content"]) == (
        "user",
        None,
        "Chia nhỏ giúp mình",
    )
    assert user["created_at"].endswith("+07:00")  # user's time zone
    assert (ai["role"], ai["kind"], ai["content"]) == (
        "assistant",
        "draft",
        "Mình chia 4 bước.",
    )
    draft = ai["draft"]
    assert draft["message_id"] == ai["id"]
    assert draft["subtasks"][0] == {
        "title": "Dựng khung",
        "estimated_minutes": 60,
        "order_index": 1,
        "reason": "r",
    }
    assert (
        draft["your_estimate_minutes"],
        draft["adjusted_minutes"],
        draft["subtask_total_minutes"],
    ) == (240, 312, 300)
    assert (draft["adjusted_is_hint"], draft["warning"], draft["similar"]) == (
        False,
        None,
        None,
    )


def test_question_turn_has_no_draft(client, world):
    world.llm.answers = [QUESTION]
    ai = send(client, world).json()["ai_message"]
    assert (ai["kind"], ai["draft"]) == ("question", None)


def test_t23_missing_token_returns_401(client, world):
    assert send(client, world, headers={}).status_code == 401


def test_t23_unknown_task_returns_404(client, world):
    response = send(client, world, task_id=uuid4())
    assert (response.status_code, response.json()) == (
        404,
        {"detail": "Task not found"},
    )


def test_t23_fixed_task_returns_409_with_reason(client, world):
    fixed = make_task(task_type=TaskType.FIXED, estimated_minutes=None)
    world.repo.add_task(USER_ID, fixed)
    response = send(client, world, task_id=fixed.task_id)
    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "task_not_splittable",
        "reason": "fixed",
    }


def test_t23_no_plan_returns_409(client, world):
    world.quota.subscriptions.clear()
    response = send(client, world)
    assert (response.status_code, response.json()["detail"]) == (
        409,
        {"code": "no_active_plan"},
    )


@pytest.mark.parametrize("message", ["", "   ", "x" * 2001])
def test_t23_bad_message_returns_422(client, world, message):
    assert send(client, world, message).status_code == 422
    assert world.llm.prompts == []


def test_t23_missing_message_field_returns_422(client, world):
    url = f"/chat/tasks/{world.task.task_id}/messages"
    assert client.post(url, json={}, headers=HEADERS).status_code == 422


def test_t23_out_of_quota_returns_429(client, world):
    world.quota.calls_used[world.subscription.subscription_id] = 300
    world.quota.subscriptions[USER_ID] = [
        (NOW, Subscription(world.subscription.subscription_id, 50_000, 300, 300))
    ]
    assert send(client, world).status_code == 429


def test_t23_ai_failure_returns_502(client, world):
    world.llm.answers = ["bad", "bad", "bad"]
    response = send(client, world)
    assert response.status_code == 502
    assert world.repo.conversations == {}


# --- T24: GET ---


def test_t24_no_chat_yet(client, world):
    response = client.get(f"/chat/tasks/{world.task.task_id}", headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == {
        "task_id": str(world.task.task_id),
        "messages": [],
        "latest_draft": None,
        "applied": False,
    }


def test_t24_chat_in_order_with_latest_draft(client, world):
    world.llm.answers = [DRAFT, QUESTION]
    first = send(client, world).json()
    send(client, world, "Thêm bước SEO")

    body = client.get(f"/chat/tasks/{world.task.task_id}", headers=HEADERS).json()

    assert [m["role"] for m in body["messages"]] == ["user", "assistant"] * 2
    assert [m["kind"] for m in body["messages"]] == [None, "draft", None, "question"]
    assert body["latest_draft"]["message_id"] == first["ai_message"]["id"]
    assert body["applied"] is False


def test_t24_other_users_task_returns_404(client, world):
    other = make_task()
    world.repo.add_task(uuid4(), other)
    response = client.get(f"/chat/tasks/{other.task_id}", headers=HEADERS)
    assert response.status_code == 404


# --- T25: apply ---


def test_t25_apply_returns_201_with_subtasks(client, world):
    world.llm.answers = [DRAFT]
    draft_id = send(client, world).json()["ai_message"]["id"]

    response = apply(client, world, {"message_id": draft_id})

    assert response.status_code == 201
    subtasks = response.json()["subtasks"]
    assert [(s["title"], s["order_index"]) for s in subtasks] == [
        ("Dựng khung", 1),
        ("Giao diện", 2),
        ("API", 3),
        ("Test", 4),
    ]
    body = client.get(f"/chat/tasks/{world.task.task_id}", headers=HEADERS).json()
    assert body["applied"] is True


def test_t25_apply_with_edits(client, world):
    world.llm.answers = [DRAFT]
    draft_id = send(client, world).json()["ai_message"]["id"]
    response = apply(
        client,
        world,
        {
            "message_id": draft_id,
            "subtasks": [{"title": "Làm hết", "estimated_minutes": 300}],
        },
    )
    assert response.status_code == 201
    assert response.json()["subtasks"][0]["estimated_minutes"] == 300


def test_t25_apply_twice_returns_409(client, world):
    world.llm.answers = [DRAFT]
    draft_id = send(client, world).json()["ai_message"]["id"]
    apply(client, world, {"message_id": draft_id})
    response = apply(client, world, {"message_id": draft_id})
    assert (response.status_code, response.json()["detail"]) == (
        409,
        {"code": "has_subtasks"},
    )


def test_t25_old_draft_returns_409(client, world):
    world.llm.answers = [DRAFT, answer(subtasks=[("A", 60), ("B", 60)])]
    old_id = send(client, world).json()["ai_message"]["id"]
    send(client, world, "Bớt bước")
    response = apply(client, world, {"message_id": old_id})
    assert (response.status_code, response.json()["detail"]) == (
        409,
        {"code": "not_latest_draft"},
    )


def test_t25_unknown_message_returns_404(client, world):
    world.llm.answers = [DRAFT]
    send(client, world)
    response = apply(client, world, {"message_id": str(uuid4())})
    assert (response.status_code, response.json()) == (
        404,
        {"detail": "Message not found"},
    )


@pytest.mark.parametrize(
    "subtasks",
    [
        pytest.param([{"title": "A", "estimated_minutes": 481}], id="481-minutes"),
        pytest.param([], id="empty"),
        pytest.param([{"title": "A", "estimated_minutes": 1.5}], id="not-whole"),
    ],
)
def test_t25_bad_edits_return_422(client, world, subtasks):
    world.llm.answers = [DRAFT]
    draft_id = send(client, world).json()["ai_message"]["id"]
    response = apply(client, world, {"message_id": draft_id, "subtasks": subtasks})
    assert response.status_code == 422
    assert world.repo.subtasks == {}
