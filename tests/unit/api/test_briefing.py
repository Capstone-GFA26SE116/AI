"""FR-12 endpoint with the fake repository (no database). T20, T21."""

import asyncio
from dataclasses import replace
from datetime import datetime
from datetime import time as clock
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from metanoia_ai.api import deps
from metanoia_ai.api.deps import get_get_daily_briefing, get_similar_finder
from metanoia_ai.api.main import app
from metanoia_ai.application.ports.briefing_repository import BriefingData, BriefingTask
from metanoia_ai.application.ports.schedule_repository import TaskStatus
from metanoia_ai.application.use_cases.find_similar_tasks import (
    SearchMode,
    SimilarTasksResult,
)
from metanoia_ai.application.use_cases.get_daily_briefing import (
    BriefingSettings,
    GetDailyBriefing,
)
from metanoia_ai.config import get_settings
from metanoia_ai.domain.briefing import BiasRule, GoogleEntry, SlotEntry, SlotStatus
from metanoia_ai.domain.models import WorkingHours
from metanoia_ai.domain.similar_tasks import SimilarSummary
from tests.unit.api.test_auth import USER_ID, make_settings, make_token
from tests.unit.application.fakes import FakeBriefingRepository

URL = "/briefing/today"
SAIGON = ZoneInfo("Asia/Ho_Chi_Minh")
NOW = datetime(2026, 10, 17, 8, 0, tzinfo=SAIGON)
OTHER_USER = uuid4()
SETTINGS = BriefingSettings(
    initial_k=Decimal("1.3"),
    top_count=3,
    risk_window_days=14,
    at_risk_limit=5,
    bias_rule=BiasRule(Decimal("1.1"), 3),
    similar_min_count=2,
    similar_min_ratio=1.3,
    similar_timeout_seconds=1.0,
    productive_top_hours=3,
    round_minutes=5,
)


def at(day: int, hour: int) -> datetime:
    return datetime(2026, 10, day, hour, tzinfo=SAIGON)


def task(title: str) -> BriefingTask:
    return BriefingTask(
        task_id=uuid4(),
        title=title,
        project_name="Website",
        domain_id=uuid4(),
        domain_name="Coding",
        status=TaskStatus.TODO,
        deadline=at(18, 17),
        estimated_minutes=60,
        has_subtasks=False,
        open_subtasks=(),
        sessions=(),
    )


def user_data(title: str, **changes) -> BriefingData:
    base = BriefingData(
        hours=WorkingHours(SAIGON, clock(8), clock(18), True, 10),
        is_free_plan=True,
        tasks=(task(title),),
        k_by_domain={},
        productive_scores={},
        has_productive_answer=False,
        slots=(),
        fixed_tasks=(),
        events=(),
        calendar_connected=True,
    )
    return replace(base, **changes)


async def no_similar(user_id, task_id):
    return SimilarTasksResult(
        task_id, SearchMode.HYBRID, SAIGON, [], SimilarSummary(2, 2, 1.5)
    )


@pytest.fixture
def repo():
    repo = FakeBriefingRepository()
    mine = task("Việc của A")
    slot = SlotEntry(
        mine.task_id, mine.title, at(17, 9), at(17, 10), SlotStatus.APPROVED
    )
    event = GoogleEntry("Sinh nhật", at(17, 12), at(17, 13), False, False, False)
    holiday = GoogleEntry("Nghỉ lễ", at(17, 0), at(18, 0), True, False, False)
    repo.add_user(
        USER_ID,
        user_data("Việc của A", tasks=(mine,), slots=(slot,), events=(event, holiday)),
    )
    repo.add_user(OTHER_USER, user_data("Việc của B"))
    return repo


@pytest.fixture
def client(repo):
    """App without startup (no real DB), using the fake repository."""
    use_case = GetDailyBriefing(repo, no_similar, SETTINGS, now=lambda: NOW)
    app.dependency_overrides[get_settings] = make_settings
    app.dependency_overrides[get_get_daily_briefing] = lambda: use_case
    yield TestClient(app)
    app.dependency_overrides.clear()


def auth(**claims) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(**claims)}"}


# --- the answer ---


def test_briefing_shape(client):
    response = client.get(URL, headers=auth())
    assert response.status_code == 200
    body = response.json()
    assert body["date"] == "2026-10-17"
    assert body["time_zone"] == "Asia/Ho_Chi_Minh"
    assert body["calendar_connected"] is True
    assert body["has_pending_proposal"] is False
    assert [(i["kind"], i["status"]) for i in body["schedule"]] == [
        ("slot", "Approved"),
        ("google", None),
    ]
    assert body["schedule"][0]["start"] == "2026-10-17T09:00:00+07:00"
    assert body["schedule"][1]["task_id"] is None
    assert body["all_day"] == [{"title": "Nghỉ lễ"}]
    [top] = body["top_tasks"]
    assert top["title"] == "Việc của A"
    assert top["deadline"] == "2026-10-18T17:00:00+07:00"
    assert top["remaining_minutes"] == 60  # Free plan: no × K
    assert top["reasons"] == [{"code": "deadline_soon", "text": "Hạn ngày mai."}]
    assert top["similar_warning"] == {
        "text": "Các việc tương tự trước đây thường mất gấp 1,5 lần dự kiến."
    }
    assert body["at_risk"] == []
    assert body["productive_hint"] is None


def test_english(client):
    body = client.get(URL, params={"language": "en"}, headers=auth()).json()
    assert body["top_tasks"][0]["reasons"][0]["text"] == "Due tomorrow."


def test_productive_hint_ranges_are_clock_strings(client, repo):
    scores = {h: Decimal("0.9") if h in (14, 15) else Decimal("0.3") for h in range(24)}
    repo.add_user(
        USER_ID,
        user_data("A", productive_scores=scores, has_productive_answer=True),
    )
    hint = client.get(URL, headers=auth()).json()["productive_hint"]
    assert hint["ranges"] == [{"start": "14:00", "end": "16:00"}]
    assert hint["text"].startswith("Khung giờ bạn làm tốt còn lại hôm nay")


def test_unknown_user_is_404(client):
    response = client.get(URL, headers=auth(sub=str(uuid4())))
    assert response.status_code == 404


# --- T20: token and language ---


def test_t20_no_token_is_401(client):
    assert client.get(URL).status_code == 401


def test_t20_bad_token_is_401(client):
    response = client.get(URL, headers={"Authorization": "Bearer not-a-token"})
    assert response.status_code == 401


def test_t20_unknown_language_is_422(client):
    response = client.get(URL, params={"language": "fr"}, headers=auth())
    assert response.status_code == 422


# --- T21: only the caller's data ---


def test_t21_each_user_sees_only_own_data(client, repo):
    mine = client.get(URL, headers=auth()).json()
    theirs = client.get(URL, headers=auth(sub=str(OTHER_USER))).json()
    assert [t["title"] for t in mine["top_tasks"]] == ["Việc của A"]
    assert [t["title"] for t in theirs["top_tasks"]] == ["Việc của B"]
    assert [user for user, *_ in repo.reads] == [USER_ID, OTHER_USER]


def test_t21_no_user_id_parameter_is_used(client, repo):
    client.get(URL, params={"user_id": str(OTHER_USER)}, headers=auth())
    assert [user for user, *_ in repo.reads] == [USER_ID]


# --- #12: each similar search gets its own DB session ---


class CountingSessionFactory:
    def __init__(self):
        self.opened = []
        self.closed = 0

    def __call__(self):
        factory = self

        class Session:
            async def __aenter__(self):
                factory.opened.append(self)
                return self

            async def __aexit__(self, *exc):
                factory.closed += 1

        return Session()


def test_similar_finder_opens_one_session_per_search(monkeypatch):
    factory = CountingSessionFactory()
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(session_factory=factory))
    )
    used_sessions = []

    class StubFind:
        def __init__(self, session):
            self.session = session

        async def execute(self, user_id, task_id, limit):
            used_sessions.append(self.session)
            await asyncio.sleep(0.05)  # stay open while the others start
            return SimilarTasksResult(task_id, SearchMode.TEXT_ONLY, SAIGON)

    monkeypatch.setattr(
        deps,
        "build_find_similar_tasks",
        lambda session, settings, client: StubFind(session),
    )
    monkeypatch.setattr(deps, "optional_embedding_client", lambda state, settings: None)
    find = get_similar_finder(request, make_settings())

    async def three_at_once():
        return await asyncio.gather(*(find(USER_ID, uuid4()) for _ in range(3)))

    results = asyncio.run(three_at_once())
    assert len(results) == 3
    assert len(factory.opened) == 3
    assert len({id(s) for s in used_sessions}) == 3  # never shared
    assert factory.closed == 3
