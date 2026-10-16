"""FR-08b-1: a client brief → milestones + Flexible tasks. Pure rules, no I/O."""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, time
from decimal import Decimal
from enum import StrEnum
from uuid import UUID
from zoneinfo import ZoneInfo

from metanoia_ai.domain.memory_text import CLIENT_MASK
from metanoia_ai.domain.models import Language
from metanoia_ai.domain.schedule_duration import scaled_minutes
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


def restore_client(
    draft: ProjectDraft, client_name: str | None, max_milestone_title_chars: int
) -> ProjectDraft:
    """Put the client's name back where the AI wrote "[client]" (#32).

    The prompt hides the name, so the AI writes the mask; the user should see the
    real name in their tasks. A title that would get too long keeps the mask.
    [email] / [phone] cannot be restored and stay as they are.
    """
    if not client_name:
        return draft

    def restored(title: str, max_chars: int) -> str:
        full = title.replace(CLIENT_MASK, client_name)
        return full if len(full) <= max_chars else title

    return replace(
        draft,
        milestones=tuple(
            replace(
                m,
                title=restored(m.title, max_milestone_title_chars),
                tasks=tuple(
                    replace(t, title=restored(t.title, MAX_TASK_TITLE_CHARS))
                    for t in m.tasks
                ),
            )
            for m in draft.milestones
        ),
    )


def restore_client_in_notes(
    draft: ProjectDraft, client_name: str | None
) -> ProjectDraft:
    """Put the client's name back in reminders and warnings (#36).

    They are shown to the user as is and never sent back to the LLM.
    """
    if not client_name:
        return draft
    return replace(
        draft,
        reminders=tuple(r.replace(CLIENT_MASK, client_name) for r in draft.reminders),
        warnings=tuple(w.replace(CLIENT_MASK, client_name) for w in draft.warnings),
    )


# Letters only Vietnamese uses (checked on lower-case text). "Ok" says nothing.
VIETNAMESE_LETTERS = frozenset(
    "ăâđêôơưàáạảãằắặẳẵầấậẩẫèéẹẻẽềếệểễìíịỉĩòóọỏõồốộổỗờớợởỡùúụủũừứựửữỳýỵỷỹ"
)


def reply_language(latest_message: str, brief: str | None) -> Language:
    """The language the AI must answer in (#35): chosen by code, not by the LLM.

    Vietnamese letters in the latest message → vi; none → look at the brief (the
    first message); none there either → en. Vietnamese typed without accents
    comes out as en: a known limit.
    """
    for text in (latest_message, brief or ""):
        if any(letter in VIETNAMESE_LETTERS for letter in text.lower()):
            return Language.VI
    return Language.EN


def task_deadline(day: date, zone: ZoneInfo) -> datetime:
    """Task.Deadline for a milestone due on `day`: 23:59 that day, user's time (#16)."""
    return datetime.combine(day, END_OF_DAY, tzinfo=zone)


# --- duplicates (#9, #17, #22) ---


class DuplicateOf(StrEnum):
    """Why a task was dropped. Values are sent in the API (`skipped[].reason`)."""

    EXISTING_TASK = "existing_task"  # the project already has a task with this title
    IN_DRAFT = "in_draft"  # an earlier task of the same draft has this title


@dataclass(frozen=True)
class DroppedTask:
    title: str
    reason: DuplicateOf


def same_title_key(title: str) -> str:
    """Compare titles ignoring case and extra spaces: " Viết  API" = "viết api"."""
    return clean_title(title).casefold()


def split_duplicates(
    draft: ProjectDraft, existing_titles: Iterable[str]
) -> tuple[ProjectDraft, list[DroppedTask]]:
    """Drop tasks whose title is already used; drop milestones left empty.

    Keys stay as they were (gaps are fine). The draft may end up with no milestone.
    """
    seen_existing = {same_title_key(t) for t in existing_titles}
    seen_draft: set[str] = set()
    dropped: list[DroppedTask] = []
    milestones: list[DraftMilestone] = []
    for milestone in draft.milestones:
        kept: list[DraftTask] = []
        for task in milestone.tasks:
            key = same_title_key(task.title)
            if key in seen_existing:
                dropped.append(DroppedTask(task.title, DuplicateOf.EXISTING_TASK))
            elif key in seen_draft:
                dropped.append(DroppedTask(task.title, DuplicateOf.IN_DRAFT))
            else:
                seen_draft.add(key)
                kept.append(task)
        if kept:
            milestones.append(replace(milestone, tasks=tuple(kept)))
    return replace(draft, milestones=tuple(milestones)), dropped


DUPLICATE_WARNINGS = {
    (Language.VI, DuplicateOf.EXISTING_TASK): 'Đã bỏ "{title}": dự án đã có việc này.',
    (
        Language.VI,
        DuplicateOf.IN_DRAFT,
    ): 'Đã bỏ "{title}": trùng một việc khác trong bản nháp.',
    (
        Language.EN,
        DuplicateOf.EXISTING_TASK,
    ): 'Removed "{title}": the project already has it.',
    (
        Language.EN,
        DuplicateOf.IN_DRAFT,
    ): 'Removed "{title}": same as another task in the draft.',
}


def drop_duplicates(
    draft: ProjectDraft,
    existing_titles: Iterable[str],
    language: Language = Language.VI,
) -> ProjectDraft:
    """An AI draft without duplicates, one warning per dropped task (#17).

    Nothing left → BadDraftError (the answer is retried).
    """
    kept, dropped = split_duplicates(draft, existing_titles)
    if not kept.milestones:
        raise BadDraftError("every task repeats an existing task")
    warnings = [
        DUPLICATE_WARNINGS[(language, d.reason)].format(title=d.title) for d in dropped
    ]
    return replace(kept, warnings=(*kept.warnings, *warnings))


# --- numbers (#8, #20) ---


@dataclass(frozen=True)
class DomainNumbers:
    domain: str
    estimated_minutes: int  # sum of the AI's minutes
    adjusted_minutes: int  # ceil(sum × the user's K for this domain)


@dataclass(frozen=True)
class ProjectNumbers:
    """The numbers shown next to a draft. Computed by code, never by the LLM (AC6)."""

    by_domain: tuple[DomainNumbers, ...]  # in the order the domains first appear
    total_estimated_minutes: int
    total_adjusted_minutes: int
    days_left: int | None  # None: no deadline to count to
    adjusted_is_hint: bool  # Free plan: the adjusted time is only a hint
    warning: str | None  # set when the work does not fit in the days left


OVERLOAD_WARNINGS = {
    Language.VI: (
        "Khoảng {hours} giờ làm việc, nhưng chỉ còn {days} ngày "
        "(~{capacity} giờ nếu làm {daily} giờ/ngày). Cân nhắc dời hạn hoặc bớt việc."
    ),
    Language.EN: (
        "About {hours} hours of work, but only {days} days left "
        "(~{capacity} hours at {daily} hours a day). Consider a later deadline or less work."
    ),
}


def _hours(minutes: float) -> str:
    """90 → "1.5", 120 → "2"."""
    return f"{minutes / 60:.1f}".removesuffix(".0")


def project_numbers(
    draft: ProjectDraft,
    k_by_domain: Mapping[UUID, Decimal],
    initial_k: Decimal,
    project_deadline: date | None,
    today: date,
    is_free_plan: bool,
    daily_work_hours: float,
    language: Language = Language.VI,
) -> ProjectNumbers:
    """Totals per domain × that domain's K (initial K when not learned yet).

    days_left counts from today to the project deadline, or to the latest milestone
    when the project has none. The warning compares the adjusted total with
    days_left × daily_work_hours (rough: FR-10 places the real slots).
    """
    estimated: dict[UUID, int] = {}
    names: dict[UUID, str] = {}
    for task in draft.tasks:
        estimated[task.domain_id] = (
            estimated.get(task.domain_id, 0) + task.estimated_minutes
        )
        names[task.domain_id] = task.domain_name
    by_domain = tuple(
        DomainNumbers(
            names[domain_id],
            minutes,
            scaled_minutes(minutes, k_by_domain.get(domain_id, initial_k)),
        )
        for domain_id, minutes in estimated.items()
    )
    total_adjusted = sum(d.adjusted_minutes for d in by_domain)

    deadlines = [m.deadline for m in draft.milestones if m.deadline is not None]
    last_day = project_deadline or (max(deadlines) if deadlines else None)
    days_left = max(0, (last_day - today).days) if last_day else None

    warning = None
    if days_left is not None:
        capacity_minutes = days_left * daily_work_hours * 60
        if total_adjusted > capacity_minutes:
            warning = OVERLOAD_WARNINGS[language].format(
                hours=_hours(total_adjusted),
                days=days_left,
                capacity=_hours(capacity_minutes),
                daily=_hours(daily_work_hours * 60),
            )
    return ProjectNumbers(
        by_domain=by_domain,
        total_estimated_minutes=sum(d.estimated_minutes for d in by_domain),
        total_adjusted_minutes=total_adjusted,
        days_left=days_left,
        adjusted_is_hint=is_free_plan,
        warning=warning,
    )


# --- who set the minutes (#11, #21, FR-17b) ---


def ai_minutes_of(key: str | None, saved: ProjectDraft) -> int | None:
    """The AI's minutes for the task with this key in the saved draft.

    Written to Task.AiEstimatedMinutes. None for a task the user added (no key)
    or a key that is not in the draft: the client cannot invent an AI number.
    """
    if key is None:
        return None
    for task in saved.tasks:
        if task.key == key:
            return task.estimated_minutes
    return None
