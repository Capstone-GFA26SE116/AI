"""FR-08b-3: "mode" in the project chat endpoints (plans/FR-08b-3.md T7)."""

import pytest
from fastapi.testclient import TestClient

from metanoia_ai.api.deps import get_get_project_chat, get_send_project_chat_message
from metanoia_ai.api.main import app
from metanoia_ai.application.use_cases.get_project_chat import GetProjectChat
from metanoia_ai.config import get_settings
from tests.unit.api.test_auth import make_settings
from tests.unit.api.test_project_chat import HEADERS, ApiWorld, send
from tests.unit.application.test_project_chat import QUESTION, draft_answer
from tests.unit.application.test_project_discuss import DISCUSS


@pytest.fixture
def world():
    return ApiWorld()


@pytest.fixture
def client(world):
    """App without startup (no real DB): send + get with the fakes."""
    app.dependency_overrides[get_settings] = make_settings
    app.dependency_overrides[get_send_project_chat_message] = lambda: world.use_case(
        world.llm
    )
    app.dependency_overrides[get_get_project_chat] = lambda: GetProjectChat(world.repo)
    yield TestClient(app)
    app.dependency_overrides.clear()


def post(client, world, body):
    return client.post(
        f"/chat/conversations/{world.chat_id}/messages", json=body, headers=HEADERS
    )


def test_discuss_turn_has_kind_discuss_and_notes(client, world):
    world.llm.answers.append(DISCUSS)

    response = post(client, world, {"message": "Brief", "mode": "discuss"})

    assert response.status_code == 200
    ai = response.json()["ai_message"]
    assert ai["kind"] == "discuss"
    assert ai["draft"] is None
    assert ai["notes"] == {
        "clear": ["Landing page 3 phần", "Bàn giao 30/10"],
        "missing": ["Có cần bản tiếng Anh không"],
        "questions": ["Mấy vòng góp ý?"],
        "risks": ["Chưa có nội dung bảng giá"],
        "ready_to_plan": False,
    }


def test_plan_is_the_default_mode(client, world):
    world.llm.answers.append(QUESTION)

    ai = send(client, world).json()["ai_message"]

    assert ai["kind"] == "question"
    assert ai["notes"] is None


def test_plan_mode_can_be_sent_explicitly(client, world):
    world.llm.answers.append(draft_answer())

    ai = post(client, world, {"message": "Lên kế hoạch", "mode": "plan"}).json()

    assert ai["ai_message"]["kind"] == "draft"


def test_unknown_mode_is_422(client, world):
    assert post(client, world, {"message": "Brief", "mode": "chat"}).status_code == 422


def test_get_chat_shows_the_saved_checklist(client, world):
    world.llm.answers.append(DISCUSS)
    post(client, world, {"message": "Brief", "mode": "discuss"})

    chat = client.get(f"/chat/conversations/{world.chat_id}", headers=HEADERS).json()

    assert chat["messages"][1]["kind"] == "discuss"
    assert chat["messages"][1]["notes"]["risks"] == ["Chưa có nội dung bảng giá"]
    assert chat["latest_draft"] is None


def test_files_endpoint_takes_the_mode_form_field(client, world):
    world.llm.answers.append(DISCUSS)

    response = client.post(
        f"/chat/conversations/{world.chat_id}/messages/files",
        data={"message": "Đọc brief giúp mình", "mode": "discuss"},
        files=[("files", ("brief.txt", "Landing page 3 phần".encode(), "text/plain"))],
        headers=HEADERS,
    )

    assert response.status_code == 200
    assert response.json()["ai_message"]["kind"] == "discuss"
