"""FR-08c: preview and create (plans/FR-08c.md T2, T3)."""

import json
from datetime import date, datetime
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest

from metanoia_ai.application.import_writer import ImportWriter
from metanoia_ai.application.ports.quota_repository import Subscription
from metanoia_ai.application.use_cases.import_projects import (
    ImportProjects,
    ProjectLimitError,
)
from metanoia_ai.application.use_cases.preview_project_import import (
    PreviewProjectImport,
)
from metanoia_ai.application.use_cases.propose_schedule import (
    PlanFeatureError,
    QuotaExceededError,
)
from metanoia_ai.application.use_cases.send_project_chat_message import (
    DocumentLimits,
)
from metanoia_ai.application.use_cases.send_task_chat_message import (
    AIUnavailableError,
)
from metanoia_ai.domain.document_text import DocumentError
from metanoia_ai.domain.models import Language
from metanoia_ai.domain.project_import import ImportInput
from metanoia_ai.domain.task_breakdown import BadDraftError
from tests.unit.application.fakes import (
    FakeLLMClient,
    FakeProjectImportRepository,
    FakeQuotaRepository,
)

USER = UUID(int=7)
NOW = datetime(2026, 11, 20, 3, 0, tzinfo=ZoneInfo("UTC"))
CSV = "Row 1: Việc | Hạn\n".encode()  # replaced per test


def answer(*names, deadline=None):
    return json.dumps(
        {
            "projects": [
                {
                    "name": n,
                    "deadline": deadline,
                    "source": "rows 2-3",
                    "brief": f"Brief {n}",
                }
                for n in names
            ]
        },
        ensure_ascii=False,
    )


class World:
    def __init__(self, price=99, calls_used=0, limit=None, used=0):
        self.repo = FakeProjectImportRepository(limit, used)
        self.quota = FakeQuotaRepository(now=NOW)
        self.plan = Subscription(
            uuid4(), price=price, ai_call_limit=50, ai_calls_used=calls_used
        )
        self.quota.add_subscription(USER, self.plan, start_at=NOW)

    def preview(
        self,
        llm,
        data=b"Viec,Han\nWebsite ACME ceo@acme.vn,2026-12-15\n",
        name="viec.csv",
    ):
        writer = ImportWriter(
            llm,
            max_retries=1,
            input_price_per_1m=0.1,
            output_price_per_1m=0.4,
            retry_budget_seconds=40,
        )
        use_case = PreviewProjectImport(
            self.repo, self.quota, writer, limits=DocumentLimits(), now=lambda: NOW
        )
        import asyncio

        return asyncio.run(use_case.execute(USER, name, data, Language.VI))

    def create(self, items, name="viec.csv"):
        import asyncio

        return asyncio.run(
            ImportProjects(self.repo, now=lambda: NOW).execute(
                USER, name, items, Language.VI
            )
        )


# --- T2: preview ---


def test_preview_splits_charges_and_masks_contacts():
    world = World(limit=5, used=2)
    llm = FakeLLMClient(answer("Website", "Logo"))
    preview = world.preview(llm)
    assert [p.name for p in preview.projects] == ["Website", "Logo"]
    assert (preview.project_limit, preview.projects_left) == (5, 3)
    assert world.quota.calls_used[world.plan.subscription_id] == 1
    assert "ceo@acme.vn" not in llm.prompts[0] and "[email]" in llm.prompts[0]
    assert "Row 2: Website ACME" in llm.prompts[0]


def test_preview_warns_about_dropped_past_deadlines():
    world = World()
    preview = world.preview(FakeLLMClient(answer("A", deadline="2026-01-01")))
    assert preview.projects[0].deadline is None
    assert preview.warnings == (
        "Đã bỏ 1 hạn đã qua trong file; bạn đặt lại hạn nếu cần.",
    )


def test_free_plan_cannot_preview():
    with pytest.raises(PlanFeatureError):
        World(price=0).preview(FakeLLMClient(answer("A")))


def test_out_of_calls():
    with pytest.raises(QuotaExceededError):
        World(calls_used=50).preview(FakeLLMClient(answer("A")))


def test_unreadable_file_is_a_document_error_and_not_charged():
    world = World()
    with pytest.raises(DocumentError):
        world.preview(FakeLLMClient(answer("A")), data=b"\x00\x01", name="x.bin")
    assert world.quota.calls_used[world.plan.subscription_id] == 0


def test_ai_failure_is_502_logged_but_not_charged():
    world = World()
    with pytest.raises(AIUnavailableError):
        world.preview(FakeLLMClient("bad", "bad"))
    assert world.quota.calls_used[world.plan.subscription_id] == 0
    entries = [entry for _, _, entry in world.quota.logs]
    assert len(entries) == 2 and not any(e.quota_charged for e in entries)


# --- T3: create ---


def items(*names, deadline=None):
    return [ImportInput(n, f"Row 2: {n}", "Mô tả", deadline, "rows 2") for n in names]


def test_create_makes_projects_and_chats_with_the_brief():
    world = World()
    created = world.create(items("Website", "Logo", deadline=date(2026, 12, 15)))
    assert [c.name for c in created] == ["Website", "Logo"]
    row = world.repo.created[0]
    assert row.project.start_date == date(2026, 11, 20)  # today, Ho Chi Minh time
    assert row.project.deadline == date(2026, 12, 15)
    [(chat_id, title, message)] = row.chats
    assert chat_id == created[0].conversation_id
    assert title == "Từ file viec.csv"
    assert message == "Nội dung từ file viec.csv (rows 2):\n\nRow 2: Website"


def test_over_the_plan_limit_creates_nothing():
    world = World(limit=3, used=2)
    with pytest.raises(ProjectLimitError) as error:
        world.create(items("A", "B"))
    assert (error.value.limit, error.value.used, error.value.wanted) == (3, 2, 2)
    assert world.repo.created == []


def test_no_limit_allows_many():
    assert len(World(limit=None, used=50).create(items(*"ABCDE"))) == 5


def test_a_failure_rolls_everything_back():
    world = World()
    world.repo.fail_on_chat = True
    with pytest.raises(RuntimeError):
        world.create(items("A", "B"))
    assert world.repo.created == []


def test_bad_edits_are_refused():
    with pytest.raises(BadDraftError):
        World().create(items("A", deadline=date(2026, 11, 1)))
