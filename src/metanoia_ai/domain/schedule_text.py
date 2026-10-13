"""FR-10 texts written by code, not by the LLM (vi / en).

- The proposal summary and warning messages: fixed sentences with real numbers.
- The "facts" of each slot: what the LLM may use to write a one-line reason.

Never show internal names (K_bias, T_proposed) to the user.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from metanoia_ai.domain.models import (
    Language,
    PlannedSlot,
    ScheduleWarning,
    WarningType,
)

MAX_TITLE_LENGTH = 100


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


def slot_facts(
    slot: PlannedSlot,
    title: str,
    deadline: datetime,
    user_k: Decimal | None,
    productive_hours: frozenset[int],
    time_zone: ZoneInfo,
) -> SlotFacts:
    """Facts for one slot.

    `user_k` is the user's K for this work domain, also on the Free plan
    (then it is only a hint and `slot.k_bias_used` is None).
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
    )


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
