"""The project chat endpoints (T24), with fake repositories and a fake LLM."""

from dataclasses import replace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from metanoia_ai.api.deps import (
    get_apply_project_breakdown,
    get_get_project_chat,
    get_list_project_chats,
    get_send_project_chat_message,
    get_start_project_chat,
    project_limits,
)
from metanoia_ai.api.main import app
from metanoia_ai.application.ports.project_chat_repository import ConversationStatus
from metanoia_ai.application.use_cases.apply_project_breakdown import (
    ApplyProjectBreakdown,
)
from metanoia_ai.application.use_cases.get_project_chat import GetProjectChat
from metanoia_ai.application.use_cases.start_project_chat import (
    ListProjectChats,
    StartProjectChat,
)
from metanoia_ai.config import get_settings
from tests.unit.api.test_auth import USER_ID, make_settings, make_token
from tests.unit.application.fakes import FakeLLMClient
from tests.unit.application.test_project_chat import (
    CODING,
    DESIGN,
    NOW,
    QUESTION,
    World,
    draft_answer,
)

HEADERS = {"Authorization": f"Bearer {make_token()}"}


class ApiWorld(World):
    def __init__(self, **kw) -> None:
        super().__init__(user=USER_ID, **kw)
        self.llm = FakeLLMClient()

    @property
    def project_id(self):
        return self.project.project_id


@pytest.fixture
def world():
    return ApiWorld()


@pytest.fixture
def client(world):
    """App without startup (no real DB), using the fakes."""
    app.dependency_overrides[get_settings] = make_settings
    app.dependency_overrides[get_start_project_chat] = lambda: StartProjectChat(
        world.repo, lambda: NOW
    )
    app.dependency_overrides[get_list_project_chats] = lambda: ListProjectChats(
        world.repo
    )
    app.dependency_overrides[get_send_project_chat_message] = lambda: world.use_case(
        world.llm
    )
    app.dependency_overrides[get_get_project_chat] = lambda: GetProjectChat(world.repo)
    app.dependency_overrides[get_apply_project_breakdown] = lambda: (
        ApplyProjectBreakdown(
            world.repo, project_limits(make_settings(), manual=True), lambda: NOW
        )
    )
    yield TestClient(app)
    app.dependency_overrides.clear()


def send(client, world, message="Brief của khách", chat_id=None, headers=HEADERS):
    url = f"/chat/conversations/{chat_id or world.chat_id}/messages"
    return client.post(url, json={"message": message}, headers=headers)


def drafted(client, world) -> str:
    world.llm.answers.append(draft_answer())
    return send(client, world).json()["ai_message"]["draft"]["message_id"]


def apply(client, world, body, chat_id=None):
    url = f"/chat/conversations/{chat_id or world.chat_id}/apply"
    return client.post(url, json=body, headers=HEADERS)


def set_status(world, status):
    owner, project = world.repo.projects[world.project_id]
    world.repo.projects[world.project_id] = (owner, replace(project, status=status))


# --- POST / GET /chat/projects/{id}/conversations ---


def test_start_chat_returns_201(client, world):
    response = client.post(
        f"/chat/projects/{world.project_id}/conversations", headers=HEADERS
    )
    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"conversation_id", "title", "status", "created_at"}
    assert (body["title"], body["status"]) == ("Brief 2026-10-16", "open")


def test_start_chat_on_another_users_project_is_404(client):
    response = client.post(f"/chat/projects/{uuid4()}/conversations", headers=HEADERS)
    assert response.status_code == 404


def test_start_chat_on_a_closed_project_is_409(client, world):
    set_status(world, "Completed")
    response = client.post(
        f"/chat/projects/{world.project_id}/conversations", headers=HEADERS
    )
    assert response.status_code == 409
    assert response.json()["detail"] == {"code": "project_closed"}


def test_list_chats_newest_first(client, world):
    client.post(f"/chat/projects/{world.project_id}/conversations", headers=HEADERS)
    response = client.get(
        f"/chat/projects/{world.project_id}/conversations", headers=HEADERS
    )
    assert response.status_code == 200
    chats = response.json()
    assert len(chats) == 2
    assert chats[1]["conversation_id"] == str(world.chat_id)


def test_list_chats_of_unknown_project_is_404(client):
    assert (
        client.get(
            f"/chat/projects/{uuid4()}/conversations", headers=HEADERS
        ).status_code
        == 404
    )


# --- POST /chat/conversations/{id}/messages ---


def test_question_turn(client, world):
    world.llm.answers.append(QUESTION)
    response = send(client, world)
    assert response.status_code == 200
    body = response.json()
    assert body["conversation_id"] == str(world.chat_id)
    assert body["user_message"]["role"] == "user"
    assert body["user_message"]["kind"] is None
    ai = body["ai_message"]
    assert (ai["role"], ai["kind"], ai["draft"]) == ("assistant", "question", None)
    assert ai["created_at"].endswith("+07:00")  # user's time zone


def test_draft_turn_matches_the_plan_shape(client, world):
    world.llm.answers.append(draft_answer(reminders=["Họp chốt giao diện"]))

    draft = send(client, world).json()["ai_message"]["draft"]

    assert set(draft) == {
        "message_id",
        "milestones",
        "reminders",
        "numbers",
        "warnings",
    }
    m1 = draft["milestones"][0]
    assert (m1["key"], m1["title"], m1["deadline"]) == ("m1", "Thiết kế", "2026-10-22")
    assert m1["tasks"][0] == {
        "key": "m1-t1",
        "title": "Wireframe",
        "domain": {"id": str(DESIGN), "name": "Design"},
        "estimated_minutes": 240,
        "reason": "r",
    }
    assert draft["milestones"][1]["deadline"] == "2026-10-30"  # project deadline
    assert draft["reminders"] == ["Họp chốt giao diện"]
    assert draft["numbers"] == {
        "by_domain": [
            {"domain": "Design", "estimated_minutes": 240, "adjusted_minutes": 312},
            {"domain": "Coding", "estimated_minutes": 300, "adjusted_minutes": 450},
        ],
        "total_estimated_minutes": 540,
        "total_adjusted_minutes": 762,
        "days_left": 14,
        "adjusted_is_hint": False,
    }
    assert "K_bias" not in str(draft)


def test_send_without_token_is_401(client, world):
    assert send(client, world, headers={}).status_code == 401


def test_send_to_a_task_chat_is_404(client, world):
    world.repo.task_chat_ids.add(world.chat_id)
    assert send(client, world).status_code == 404


def test_send_to_unknown_chat_is_404(client, world):
    assert send(client, world, chat_id=uuid4()).status_code == 404


def test_send_to_an_applied_chat_is_409(client, world):
    message_id = drafted(client, world)
    apply(client, world, {"message_id": message_id})
    response = send(client, world)
    assert response.status_code == 409
    assert response.json()["detail"] == {"code": "already_applied"}


def test_send_on_a_closed_project_is_409(client, world):
    set_status(world, "Archived")
    response = send(client, world)
    assert (response.status_code, response.json()["detail"]) == (
        409,
        {"code": "project_closed"},
    )


def test_send_without_a_plan_is_409(client, world):
    world.quota.subscriptions[USER_ID] = []
    response = send(client, world)
    assert (response.status_code, response.json()["detail"]) == (
        409,
        {"code": "no_active_plan"},
    )


@pytest.mark.parametrize("message", ["", "x" * 8001])
def test_bad_message_is_422(client, world, message):
    response = send(client, world, message=message)
    assert response.status_code == 422
    assert world.llm.prompts == []


def test_missing_body_is_422(client, world):
    url = f"/chat/conversations/{world.chat_id}/messages"
    assert client.post(url, json={}, headers=HEADERS).status_code == 422


def test_out_of_quota_is_429(client, world):
    world.quota.calls_used[world.plan.subscription_id] = 50
    assert send(client, world).status_code == 429


def test_ai_failure_is_502(client, world):
    world.llm.answers.extend(["bad", "still bad"])
    response = send(client, world)
    assert response.status_code == 502
    assert world.calls_used == 0


# --- GET /chat/conversations/{id} ---


def test_get_chat(client, world):
    message_id = drafted(client, world)
    response = client.get(f"/chat/conversations/{world.chat_id}", headers=HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["project_id"] == str(world.project_id)
    assert (body["status"], body["applied"]) == ("open", False)
    assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
    assert body["latest_draft"]["message_id"] == message_id


def test_get_chat_after_apply(client, world):
    message_id = drafted(client, world)
    apply(client, world, {"message_id": message_id})
    body = client.get(f"/chat/conversations/{world.chat_id}", headers=HEADERS).json()
    assert (body["status"], body["applied"]) == ("applied", True)


def test_get_a_task_chat_is_404(client, world):
    world.repo.task_chat_ids.add(world.chat_id)
    response = client.get(f"/chat/conversations/{world.chat_id}", headers=HEADERS)
    assert response.status_code == 404


def test_get_chat_without_token_is_401(client, world):
    assert client.get(f"/chat/conversations/{world.chat_id}").status_code == 401


# --- POST /chat/conversations/{id}/apply ---


def test_apply_returns_201_with_estimate_sources(client, world):
    message_id = drafted(client, world)

    response = apply(client, world, {"message_id": message_id})

    assert response.status_code == 201
    body = response.json()
    assert body["skipped"] == []
    first = body["milestones"][0]
    assert (first["title"], first["deadline"]) == ("Thiết kế", "2026-10-22")
    task = first["tasks"][0]
    assert set(task) == {"id", "title", "estimated_minutes", "estimate_source"}
    assert (task["title"], task["estimate_source"]) == ("Wireframe", "ai")


def test_apply_with_edits(client, world):
    message_id = drafted(client, world)
    body = {
        "message_id": message_id,
        "milestones": [
            {
                "key": "m1",
                "title": "Thiết kế",
                "deadline": "2026-10-24",
                "tasks": [
                    {
                        "key": "m1-t1",
                        "title": "Wireframe",
                        "domain_id": str(DESIGN),
                        "estimated_minutes": 300,
                    },
                    {
                        "title": "Logo",
                        "domain_id": str(CODING),
                        "estimated_minutes": 60,
                    },
                ],
            }
        ],
    }

    response = apply(client, world, body)

    assert response.status_code == 201
    tasks = response.json()["milestones"][0]["tasks"]
    assert [t["estimate_source"] for t in tasks] == ["user", "user"]
    assert [r.task.ai_estimated_minutes for r in world.repo.new_tasks] == [240, None]


def test_apply_skipped_shape(client, world):
    message_id = drafted(client, world)
    world.repo.add_old_task(world.project_id, "Wireframe")
    body = apply(client, world, {"message_id": message_id}).json()
    assert body["skipped"] == [{"title": "Wireframe", "reason": "existing_task"}]


def test_apply_twice_is_409(client, world):
    message_id = drafted(client, world)
    apply(client, world, {"message_id": message_id})
    response = apply(client, world, {"message_id": message_id})
    assert (response.status_code, response.json()["detail"]) == (
        409,
        {"code": "already_applied"},
    )


def test_apply_an_older_draft_is_409(client, world):
    first = drafted(client, world)
    drafted(client, world)
    response = apply(client, world, {"message_id": first})
    assert response.json()["detail"] == {"code": "not_latest_draft"}


def test_apply_after_a_domain_is_turned_off_is_409(client, world):
    message_id = drafted(client, world)
    world.repo.add_domain(CODING, "Coding", is_active=False)
    response = apply(client, world, {"message_id": message_id})
    assert response.json()["detail"] == {"code": "domain_inactive"}


def test_apply_on_a_closed_project_is_409(client, world):
    message_id = drafted(client, world)
    set_status(world, "Completed")
    response = apply(client, world, {"message_id": message_id})
    assert response.json()["detail"] == {"code": "project_closed"}


def test_apply_unknown_message_is_404(client, world):
    drafted(client, world)
    assert apply(client, world, {"message_id": str(uuid4())}).status_code == 404


def test_apply_to_a_task_chat_is_404(client, world):
    message_id = drafted(client, world)
    world.repo.task_chat_ids.add(world.chat_id)
    assert apply(client, world, {"message_id": message_id}).status_code == 404


def test_apply_bad_edit_is_422(client, world):
    message_id = drafted(client, world)
    body = {
        "message_id": message_id,
        "milestones": [
            {
                "title": "M",
                "tasks": [
                    {"title": "A", "domain_id": str(DESIGN), "estimated_minutes": 4}
                ],
            }
        ],
    }
    assert apply(client, world, body).status_code == 422
    assert world.repo.new_tasks == []


def test_apply_without_token_is_401(client, world):
    url = f"/chat/conversations/{world.chat_id}/apply"
    assert client.post(url, json={"message_id": str(uuid4())}).status_code == 401


def test_status_values_are_stable_api_strings():
    assert [s.value for s in ConversationStatus] == ["open", "applied", "closed"]
