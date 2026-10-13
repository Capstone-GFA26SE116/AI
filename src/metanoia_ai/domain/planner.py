"""FR-10 planner: place schedule units into free time. Pure code, no LLM.

Order: nearest deadline first → longer task first → task_id (stable result).
Place: earliest day with room; inside that day prefer a top productive hour,
otherwise the earliest free time. Each slot is followed by the user's buffer,
and the next slot starts on a round time.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from itertools import groupby
from uuid import UUID
from zoneinfo import ZoneInfo

from metanoia_ai.domain.free_time import round_up
from metanoia_ai.domain.models import (
    PlannedSlot,
    ScheduleUnit,
    ScheduleWarning,
    TimeRange,
    WorkingHours,
)


@dataclass(frozen=True)
class PlanResult:
    """What the planner made: slots in time order, and warnings."""

    slots: tuple[PlannedSlot, ...]
    warnings: tuple[ScheduleWarning, ...]


def top_productive_hours(scores: Mapping[int, Decimal], count: int) -> frozenset[int]:
    """The `count` hours of the day with the highest score (tie: earlier hour)."""
    ranked = sorted(scores, key=lambda hour: (-scores[hour], hour))
    return frozenset(ranked[:count])


def order_units(units: Iterable[ScheduleUnit]) -> list[ScheduleUnit]:
    """Nearest deadline first; tie: longer task first; tie: task_id.

    Units of one task (its subtasks) stay together, in OrderIndex order.
    """
    units = list(units)
    task_minutes: dict[UUID, int] = {}
    for unit in units:
        task_minutes[unit.task_id] = task_minutes.get(unit.task_id, 0) + unit.minutes
    return sorted(
        units,
        key=lambda u: (
            u.deadline,
            -task_minutes[u.task_id],
            str(u.task_id),
            u.order_index or 0,
        ),
    )


def plan_schedule(
    units: Iterable[ScheduleUnit],
    free: Sequence[TimeRange],
    hours: WorkingHours,
    productive_hours: frozenset[int],
    round_minutes: int,
) -> PlanResult:
    """Place each unit, in order, into the free time that is left."""
    gaps = sorted(free, key=lambda g: g.start)
    slots = []
    for unit in order_units(units):
        start = find_start(unit, gaps, hours.time_zone, productive_hours)
        if start is None:
            continue
        slot = PlannedSlot(
            task_id=unit.task_id,
            subtask_id=unit.subtask_id,
            start=start,
            end=start + timedelta(minutes=unit.minutes),
            k_bias_used=unit.k_bias_used,
        )
        slots.append(slot)
        gaps = take(gaps, slot, hours, round_minutes)
    return PlanResult(slots=tuple(sorted(slots, key=lambda s: s.start)), warnings=())


def find_start(
    unit: ScheduleUnit,
    gaps: Sequence[TimeRange],
    time_zone: ZoneInfo,
    productive_hours: frozenset[int],
) -> datetime | None:
    """Start time for the whole unit, or None if it fits nowhere before its deadline."""
    length = timedelta(minutes=unit.minutes)

    def fits(start: datetime, gap: TimeRange) -> bool:
        return start + length <= min(gap.end, unit.deadline)

    for _, day_gaps in groupby(gaps, key=lambda g: local_date(g.start, time_zone)):
        fitting = [g for g in day_gaps if fits(g.start, g)]
        if not fitting:
            continue
        productive = [
            start
            for gap in fitting
            for start in productive_starts(gap, time_zone, productive_hours)
            if fits(start, gap)
        ]
        return min(productive) if productive else fitting[0].start
    return None


def productive_starts(
    gap: TimeRange, time_zone: ZoneInfo, productive_hours: frozenset[int]
) -> list[datetime]:
    """Earliest start inside each productive hour that overlaps the gap."""
    day = local_date(gap.start, time_zone)
    starts = []
    for hour in sorted(productive_hours):
        hour_start = datetime(day.year, day.month, day.day, hour, tzinfo=time_zone)
        start = max(gap.start, hour_start)
        if start < min(gap.end, hour_start + timedelta(hours=1)):
            starts.append(start)
    return starts


def take(
    gaps: Sequence[TimeRange],
    slot: PlannedSlot,
    hours: WorkingHours,
    round_minutes: int,
) -> list[TimeRange]:
    """Remove the slot and its buffer from the free time.

    Before the slot, the buffer is kept free so an earlier slot still gets its break.
    After the slot, the next free time starts on a round time after the buffer.
    """
    buffer = timedelta(minutes=hours.buffer_minutes)
    result = []
    for gap in gaps:
        if slot.end <= gap.start or slot.start >= gap.end:
            result.append(gap)
            continue
        before_end = slot.start - buffer
        if before_end > gap.start:
            result.append(TimeRange(gap.start, before_end))
        after_start = round_up(slot.end + buffer, round_minutes, hours.time_zone)
        if after_start < gap.end:
            result.append(TimeRange(after_start, gap.end))
    return result


def local_date(moment: datetime, time_zone: ZoneInfo) -> date:
    return moment.astimezone(time_zone).date()
