"""FR-10: free time = working hours in the window, minus busy time."""

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from metanoia_ai.domain.models import BusyKind, TimeRange, WorkingHours

SATURDAY = 5  # date.weekday(): Monday = 0 ... Sunday = 6


@dataclass(frozen=True)
class FixedTaskTime:
    """A Fixed task (e.g. a meeting) as busy time."""

    start: datetime
    end: datetime
    is_skipped: bool  # Status = 'Skipped': the meeting was cancelled


@dataclass(frozen=True)
class CalendarEvent:
    """An event synced from Google Calendar."""

    start: datetime
    end: datetime
    is_all_day: bool
    is_cancelled: bool
    is_created_by_metanoia: bool  # already in ScheduleSlot; would count twice


@dataclass(frozen=True)
class BusyBlock:
    """One busy time and what it is (to explain why a slot is not earlier)."""

    time: TimeRange
    kind: BusyKind


def round_up(moment: datetime, step_minutes: int, time_zone: ZoneInfo) -> datetime:
    """Round up to the next multiple of `step_minutes` on the user's clock (09:07 → 09:10)."""
    local = moment.astimezone(time_zone)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    step = timedelta(minutes=step_minutes)
    return midnight + math.ceil((local - midnight) / step) * step


def running_timer_busy(now: datetime, remaining_minutes: int) -> TimeRange:
    """A task with a running timer blocks the time from now for its remaining part."""
    return TimeRange(now, now + timedelta(minutes=remaining_minutes))


def busy_blocks(
    fixed_tasks: Iterable[FixedTaskTime],
    events: Iterable[CalendarEvent],
    approved_slots: Iterable[TimeRange],
    running_timers: Iterable[TimeRange] = (),
) -> list[BusyBlock]:
    """All busy time with its kind, sorted by start (not merged).

    Skipped: Fixed tasks with status Skipped; all-day, cancelled and
    Metanoia-made Google events; zero-length events.
    """
    blocks = [
        BusyBlock(TimeRange(t.start, t.end), BusyKind.MEETING)
        for t in fixed_tasks
        if not t.is_skipped
    ]
    blocks += [
        BusyBlock(TimeRange(e.start, e.end), BusyKind.CALENDAR_EVENT)
        for e in events
        if not (e.is_all_day or e.is_cancelled or e.is_created_by_metanoia)
        and e.end > e.start
    ]
    blocks += [BusyBlock(r, BusyKind.APPROVED_SLOT) for r in approved_slots]
    blocks += [BusyBlock(r, BusyKind.OTHER_TASK) for r in running_timers]
    return sorted(blocks, key=lambda b: b.time.start)


def busy_ranges(
    fixed_tasks: Iterable[FixedTaskTime],
    events: Iterable[CalendarEvent],
    approved_slots: Iterable[TimeRange],
    running_timers: Iterable[TimeRange] = (),
) -> list[TimeRange]:
    """All busy time, merged and sorted (the same rules as `busy_blocks`)."""
    blocks = busy_blocks(fixed_tasks, events, approved_slots, running_timers)
    return merge(b.time for b in blocks)


def merge(ranges: Iterable[TimeRange]) -> list[TimeRange]:
    """Sort and join ranges that overlap or touch."""
    merged: list[TimeRange] = []
    for current in sorted(ranges, key=lambda r: r.start):
        if merged and current.start <= merged[-1].end:
            last = merged[-1]
            merged[-1] = TimeRange(last.start, max(last.end, current.end))
        else:
            merged.append(current)
    return merged


def working_days(hours: WorkingHours, first: date, last: date) -> list[TimeRange]:
    """Working hours of each day from `first` to `last`, skipping weekends if needed."""
    days = []
    day = first
    while day <= last:
        if hours.works_on_weekend or day.weekday() < SATURDAY:
            start = datetime.combine(day, hours.day_start, tzinfo=hours.time_zone)
            end = datetime.combine(day, hours.day_end, tzinfo=hours.time_zone)
            days.append(TimeRange(start, end))
        day += timedelta(days=1)
    return days


def subtract(free: TimeRange, busy: Sequence[TimeRange]) -> list[TimeRange]:
    """Parts of `free` not covered by `busy` (busy must be merged and sorted)."""
    gaps = []
    cursor = free.start
    for block in busy:
        if block.end <= cursor or block.start >= free.end:
            continue
        if block.start > cursor:
            gaps.append(TimeRange(cursor, block.start))
        cursor = max(cursor, block.end)
    if cursor < free.end:
        gaps.append(TimeRange(cursor, free.end))
    return gaps


def free_ranges(
    hours: WorkingHours,
    now: datetime,
    window_days: int,
    busy: Sequence[TimeRange],
    round_minutes: int,
) -> list[TimeRange]:
    """Free time from now until now + window_days, inside working hours.

    Each free range starts on a round time (multiple of `round_minutes`, rounded up).
    """
    window_end = now + timedelta(days=window_days)
    first = now.astimezone(hours.time_zone).date()
    last = window_end.astimezone(hours.time_zone).date()
    merged = merge(busy)

    result = []
    for day in working_days(hours, first, last):
        start = max(day.start, now)
        end = min(day.end, window_end)
        if end <= start:
            continue
        for gap in subtract(TimeRange(start, end), merged):
            rounded = round_up(gap.start, round_minutes, hours.time_zone)
            if rounded < gap.end:
                result.append(TimeRange(rounded, gap.end))
    return result
