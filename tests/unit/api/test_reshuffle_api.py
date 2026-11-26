"""FR-22d endpoints with a fake repository (no DB). T11."""

from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from metanoia_ai.api.deps import get_catch_up, get_insert_block, get_postpone_slot
from metanoia_ai.api.main import app
from metanoia_ai.application.ports.reshuffle_repository import FixedTask
from metanoia_ai.application.use_cases.reshuffle import (
    CatchUp,
    InsertBlock,
    PostponeSlot,
)
from metanoia_ai.config import get_settings
from metanoia_ai.domain.overrun_plan import SlotState
from tests.unit.api.test_auth import USER_ID, make_settings, make_token
from tests.unit.application.test_reshuffle_use_cases import (
    NOW,
    SETTINGS,
    FakeRepo,
    at,
    data,
    row,
)


def make_client(d, now=NOW) -> TestClient:
    repo = FakeRepo(d, USER_ID)
    app.dependency_overrides[get_settings] = make_settings
    app.dependency_overrides[get_insert_block] = lambda: InsertBlock(
        repo, SETTINGS, lambda: now
    )
    app.dependency_overrides[get_postpone_slot] = lambda: PostponeSlot(
        repo, SETTINGS, lambda: now
    )
    app.dependency_overrides[get_catch_up] = lambda: CatchUp(
        repo, SETTINGS, lambda: now
    )
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()


def auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token()}"}


def block(**fields) -> dict:
    return {
        "title": "Sửa lỗi gấp",
        "start": "2026-11-10T14:00:00+07:00",
        "end": "2026-11-10T15:00:00+07:00",
        **fields,
    }


# --- /schedule/insert-block ---


def test_insert_shape_in_the_user_time_zone() -> None:
    meeting = FixedTask(UUID(int=9), "Họp khách", at(14, 30), at(15), False, True)
    client = make_client(data([row(1, at(14), at(15))], fixed=[meeting]))

    response = client.post(
        "/schedule/insert-block",
        json=block(start="2026-11-10T07:00:00Z"),
        headers=auth(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["block"] == {
        "title": "Sửa lỗi gấp",
        "start": "2026-11-10T14:00:00+07:00",
        "end": "2026-11-10T15:00:00+07:00",
    }
    assert body["moves"] == [
        {
            "slot_id": str(UUID(int=1)),
            "task_title": "Việc 1",
            "old_start": "2026-11-10T14:00:00+07:00",
            "old_end": "2026-11-10T15:00:00+07:00",
            "new_start": "2026-11-10T15:15:00+07:00",
            "new_end": "2026-11-10T16:15:00+07:00",
        }
    ]
    assert body["conflicts"] == [
        {
            "kind": "fixed_task",
            "id": str(UUID(int=9)),
            "title": "Họp khách",
            "start": "2026-11-10T14:30:00+07:00",
            "end": "2026-11-10T15:00:00+07:00",
            "important": True,
            "why": "client",
        }
    ]
    assert body["options"] == ["other_time", "overwrite", "cancel"]
    assert body["recommendation"] == "other_time"
    assert body["other_time"] == {
        "start": "2026-11-10T09:00:00+07:00",
        "end": "2026-11-10T10:00:00+07:00",
    }
    assert body["skip_task_ids"] == [str(UUID(int=9))]
    assert body["project_id"] == str(UUID(int=500))
    assert body["domain_id"] == str(UUID(int=600))
    assert body["past_day_end"] is False and body["not_placed"] == []
    assert "trùng với Họp khách" in body["summary"]


@pytest.mark.parametrize(
    "fields",
    [
        {"start": "2026-11-10T14:00:00"},  # no time zone
        {"title": ""},
        {"end": "2026-11-10T13:00:00+07:00"},  # before start
        {"start": "2026-11-10T08:00:00+07:00"},  # in the past
    ],
)
def test_insert_rejects_bad_bodies(fields: dict) -> None:
    response = make_client(data()).post(
        "/schedule/insert-block", json=block(**fields), headers=auth()
    )
    assert response.status_code == 422


def test_every_endpoint_needs_a_token() -> None:
    client = make_client(data())
    assert client.post("/schedule/insert-block", json=block()).status_code == 401
    assert (
        client.post(
            "/schedule/postpone", json={"slot_id": str(UUID(int=1))}
        ).status_code
        == 401
    )
    assert client.post("/schedule/catch-up", json={}).status_code == 401


def test_unknown_user_is_404() -> None:
    client = make_client(None)
    assert (
        client.post("/schedule/insert-block", json=block(), headers=auth()).status_code
        == 404
    )
    assert client.post("/schedule/catch-up", json={}, headers=auth()).status_code == 404


# --- /schedule/postpone ---


def test_postpone_shape() -> None:
    client = make_client(data([row(1, at(14), at(15))]))

    response = client.post(
        "/schedule/postpone",
        json={"slot_id": str(UUID(int=1)), "language": "en"},
        headers=auth(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["move"]["new_start"] == "2026-11-11T08:00:00+07:00"
    assert (body["not_placed"], body["task_title"]) == (False, "Việc 1")
    assert body["summary"] == "Move Việc 1 to 8 am tomorrow?"


def test_postpone_errors() -> None:
    client = make_client(data([row(1, at(14), at(15), started=True)]))
    other = client.post(
        "/schedule/postpone", json={"slot_id": str(UUID(int=2))}, headers=auth()
    )
    started = client.post(
        "/schedule/postpone", json={"slot_id": str(UUID(int=1))}, headers=auth()
    )

    assert (other.status_code, started.status_code) == (404, 409)


# --- /schedule/catch-up ---


def test_catch_up_shape() -> None:
    client = make_client(
        data([row(1, at(8), at(8, 45), SlotState.MISSED)]), now=at(13, 2)
    )

    response = client.post("/schedule/catch-up", json={}, headers=auth())

    assert response.status_code == 200
    (move,) = response.json()["moves"]
    assert move["new_start"] == "2026-11-10T13:05:00+07:00"
    assert move["task_id"] == str(UUID(int=101)) and move["subtask_id"] is None
    assert response.json()["summary"].startswith("Có 1 việc bị lỡ.")
