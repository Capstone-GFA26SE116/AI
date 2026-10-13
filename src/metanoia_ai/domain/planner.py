"""FR-10 planner: place schedule units into free time. Pure code, no LLM.

Order: nearest deadline first → longer task first → task_id (stable result).
Place: earliest day with room for the whole unit; inside that day prefer a top
productive hour, otherwise the earliest free time. If the whole unit fits nowhere,
split it over free time in time order (each part ≥ the minimum chunk).
Each slot is followed by the user's buffer; the next slot starts on a round time.
Units of one task (its subtasks) are placed one after another, in OrderIndex order.

Cannot finish before the deadline → nothing is placed for that task, and a warning
is added. Deadline after the window → the part that fits is placed, no warning.
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
    WarningType,
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
    task_minutes = total_minutes_by_task(units)
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
    *,
    now: datetime,
    window_end: datetime,
    round_minutes: int,
    min_chunk_minutes: int,
) -> PlanResult:
    """Place each task (all its units), in order, into the free time that is left.

    A task is all-or-nothing when its deadline is inside the window: if one of
    its units cannot be placed, none are, and a warning is added.
    """
    gaps = sorted(free, key=lambda g: g.start)
    slots: list[PlannedSlot] = []
    warnings: list[ScheduleWarning] = []

    for task_id, task_units in groupby(order_units(units), key=lambda u: u.task_id):
        task_units = list(task_units)
        deadline = task_units[0].deadline
        if deadline <= now:
            warnings.append(ScheduleWarning(WarningType.OVERDUE, task_id))
            continue

        task_slots, gaps_left, complete = place_task(
            task_units,
            gaps,
            hours,
            productive_hours,
            now=now,
            round_minutes=round_minutes,
            min_chunk_minutes=min_chunk_minutes,
        )
        if not complete and deadline <= window_end:
            warnings.append(
                ScheduleWarning(
                    WarningType.NOT_ENOUGH_TIME,
                    task_id,
                    needed_minutes=sum(u.minutes for u in task_units),
                    free_minutes=free_minutes_before(gaps, deadline),
                )
            )
            continue
        slots += task_slots
        gaps = gaps_left

    return PlanResult(
        slots=tuple(sorted(slots, key=lambda s: s.start)), warnings=tuple(warnings)
    )


def place_task(
    task_units: Sequence[ScheduleUnit],
    gaps: Sequence[TimeRange],
    hours: WorkingHours,
    productive_hours: frozenset[int],
    *,
    now: datetime,
    round_minutes: int,
    min_chunk_minutes: int,
) -> tuple[list[PlannedSlot], list[TimeRange], bool]:
    """Try to place every unit of one task, one after another.

    Returns the slots, the free time left after them, and whether all minutes fit.
    Works on a copy: the caller decides whether to keep the result.
    """
    gaps = list(gaps)
    slots: list[PlannedSlot] = []
    earliest = now  # a subtask never starts before the previous one ends
    for unit in task_units:
        usable = after(gaps, earliest)
        pieces = whole(unit, usable, hours.time_zone, productive_hours)
        missing = 0
        if pieces is None:
            pieces, missing = split(unit, usable, min_chunk_minutes)
        for index, piece in enumerate(pieces, start=1):
            slot = PlannedSlot(
                task_id=unit.task_id,
                subtask_id=unit.subtask_id,
                start=piece.start,
                end=piece.end,
                k_bias_used=unit.k_bias_used,
                part_index=index,
                part_count=len(pieces),
            )
            slots.append(slot)
            gaps = take(gaps, slot, hours, round_minutes)
        if missing:
            return slots, gaps, False
        earliest = pieces[-1].end
    return slots, gaps, True


def whole(
    unit: ScheduleUnit,
    gaps: Sequence[TimeRange],
    time_zone: ZoneInfo,
    productive_hours: frozenset[int],
) -> list[TimeRange] | None:
    """The unit in one piece, or None if it fits nowhere before its deadline."""
    start = find_start(unit, gaps, time_zone, productive_hours)
    if start is None:
        return None
    return [TimeRange(start, start + timedelta(minutes=unit.minutes))]


def split(
    unit: ScheduleUnit, gaps: Sequence[TimeRange], min_chunk_minutes: int
) -> tuple[list[TimeRange], int]:
    """Fill free time in time order. Returns the pieces and the minutes still missing.

    Every piece is at least `min_chunk_minutes`. When the last piece would be
    shorter, the piece before it is made shorter so the last one is exactly
    `min_chunk_minutes`.
    """
    pieces = []
    remaining = unit.minutes
    for gap in gaps:
        end = min(gap.end, unit.deadline)
        room = minutes_between(gap.start, end)
        if room < min_chunk_minutes:
            continue
        length = min(room, remaining)
        if 0 < remaining - length < min_chunk_minutes:
            length = remaining - min_chunk_minutes
            if length < min_chunk_minutes:
                continue
        pieces.append(TimeRange(gap.start, gap.start + timedelta(minutes=length)))
        remaining -= length
        if remaining == 0:
            break
    return pieces, remaining


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


def after(gaps: Sequence[TimeRange], earliest: datetime) -> list[TimeRange]:
    """The free time from `earliest` on."""
    return [TimeRange(max(g.start, earliest), g.end) for g in gaps if g.end > earliest]


def free_minutes_before(gaps: Sequence[TimeRange], deadline: datetime) -> int:
    """All free minutes before the deadline (shown in the not-enough-time warning)."""
    return sum(minutes_between(g.start, min(g.end, deadline)) for g in gaps)


def total_minutes_by_task(units: Iterable[ScheduleUnit]) -> dict[UUID, int]:
    totals: dict[UUID, int] = {}
    for unit in units:
        totals[unit.task_id] = totals.get(unit.task_id, 0) + unit.minutes
    return totals


def minutes_between(start: datetime, end: datetime) -> int:
    """Whole minutes from start to end; 0 if end is not after start."""
    return max(0, int((end - start) // timedelta(minutes=1)))


def local_date(moment: datetime, time_zone: ZoneInfo) -> date:
    return moment.astimezone(time_zone).date()
