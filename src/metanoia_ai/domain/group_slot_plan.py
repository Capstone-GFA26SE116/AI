"""FR-25: which parts of a Work Group task the caller still has to place, and the texts.

Pure rules, no DB. The placing itself is the FR-10 planner.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from metanoia_ai.domain.assignee_ranking import format_moment
from metanoia_ai.domain.models import Language, ScheduleUnit, WorkingHours

MAX_REASON_CHARS = 500  # ScheduleSlot.Explanation limit in the Backend


@dataclass(frozen=True)
class GroupPart:
    """The task itself (no subtasks) or one of its subtasks."""

    subtask_id: UUID | None
    title: str  # "Task: Subtask" for a subtask
    assignee_id: UUID | None  # already resolved: subtask's own, else the task's
    is_open: bool  # ToDo / InProgress
    estimated_minutes: int | None
    # The caller's group slots for this part, on the calendar or done.
    booked_minutes: int
    order_index: int | None = None


@dataclass(frozen=True)
class PartsToPlace:
    units: tuple[ScheduleUnit, ...]
    no_estimate: tuple[GroupPart, ...]  # mine and open, but nothing to size them with
    mine: int  # open parts given to the caller


def is_given_to(parts: Sequence[GroupPart], caller_id: UUID) -> bool:
    """At least one open part is the caller's."""
    return any(p.is_open and p.assignee_id == caller_id for p in parts)


def due_at(
    task_deadline: datetime | None,
    group_deadline: date | None,
    hours: WorkingHours,
) -> datetime | None:
    """The task's deadline, else the end of the working day of the group's (plan #1)."""
    if task_deadline is not None:
        return task_deadline
    if group_deadline is None:
        return None
    return datetime.combine(group_deadline, hours.day_end, tzinfo=hours.time_zone)


def parts_to_place(
    task_id: UUID,
    parts: Sequence[GroupPart],
    caller_id: UUID,
    deadline: datetime,
) -> PartsToPlace:
    """Open parts given to the caller, minus the minutes they already booked (AC2)."""
    mine = [p for p in parts if p.is_open and p.assignee_id == caller_id]
    units: list[ScheduleUnit] = []
    no_estimate: list[GroupPart] = []
    for part in sorted(mine, key=lambda p: (p.order_index or 0, str(p.subtask_id))):
        if not part.estimated_minutes:
            no_estimate.append(part)
            continue
        left = part.estimated_minutes - part.booked_minutes
        if left <= 0:
            continue  # already on the calendar
        units.append(
            ScheduleUnit(
                task_id=task_id,
                subtask_id=part.subtask_id,
                title=part.title,
                deadline=deadline,
                minutes=left,
                k_bias_used=None,  # no Domain on group work: no K (plan #3)
                order_index=part.order_index,
                estimate_minutes=left,
            )
        )
    return PartsToPlace(tuple(units), tuple(no_estimate), len(mine))


def slot_reason(deadline: datetime | None, zone: ZoneInfo, language: Language) -> str:
    """One plain line per suggested slot (AC4)."""
    vi = language is Language.VI
    if deadline is None:
        text = (
            "Khoảng trống sớm nhất trong lịch của bạn."
            if vi
            else "The earliest free time on your calendar."
        )
    else:
        when = format_moment(deadline, zone, language)
        text = (
            f"Khoảng trống trong lịch của bạn, xong trước hạn {when}."
            if vi
            else f"Free time on your calendar, done before the deadline {when}."
        )
    return text[:MAX_REASON_CHARS]


def summary_text(title: str, slot_count: int, language: Language) -> str:
    vi = language is Language.VI
    if slot_count == 0:
        return (
            f"Chưa tìm được giờ trống cho {title}."
            if vi
            else f"No free time found for {title}."
        )
    return (
        f"Mình gợi ý {slot_count} khung giờ cho {title}."
        if vi
        else f"{slot_count} time slot(s) suggested for {title}."
    )
