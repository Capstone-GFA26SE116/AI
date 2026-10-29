"""FR-20 endpoints with the fake repository (no database). T13, T14."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from metanoia_ai.api.deps import get_get_analytics_breakdown, get_get_analytics_overview
from metanoia_ai.api.main import app
from metanoia_ai.application.ports.analytics_repository import AnalyticsDomainProfile
from metanoia_ai.application.use_cases.get_analytics_breakdown import (
    GetAnalyticsBreakdown,
)
from metanoia_ai.application.use_cases.get_analytics_overview import (
    AnalyticsSettings,
    GetAnalyticsOverview,
)
from metanoia_ai.config import get_settings
from metanoia_ai.domain.analytics import DeadlineTask, DoneTask, KUpdate, TaskStatus
from metanoia_ai.domain.models import ActualTimeSource, FocusSessionRecord, TaskType
from tests.unit.api.test_auth import USER_ID, make_settings, make_token
from tests.unit.application.fakes import FakeAnalyticsRepository, FakeAnalyticsUser

SAIGON = ZoneInfo("Asia/Ho_Chi_Minh")
NOW = datetime(2026, 10, 29, 17, 20, tzinfo=SAIGON)
OTHER_USER = uuid4()
MY_PROJECT = uuid4()
OTHER_PROJECT = uuid4()
SETTINGS = AnalyticsSettings(5, 10, Decimal("0.10"), 20, 3, 3)


def done(project, title: str, estimated: int, actual: int, days_ago: int) -> DoneTask:
    start = datetime(2026, 10, 29 - days_ago, 2, 0, tzinfo=UTC)
    return DoneTask(
        task_id=uuid4(),
        title=title,
        project_id=project,
        project_name="Landing page ABC" if project == MY_PROJECT else "B project",
        client_id=None,
        client_name=None,
        domain_name="Writing",
        estimated_minutes=estimated,
        ai_estimated_minutes=None,
        completed_at=start + timedelta(hours=3),  # stored in UTC
        sessions=(
            FocusSessionRecord(
                start, start + timedelta(minutes=actual), ActualTimeSource.TIMER
            ),
        ),
    )


@pytest.fixture
def repo():
    repo = FakeAnalyticsRepository()
    writing = AnalyticsDomainProfile(
        uuid4(),
        "Writing",
        Decimal("1.310"),
        12,
        (KUpdate(datetime(2026, 10, 6, 9, tzinfo=SAIGON), Decimal("1.360")),),
    )
    repo.add_user(
        USER_ID,
        FakeAnalyticsUser(
            time_zone=SAIGON,
            project_ids={MY_PROJECT},
            deadline_tasks=[
                DeadlineTask(
                    TaskType.FLEXIBLE,
                    TaskStatus.DONE,
                    NOW - timedelta(days=1),
                    NOW - timedelta(days=2),
                ),
                DeadlineTask(
                    TaskType.FLEXIBLE, TaskStatus.SKIPPED, NOW - timedelta(days=1), None
                ),
            ],
            profiles=[writing],
            done_tasks=[done(MY_PROJECT, f"Task {i}", 120, 150, i) for i in (1, 2, 3)],
        ),
    )
    repo.add_user(
        OTHER_USER,
        FakeAnalyticsUser(
            time_zone=SAIGON,
            project_ids={OTHER_PROJECT},
            done_tasks=[done(OTHER_PROJECT, "B secret", 60, 30, 1)],
        ),
    )
    return repo


@pytest.fixture
def client(repo):
    """App without startup (no real DB), using the fake repository."""
    app.dependency_overrides[get_settings] = make_settings
    app.dependency_overrides[get_get_analytics_overview] = lambda: GetAnalyticsOverview(
        repo, SETTINGS, now=lambda: NOW
    )
    app.dependency_overrides[get_get_analytics_breakdown] = lambda: (
        GetAnalyticsBreakdown(repo, now=lambda: NOW)
    )
    yield TestClient(app)
    app.dependency_overrides.clear()


def auth(**claims) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(**claims)}"}


# --- T14: the answer matches the plan's example shape ---


def test_overview_shape(client):
    response = client.get("/analytics/overview", headers=auth())

    assert response.status_code == 200
    body = response.json()
    assert body["range"] == "30d"
    assert body["from"] == "2026-09-29T00:00:00+07:00"
    assert body["to"] == "2026-10-29T17:20:00+07:00"
    assert body["time_zone"] == "Asia/Ho_Chi_Minh"
    assert body["completion"] == {
        "total": 2,
        "done": 1,
        "skipped": 1,
        "overdue_open": 0,
        "completion_rate": 0.5,
        "on_time_done": 1,
        "on_time_rate": 1.0,
        "text": "Bạn hoàn thành 1/2 việc (50%), trong đó 1 việc xong đúng hạn.",
    }
    [domain] = body["domains"]
    assert domain["domain_name"] == "Writing"
    assert (domain["samples"], domain["confidence"], domain["factor"]) == (
        12,
        "confident",
        1.31,
    )
    assert (domain["direction"], domain["percent"]) == ("slower", 31)
    assert domain["text"] == "Bạn thường cần thêm khoảng 30% thời gian cho Writing."
    assert domain["trend"] == [
        {"week_start": "2026-10-05", "factor": 1.36, "samples": 1}
    ]
    assert body["insights"] == [
        {"code": "domain_slower", "text": domain["text"]},
        {
            "code": "project_over",
            "text": "Dự án Landing page ABC tốn nhiều hơn dự kiến khoảng 25%.",
        },
    ]


def test_overview_all_range_and_english(client):
    body = client.get(
        "/analytics/overview", params={"range": "all", "language": "en"}, headers=auth()
    ).json()

    assert body["from"] is None
    assert (
        body["domains"][0]["text"]
        == "You usually need about 30% more time for Writing."
    )


def test_breakdown_by_project_shape(client):
    response = client.get(
        "/analytics/breakdown", params={"by": "project"}, headers=auth()
    )

    assert response.status_code == 200
    body = response.json()
    assert (body["by"], body["range"]) == ("project", "30d")
    assert body["items"] == [
        {
            "id": str(MY_PROJECT),
            "name": "Landing page ABC",
            "project_name": None,
            "domain_name": None,
            "task_count": 3,
            "missing_actual_count": 0,
            "estimated_minutes": 360,
            "actual_minutes": 450,
            "difference_percent": 25,
            "estimate_source": None,
            "completed_at": None,
            "text": "Tốn nhiều hơn dự kiến khoảng 25%.",
        }
    ]


def test_breakdown_by_task_times_in_user_zone(client):
    body = client.get(
        "/analytics/breakdown",
        params={"by": "task", "project_id": str(MY_PROJECT), "limit": 2},
        headers=auth(),
    ).json()

    assert [i["name"] for i in body["items"]] == ["Task 1", "Task 2"]
    first = body["items"][0]
    assert first["completed_at"] == "2026-10-28T12:00:00+07:00"
    assert (first["estimate_source"], first["project_name"]) == (
        "user",
        "Landing page ABC",
    )


def test_breakdown_by_client_names_no_client_group(client):
    body = client.get(
        "/analytics/breakdown",
        params={"by": "client", "language": "en"},
        headers=auth(),
    ).json()

    assert [(i["id"], i["name"]) for i in body["items"]] == [(None, "No client")]


# --- T13: auth and validation ---


@pytest.mark.parametrize(
    "url", ["/analytics/overview", "/analytics/breakdown?by=project"]
)
def test_no_or_bad_token_is_401(client, url):
    assert client.get(url).status_code == 401
    assert client.get(url, headers={"Authorization": "Bearer nope"}).status_code == 401


@pytest.mark.parametrize(
    ("url", "params"),
    [
        ("/analytics/overview", {"range": "7d"}),
        ("/analytics/overview", {"language": "fr"}),
        ("/analytics/breakdown", {}),  # by is required
        ("/analytics/breakdown", {"by": "domain"}),
        ("/analytics/breakdown", {"by": "task", "limit": 0}),
        ("/analytics/breakdown", {"by": "task", "limit": 201}),
        ("/analytics/breakdown", {"by": "task", "project_id": "not-a-uuid"}),
        ("/analytics/breakdown", {"by": "project", "limit": 5}),
        ("/analytics/breakdown", {"by": "client", "project_id": str(MY_PROJECT)}),
    ],
)
def test_bad_query_is_422(client, url, params):
    assert client.get(url, params=params, headers=auth()).status_code == 422


# --- AC6: each user sees only their own data ---


def test_project_of_another_user_is_404(client):
    response = client.get(
        "/analytics/breakdown",
        params={"by": "task", "project_id": str(OTHER_PROJECT)},
        headers=auth(),
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Project not found"}


@pytest.mark.parametrize("url", ["/analytics/overview", "/analytics/breakdown?by=task"])
def test_unknown_user_is_404(client, url):
    response = client.get(url, headers=auth(sub=str(uuid4())))

    assert response.status_code == 404
    assert response.json() == {"detail": "User not found"}


def test_each_user_sees_only_own_tasks(client):
    mine = client.get(
        "/analytics/breakdown", params={"by": "task"}, headers=auth()
    ).json()
    theirs = client.get(
        "/analytics/breakdown", params={"by": "task"}, headers=auth(sub=str(OTHER_USER))
    ).json()

    assert "B secret" not in [i["name"] for i in mine["items"]]
    assert [i["name"] for i in theirs["items"]] == ["B secret"]
