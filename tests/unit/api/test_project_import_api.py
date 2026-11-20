"""FR-08c endpoints (plans/FR-08c.md T5), with fakes."""

import pytest
from fastapi.testclient import TestClient

from metanoia_ai.api.deps import get_import_projects, get_preview_project_import
from metanoia_ai.api.main import app
from metanoia_ai.config import get_settings
from tests.unit.api.test_auth import USER_ID, make_settings, make_token
from tests.unit.application.fakes import FakeLLMClient
from tests.unit.application.test_project_import import NOW, World, answer

HEADERS = {"Authorization": f"Bearer {make_token()}"}


@pytest.fixture
def world(monkeypatch):
    import tests.unit.application.test_project_import as module

    monkeypatch.setattr(module, "USER", USER_ID)
    w = World(limit=3, used=1)
    return w


@pytest.fixture
def client(world):
    from metanoia_ai.application.import_writer import ImportWriter
    from metanoia_ai.application.use_cases.import_projects import ImportProjects
    from metanoia_ai.application.use_cases.preview_project_import import (
        PreviewProjectImport,
    )
    from metanoia_ai.application.use_cases.send_project_chat_message import (
        DocumentLimits,
    )

    world.llm = FakeLLMClient()
    world.quota.add_subscription(USER_ID, world.plan, start_at=NOW)
    writer = ImportWriter(
        world.llm,
        max_retries=0,
        input_price_per_1m=0,
        output_price_per_1m=0,
        retry_budget_seconds=40,
    )
    app.dependency_overrides[get_settings] = make_settings
    app.dependency_overrides[get_preview_project_import] = lambda: PreviewProjectImport(
        world.repo,
        world.quota,
        writer,
        limits=DocumentLimits(max_file_bytes=1000),
        now=lambda: NOW,
    )
    app.dependency_overrides[get_import_projects] = lambda: ImportProjects(
        world.repo, now=lambda: NOW
    )
    yield TestClient(app)
    app.dependency_overrides.clear()


def upload(client, data=b"Viec,Han\nWeb,2026-12-15\n", name="viec.csv"):
    return client.post(
        "/projects/import/preview",
        files={"file": (name, data, "text/csv")},
        data={"language": "vi"},
        headers=HEADERS,
    )


def test_preview_returns_projects_and_the_allowance(client, world):
    world.llm.answers.append(answer("Website", "Logo"))
    response = upload(client)
    assert response.status_code == 200
    body = response.json()
    assert [p["name"] for p in body["projects"]] == ["Website", "Logo"]
    assert body["projects"][0]["brief"] == "Brief Website"
    assert (body["project_limit"], body["projects_left"]) == (3, 2)


def test_preview_errors(client, world):
    assert upload(client, data=b"x" * 1001).json()["detail"]["code"] == "too_large"
    assert upload(client, data=b"\x00\x01", name="a.bin").status_code == 422
    assert client.post("/projects/import/preview").status_code == 401


def body(*names):
    return {
        "file_name": "viec.csv",
        "projects": [
            {"name": n, "brief": f"Row 2: {n}", "deadline": "2026-12-15"} for n in names
        ],
    }


def test_create_returns_projects_and_chats(client, world):
    response = client.post(
        "/projects/import", json=body("Web", "Logo"), headers=HEADERS
    )
    assert response.status_code == 201
    created = response.json()["projects"]
    assert [c["name"] for c in created] == ["Web", "Logo"]
    assert all(c["conversation_id"] for c in created)


def test_create_over_the_limit_is_409_with_numbers(client, world):
    response = client.post(
        "/projects/import", json=body("A", "B", "C"), headers=HEADERS
    )
    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "project_limit",
        "limit": 3,
        "used": 1,
        "wanted": 3,
    }
    assert world.repo.created == []


def test_create_bad_list_is_422(client, world):
    bad = {
        "file_name": "a",
        "projects": [{"name": "A", "brief": "x"}, {"name": "a", "brief": "y"}],
    }
    assert client.post("/projects/import", json=bad, headers=HEADERS).status_code == 422
