"""FR-08b-1: a client brief → milestones + Flexible tasks. Pure rules, no I/O."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, time
from uuid import UUID
from zoneinfo import ZoneInfo

from metanoia_ai.domain.models import Language
from metanoia_ai.domain.task_breakdown import BadDraftError, ReplyKind, clean_title

MAX_TASK_TITLE_CHARS = 200  # Task.Title is varchar(200)
END_OF_DAY = time(23, 59)  # a task due on a day is due at 23:59 that day (#16)


@dataclass(frozen=True)
class TaskInput:
    """One task as received (from the LLM or the user), not checked yet."""

    title: object
    domain_id: object  # must turn out to be the id of an active domain
    estimated_minutes: object  # must turn out to be an int
    reason: str | None = None
    key: str | None = None  # manual edits: the draft key the task came from


@dataclass(frozen=True)
class MilestoneInput:
    title: object
    deadline: date | None
    tasks: Sequence[TaskInput]
    key: str | None = None


@dataclass(frozen=True)
class DraftTask:
    key: str | None  # "m1-t3"; None for a task the user added at Apply
    title: str
    domain_id: UUID
    domain_name: str
    estimated_minutes: int
    reason: str | None = None


@dataclass(frozen=True)
class DraftMilestone:
    key: str | None  # "m1"
    title: str
    deadline: date | None  # always set after resolve_deadlines
    tasks: tuple[DraftTask, ...]


@dataclass(frozen=True)
class ProjectDraft:
    milestones: tuple[DraftMilestone, ...]
    reminders: tuple[str, ...] = ()  # Fixed things to create by hand (#13b)
    warnings: tuple[str, ...] = ()

    @property
    def tasks(self) -> list[DraftTask]:
        return [task for milestone in self.milestones for task in milestone.tasks]


@dataclass(frozen=True)
class ProjectLimits:
    """Sizes allowed in a draft (#7). From config; manual edits allow more minutes."""

    max_milestones: int
    max_tasks_per_milestone: int
    max_tasks: int
    min_minutes: int
    max_minutes: int
    max_milestone_title_chars: int  # Milestone.Name is varchar(150)


def _title(value: object, where: str, max_chars: int) -> str:
    title = clean_title(value) if isinstance(value, str) else ""
    if not title:
        raise BadDraftError(f"{where}: empty title")
    if len(title) > max_chars:
        raise BadDraftError(f"{where}: title over {max_chars} chars")
    return title


def _domain_id(value: object, where: str, domains: Mapping[UUID, str]) -> UUID:
    try:
        domain_id = value if isinstance(value, UUID) else UUID(str(value))
    except ValueError:
        domain_id = None
    if domain_id not in domains:
        raise BadDraftError(f"{where}: unknown domain")
    return domain_id


def _minutes(value: object, where: str, limits: ProjectLimits) -> int:
    # bool is an int in Python; 60.0 is not a whole-number answer either.
    if isinstance(value, bool) or not isinstance(value, int):
        raise BadDraftError(f"{where}: minutes must be a whole number")
    if not limits.min_minutes <= value <= limits.max_minutes:
        raise BadDraftError(
            f"{where}: minutes must be {limits.min_minutes}-{limits.max_minutes}, "
            f"got {value}"
        )
    return value


def check_project_draft(
    milestones: Sequence[MilestoneInput],
    domains: Mapping[UUID, str],
    limits: ProjectLimits,
    *,
    assign_keys: bool,
) -> tuple[DraftMilestone, ...]:
    """Check sizes, titles, domains and minutes (#7, #14 for AI drafts, #21 for edits).

    `domains`: id → name of the active domains. assign_keys=True (AI drafts): keys
    m1, m1-t1, … in list order. False (manual edits): keys kept as sent.
    Duplicate titles are not an error here: drop_duplicates removes them (#9).
    """
    if not 1 <= len(milestones) <= limits.max_milestones:
        raise BadDraftError(
            f"need 1-{limits.max_milestones} milestones, got {len(milestones)}"
        )
    total = sum(len(m.tasks) for m in milestones)
    if total > limits.max_tasks:
        raise BadDraftError(f"at most {limits.max_tasks} tasks, got {total}")

    checked: list[DraftMilestone] = []
    for m_number, milestone in enumerate(milestones, start=1):
        where = f"milestone {m_number}"
        title = _title(milestone.title, where, limits.max_milestone_title_chars)
        if not 1 <= len(milestone.tasks) <= limits.max_tasks_per_milestone:
            raise BadDraftError(
                f"{where}: need 1-{limits.max_tasks_per_milestone} tasks, "
                f"got {len(milestone.tasks)}"
            )
        m_key = f"m{m_number}" if assign_keys else milestone.key
        tasks: list[DraftTask] = []
        for t_number, item in enumerate(milestone.tasks, start=1):
            t_where = f"{where} task {t_number}"
            domain_id = _domain_id(item.domain_id, t_where, domains)
            reason = clean_title(item.reason) if item.reason else None
            tasks.append(
                DraftTask(
                    key=f"{m_key}-t{t_number}" if assign_keys else item.key,
                    title=_title(item.title, t_where, MAX_TASK_TITLE_CHARS),
                    domain_id=domain_id,
                    domain_name=domains[domain_id],
                    estimated_minutes=_minutes(item.estimated_minutes, t_where, limits),
                    reason=reason or None,
                )
            )
        checked.append(DraftMilestone(m_key, title, milestone.deadline, tuple(tasks)))
    return tuple(checked)


def check_project_reply(
    kind: ReplyKind,
    milestones: Sequence[MilestoneInput],
    reminders: Sequence[str],
    domains: Mapping[UUID, str],
    limits: ProjectLimits,
) -> ProjectDraft | None:
    """An AI answer: a question has no milestones (None); a draft must have some."""
    if kind is ReplyKind.QUESTION:
        if milestones:
            raise BadDraftError("a question must not list milestones")
        return None
    if not milestones:
        raise BadDraftError("a draft must list milestones")
    checked = check_project_draft(milestones, domains, limits, assign_keys=True)
    cleaned = (clean_title(r) for r in reminders if isinstance(r, str))
    return ProjectDraft(checked, tuple(r for r in cleaned if r))


# Shown to the user: no internal names.
LATE_WARNINGS = {
    Language.VI: 'Hạn của "{title}" ({day}) sau hạn dự án ({project}).',
    Language.EN: 'The deadline of "{title}" ({day}) is after the project deadline ({project}).',
}
DATE_FORMATS = {Language.VI: "%d/%m/%Y", Language.EN: "%Y-%m-%d"}


def resolve_deadlines(
    draft: ProjectDraft,
    project_deadline: date | None,
    today: date,
    language: Language = Language.VI,
) -> ProjectDraft:
    """Fill and check milestone deadlines (#6, #14, #15). `today`: in the user's zone.

    Empty → the project deadline; both empty → error (the AI must ask the user).
    Before today → error. After the project deadline → kept, with a warning.
    """
    milestones: list[DraftMilestone] = []
    warnings = list(draft.warnings)
    for number, milestone in enumerate(draft.milestones, start=1):
        day = milestone.deadline or project_deadline
        if day is None:
            raise BadDraftError(
                f"milestone {number}: no deadline and the project has none; "
                "ask the user for a deadline"
            )
        if day < today:
            raise BadDraftError(f"milestone {number}: deadline is in the past")
        if project_deadline is not None and day > project_deadline:
            fmt = DATE_FORMATS[language]
            warnings.append(
                LATE_WARNINGS[language].format(
                    title=milestone.title,
                    day=day.strftime(fmt),
                    project=project_deadline.strftime(fmt),
                )
            )
        milestones.append(replace(milestone, deadline=day))
    return replace(draft, milestones=tuple(milestones), warnings=tuple(warnings))


def task_deadline(day: date, zone: ZoneInfo) -> datetime:
    """Task.Deadline for a milestone due on `day`: 23:59 that day, user's time (#16)."""
    return datetime.combine(day, END_OF_DAY, tzinfo=zone)
