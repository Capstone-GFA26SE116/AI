from dataclasses import asdict
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from metanoia_ai.api.deps import get_import_excel_plan
from metanoia_ai.api.main import app
from metanoia_ai.application.use_cases.import_excel_plan import ImportExcelPlan
from metanoia_ai.config import get_settings
from tests.unit.api.test_auth import make_settings, make_token
from tests.unit.application.test_import_excel_plan import NOW, PlanRepo, plan
from tests.unit.infrastructure.files.test_excel_plan import workbook

HEADERS = {"Authorization": f"Bearer {make_token()}"}
PATH = "/projects/import/plan"


@pytest.fixture
def client():
    app.dependency_overrides[get_settings] = make_settings
    app.dependency_overrides[get_import_excel_plan] = lambda: ImportExcelPlan(
        PlanRepo(), now=lambda: NOW
    )
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_excel_preview_requires_auth_and_returns_workbook_rows(
    client: TestClient,
) -> None:
    files = {"file": ("plan.xlsx", workbook())}
    assert client.post(f"{PATH}/preview", files=files).status_code == 401
    result = client.post(f"{PATH}/preview", files=files, headers=HEADERS)
    assert result.status_code == 200
    body = result.json()
    assert body["rows"]["projects"][0]["project_code"] == "001"
    assert body["issues"][0]["code"] == "unknown_milestone"
    assert body["projects_left"] == 2


def test_excel_confirm_validates_and_returns_counts(client: TestClient) -> None:
    request_id = str(uuid4())
    bad = client.post(
        PATH,
        json={"request_id": request_id, **asdict(plan("missing"))},
        headers=HEADERS,
    )
    assert bad.status_code == 422
    assert bad.json()["detail"]["issues"][0]["source"] == "Tasks!2"
    good = client.post(
        PATH, json={"request_id": request_id, **asdict(plan())}, headers=HEADERS
    )
    assert good.status_code == 201
    assert (good.json()["milestone_count"], good.json()["task_count"]) == (2, 1)
