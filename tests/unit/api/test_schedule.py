"""The schedule endpoint, with fake repositories and a fake LLM (no DB, no network)."""

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from metanoia_ai.api.deps import get_propose_schedule
from metanoia_ai.api.main import app
from metanoia_ai.application.ports.quota_repository import Subscription
from metanoia_ai.application.reason_writer import ReasonWriter
from metanoia_ai.application.use_cases.propose_schedule import ProposeSchedule
from metanoia_ai.config import get_settings
from tests.unit.api.test_auth import USER_ID, make_settings, make_token
from tests.unit.application.fakes import (
    FakeLLMClient,
    FakeQuotaRepository,
    FakeScheduleRepository,
)
from tests.unit.application.test_propose_schedule import (
    NOW,
    at,
    good_answer,
    planning,
    task,
)

URL = "/schedule/propose"


class Setup:
    def __init__(self) -> None:
        self.quota = FakeQuotaRepository(NOW)
        self.repo = FakeScheduleRepository(self.quota)
        self.subscription = Subscription(uuid4(), 100_000, 10, 0)
        self.quota.add_subscription(USER_ID, self.subscription, NOW - timedelta(days=1))
        self.task = task(minutes=60)
        self.repo.planning[USER_ID] = planning(self.task)
        writer = ReasonWriter(
            FakeLLMClient(good_answer),
            max_retries=2,
            input_price_per_1m=0.1,
            output_price_per_1m=0.4,
        )
        self.use_case = ProposeSchedule(
            self.repo,
            self.quota,
            writer,
            initial_k=Decimal("1.3"),
            window_days=7,
            min_chunk_minutes=30,
            round_minutes=5,
            top_productive_hours=3,
            quota_daily_max=50,
            clock=lambda: NOW.astimezone(),  # any zone: results come back in the user's
        )


@pytest.fixture
def setup():
    return Setup()


@pytest.fixture
def client(setup):
    """App without startup (no real DB), using the fakes."""
    app.dependency_overrides[get_settings] = make_settings
    app.dependency_overrides[get_propose_schedule] = lambda: setup.use_case
    yield TestClient(app)
    app.dependency_overrides.clear()


def post(client, body=None, token=None):
    headers = {"Authorization": f"Bearer {token or make_token()}"}
    return client.post(URL, json=body if body is not None else {}, headers=headers)


def test_t30_normal_call(client, setup):
    response = post(client)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "proposal_id",
        "is_feasible",
        "missing_minutes",
        "explanation",
        "slots",
        "warnings",
    }
    assert body["is_feasible"] is True
    assert body["missing_minutes"] is None
    assert body["explanation"] == "Xếp được 1/1 task."
    assert body["warnings"] == []
    (slot,) = body["slots"]
    assert slot["task_id"] == str(setup.task.task_id)
    assert slot["subtask_id"] is None
    assert slot["start"] == "2026-10-19T09:00:00+07:00"  # user's time zone
    assert slot["end"] == "2026-10-19T10:18:00+07:00"
    assert slot["proposed_minutes"] == 78
    assert slot["explanation"] == "Good time for this task."
    assert body["proposal_id"] == str(setup.repo.pending(USER_ID)[0].proposal_id)


def test_english_and_task_ids(client, setup):
    other = task(title="Other")
    setup.repo.planning[USER_ID] = planning(setup.task, other)
    response = post(client, {"task_ids": [str(other.task_id)], "language": "en"})
    assert response.status_code == 200
    body = response.json()
    assert body["explanation"] == "Scheduled 1 of 1 tasks."
    assert [s["task_id"] for s in body["slots"]] == [str(other.task_id)]


def test_warning_shape(client, setup):
    setup.repo.planning[USER_ID] = planning(task(minutes=600, deadline=at(19, 17)))
    body = post(client).json()
    assert body["is_feasible"] is False
    assert body["missing_minutes"] == 300
    assert body["warnings"] == [
        {
            "task_id": body["warnings"][0]["task_id"],
            "type": "not_enough_time",
            "needed_minutes": 780,
            "free_minutes": 480,
            "message": "Cần 780 phút, nhưng trước deadline chỉ còn trống 480 phút.",
        }
    ]


def test_nothing_to_schedule_returns_200_without_id(client, setup):
    setup.repo.planning[USER_ID] = planning()
    response = post(client)
    assert response.status_code == 200
    assert response.json()["proposal_id"] is None
    assert response.json()["slots"] == []


def test_t31_missing_token_returns_401(client):
    assert client.post(URL, json={}).status_code == 401


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"task_ids": ["not-a-uuid"]}, id="bad-task-id"),
        pytest.param({"task_ids": "abc"}, id="task-ids-not-a-list"),
        pytest.param({"language": "fr"}, id="unknown-language"),
    ],
)
def test_t32_bad_body_returns_422(client, body):
    assert post(client, body).status_code == 422


def test_t33_out_of_quota_returns_429(client, setup):
    setup.quota.calls_used[setup.subscription.subscription_id] = 10  # limit 10
    response = post(client)
    assert response.status_code == 429
    assert response.json() == {"detail": "AI quota exceeded"}


def test_no_subscription_returns_409(client, setup):
    setup.quota.subscriptions.clear()
    response = post(client)
    assert response.status_code == 409
    assert response.json() == {"detail": "No active subscription"}
