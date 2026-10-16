"""The learning endpoint, with fake repositories (no database)."""

from dataclasses import replace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from metanoia_ai.api.deps import (
    get_memory_embedder,
    get_record_task_completion,
    get_save_task_memory,
)
from metanoia_ai.api.main import app
from metanoia_ai.application.ports.learning_repository import TaskForLearning
from metanoia_ai.application.ports.memory_repository import TaskForMemory
from metanoia_ai.application.use_cases.record_task_completion import (
    RecordTaskCompletion,
)
from metanoia_ai.application.use_cases.save_task_memory import SaveTaskMemory
from metanoia_ai.config import get_settings
from metanoia_ai.domain.models import EstimateSource, TaskType
from tests.unit.api.test_auth import USER_ID, make_settings, make_token
from tests.unit.application.fakes import FakeLearningRepository, FakeMemoryRepository
from tests.unit.application.test_record_task_completion import make_task, session

URL = "/learning/task-completed"


class Repos:
    """The FR-17 and FR-19 fakes, holding the same task."""

    def __init__(self) -> None:
        self.learning = FakeLearningRepository()
        self.memory = FakeMemoryRepository()
        self.embedded: list[tuple] = []  # background jobs run by the app

    def add(self, owner_id, task: TaskForLearning, status: str | None = None) -> None:
        self.learning.add_task(owner_id, task)
        self.memory.add_task(
            owner_id,
            TaskForMemory(
                task_id=task.task_id,
                task_type=task.task_type,
                status=status or ("Done" if task.is_done else "ToDo"),
                title="Write the landing page",
                domain_name="Writing",
                estimated_minutes=task.estimated_minutes,
                client_name=None,
                sessions=(),
            ),
        )

    async def embed(self, user_id, memory_id) -> None:
        self.embedded.append((user_id, memory_id))


class BrokenSaveTaskMemory:
    async def execute(self, user_id, task_id):
        raise RuntimeError("memory part exploded")


@pytest.fixture
def repos():
    return Repos()


@pytest.fixture
def client(repos):
    """App without startup (no real DB), using the fake repositories."""
    use_case = RecordTaskCompletion(
        repos.learning, alpha=0.2, initial_k=1.3, outlier_ratio=5
    )
    memory = SaveTaskMemory(repos.memory, model="fake-embedding", max_chars=1000)
    app.dependency_overrides[get_settings] = make_settings
    app.dependency_overrides[get_record_task_completion] = lambda: use_case
    app.dependency_overrides[get_save_task_memory] = lambda: memory
    app.dependency_overrides[get_memory_embedder] = lambda: repos.embed
    yield TestClient(app)
    app.dependency_overrides.clear()


def post(client, task_id, token=None):
    headers = {"Authorization": f"Bearer {token or make_token()}"}
    return client.post(URL, json={"task_id": str(task_id)}, headers=headers)


# --- FR-17 (unchanged, plus the new `memory` field) ---


def test_learns_from_a_done_task(client, repos):
    task = make_task()  # 60' estimate, 90' actual
    repos.add(USER_ID, task)

    response = post(client, task.task_id)

    assert response.status_code == 200
    assert response.json() == {
        "learned": True,
        "reason": None,
        "k_old": 1.3,
        "k_new": 1.34,
        "sample_index": 1,
        "memory": "queued",
    }


def test_second_call_returns_already_learned(client, repos):
    task = make_task()
    repos.add(USER_ID, task)
    post(client, task.task_id)

    response = post(client, task.task_id)

    assert response.status_code == 200
    assert response.json()["reason"] == "already_learned"
    assert response.json()["k_new"] == 1.34
    assert len(repos.memory.rows) == 1  # still one memory (AC6)


def test_skipped_task_returns_the_reason(client, repos):
    task = make_task(task_type=TaskType.FIXED, estimated=None)
    repos.add(USER_ID, task)

    response = post(client, task.task_id)

    assert response.status_code == 200
    assert response.json() == {
        "learned": False,
        "reason": "fixed_task",
        "k_old": None,
        "k_new": None,
        "sample_index": None,
        "memory": "skipped",
    }
    assert repos.embedded == []


def test_outlier_returns_the_reason(client, repos):
    task = make_task(sessions=[session(400)])
    repos.add(USER_ID, task)
    assert post(client, task.task_id).json()["reason"] == "outlier"


def test_fr17b_t5_ai_estimate_returns_the_reason_and_keeps_memory(client, repos):
    # AC1 + AC3: not learned, but the FR-19 memory is still saved.
    task = replace(make_task(), estimate_source=EstimateSource.AI)
    repos.add(USER_ID, task)

    response = post(client, task.task_id)

    assert response.status_code == 200
    assert response.json() == {
        "learned": False,
        "reason": "ai_estimate",
        "k_old": None,
        "k_new": None,
        "sample_index": None,
        "memory": "queued",
    }
    assert repos.learning.logs == {}
    assert len(repos.embedded) == 1


def test_unknown_task_returns_404(client, repos):
    response = post(client, uuid4())
    assert response.status_code == 404
    assert response.json() == {"detail": "Task not found"}
    assert repos.embedded == []


def test_other_users_task_returns_404(client, repos):
    task = make_task()
    repos.add(uuid4(), task)  # owned by someone else
    assert post(client, task.task_id).status_code == 404
    assert repos.memory.rows == {}


# --- T20: memory queued and embedded in the background ---


def test_t20_done_task_queues_memory_and_background_job(client, repos):
    task = make_task()
    repos.add(USER_ID, task)

    response = post(client, task.task_id)

    assert response.json()["learned"] is True
    assert response.json()["memory"] == "queued"
    memory_id, row = repos.memory.row_for_task(task.task_id)
    assert row.status == "Pending"
    assert repos.embedded == [(USER_ID, memory_id)]


def test_t20_skipped_task_gets_memory_but_no_learning(client, repos):
    # The example in plan §1: FR-17 says task_not_done, FR-19 still saves a memory.
    task = make_task(is_done=False)
    repos.add(USER_ID, task, status="Skipped")

    response = post(client, task.task_id)

    assert response.json() == {
        "learned": False,
        "reason": "task_not_done",
        "k_old": None,
        "k_new": None,
        "sample_index": None,
        "memory": "queued",
    }
    assert len(repos.embedded) == 1


# --- T21: the memory part fails ---


def test_t21_memory_error_keeps_the_learning_result(client, repos):
    task = make_task()
    repos.add(USER_ID, task)
    app.dependency_overrides[get_save_task_memory] = BrokenSaveTaskMemory

    response = post(client, task.task_id)

    assert response.status_code == 200
    assert response.json()["k_new"] == 1.34
    assert response.json()["memory"] == "failed"
    assert repos.embedded == []


def test_t21_database_error_while_saving_memory(client, repos):
    task = make_task()
    repos.add(USER_ID, task)
    repos.memory.fail_on_upsert = True

    response = post(client, task.task_id)

    assert response.json()["learned"] is True
    assert response.json()["memory"] == "failed"
    assert repos.embedded == []


# --- T22: auth and body as before ---


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
