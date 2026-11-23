"""FR-23 endpoint with fake repositories (no DB, no real LLM). T12."""

import pytest
from fastapi.testclient import TestClient

from metanoia_ai.api.deps import get_group_chat, get_suggest_group_assignee
from metanoia_ai.api.main import app
from metanoia_ai.config import get_settings
from metanoia_ai.domain.assignee_ranking import GroupRole
from tests.unit.api.test_auth import USER_ID, make_settings, make_token
from tests.unit.application.test_suggest_group_assignee import (
    GROUP,
    MEMBER_A,
    TASK,
    World,
    reasons_for,
    task_info,
)

URL = f"/groups/tasks/{TASK}/assignee-suggestions"


def make_client(world: World) -> TestClient:
    """The token's user (USER_ID) is the Leader of the fake group."""
    world.groups.roles[(GROUP, USER_ID)] = GroupRole.LEADER
    world.groups.roles.pop((GROUP, MEMBER_A), None)
    app.dependency_overrides[get_settings] = make_settings
    app.dependency_overrides[get_suggest_group_assignee] = lambda: world.use_case()
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()


def auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token()}"}


def test_suggestions_shape_without_private_numbers():
    world = World(reasons_for)
    world.quota.add_subscription(USER_ID, world.subscription, world.quota.now)
    response = make_client(world).post(URL, json={"language": "en"}, headers=auth())

    assert response.status_code == 200
    data = response.json()
    assert data["task_id"] == str(TASK) and data["reasons_skipped"] is None
    first = data["suggestions"][0]
    assert set(first) == {
        "user_id",
        "role",
        "rank",
        "is_current_assignee",
        "busy_at_fixed_time",
        "free_minutes",
        "group_work_minutes",
        "fits",
        "reason",
        "free_times",
    }  # no K, no needed minutes
    assert first["rank"] == 1 and first["reason"]
    assert first["free_times"][0]["start"].endswith("+07:00")  # member's clock


def test_without_quota_the_ranking_still_comes_back():
    world = World(calls_left=None)
    data = make_client(world).post(URL, json={}, headers=auth()).json()
    assert data["reasons_skipped"] == "quota"
    assert len(data["suggestions"]) == 3


@pytest.mark.parametrize(
    ("role", "changes", "code"),
    [
        (None, {}, 404),  # not a member
        (GroupRole.MEMBER, {}, 403),
        (GroupRole.LEADER, {"group_archived": True}, 409),
        (GroupRole.LEADER, {"status": "done"}, 409),
    ],
)
def test_error_codes(role, changes, code):
    world = World()
    client = make_client(world)
    if role is None:
        world.groups.roles.pop((GROUP, USER_ID))
    else:
        world.groups.roles[(GROUP, USER_ID)] = role
    world.groups.tasks[TASK] = task_info(**changes)
    response = client.post(URL, json={}, headers=auth())
    assert response.status_code == code


def test_needs_a_token():
    client = make_client(World())
    assert client.post(URL, json={}).status_code == 401


# --- FR-24: group plan chat endpoints (T9) ---

from tests.unit.application import test_group_chat as chat_world


def chat_client(world: "chat_world.World") -> TestClient:
    """The token's user (USER_ID) is the Leader of the chat world's group."""
    world.chats.roles[(chat_world.GROUP, USER_ID)] = GroupRole.LEADER
    world.quota.add_subscription(USER_ID, world.plan, world.quota.now)
    world.members.members[chat_world.GROUP].append(
        chat_world.GroupMember(USER_ID, GroupRole.LEADER)
    )
    world.schedules.planning[USER_ID] = chat_world.planning()
    app.dependency_overrides[get_settings] = make_settings
    app.dependency_overrides[get_group_chat] = lambda: world.use_case()
    return TestClient(app)


def test_chat_flow_start_plan_read_and_apply():
    world = chat_world.World(chat_world.draft_answer())
    client = chat_client(world)
    group = chat_world.GROUP

    started = client.post(f"/groups/{group}/chats", json={}, headers=auth())
    assert started.status_code == 201
    chat = started.json()["conversation_id"]

    turn = client.post(
        f"/groups/chats/{chat}/messages",
        json={"text": "Brief của khách", "mode": "plan"},
        headers=auth(),
    )
    assert turn.status_code == 200
    ai = turn.json()["ai_message"]
    tasks = [t for m in ai["draft"]["milestones"] for t in m["tasks"]]
    assert len(tasks) == 3
    assert all(t["suggested_assignee"]["reason"] for t in tasks)
    assert "k_bias" not in turn.text.lower()

    listed = client.get(f"/groups/{group}/chats", headers=auth()).json()
    assert [c["conversation_id"] for c in listed] == [chat]
    view = client.get(f"/groups/chats/{chat}", headers=auth()).json()
    assert [m["role"] for m in view["messages"]] == ["user", "assistant"]

    url = f"/groups/chats/{chat}/messages/{ai['message_id']}/applied"
    assert client.post(url, headers=auth()).status_code == 204
    assert client.post(url, headers=auth()).status_code == 409


def test_chat_error_codes():
    world = chat_world.World("bad", "bad")
    client = chat_client(world)
    group = chat_world.GROUP
    chat = client.post(f"/groups/{group}/chats", json={}, headers=auth()).json()[
        "conversation_id"
    ]
    send = f"/groups/chats/{chat}/messages"
    assert (
        client.post(
            send, json={"text": " ", "mode": "discuss"}, headers=auth()
        ).status_code
        == 422
    )
    assert client.post(send, json={"text": "Brief"}, headers=auth()).status_code == 502
    world.chats.roles[(group, USER_ID)] = GroupRole.MEMBER
    assert client.get(f"/groups/{group}/chats", headers=auth()).status_code == 403
    world.chats.roles.pop((group, USER_ID))
    assert client.get(f"/groups/chats/{chat}", headers=auth()).status_code == 404
    assert client.get(f"/groups/{group}/chats").status_code == 401


def test_free_plan_gets_plan_feature_locked():
    world = chat_world.World(price=0)
    client = chat_client(world)
    chat = client.post(
        f"/groups/{chat_world.GROUP}/chats", json={}, headers=auth()
    ).json()["conversation_id"]
    response = client.post(
        f"/groups/chats/{chat}/messages", json={"text": "Brief"}, headers=auth()
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "plan_feature_locked"
