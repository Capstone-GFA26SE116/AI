"""FR-25 endpoint with fake repositories (no DB). T6."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from metanoia_ai.api.deps import get_suggest_group_slots
from metanoia_ai.api.main import app
from metanoia_ai.application.use_cases.suggest_group_slots import SuggestGroupSlots
from metanoia_ai.config import get_settings
from tests.unit.api.test_auth import USER_ID, make_settings, make_token
from tests.unit.application.fakes import (
    FakeGroupSlotRepository,
    FakeQuotaRepository,
    FakeScheduleRepository,
)
from tests.unit.application.test_suggest_group_slots import (
    NOW,
    OTHER,
    SETTINGS,
    TASK,
    part,
    planning,
    task,
)

URL = f"/groups/tasks/{TASK}/slot-suggestions"


def make_client(group_task) -> TestClient:
    groups = FakeGroupSlotRepository()
    if group_task is not None:
        groups.tasks[TASK] = group_task
    schedules = FakeScheduleRepository(FakeQuotaRepository(now=NOW))
    schedules.planning[USER_ID] = planning()
    app.dependency_overrides[get_settings] = make_settings
    app.dependency_overrides[get_suggest_group_slots] = lambda: SuggestGroupSlots(
        groups, schedules, SETTINGS, lambda: NOW
    )
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()


def auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token()}"}


def mine(group_task):
    """The token's user is the assignee of every part."""
    return replace(
        group_task,
        parts=tuple(
            replace(p, assignee_id=USER_ID if p.assignee_id != OTHER else OTHER)
            for p in group_task.parts
        ),
    )


def test_returns_slots_in_the_users_time_zone():
    response = make_client(mine(task())).post(
        URL, json={"language": "en"}, headers=auth()
    )

    assert response.status_code == 200
    data = response.json()
    assert data["task_id"] == str(TASK)
    assert [s["start"] for s in data["slots"]] == [
        "2026-11-23T08:00:00+07:00",
        "2026-11-23T09:15:00+07:00",
    ]
    assert data["slots"][0]["minutes"] == 60
    assert data["slots"][0]["reason"].startswith("Free time on your calendar")
    assert data["warnings"] == []
    assert data["time_zone"] == "Asia/Ho_Chi_Minh"


@pytest.mark.parametrize(
    ("group_task", "code"),
    [
        (None, 404),
        (mine(task(is_member=False)), 404),
        (task(parts=(part(3, OTHER),)), 403),
        (mine(task(group_archived=True)), 409),
        (mine(task(is_closed=True)), 409),
        (mine(task(has_fixed_time=True)), 409),
    ],
)
def test_errors(group_task, code):
    response = make_client(group_task).post(URL, json={}, headers=auth())
    assert response.status_code == code


def test_needs_a_token():
    assert make_client(None).post(URL, json={}).status_code == 401
