"""POST /chat/conversations/{id}/apply-and-schedule (plans/FR-08b-7.md T4)."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from metanoia_ai.api.deps import get_apply_and_schedule, project_limits
from metanoia_ai.api.main import app
from metanoia_ai.application.use_cases.apply_and_schedule import ApplyAndSchedule
from metanoia_ai.application.use_cases.apply_project_breakdown import (
    ApplyProjectBreakdown,
)
from metanoia_ai.application.use_cases.propose_schedule import (
    ProposalResult,
    ProposedSlot,
    QuotaExceededError,
)
from tests.unit.api.test_auth import make_settings
from tests.unit.api.test_project_chat import (  # noqa: F401
    HEADERS,
    client,
    drafted,
    world,
)
from tests.unit.application.test_project_chat import NOW


class Scheduler:
    def __init__(self):
        self.error = None
        self.calls = []

    async def execute(self, user_id, task_ids, language):
        self.calls.append((list(task_ids), language))
        if self.error:
            raise self.error
        start = datetime(2026, 10, 20, 2, 0, tzinfo=UTC)
        slot = ProposedSlot(uuid4(), task_ids[0], None, start, start, 78, "Lý do")
        return ProposalResult(uuid4(), True, None, "Đã xếp 1 việc.", (slot,), ())


@pytest.fixture
def scheduler(client, world):  # noqa: F811
    fake = Scheduler()
    app.dependency_overrides[get_apply_and_schedule] = lambda: ApplyAndSchedule(
        ApplyProjectBreakdown(
            world.repo, project_limits(make_settings(), manual=True), lambda: NOW
        ),
        fake,
    )
    return fake


def post(client, world, body):  # noqa: F811
    url = f"/chat/conversations/{world.chat_id}/apply-and-schedule"
    return client.post(url, json=body, headers=HEADERS)


def test_applies_and_returns_the_schedule(client, world, scheduler):  # noqa: F811
    message_id = drafted(client, world)
    response = post(client, world, {"message_id": message_id, "language": "en"})
    assert response.status_code == 201
    body = response.json()
    created = [t["id"] for m in body["applied"]["milestones"] for t in m["tasks"]]
    assert scheduler.calls == [([UUID(c) for c in created], "en")]
    assert body["schedule"]["explanation"] == "Đã xếp 1 việc."
    assert body["schedule"]["slots"][0]["task_id"] == created[0]
    assert body["schedule_error"] is None


def test_out_of_quota_keeps_the_work(client, world, scheduler):  # noqa: F811
    scheduler.error = QuotaExceededError("x")
    response = post(client, world, {"message_id": drafted(client, world)})
    assert response.status_code == 201
    body = response.json()
    assert body["applied"]["milestones"]
    assert body["schedule"] is None
    assert body["schedule_error"] == "quota_exceeded"


def test_apply_errors_are_the_same_as_apply(client, world, scheduler):  # noqa: F811
    message_id = drafted(client, world)
    assert post(client, world, {"message_id": message_id}).status_code == 201
    again = post(client, world, {"message_id": message_id})
    assert again.status_code == 409
    assert again.json()["detail"]["code"] == "already_applied"
    assert len(scheduler.calls) == 1
