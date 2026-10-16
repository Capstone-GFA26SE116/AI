"""FR-18 endpoints with the fake repository (no database). T13."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from metanoia_ai.api.deps import (
    get_get_productive_hours,
    get_recompute_productive_hours,
    get_set_onboarding_hours,
)
from metanoia_ai.api.main import app
from metanoia_ai.application.use_cases.get_productive_hours import GetProductiveHours
from metanoia_ai.application.use_cases.recompute_productive_hours import (
    RecomputeProductiveHours,
)
from metanoia_ai.application.use_cases.set_onboarding_hours import SetOnboardingHours
from metanoia_ai.config import get_settings
from metanoia_ai.domain.models import TaskType
from metanoia_ai.domain.productive_hours import FocusSample, StopReason
from tests.unit.api.test_auth import USER_ID, make_settings, make_token
from tests.unit.application.fakes import FakeProductiveHoursRepository

ONBOARDING_URL = "/profile/productive-hours/onboarding"
GET_URL = "/profile/productive-hours"
RECOMPUTE_URL = "/learning/productive-hours/recompute"
NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
VN = ZoneInfo("Asia/Ho_Chi_Minh")


@pytest.fixture
def repo():
    repo = FakeProductiveHoursRepository()
    repo.add_user(USER_ID, plan_name="Cao cấp")
    return repo


@pytest.fixture
def client(repo):
    """App without startup (no real DB), using the fake repository."""
    recompute = RecomputeProductiveHours(
        repo,
        prior_weight=Decimal(5),
        min_session_minutes=25,
        window_days=90,
        learning_plans=["Cao cấp"],
        now=lambda: NOW,
    )
    onboarding = SetOnboardingHours(
        recompute,
        selected_score=Decimal("0.8"),
        unselected_score=Decimal("0.3"),
        top_count=3,
    )
    app.dependency_overrides[get_settings] = make_settings
    app.dependency_overrides[get_recompute_productive_hours] = lambda: recompute
    app.dependency_overrides[get_set_onboarding_hours] = lambda: onboarding
    app.dependency_overrides[get_get_productive_hours] = lambda: GetProductiveHours(
        repo, top_count=3
    )
    yield TestClient(app)
    app.dependency_overrides.clear()


def auth(token=None) -> dict[str, str]:
    return {"Authorization": f"Bearer {token or make_token()}"}


def good_session(hour: int, days_ago: int = 1) -> FocusSample:
    day = (NOW - timedelta(days=days_ago)).astimezone(VN).date()
    start = datetime(day.year, day.month, day.day, hour, tzinfo=VN)
    return FocusSample(
        start,
        start + timedelta(minutes=30),
        StopReason.COMPLETED,
        True,
        TaskType.FLEXIBLE,
    )


# --- PUT onboarding ---


def test_onboarding_returns_the_24_hours(client, repo):
    response = client.put(
        ONBOARDING_URL, json={"blocks": ["morning", "evening"]}, headers=auth()
    )

    assert response.status_code == 200
    body = response.json()
    assert body["onboarded"] is True
    assert body["blocks"] == ["morning", "evening"]
    assert len(body["hours"]) == 24
    assert body["hours"][0] == {"hour": 0, "score": 0.3, "source": "Onboarding"}
    assert body["hours"][8] == {"hour": 8, "score": 0.8, "source": "Onboarding"}
    assert body["top_hours"] == [6, 7, 8]
    assert len(repo.hours[USER_ID]) == 24  # saved for the user in the token (AC1)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"blocks": []}, id="empty"),
        pytest.param({"blocks": ["morning"] * 2}, id="repeated"),
        pytest.param({"blocks": ["night"]}, id="unknown"),
        pytest.param(
            {
                "blocks": [
                    "morning",
                    "midday",
                    "afternoon",
                    "evening",
                    "late_night",
                    "extra",
                ]
            },
            id="six",
        ),
        pytest.param({}, id="missing"),
        pytest.param({"blocks": "morning"}, id="not-a-list"),
    ],
)
def test_bad_onboarding_returns_422(client, repo, body):
    response = client.put(ONBOARDING_URL, json=body, headers=auth())
    assert response.status_code == 422
    assert repo.saves == 0


def test_onboarding_without_token_returns_401(client, repo):
    response = client.put(ONBOARDING_URL, json={"blocks": ["morning"]})
    assert response.status_code == 401
    assert repo.saves == 0


def test_onboarding_for_unknown_user_returns_404(client):
    token = make_token(sub=str(uuid4()))
    response = client.put(
        ONBOARDING_URL, json={"blocks": ["morning"]}, headers=auth(token)
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "User not found"}


# --- GET ---


def test_get_before_anything(client):
    response = client.get(GET_URL, headers=auth())
    assert response.status_code == 200
    assert response.json() == {
        "onboarded": False,
        "blocks": [],
        "hours": [],
        "top_hours": [],
    }


def test_get_after_onboarding_and_learning(client, repo):
    for d in range(1, 7):
        repo.add_session(USER_ID, good_session(21, d))
    client.put(ONBOARDING_URL, json={"blocks": ["morning"]}, headers=auth())

    body = client.get(GET_URL, headers=auth()).json()

    assert body["onboarded"] is True
    assert body["hours"][21] == {"hour": 21, "score": 0.682, "source": "Learned"}


def test_get_without_token_returns_401(client):
    assert client.get(GET_URL).status_code == 401


def test_get_for_unknown_user_returns_404(client):
    response = client.get(GET_URL, headers=auth(make_token(sub=str(uuid4()))))
    assert response.status_code == 404


def test_get_has_no_user_id_parameter(client, repo):
    # AC1: the user comes from the token only; a query parameter is ignored.
    other = uuid4()
    repo.add_user(other, blocks=None)
    body = client.get(f"{GET_URL}?user_id={other}", headers=auth()).json()
    assert body["onboarded"] is False
    assert client.get(GET_URL, headers=auth()).status_code == 200


# --- POST recompute ---


def test_recompute(client, repo):
    client.put(ONBOARDING_URL, json={"blocks": ["morning"]}, headers=auth())
    repo.add_session(USER_ID, good_session(21))

    response = client.post(RECOMPUTE_URL, headers=auth())

    assert response.status_code == 200
    assert response.json() == {"hours_updated": 24, "sessions_used": 1}


def test_recompute_with_nothing_to_write(client):
    response = client.post(RECOMPUTE_URL, headers=auth())
    assert response.json() == {"hours_updated": 0, "sessions_used": 0}


def test_recompute_without_token_returns_401(client):
    assert client.post(RECOMPUTE_URL).status_code == 401


def test_recompute_for_unknown_user_returns_404(client):
    response = client.post(RECOMPUTE_URL, headers=auth(make_token(sub=str(uuid4()))))
    assert response.status_code == 404


# --- wiring ---


def test_real_wiring_builds_the_use_cases():
    settings = make_settings()
    recompute = get_recompute_productive_hours(session=None, settings=settings)
    assert recompute.learning_plans == frozenset({"Cao cấp"})
    assert recompute.prior_weight == Decimal(5)
    onboarding = get_set_onboarding_hours(recompute, settings)
    assert onboarding.selected_score == Decimal("0.8")
    assert onboarding.top_count == settings.schedule_top_productive_hours
    reader = get_get_productive_hours(session=None, settings=settings)
    assert reader.top_count == settings.schedule_top_productive_hours
