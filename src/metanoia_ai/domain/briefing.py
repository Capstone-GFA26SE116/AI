"""FR-12 daily "Today" briefing: pure rules, no DB, no LLM.

All times given back are in the user's time zone.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID
from zoneinfo import ZoneInfo

from metanoia_ai.domain.models import TimeRange
from metanoia_ai.domain.planner import local_date


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
