"""FR-16b endpoint with a fake repository (no DB). T8."""

import pytest
from fastapi.testclient import TestClient

from metanoia_ai.api.deps import get_plan_overrun
from metanoia_ai.api.main import app
from metanoia_ai.application.ports.overrun_repository import OverrunData
from metanoia_ai.application.use_cases.plan_overrun import PlanOverrun
from metanoia_ai.config import get_settings
from metanoia_ai.domain.overrun_plan import SlotState
from tests.unit.api.test_auth import USER_ID, make_settings, make_token
from tests.unit.application.fakes import FakeOverrunRepository
from tests.unit.application.test_plan_overrun import (
    HOURS,
    LATE,
    LATE_ID,
    NOW,
    SETTINGS,
    at,
    row,
)

URL = "/schedule/overrun"


def make_client(*slots):
    """App without startup (no real DB): the use case runs on a fake for USER_ID."""
    repo = FakeOverrunRepository()
    repo.users[USER_ID] = OverrunData(HOURS, None, (LATE, *slots), (), ())
    use_case = PlanOverrun(repo, SETTINGS, now=lambda: NOW)
    app.dependency_overrides[get_settings] = make_settings
    app.dependency_overrides[get_plan_overrun] = lambda: use_case
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()


def auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token()}"}


def body(**fields) -> dict:
    return {"slot_id": str(LATE_ID), "extra_minutes": 30, **fields}


def test_replan_shape_in_the_user_time_zone() -> None:
    client = make_client(row(1, at(10, 45), at(11, 45)))

    response = client.post(URL, json=body(), headers=auth())

    assert response.status_code == 200
    data = response.json()
    assert data["kind"] == "replan"
    assert data["extended"] == {
        "slot_id": str(LATE_ID),
        "start": "2026-11-10T09:00:00+07:00",
        "end": "2026-11-10T10:35:00+07:00",
    }
    assert data["moves"] == [
        {
            "slot_id": str(row(1, at(10), at(11)).slot_id),
            "task_title": "Việc 1",
            "old_start": "2026-11-10T10:45:00+07:00",
            "old_end": "2026-11-10T11:45:00+07:00",
            "new_start": "2026-11-10T10:50:00+07:00",
            "new_end": "2026-11-10T11:50:00+07:00",
        }
    ]
    assert data["not_placed"] == [] and data["past_day_end"] is False
    assert data["summary"].startswith("Để làm tiếp Viết blog đến 10 giờ 35")


def test_extend_in_english() -> None:
    response = make_client().post(URL, json=body(language="en"), headers=auth())

    assert response.json()["kind"] == "extend"
    assert response.json()["moves"] == []
    assert response.json()["summary"] == "Keep working on Viết blog until 10:35 am."


def test_needs_a_token() -> None:
    assert make_client().post(URL, json=body()).status_code == 401


@pytest.mark.parametrize("extra", [0, 4, 481])
def test_extra_minutes_out_of_range_is_422(extra: int) -> None:
    response = make_client().post(URL, json=body(extra_minutes=extra), headers=auth())

    assert response.status_code == 422


def test_unknown_slot_is_404() -> None:
    response = make_client().post(
        URL, json=body(slot_id="00000000-0000-0000-0000-000000000999"), headers=auth()
    )

    assert response.status_code == 404


def test_finished_slot_is_409() -> None:
    client = make_client()
    done = row(LATE_ID.int, at(9), at(10), SlotState.DONE)
    repo = FakeOverrunRepository()
    repo.users[USER_ID] = OverrunData(HOURS, None, (done,), (), ())
    app.dependency_overrides[get_plan_overrun] = lambda: PlanOverrun(
        repo, SETTINGS, now=lambda: NOW
    )

    assert client.post(URL, json=body(), headers=auth()).status_code == 409
