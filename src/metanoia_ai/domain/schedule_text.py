"""FR-10 texts written by code, not by the LLM (vi / en).

- The proposal summary and warning messages: fixed sentences with real numbers.
- The "facts" of each slot: what the LLM may use to write a short reason,
  including why the planner chose that time (plan #32).

Never show internal names (K_bias, T_proposed) to the user.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from metanoia_ai.domain.free_time import BusyBlock, round_up, working_days
from metanoia_ai.domain.models import (
    BlockedBy,
    Language,
    Placement,
    PlannedSlot,
    ScheduleUnit,
    ScheduleWarning,
    SplitReason,
    WarningType,
    WorkingHours,
)
from metanoia_ai.domain.planner import order_units

MAX_TITLE_LENGTH = 100
# Free time is searched at most this many days ahead (covers a long weekend).
SEARCH_DAYS = 8


def summary(
    task_count: int,
    placed_count: int,
    warnings: Sequence[ScheduleWarning],
    lang: Language,
) -> str:
    """One or two sentences about the whole proposal."""
    late = count(warnings, WarningType.NOT_ENOUGH_TIME)
    overdue = count(warnings, WarningType.OVERDUE)
    if lang is Language.EN:
        if task_count == 0:
            return "Nothing to schedule."
        parts = [f"Scheduled {placed_count} of {task_count} tasks."]
        if late:
            parts.append(f"{late} {plural(late, 'task')} can't meet the deadline.")
        if overdue:
            verb = "is" if overdue == 1 else "are"
            parts.append(
                f"{overdue} {plural(overdue, 'task')} {verb} past the deadline."
            )
        return " ".join(parts)

    if task_count == 0:
        return "Không có việc nào cần xếp."
    parts = [f"Xếp được {placed_count}/{task_count} task."]
    if late:
        parts.append(f"{late} task không kịp deadline.")
    if overdue:
        parts.append(f"{overdue} task đã quá hạn.")
    return " ".join(parts)


def warning_message(warning: ScheduleWarning, lang: Language) -> str:
    """The text shown for one warning."""
    match warning.type, lang:
        case WarningType.NOT_ENOUGH_TIME, Language.EN:
            return (
                f"Needs {warning.needed_minutes} minutes, but only "
                f"{warning.free_minutes} minutes are free before the deadline."
            )
        case WarningType.NOT_ENOUGH_TIME, _:
            return (
                f"Cần {warning.needed_minutes} phút, nhưng trước deadline "
                f"chỉ còn trống {warning.free_minutes} phút."
            )
        case WarningType.OVERDUE, Language.EN:
            return "This task is past its deadline."
        case WarningType.OVERDUE, _:
            return "Task này đã quá hạn."
        case WarningType.CALENDAR_NOT_SYNCED, Language.EN:
            return (
                "Your Google Calendar is not synced, so some events may be "
                "missing. Please reconnect it."
            )
        case _:
            return (
                "Lịch Google chưa được đồng bộ nên có thể thiếu sự kiện. "
                "Hãy kết nối lại."
            )


@dataclass(frozen=True)
class SlotFacts:
    """Facts about one slot, sent to the LLM to write its reason."""

    task_title: str  # at most 100 characters
    days_to_deadline: int  # calendar days from the slot's day to the deadline's day
    in_productive_hour: bool
    extra_percent: int  # how much more time than the user's estimate (K − 1)
    extra_applied: (
        bool  # False on the Free plan: only a hint ("you usually need X% more")
    )
    part_index: int
    part_count: int
    # Why this time (plan #32)
    placement: Placement
    earlier_blocked_by: BlockedBy
    priority_rank: int
    priority_total: int
    estimate_minutes: int  # the user's own estimate of what is left
    planned_minutes: int  # all minutes kept for the unit (every part)
    spare_hours: int  # from the end of the task's last slot to the deadline
    split_reason: SplitReason | None


def slot_facts(
    slot: PlannedSlot,
    title: str,
    deadline: datetime,
    user_k: Decimal | None,
    productive_hours: frozenset[int],
    time_zone: ZoneInfo,
    *,
    blocked_by: BlockedBy,
    priority: tuple[int, int],
    estimate_minutes: int,
    planned_minutes: int,
    task_end: datetime,
) -> SlotFacts:
    """Facts for one slot.

    `user_k` is the user's K for this work domain, also on the Free plan
    (then it is only a hint and `slot.k_bias_used` is None).
    `priority` is (rank, total); `task_end` is when the task's last slot ends.
    """
    start = slot.start.astimezone(time_zone)
    return SlotFacts(
        task_title=short_title(title),
        days_to_deadline=(deadline.astimezone(time_zone).date() - start.date()).days,
        in_productive_hour=start.hour in productive_hours,
        extra_percent=extra_percent(user_k),
        extra_applied=slot.k_bias_used is not None,
        part_index=slot.part_index,
        part_count=slot.part_count,
        placement=slot.placement,
        earlier_blocked_by=blocked_by,
        priority_rank=priority[0],
        priority_total=priority[1],
        estimate_minutes=estimate_minutes,
        planned_minutes=planned_minutes,
        spare_hours=spare_hours(task_end, deadline),
        split_reason=split_reason(slot),
    )


def earlier_blocked_by(
    slot: PlannedSlot,
    earliest: datetime,
    blocks: Sequence[BusyBlock],
    hours: WorkingHours,
    round_minutes: int,
) -> BlockedBy:
    """What keeps the slot from starting earlier.

    `earliest`: when the slot could start at the soonest (now, or the end of the
    previous part / subtask of the same task). `blocks`: busy time, including the
    slots of other tasks (kind OTHER_TASK). Rules (plan §2):
    - waited for a focus hour → NONE (the earlier time was free);
    - starts at the first work time after `earliest` → NONE, or OUTSIDE_WORK_HOURS
      when that is on a later day (the work day was over);
    - otherwise → the kind of the busy block that ends last before the slot.
    """
    if slot.placement is Placement.WAITED_FOR_FOCUS_HOUR:
        return BlockedBy.NONE
    first = first_work_time(earliest, hours, round_minutes)
    zone = hours.time_zone
    if slot.start <= first:
        if first.astimezone(zone).date() > earliest.astimezone(zone).date():
            return BlockedBy.OUTSIDE_WORK_HOURS
        return BlockedBy.NONE
    before = [b for b in blocks if b.time.start < slot.start and b.time.end > first]
    if before:
        last = max(before, key=lambda b: b.time.end)
        return BlockedBy(last.kind.value)
    if slot.start.astimezone(zone).time() == hours.day_start:
        return BlockedBy.OUTSIDE_WORK_HOURS
    return BlockedBy.NONE


def first_work_time(
    moment: datetime, hours: WorkingHours, round_minutes: int
) -> datetime:
    """The first round time at or after `moment` inside working hours."""
    zone = hours.time_zone
    first_day = moment.astimezone(zone).date()
    last_day = first_day + timedelta(days=SEARCH_DAYS)
    for day in working_days(hours, first_day, last_day):
        start = round_up(max(day.start, moment), round_minutes, zone)
        if start < day.end:
            return start
    return round_up(moment, round_minutes, zone)


def priority_ranks(units: Iterable[ScheduleUnit]) -> dict[UUID, int]:
    """Each task's place in the planner's order (1 = placed first)."""
    ranks: dict[UUID, int] = {}
    for unit in order_units(units):
        ranks.setdefault(unit.task_id, len(ranks) + 1)
    return ranks


def spare_hours(task_end: datetime, deadline: datetime) -> int:
    """Whole hours between the end of the task's last slot and the deadline."""
    return max(0, int((deadline - task_end) // timedelta(hours=1)))


def split_reason(slot: PlannedSlot) -> SplitReason | None:
    """The planner splits a unit only when no free time is long enough for it."""
    return SplitReason.NO_LONG_GAP if slot.part_count > 1 else None


def extra_percent(k: Decimal | None) -> int:
    """K 1.3 → 30 (%). K below 1 gives a negative number. No K → 0."""
    if k is None:
        return 0
    return int(((k - 1) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def short_title(title: str) -> str:
    """Cut the task title to at most 100 characters."""
    title = title.strip()
    if len(title) <= MAX_TITLE_LENGTH:
        return title
    return title[: MAX_TITLE_LENGTH - 1] + "…"


def count(warnings: Sequence[ScheduleWarning], kind: WarningType) -> int:
    return sum(1 for w in warnings if w.type is kind)


def plural(n: int, word: str) -> str:
    return word if n == 1 else word + "s"
