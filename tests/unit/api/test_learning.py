"""The learning endpoint, with the fake repository (no database)."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from metanoia_ai.api.deps import get_record_task_completion
from metanoia_ai.api.main import app
from metanoia_ai.application.use_cases.record_task_completion import (
    RecordTaskCompletion,
)
from metanoia_ai.config import get_settings
from metanoia_ai.domain.models import TaskType
from tests.unit.api.test_auth import USER_ID, make_settings, make_token
from tests.unit.application.fakes import FakeLearningRepository
from tests.unit.application.test_record_task_completion import make_task, session

URL = "/learning/task-completed"


@pytest.fixture
def repo():
    return FakeLearningRepository()


@pytest.fixture
def client(repo):
    """App without startup (no real DB), using the fake repository."""
    use_case = RecordTaskCompletion(repo, alpha=0.2, initial_k=1.3, outlier_ratio=5)
    app.dependency_overrides[get_settings] = make_settings
    app.dependency_overrides[get_record_task_completion] = lambda: use_case
    yield TestClient(app)
    app.dependency_overrides.clear()


def post(client, task_id, token=None):
    headers = {"Authorization": f"Bearer {token or make_token()}"}
    return client.post(URL, json={"task_id": str(task_id)}, headers=headers)


def test_learns_from_a_done_task(client, repo):
    task = make_task()  # 60' estimate, 90' actual
    repo.add_task(USER_ID, task)

    response = post(client, task.task_id)

    assert response.status_code == 200
    assert response.json() == {
        "learned": True,
        "reason": None,
        "k_old": 1.3,
        "k_new": 1.34,
        "sample_index": 1,
    }


def test_second_call_returns_already_learned(client, repo):
    task = make_task()
    repo.add_task(USER_ID, task)
    post(client, task.task_id)

    response = post(client, task.task_id)

    assert response.status_code == 200
    assert response.json()["reason"] == "already_learned"
    assert response.json()["k_new"] == 1.34


def test_skipped_task_returns_the_reason(client, repo):
    task = make_task(task_type=TaskType.FIXED, estimated=None)
    repo.add_task(USER_ID, task)

    response = post(client, task.task_id)

    assert response.status_code == 200
    assert response.json() == {
        "learned": False,
        "reason": "fixed_task",
        "k_old": None,
        "k_new": None,
        "sample_index": None,
    }


def test_outlier_returns_the_reason(client, repo):
    task = make_task(sessions=[session(400)])
    repo.add_task(USER_ID, task)
    assert post(client, task.task_id).json()["reason"] == "outlier"


def test_unknown_task_returns_404(client):
    response = post(client, uuid4())
    assert response.status_code == 404
    assert response.json() == {"detail": "Task not found"}


def test_other_users_task_returns_404(client, repo):
    task = make_task()
    repo.add_task(uuid4(), task)  # owned by someone else
    assert post(client, task.task_id).status_code == 404


def test_missing_token_returns_401(client):
    response = client.post(URL, json={"task_id": str(uuid4())})
    assert response.status_code == 401


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({}, id="missing-task-id"),
        pytest.param({"task_id": "not-a-uuid"}, id="bad-uuid"),
    ],
)
def test_bad_body_returns_422(client, body):
    headers = {"Authorization": f"Bearer {make_token()}"}
    assert client.post(URL, json=body, headers=headers).status_code == 422
