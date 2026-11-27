"""Validate structured workbook rows shared by preview and confirmation."""

from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from uuid import UUID


class WorkbookError(ValueError):
    """Invalid or unsupported structured workbook."""


LIMIT = 5000
SHEETS = {
    "projects": (
        "Projects",
        "Danh sách dự án",
        ("project_code", "name", "description", "deadline"),
    ),
    "milestones": (
        "Milestones",
        "Cột mốc",
        ("project_code", "milestone_code", "name", "deadline"),
    ),
    "tasks": (
        "Tasks",
        "Công việc chi tiết",
        (
            "project_code",
            "milestone_code",
            "name",
            "estimated_minutes",
            "domain",
            "deadline",
        ),
    ),
}


@dataclass(frozen=True)
class PlanRow:
    source: str
    project_code: str = ""
    milestone_code: str = ""
    name: str = ""
    description: str = ""
    deadline: str = ""
    estimated_minutes: str = ""
    domain: str = ""


@dataclass(frozen=True)
class PlanRows:
    projects: list[PlanRow] = field(default_factory=list)
    milestones: list[PlanRow] = field(default_factory=list)
    tasks: list[PlanRow] = field(default_factory=list)


@dataclass(frozen=True)
class PlanIssue:
    source: str
    field: str
    code: str


def code(value: str) -> str:
    return value.strip().lower()


def day(value: str) -> date | None:
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def minutes(value: str) -> int | None:
    value = value.strip()
    if not value.isascii() or not value.isdecimal() or len(value) > 10:
        return None
    number = int(value)
    return number if 0 < number <= 2_147_483_647 else None


def validate(rows: PlanRows, domains: dict[str, UUID], today: date) -> list[PlanIssue]:
    issues: list[PlanIssue] = []
    projects = {code(p.project_code): p for p in rows.projects}
    milestones = {
        (code(m.project_code), code(m.milestone_code)): m for m in rows.milestones
    }
    project_counts = Counter(code(p.project_code) for p in rows.projects)
    milestone_counts = Counter(
        (code(m.project_code), code(m.milestone_code)) for m in rows.milestones
    )
    for kind in SHEETS:
        for row in getattr(rows, kind):
            issues.extend(row_issues(row, kind))
            project_key = code(row.project_code)
            milestone_key = (project_key, code(row.milestone_code))
            project = projects.get(project_key)
            milestone = milestones.get(milestone_key)
            if kind == "projects":
                deadline = day(row.deadline)
                if deadline is not None and deadline < today:
                    issues.append(
                        PlanIssue(row.source, "deadline", "past_project_deadline")
                    )
            if kind == "projects" and project_counts[project_key] > 1:
                issues.append(PlanIssue(row.source, "project_code", "duplicate"))
            if kind != "projects" and (
                project is None or project_counts[project_key] != 1
            ):
                issues.append(PlanIssue(row.source, "project_code", "unknown_project"))
            if kind == "milestones" and milestone_counts[milestone_key] > 1:
                issues.append(PlanIssue(row.source, "milestone_code", "duplicate"))
            if kind == "tasks":
                if milestone is None or milestone_counts[milestone_key] != 1:
                    issues.append(
                        PlanIssue(row.source, "milestone_code", "unknown_milestone")
                    )
                if code(row.domain) not in domains:
                    issues.append(PlanIssue(row.source, "domain", "unknown_domain"))
                if minutes(row.estimated_minutes) is None:
                    issues.append(
                        PlanIssue(row.source, "estimated_minutes", "invalid_minutes")
                    )
                if (
                    effective_deadline(row, milestone, project) is None
                    and not row.deadline.strip()
                ):
                    issues.append(PlanIssue(row.source, "deadline", "missing_deadline"))
    return issues


def row_issues(row: PlanRow, kind: str) -> list[PlanIssue]:
    limits = {"name": 200 if kind == "tasks" else 150, "project_code": 100}
    if kind != "projects":
        limits["milestone_code"] = 100
    issues = [
        PlanIssue(row.source, field_name, "required_or_too_long")
        for field_name, size in limits.items()
        if not 1 <= len(getattr(row, field_name).strip()) <= size
    ]
    if len(row.description.strip()) > 1000:
        issues.append(PlanIssue(row.source, "description", "too_long"))
    if row.deadline.strip() and day(row.deadline) is None:
        issues.append(PlanIssue(row.source, "deadline", "invalid_date"))
    return issues


def effective_deadline(
    task: PlanRow, milestone: PlanRow | None, project: PlanRow | None
) -> date | None:
    for row in (task, milestone, project):
        if row is not None and row.deadline.strip():
            return day(row.deadline)
    return None
