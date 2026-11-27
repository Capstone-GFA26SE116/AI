from asyncio import run
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest

from metanoia_ai.application.ports.project_chat_repository import NewMilestone, NewTask
from metanoia_ai.application.ports.project_import_repository import (
    NewProject,
    ProjectAllowance,
)
from metanoia_ai.application.use_cases.import_excel_plan import (
    ImportExcelPlan,
    InvalidExcelPlan,
)
from metanoia_ai.application.use_cases.import_projects import ProjectLimitError
from metanoia_ai.domain.excel_plan import PlanRow, PlanRows

NOW = datetime(2026, 11, 27, tzinfo=UTC)
DOMAIN = UUID(int=41)


class PlanRepo:
    def __init__(self, limit: int = 2) -> None:
        self.limit = limit
        self.projects: dict[UUID, NewProject] = {}
        self.milestones: dict[UUID, tuple[UUID, NewMilestone]] = {}
        self.tasks: list[tuple[UUID, NewTask]] = []
        self.fail_task = False

    async def time_zone(self, user_id: UUID) -> ZoneInfo:
        return ZoneInfo("Asia/Ho_Chi_Minh")

    async def allowance(self, user_id: UUID, lock: bool = False) -> ProjectAllowance:
        return ProjectAllowance(self.limit, len(self.projects))

    async def active_domains(self) -> dict[UUID, str]:
        return {DOMAIN: "Coding"}

    async def existing_projects(self, user_id: UUID, ids: list[UUID]) -> set[UUID]:
        return self.projects.keys() & set(ids)

    async def create_plan_project(
        self, user_id: UUID, project_id: UUID, project: NewProject
    ) -> None:
        self.projects[project_id] = project

    async def add_milestone(
        self, user_id: UUID, project_id: UUID, milestone: NewMilestone
    ) -> UUID:
        id_ = uuid4()
        self.milestones[id_] = (project_id, milestone)
        return id_

    async def add_task(self, user_id: UUID, project_id: UUID, task: NewTask) -> UUID:
        if self.fail_task:
            raise RuntimeError("insert failed")
        self.tasks.append((project_id, task))
        return uuid4()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        projects = self.projects.copy()
        milestones = self.milestones.copy()
        tasks = self.tasks.copy()
        try:
            yield
        except Exception:
            self.projects, self.milestones, self.tasks = projects, milestones, tasks
            raise


def plan(milestone: str = "M1") -> PlanRows:
    return PlanRows(
        [
            PlanRow("Projects!2", "P1", name="A", deadline="2027-01-03"),
            PlanRow("Projects!3", "P2", name="B"),
        ],
        [
            PlanRow("Milestones!2", "P1", "M1", "First"),
            PlanRow("Milestones!3", "P2", "M1", "Second", deadline="2027-01-04"),
        ],
        [
            PlanRow(
                "Tasks!2",
                "P2",
                milestone,
                "Code",
                estimated_minutes="60",
                domain="coding",
            )
        ],
    )


def test_confirm_reuses_ids_and_inherits_correct_parent_deadline() -> None:
    repo = PlanRepo()
    service = ImportExcelPlan(repo, now=lambda: NOW)
    user, request = uuid4(), uuid4()
    first = run(service.confirm(user, request, plan()))
    assert first == run(service.confirm(user, request, plan()))
    assert list(repo.projects) == [project.project_id for project in first.projects]
    [(owner, task)] = repo.tasks
    assert (
        owner == first.projects[1].project_id == repo.milestones[task.milestone_id][0]
    )
    assert task.deadline.date() == date(2027, 1, 4)
    assert task.deadline.utcoffset().total_seconds() == 7 * 3600
    assert task.estimated_minutes == 60
    assert task.ai_estimated_minutes is None


def test_invalid_plan_and_plan_limit_save_nothing() -> None:
    repo = PlanRepo(limit=1)
    service = ImportExcelPlan(repo, now=lambda: NOW)
    with pytest.raises(InvalidExcelPlan) as error:
        run(service.confirm(uuid4(), uuid4(), plan("missing")))
    assert any(issue.code == "unknown_milestone" for issue in error.value.issues)
    with pytest.raises(ProjectLimitError):
        run(service.confirm(uuid4(), uuid4(), plan()))
    assert not repo.projects


def test_failed_task_insert_rolls_back_batch() -> None:
    repo = PlanRepo()
    repo.fail_task = True
    service = ImportExcelPlan(repo, now=lambda: NOW)
    with pytest.raises(RuntimeError, match="insert failed"):
        run(service.confirm(uuid4(), uuid4(), plan()))
    assert not repo.projects and not repo.milestones and not repo.tasks
