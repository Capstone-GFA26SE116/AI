"""FR-22 endpoint with fakes (no DB, no Gemini, no Backend). T10."""

from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from metanoia_ai.api.deps import get_assistant_turn
from metanoia_ai.api.main import app
from metanoia_ai.application.ports.backend_reader import RunningFocus
from metanoia_ai.application.ports.quota_repository import Subscription
from metanoia_ai.config import get_settings
from tests.unit.api.test_auth import USER_ID, make_settings, make_token
from tests.unit.application.fakes import FakeBackendReader
from tests.unit.application.test_assistant_turn import NOW, pick
from tests.unit.application.test_assistant_turn import setup as build_use_case

URL = "/assistant/turn"


def make_client(*answers, **options):
    """App without startup (no real DB): the use case runs on fakes for USER_ID."""
    use_case, _, quota, llm, backend, _ = build_use_case(
        *answers, user=USER_ID, **options
    )
    app.dependency_overrides[get_settings] = make_settings
    app.dependency_overrides[get_assistant_turn] = lambda: use_case
    return TestClient(app), quota, llm, backend


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()


def auth(**claims) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(**claims)}"}


def test_answer_shape_and_token_passed_on() -> None:
    focus = RunningFocus("Viết blog", NOW, 600, False)
    client, _, _, backend = make_client(
        pick("running_timer"), backend=FakeBackendReader(focus=focus)
    )
    headers = auth()

    response = client.post(URL, json={"text": "Tôi đang làm gì?"}, headers=headers)

    assert response.status_code == 200
    assert response.json() == {
        "kind": "answer",
        "tool": "running_timer",
        "reply": "Bạn đang bấm giờ cho Viết blog, được khoảng 10 phút rồi.",
        "emotion": "happy",
        "options": [],
        "suggestions": [],
        "action": None,
    }
    assert backend.calls == [("current_focus", headers["Authorization"])]


def test_clarify_options() -> None:
    client, *_ = make_client(pick("project_progress", name="landing page"))

    body = client.post(
        URL, json={"text": "Dự án landing page thế nào?"}, headers=auth()
    ).json()

    assert (body["kind"], body["tool"], body["emotion"]) == (
        "clarify",
        "project_progress",
        "thinking",
    )
    assert [o["title"] for o in body["options"]] == [
        "Landing page Client A",
        "Landing page Client B",
    ]


def test_selected_id_and_history_are_accepted() -> None:
    client, _, llm, _ = make_client(pick("smalltalk", smalltalk="greet"))

    response = client.post(
        URL,
        json={
            "text": "chào",
            "language": "en",
            "history": [
                {"role": "user", "text": "hi"},
                {"role": "assistant", "text": "hello"},
            ],
            "selected_id": str(uuid4()),
        },
        headers=auth(),
    )

    assert response.status_code == 200
    assert response.json()["kind"] == "smalltalk"
    assert "user: hi" in llm.prompts[0]


# --- T10: errors ---


def test_no_or_bad_token_is_401() -> None:
    client, *_ = make_client(pick("ai_quota"))

    assert client.post(URL, json={"text": "hi"}).status_code == 401
    bad = client.post(
        URL, json={"text": "hi"}, headers={"Authorization": "Bearer nope"}
    )
    assert bad.status_code == 401


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"text": ""},
        {"text": "   "},
        {"text": "a" * 501},
        {"text": "hi", "language": "fr"},
        {"text": "hi", "history": [{"role": "user", "text": "x"}] * 7},
        {"text": "hi", "history": [{"role": "user", "text": "x" * 2001}]},
        {"text": "hi", "history": [{"role": "system", "text": "x"}]},
        {"text": "hi", "selected_id": "not-a-uuid"},
    ],
)
def test_bad_body_is_422(body) -> None:
    client, _, llm, _ = make_client(pick("ai_quota"))

    assert client.post(URL, json=body, headers=auth()).status_code == 422
    assert llm.prompts == []


def test_no_calls_left_is_429() -> None:
    client, *_ = make_client(pick("ai_quota"), calls_used=30, limit=30)

    response = client.post(URL, json={"text": "hi"}, headers=auth())

    assert response.status_code == 429


def test_no_active_plan_is_409() -> None:
    client, quota, *_ = make_client(pick("ai_quota"))
    quota.subscriptions.clear()  # the user exists but has no plan

    response = client.post(URL, json={"text": "hi"}, headers=auth())

    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "no_active_plan"}}


def test_llm_failure_is_502_and_not_charged() -> None:
    client, quota, *_ = make_client("not json")

    response = client.post(URL, json={"text": "hi"}, headers=auth())

    assert response.status_code == 502
    assert [log.quota_charged for _, _, log in quota.logs] == [False]


def test_unknown_user_is_404() -> None:
    client, quota, *_ = make_client(pick("ai_quota"))
    other = uuid4()  # has a plan, but no user row
    quota.add_subscription(
        other, Subscription(uuid4(), 149000, 30, 0, "Premium"), NOW - timedelta(days=1)
    )

    response = client.post(URL, json={"text": "hi"}, headers=auth(sub=str(other)))

    assert response.status_code == 404


def test_confirm_turn_sends_the_action() -> None:
    from tests.unit.application.test_assistant_turn import SESSION, running

    client, *_ = make_client(
        pick("finish_timer"), backend=FakeBackendReader(focus=running())
    )

    response = client.post(URL, json={"text": "Xong rồi"}, headers=auth())

    body = response.json()
    assert body["kind"] == "confirm"
    assert body["action"] == {
        "type": "stop_timer",
        "task_id": None,
        "task_title": "Viết blog",
        "slot_id": None,
        "session_id": str(SESSION),
        "stop_reason": "Completed",
        "minutes": None,
        "proposal_id": None,
    }
