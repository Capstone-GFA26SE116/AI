"""FR-12 daily "Today" briefing: pure rules, no DB, no LLM.

All times given back are in the user's time zone.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from uuid import UUID
from zoneinfo import ZoneInfo

from metanoia_ai.domain.free_time import (
    CalendarEvent,
    FixedTaskTime,
    busy_ranges,
    free_ranges,
)
from metanoia_ai.domain.models import FocusSessionRecord, TimeRange, WorkingHours
from metanoia_ai.domain.planner import local_date
from metanoia_ai.domain.schedule_duration import minutes_to_schedule, sessions_of
from metanoia_ai.domain.schedule_text import extra_percent


class SlotStatus(StrEnum):
    """The ScheduleSlot states the briefing reads."""

    PROPOSED = "proposed"  # not approved yet: only counted (#3)
    APPROVED = "approved"
    DONE = "done"


class ScheduleKind(StrEnum):
    """What a line of today's schedule is. Values are sent in the API."""

    SLOT = "slot"  # an approved / done slot of a Flexible task
    FIXED = "fixed"  # a Fixed task (meeting)
    GOOGLE = "google"  # an event synced from Google Calendar


@dataclass(frozen=True)
class SlotEntry:
    task_id: UUID
    title: str  # "Task: Subtask" for a subtask slot
    start: datetime
    end: datetime
    status: SlotStatus


@dataclass(frozen=True)
class FixedEntry:
    task_id: UUID
    title: str
    start: datetime
    end: datetime
    is_skipped: bool  # the meeting was cancelled: not shown


@dataclass(frozen=True)
class GoogleEntry:
    title: str
    start: datetime
    end: datetime  # may equal start
    is_all_day: bool
    is_cancelled: bool
    is_created_by_metanoia: bool  # already a ScheduleSlot: would show twice


@dataclass(frozen=True)
class ScheduleItem:
    kind: ScheduleKind
    start: datetime
    end: datetime
    title: str
    task_id: UUID | None  # None for a Google event
    status: SlotStatus | None = None  # only for slots


@dataclass(frozen=True)
class TodaySchedule:
    items: tuple[ScheduleItem, ...]  # sorted by start
    all_day: tuple[str, ...]  # titles of all-day Google events
    has_pending_proposal: bool


def today_range(now: datetime, time_zone: ZoneInfo) -> TimeRange:
    """[00:00, 24:00) of today on the user's clock."""
    day = local_date(now, time_zone)
    start = datetime.combine(day, datetime.min.time(), tzinfo=time_zone)
    end = datetime.combine(
        day + timedelta(days=1), datetime.min.time(), tzinfo=time_zone
    )
    return TimeRange(start, end)


def falls_on(start: datetime, end: datetime, day: TimeRange) -> bool:
    """True when [start, end) overlaps the day. A zero-length event counts by its start."""
    if end <= start:
        return day.start <= start < day.end
    return start < day.end and end > day.start


def todays_schedule(
    slots: Iterable[SlotEntry],
    fixed_tasks: Iterable[FixedEntry],
    events: Iterable[GoogleEntry],
    now: datetime,
    time_zone: ZoneInfo,
) -> TodaySchedule:
    """Today's real schedule (#3): approved/done slots, Fixed tasks, Google events.

    Proposed slots are not shown; a Proposed slot that has not ended yet only
    sets `has_pending_proposal`.
    """
    day = today_range(now, time_zone)
    items: list[ScheduleItem] = []
    pending = False

    for slot in slots:
        if not falls_on(slot.start, slot.end, day):
            continue
        if slot.status is SlotStatus.PROPOSED:
            pending = pending or slot.end > now
            continue
        items.append(
            ScheduleItem(
                ScheduleKind.SLOT,
                slot.start,
                slot.end,
                slot.title,
                slot.task_id,
                slot.status,
            )
        )

    for task in fixed_tasks:
        if not task.is_skipped and falls_on(task.start, task.end, day):
            items.append(
                ScheduleItem(
                    ScheduleKind.FIXED, task.start, task.end, task.title, task.task_id
                )
            )

    all_day: list[tuple[datetime, str]] = []
    for event in events:
        if event.is_cancelled or event.is_created_by_metanoia:
            continue
        if not falls_on(event.start, event.end, day):
            continue
        if event.is_all_day:
            all_day.append((event.start, event.title))
        else:
            items.append(
                ScheduleItem(
                    ScheduleKind.GOOGLE, event.start, event.end, event.title, None
                )
            )

    items.sort(key=lambda i: (i.start, i.end, i.title))
    return TodaySchedule(
        items=tuple(_in_zone(i, time_zone) for i in items),
        all_day=tuple(title for _, title in sorted(all_day)),
        has_pending_proposal=pending,
    )


def _in_zone(item: ScheduleItem, time_zone: ZoneInfo) -> ScheduleItem:
    return ScheduleItem(
        item.kind,
        item.start.astimezone(time_zone),
        item.end.astimezone(time_zone),
        item.title,
        item.task_id,
        item.status,
    )


# --- Deadline risk, counted together (#1-#4) ---


@dataclass(frozen=True)
class OpenSubtask:
    subtask_id: UUID
    estimated_minutes: int


def needed_minutes(
    estimated_minutes: int,
    open_subtasks: Sequence[OpenSubtask],
    has_subtasks: bool,
    sessions: Sequence[FocusSessionRecord],
    k_bias: Decimal | None,
    now: datetime,
) -> int:
    """Work minutes a task still needs, like FR-10 (K is None on the Free plan).

    A task with subtasks: the sum over its open subtasks (0 when all are closed).
    """
    if not has_subtasks:
        return minutes_to_schedule(estimated_minutes, sessions, k_bias, now)
    return sum(
        minutes_to_schedule(
            sub.estimated_minutes, sessions_of(sessions, sub.subtask_id), k_bias, now
        )
        for sub in open_subtasks
    )


def risk_free_ranges(
    hours: WorkingHours,
    now: datetime,
    window_days: int,
    fixed_tasks: Iterable[FixedTaskTime],
    events: Iterable[CalendarEvent],
    round_minutes: int,
) -> list[TimeRange]:
    """Free working time from now for the risk check.

    Busy = Fixed tasks + Google events only. Approved slots of Flexible tasks are
    NOT busy: they are time kept for the very tasks being checked (#2).
    """
    busy = busy_ranges(fixed_tasks, events, approved_slots=())
    return free_ranges(hours, now, window_days, busy, round_minutes)


def free_minutes_before(free: Sequence[TimeRange], deadline: datetime) -> int:
    """Free minutes from the start of `free` until the deadline."""
    total = 0
    for gap in free:
        if gap.start >= deadline:
            break
        total += TimeRange(gap.start, min(gap.end, deadline)).minutes
    return total


@dataclass(frozen=True)
class RiskTask:
    task_id: UUID
    deadline: datetime
    needed_minutes: int


@dataclass(frozen=True)
class DeadlineRisk:
    """The result for one task inside the window, in the order it was counted."""

    task_id: UUID
    deadline: datetime
    needed_minutes: int
    available_minutes: int  # free time before the deadline left for this task
    is_overdue: bool

    @property
    def missing_minutes(self) -> int:
        """Minutes of this task that do not fit before its deadline."""
        return max(0, self.needed_minutes - self.available_minutes)

    @property
    def is_at_risk(self) -> bool:
        """An overdue task is not at risk; it is overdue (#4)."""
        return not self.is_overdue and self.missing_minutes > 0


def deadline_risks(
    tasks: Iterable[RiskTask],
    free: Sequence[TimeRange],
    now: datetime,
    window_days: int,
) -> list[DeadlineRisk]:
    """Check deadlines together, in FR-10 order (#1).

    Overdue first, then nearest deadline; tie: more minutes needed first.
    Task i is at risk when the minutes needed by it and all tasks before it are
    more than the free time from now to its deadline. Deadlines after the
    window are safe and not counted (#3). Tasks needing 0 minutes are skipped.
    """
    window_end = now + timedelta(days=window_days)
    counted = sorted(
        (t for t in tasks if t.deadline <= window_end and t.needed_minutes > 0),
        key=lambda t: (
            t.deadline >= now,
            t.deadline,
            -t.needed_minutes,
            str(t.task_id),
        ),
    )
    result = []
    used = 0  # minutes needed by the tasks counted so far
    for task in counted:
        overdue = task.deadline < now
        free_before = 0 if overdue else free_minutes_before(free, task.deadline)
        result.append(
            DeadlineRisk(
                task.task_id,
                task.deadline,
                task.needed_minutes,
                available_minutes=max(0, free_before - used),
                is_overdue=overdue,
            )
        )
        used += task.needed_minutes
    return result


def at_risk_tasks(risks: Iterable[DeadlineRisk], limit: int) -> list[DeadlineRisk]:
    """Tasks at risk, nearest deadline first, at most `limit`."""
    return sorted((r for r in risks if r.is_at_risk), key=lambda r: r.deadline)[:limit]


# --- Top tasks and reason codes (#5-#8) ---


class ReasonCode(StrEnum):
    """Why a task is in the top list. Values are sent in the API (icon / colour)."""

    OVERDUE = "overdue"
    AT_RISK = "at_risk"
    IN_PROGRESS = "in_progress"
    DEADLINE_SOON = "deadline_soon"
    BIAS = "bias"


MAX_REASONS = 2


@dataclass(frozen=True)
class Reason:
    """A reason code with the numbers its sentence needs (text: briefing_text.py)."""

    code: ReasonCode
    days: int = 0  # overdue: days late · deadline_soon: days left (0 = today)
    needed_minutes: int = 0  # at_risk, in_progress
    available_minutes: int = 0  # at_risk
    extra_percent: int = 0  # bias
    domain_name: str = ""  # bias


@dataclass(frozen=True)
class Candidate:
    """An open Flexible task that may go in the top list (plan §2 step 3)."""

    task_id: UUID
    title: str
    project_name: str
    domain_name: str
    deadline: datetime
    needed_minutes: int  # from needed_minutes(); K not applied on the Free plan
    in_progress: bool  # Status InProgress, or has a focus session
    k_bias: Decimal | None  # the user's K for the domain; None: no profile yet
    sample_count: int


@dataclass(frozen=True)
class TopTask:
    candidate: Candidate
    reasons: tuple[Reason, ...]  # 1-2, most important first


@dataclass(frozen=True)
class BiasRule:
    """When the "you usually need X% more" reason is shown (#7)."""

    min_k: Decimal
    min_samples: int


def bias_reason(candidate: Candidate, rule: BiasRule) -> Reason | None:
    """Only for a learned K: K >= min_k and enough samples. Also on the Free plan (#8)."""
    k = candidate.k_bias
    if k is None or k < rule.min_k or candidate.sample_count < rule.min_samples:
        return None
    return Reason(
        ReasonCode.BIAS,
        extra_percent=extra_percent(k),
        domain_name=candidate.domain_name,
    )


def days_between(earlier: datetime, later: datetime, time_zone: ZoneInfo) -> int:
    """Calendar days on the user's clock (today → tomorrow = 1)."""
    return (local_date(later, time_zone) - local_date(earlier, time_zone)).days


def task_reasons(
    candidate: Candidate,
    risk: DeadlineRisk | None,
    now: datetime,
    time_zone: ZoneInfo,
    rule: BiasRule,
) -> tuple[Reason, ...]:
    """All reasons that apply, in priority order (#6), cut to MAX_REASONS."""
    reasons: list[Reason] = []
    overdue = candidate.deadline < now
    if overdue:
        days = days_between(candidate.deadline, now, time_zone)
        reasons.append(Reason(ReasonCode.OVERDUE, days=days))
    if risk is not None and risk.is_at_risk:
        reasons.append(
            Reason(
                ReasonCode.AT_RISK,
                needed_minutes=risk.needed_minutes,
                available_minutes=risk.available_minutes,
            )
        )
    if candidate.in_progress:
        reasons.append(
            Reason(ReasonCode.IN_PROGRESS, needed_minutes=candidate.needed_minutes)
        )
    if not overdue:
        days = days_between(now, candidate.deadline, time_zone)
        reasons.append(Reason(ReasonCode.DEADLINE_SOON, days=days))
    if bias := bias_reason(candidate, rule):
        reasons.append(bias)
    return tuple(reasons[:MAX_REASONS])


def select_top(
    candidates: Iterable[Candidate],
    risks: Iterable[DeadlineRisk],
    now: datetime,
    time_zone: ZoneInfo,
    count: int,
    rule: BiasRule,
) -> list[TopTask]:
    """Top tasks (#5): overdue → at risk → in progress → nearest deadline.

    Inside each group: nearest deadline; tie: more minutes needed first.
    Tasks that need 0 minutes (all subtasks closed) are left out.
    """
    by_task = {r.task_id: r for r in risks}

    def group(c: Candidate) -> int:
        risk = by_task.get(c.task_id)
        if c.deadline < now:
            return 0
        if risk is not None and risk.is_at_risk:
            return 1
        return 2 if c.in_progress else 3

    ranked = sorted(
        (c for c in candidates if c.needed_minutes > 0),
        key=lambda c: (group(c), c.deadline, -c.needed_minutes, str(c.task_id)),
    )
    return [
        TopTask(c, task_reasons(c, by_task.get(c.task_id), now, time_zone, rule))
        for c in ranked[:count]
    ]
