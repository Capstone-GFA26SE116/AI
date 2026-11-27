"""Preview and confirm rows imported from the Excel plan template."""

import json
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from metanoia_ai.application.ports.excel_plan_repository import ExcelPlanRepository
from metanoia_ai.application.ports.project_chat_repository import NewMilestone, NewTask
from metanoia_ai.application.ports.project_import_repository import NewProject
from metanoia_ai.application.use_cases.import_projects import ProjectLimitError
from metanoia_ai.application.use_cases.list_chat_history import UserNotFoundError
from metanoia_ai.domain.excel_plan import (
    PlanIssue,
    PlanRows,
    code,
    day,
    effective_deadline,
    minutes,
    validate,
)
from metanoia_ai.domain.project_breakdown import task_deadline
from metanoia_ai.domain.project_import import MAX_IMPORT_PROJECTS, projects_left


class InvalidExcelPlan(ValueError):
    def __init__(self, issues: list[PlanIssue]) -> None:
        self.issues = issues


class PartialExcelImport(ValueError):
    pass


@dataclass(frozen=True)
class ExcelPreview:
    rows: PlanRows
    domains: list[str]
    issues: list[PlanIssue]
    project_limit: int | None
    projects_left: int | None


@dataclass(frozen=True)
class PlanProject:
    project_id: UUID
    name: str


@dataclass(frozen=True)
class SavedExcelPlan:
    projects: list[PlanProject]
    milestone_count: int
    task_count: int


class ImportExcelPlan:
    def __init__(
        self, repo: ExcelPlanRepository, *, now: Callable[[], datetime]
    ) -> None:
        self.repo = repo
        self.now = now

    async def zone(self, user_id: UUID) -> ZoneInfo:
        zone = await self.repo.time_zone(user_id)
        if zone is None:
            raise UserNotFoundError(str(user_id))
        return zone

    async def preview(self, user_id: UUID, rows: PlanRows) -> ExcelPreview:
        zone = await self.zone(user_id)
        domains = await self.repo.active_domains()
        allowance = await self.repo.allowance(user_id)
        return ExcelPreview(
            rows,
            list(domains.values()),
            validate(
                rows,
                {code(name): id_ for id_, name in domains.items()},
                self.now().astimezone(zone).date(),
            ),
            allowance.limit,
            projects_left(allowance.limit, allowance.used),
        )

    async def confirm(
        self, user_id: UUID, request_id: UUID, rows: PlanRows
    ) -> SavedExcelPlan:
        if not 1 <= len(rows.projects) <= MAX_IMPORT_PROJECTS:
            raise InvalidExcelPlan([PlanIssue("", "projects", "project_count")])
        zone = await self.zone(user_id)
        today = self.now().astimezone(zone).date()
        namespace = uuid5(user_id, f"excel-plan:{request_id}")
        batch = uuid5(namespace, json.dumps(asdict(rows), sort_keys=True))
        planned = [
            PlanProject(uuid5(batch, code(row.project_code)), row.name.strip())
            for row in rows.projects
        ]
        ids = [project.project_id for project in planned]
        result = SavedExcelPlan(planned, len(rows.milestones), len(rows.tasks))
        async with self.repo.transaction():
            allowance = await self.repo.allowance(user_id, lock=True)
            existing = await self.repo.existing_projects(user_id, ids)
            if existing == set(ids):
                return result
            if existing:
                raise PartialExcelImport
            domains = {
                code(name): domain_id
                for domain_id, name in (await self.repo.active_domains()).items()
            }
            issues = validate(rows, domains, today)
            if issues:
                raise InvalidExcelPlan(issues)
            left = projects_left(allowance.limit, allowance.used)
            if allowance.limit is not None and left is not None and len(planned) > left:
                raise ProjectLimitError(allowance.limit, allowance.used, len(planned))

            projects = {}
            for row, project in zip(rows.projects, planned, strict=True):
                await self.repo.create_plan_project(
                    user_id,
                    project.project_id,
                    NewProject(
                        project.name,
                        row.description.strip() or None,
                        today,
                        day(row.deadline),
                    ),
                )
                projects[code(row.project_code)] = (project.project_id, row)
            order: Counter[str] = Counter()
            milestones = {}
            for row in rows.milestones:
                key = code(row.project_code)
                project_id, _ = projects[key]
                order[key] += 1
                milestone_id = await self.repo.add_milestone(
                    user_id,
                    project_id,
                    NewMilestone(row.name.strip(), day(row.deadline), order[key]),
                )
                milestones[key, code(row.milestone_code)] = (milestone_id, row)
            for row in rows.tasks:
                key = code(row.project_code)
                project_id, project_row = projects[key]
                milestone_id, milestone_row = milestones[key, code(row.milestone_code)]
                deadline = effective_deadline(row, milestone_row, project_row)
                estimate = minutes(row.estimated_minutes)
                assert deadline is not None and estimate is not None
                await self.repo.add_task(
                    user_id,
                    project_id,
                    NewTask(
                        milestone_id,
                        row.name.strip(),
                        domains[code(row.domain)],
                        task_deadline(deadline, zone),
                        estimate,
                        None,
                    ),
                )
        return result
