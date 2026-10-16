"""The similar tasks endpoint, with fake repositories (no database, no Gemini)."""

from datetime import UTC, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from metanoia_ai.api.deps import get_find_similar_tasks
from metanoia_ai.api.main import app
from metanoia_ai.application.ports.quota_repository import Subscription
from metanoia_ai.application.ports.similar_task_repository import QueryTask
from metanoia_ai.application.use_cases.find_similar_tasks import FindSimilarTasks
from metanoia_ai.config import get_settings
from metanoia_ai.domain.models import EstimateSource, TaskType
from metanoia_ai.domain.similar_tasks import PastTask, SimilarOutcome
from tests.unit.api.test_auth import USER_ID, make_settings, make_token
from tests.unit.application.fakes import (
    FakeEmbeddingClient,
    FakeMemory,
    FakeQuotaRepository,
    FakeSimilarTaskRepository,
)

URL = "/memory/similar"
NOW = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)
SAIGON = ZoneInfo("Asia/Ho_Chi_Minh")
DIMS = 4


class Fakes:
    def __init__(self) -> None:
        self.repo = FakeSimilarTaskRepository()
        self.quota = FakeQuotaRepository(now=NOW)
        self.quota.add_subscription(
            USER_ID, Subscription(uuid4(), 0, 50, 0), start_at=NOW
        )
        self.client = FakeEmbeddingClient(*([[0.1, 0.2, 0.3, 0.4]] * 5))
        self.task = QueryTask(
            uuid4(),
            TaskType.FLEXIBLE,
            "Landing page cho Acme",
            "Coding",
            "Acme",
            SAIGON,
        )
        self.repo.add_task(USER_ID, self.task)

    def use_case(self) -> FindSimilarTasks:
        return FindSimilarTasks(
            self.repo,
            self.quota,
            self.client,
            dimensions=DIMS,
            price_per_1m=0.15,
            min_similarity=0.5,
            rrf_k=60,
            branch_limit=20,
            embed_timeout_seconds=1.5,
            now=lambda: NOW,
        )

    def add(self, title: str, **changes) -> PastTask:
        values = {
            "task_id": uuid4(),
            "title": title,
            "domain_name": "Coding",
            "outcome": SimilarOutcome.DONE,
            "estimated_minutes": 120,
            "actual_minutes": 180,
            "finished_at": datetime(2026, 10, 10, 10, 0, tzinfo=UTC),
        } | changes
        task = PastTask(**values)
        content = f"title: {title} | text: Coding. Done."
        self.repo.add_memory(FakeMemory(USER_ID, task, content))
        return task


@pytest.fixture
def fakes():
    return Fakes()


@pytest.fixture
def client(fakes):
    """App without startup (no real DB), using the fakes."""
    app.dependency_overrides[get_settings] = make_settings
    app.dependency_overrides[get_find_similar_tasks] = fakes.use_case
    yield TestClient(app)
    app.dependency_overrides.clear()


def get(client, task_id, token=None, **params):
    headers = {"Authorization": f"Bearer {token or make_token()}"}
    return client.get(URL, params={"task_id": str(task_id), **params}, headers=headers)


# --- T19: normal answer ---


def test_t19_answer_matches_the_plan(client, fakes):
    found = fakes.add("Landing page khách B")

    response = get(client, fakes.task.task_id)

    assert response.status_code == 200
    assert response.json() == {
        "task_id": str(fakes.task.task_id),
        "search_mode": "hybrid",
        "items": [
            {
                "task_id": str(found.task_id),
                "title": "Landing page khách B",
                "domain": "Coding",
                "outcome": "done",
                "estimated_minutes": 120,
                "actual_minutes": 180,
                "estimate_source": "user",
                "ratio": 1.5,
                "finished_at": "2026-10-10T17:00:00+07:00",  # user's time zone
                "score": 0.0328,
                "matched_by": ["vector", "text"],
            }
        ],
        "summary": {"count": 1, "done_count": 1, "average_ratio": 1.5},
    }


def test_skipped_task_without_finish_time(client, fakes):
    fakes.add(
        "Landing page bỏ dở",
        outcome=SimilarOutcome.SKIPPED,
        actual_minutes=0,
        finished_at=None,
    )
    item = get(client, fakes.task.task_id).json()["items"][0]
    assert (item["outcome"], item["ratio"], item["finished_at"]) == (
        "skipped",
        None,
        None,
    )


def test_fr17b_t5_ai_estimate_has_no_ratio(client, fakes):
    fakes.add("Landing page AI đoán", estimate_source=EstimateSource.AI)

    body = get(client, fakes.task.task_id).json()

    item = body["items"][0]
    assert (item["estimate_source"], item["ratio"]) == ("ai", None)
    assert body["summary"] == {"count": 1, "done_count": 1, "average_ratio": None}


def test_limit_is_used(client, fakes):
    for i in range(4):
        fakes.add(f"Landing page {i}")
    assert len(get(client, fakes.task.task_id, limit=2).json()["items"]) == 2


def test_default_limit_comes_from_config(client, fakes):
    for i in range(7):
        fakes.add(f"Landing page {i}")
    assert len(get(client, fakes.task.task_id).json()["items"]) == 5


def test_fixed_task_returns_none_mode(client, fakes):
    fixed = QueryTask(uuid4(), TaskType.FIXED, "Họp", "Meeting", None, SAIGON)
    fakes.repo.add_task(USER_ID, fixed)
    body = get(client, fixed.task_id).json()
    assert (body["search_mode"], body["items"]) == ("none", [])


# --- T20: errors ---


def test_t20_unknown_task_returns_404(client):
    response = get(client, uuid4())
    assert response.status_code == 404
    assert response.json() == {"detail": "Task not found"}


def test_t20_other_users_task_returns_404(client, fakes):
    other = QueryTask(uuid4(), TaskType.FLEXIBLE, "X", "Coding", None, SAIGON)
    fakes.repo.add_task(uuid4(), other)
    assert get(client, other.task_id).status_code == 404


def test_t20_missing_token_returns_401(client, fakes):
    response = client.get(URL, params={"task_id": str(fakes.task.task_id)})
    assert response.status_code == 401


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"limit": 0}, id="limit-0"),
        pytest.param({"limit": 11}, id="limit-11"),
        pytest.param({"task_id": "not-a-uuid"}, id="bad-uuid"),
    ],
)
def test_t20_bad_parameters_return_422(client, fakes, params):
    query = {"task_id": str(fakes.task.task_id)} | params
    headers = {"Authorization": f"Bearer {make_token()}"}
    assert client.get(URL, params=query, headers=headers).status_code == 422


def test_t20_missing_task_id_returns_422(client):
    headers = {"Authorization": f"Bearer {make_token()}"}
    assert client.get(URL, headers=headers).status_code == 422
