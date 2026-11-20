"""FR-10 planner: place schedule units into free time. Pure code, no LLM.

Order: nearest deadline first → longer task first → task_id (stable result).
Place: earliest day with room for the whole unit; inside that day prefer a top
productive hour, otherwise the earliest free time. If the whole unit fits nowhere,
split it over free time in time order (each part ≥ the minimum chunk).
Each slot is followed by the user's buffer; the next slot starts on a round time.
Units of one task (its subtasks) are placed one after another, in OrderIndex order.

Cannot finish before the deadline → nothing is placed for that task, and a warning
is added. Deadline after the window → the part that fits is placed, no warning.

FR-10c (both off by default, turned on by the use case from config):
- max_block_minutes: a unit longer than this is cut into even blocks first.
- spread: each block goes to the day with the least work so far (the deadline
  day last), and two back-to-back slots avoid the same task, then the same
  project, then the same kind of work. Inside the chosen day the old rules apply.
"""

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from itertools import groupby
from uuid import UUID
from zoneinfo import ZoneInfo

from metanoia_ai.domain.free_time import round_up
from metanoia_ai.domain.models import (
    Placement,
    PlannedSlot,
    ScheduleUnit,
    ScheduleWarning,
    SplitReason,
    TimeRange,
    WarningType,
    WorkingHours,
)


@dataclass(frozen=True)
class PlanResult:
    """What the planner made: slots in time order, and warnings."""

    slots: tuple[PlannedSlot, ...]
    warnings: tuple[ScheduleWarning, ...]


@dataclass
class Spread:
    """FR-10c state while planning: work minutes per day and the slots placed so far."""

    load: dict[date, int] = field(default_factory=dict)
    placed: list[tuple[PlannedSlot, ScheduleUnit]] = field(default_factory=list)
    back_to_back_minutes: int = 0  # closer than this = no real break between two slots

    def copy(self) -> "Spread":
        return Spread(dict(self.load), list(self.placed), self.back_to_back_minutes)

    def add(self, slot: PlannedSlot, unit: ScheduleUnit, time_zone: ZoneInfo) -> None:
        day = local_date(slot.start, time_zone)
        self.load[day] = self.load.get(day, 0) + minutes_between(slot.start, slot.end)
        self.placed.append((slot, unit))

    def clash(self, start: datetime, end: datetime, unit: ScheduleUnit) -> int:
        """How bad it is to put `unit` here next to the slots already placed.

        3 = right next to the same task, 2 = same project, 1 = same kind of work, 0 = fine.
        """
        gap = timedelta(minutes=self.back_to_back_minutes)
        worst = 0
        for slot, other in self.placed:
            touching = timedelta(0) <= start - slot.end <= gap or (
                timedelta(0) <= slot.start - end <= gap
            )
            if not touching:
                continue
            if other.task_id == unit.task_id:
                worst = max(worst, 3)
            elif unit.project_id and other.project_id == unit.project_id:
                worst = max(worst, 2)
            elif unit.domain_id and other.domain_id == unit.domain_id:
                worst = max(worst, 1)
        return worst


def block_sizes(minutes: int, max_block: int | None, min_chunk: int) -> list[int]:
    """FR-10c: cut long work into even blocks of at most `max_block` minutes.

    300 → [150, 150]; 400 → [134, 133, 133]. Off (None / 0) or short enough → [minutes].
    """
    if not max_block or minutes <= max_block:
        return [minutes]
    count = math.ceil(minutes / max_block)
    while count > 1 and minutes // count < min_chunk:
        count -= 1
    base, extra = divmod(minutes, count)
    return [base + 1] * extra + [base] * (count - extra)


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


def interleave_projects(
    units: Sequence[ScheduleUnit], time_zone: ZoneInfo
) -> list[ScheduleUnit]:
    """FR-10c: among tasks due the same day, alternate projects (then kinds of work),
    so the day switches between jobs: A, B, A instead of A, A, B.

    Input is in `order_units` order; tasks due on an earlier day stay first and
    the units of one task stay together.
    """
    tasks = [list(group) for _, group in groupby(units, key=lambda u: u.task_id)]
    result: list[ScheduleUnit] = []
    previous: ScheduleUnit | None = None  # carried over: the last task placed
    for _, same_day in groupby(
        tasks, key=lambda t: local_date(t[0].deadline, time_zone)
    ):
        waiting = list(same_day)
        while waiting:
            chosen = waiting[0]
            if previous is not None:
                chosen = next(
                    (t for t in waiting if differs(t[0], previous, "project_id")),
                    next(
                        (t for t in waiting if differs(t[0], previous, "domain_id")),
                        waiting[0],
                    ),
                )
            waiting.remove(chosen)
            result += chosen
            previous = chosen[0]
    return result


def differs(unit: ScheduleUnit, other: ScheduleUnit, name: str) -> bool:
    """True when both have the value and it is not the same."""
    mine, theirs = getattr(unit, name), getattr(other, name)
    return mine is not None and theirs is not None and mine != theirs


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
    spread: bool = False,
    max_block_minutes: int | None = None,
    existing_load: Mapping[date, int] | None = None,
) -> PlanResult:
    """Place each task (all its units), in order, into the free time that is left.

    A task is all-or-nothing when its deadline is inside the window: if one of
    its units cannot be placed, none are, and a warning is added.
    existing_load (FR-10c): approved work minutes per local day, counted when spreading.
    """
    gaps = sorted(free, key=lambda g: g.start)
    slots: list[PlannedSlot] = []
    warnings: list[ScheduleWarning] = []
    state = (
        Spread(dict(existing_load or {}), [], hours.buffer_minutes + round_minutes)
        if spread
        else None
    )

    ordered = order_units(units)
    if spread:
        ordered = interleave_projects(ordered, hours.time_zone)
    for task_id, task_units in groupby(ordered, key=lambda u: u.task_id):
        task_units = list(task_units)
        deadline = task_units[0].deadline
        if deadline <= now:
            warnings.append(ScheduleWarning(WarningType.OVERDUE, task_id))
            continue

        task_state = state.copy() if state else None
        task_slots, gaps_left, complete = place_task(
            task_units,
            gaps,
            hours,
            productive_hours,
            now=now,
            round_minutes=round_minutes,
            min_chunk_minutes=min_chunk_minutes,
            max_block_minutes=max_block_minutes,
            spread=task_state,
        )
        if not complete and (productive_hours or task_state is not None):
            # Waiting for a focus hour or spreading can leave no room for the next
            # part before the deadline. Try again: earliest free time only (FR-10c).
            task_state = state.copy() if state else None
            plain = (
                Spread(task_state.load, task_state.placed, 0) if task_state else None
            )
            task_slots, gaps_left, complete = place_task(
                task_units,
                gaps,
                hours,
                frozenset(),
                now=now,
                round_minutes=round_minutes,
                min_chunk_minutes=min_chunk_minutes,
                max_block_minutes=max_block_minutes,
            )
            if plain is not None:
                for slot in task_slots:
                    unit = next(
                        u for u in task_units if u.subtask_id == slot.subtask_id
                    )
                    plain.add(slot, unit, hours.time_zone)
                task_state = plain
        if not complete and deadline > window_end:  # FR-10e: placed later, not late
            placed = sum(minutes_between(s.start, s.end) for s in task_slots)
            warnings.append(
                ScheduleWarning(
                    WarningType.BEYOND_WINDOW,
                    task_id,
                    needed_minutes=sum(u.minutes for u in task_units) - placed,
                    day=local_date(deadline, hours.time_zone),
                )
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
        state = task_state  # keep the load only when the task was kept

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
    max_block_minutes: int | None = None,
    spread: Spread | None = None,
) -> tuple[list[PlannedSlot], list[TimeRange], bool]:
    """Try to place every unit of one task, one after another.

    Returns the slots, the free time left after them, and whether all minutes fit.
    Works on a copy: the caller decides whether to keep the result (and `spread`,
    which this updates, is the caller's copy too).
    """
    gaps = list(gaps)
    slots: list[PlannedSlot] = []
    earliest = now  # a subtask never starts before the previous one ends
    for unit in task_units:
        parts: list[tuple[TimeRange, Placement, SplitReason | None]] = []
        missing = 0
        sizes = block_sizes(unit.minutes, max_block_minutes, min_chunk_minutes)
        for size in sizes:
            block = replace(unit, minutes=size)
            usable = after(gaps, earliest)
            found = whole(block, usable, hours.time_zone, productive_hours, spread)
            if found is None:
                pieces, missing = split(block, usable, min_chunk_minutes)
                placement = Placement.EARLIEST_FREE  # parts fill free time in order
                why: SplitReason | None = SplitReason.NO_LONG_GAP
            else:
                pieces, placement = found
                why = SplitReason.LONG_WORK if len(sizes) > 1 else None  # FR-10f
            for piece in pieces:
                parts.append((piece, placement, why))
                gaps = take_range(gaps, piece, hours, round_minutes)
                if spread is not None:
                    spread.add(to_slot(unit, piece, placement), unit, hours.time_zone)
            if missing:
                break
            earliest = pieces[-1].end
        slots += [
            to_slot(unit, piece, placement, index, len(parts), why)
            for index, (piece, placement, why) in enumerate(parts, start=1)
        ]
        if missing:
            return slots, gaps, False
    return slots, gaps, True


def to_slot(
    unit: ScheduleUnit,
    piece: TimeRange,
    placement: Placement,
    part_index: int = 1,
    part_count: int = 1,
    split_reason: SplitReason | None = None,
) -> PlannedSlot:
    return PlannedSlot(
        task_id=unit.task_id,
        subtask_id=unit.subtask_id,
        start=piece.start,
        end=piece.end,
        k_bias_used=unit.k_bias_used,
        part_index=part_index,
        part_count=part_count,
        placement=placement,
        split_reason=split_reason if part_count > 1 else None,
    )


def whole(
    unit: ScheduleUnit,
    gaps: Sequence[TimeRange],
    time_zone: ZoneInfo,
    productive_hours: frozenset[int],
    spread: Spread | None = None,
) -> tuple[list[TimeRange], Placement] | None:
    """The unit in one piece and how its start was chosen, or None if it fits nowhere."""
    if spread is None:
        found = find_start(unit, gaps, time_zone, productive_hours)
    else:
        found = find_spread_start(unit, gaps, time_zone, productive_hours, spread)
    if found is None:
        return None
    start, placement = found
    return [TimeRange(start, start + timedelta(minutes=unit.minutes))], placement


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
) -> tuple[datetime, Placement] | None:
    """Start time for the whole unit, or None if it fits nowhere before its deadline.

    WAITED_FOR_FOCUS_HOUR: the unit also fit earlier that day, but a productive
    hour was chosen.
    """
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
        earliest = fitting[0].start
        if productive and min(productive) > earliest:
            return min(productive), Placement.WAITED_FOR_FOCUS_HOUR
        return earliest, Placement.EARLIEST_FREE
    return None


def find_spread_start(
    unit: ScheduleUnit,
    gaps: Sequence[TimeRange],
    time_zone: ZoneInfo,
    productive_hours: frozenset[int],
    spread: Spread,
) -> tuple[datetime, Placement] | None:
    """FR-10c: like `find_start`, but pick the day with the least work first.

    Order: not the deadline day → least work that day → no clash with the slot
    next to it → inside a focus hour → earliest. SPREAD_OUT when the old rules
    (`find_start`) would have picked another time.
    """
    length = timedelta(minutes=unit.minutes)
    deadline_day = local_date(unit.deadline, time_zone)
    candidates = []
    for gap in gaps:
        for start in {gap.start, *productive_starts(gap, time_zone, productive_hours)}:
            if start + length <= min(gap.end, unit.deadline):
                candidates.append(start)
    if not candidates:
        return None

    def rank(start: datetime) -> tuple[bool, int, int, bool, datetime]:
        day = local_date(start, time_zone)
        return (
            day == deadline_day,
            spread.load.get(day, 0),
            spread.clash(start, start + length, unit),
            start.astimezone(time_zone).hour not in productive_hours,
            start,
        )

    chosen = min(candidates, key=rank)
    old = find_start(unit, gaps, time_zone, productive_hours)
    if old is not None and old[0] == chosen:
        return old
    same_day = old is not None and local_date(old[0], time_zone) == local_date(
        chosen, time_zone
    )
    in_focus_hour = chosen.astimezone(time_zone).hour in productive_hours
    if same_day and in_focus_hour and spread.clash(chosen, chosen + length, unit) == 0:
        return chosen, Placement.WAITED_FOR_FOCUS_HOUR
    return chosen, Placement.SPREAD_OUT


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


def take_range(
    gaps: Sequence[TimeRange],
    piece: TimeRange,
    hours: WorkingHours,
    round_minutes: int,
) -> list[TimeRange]:
    """`take` for a piece that is not a slot yet."""
    return take(
        gaps,
        PlannedSlot(UUID(int=0), None, piece.start, piece.end, None),
        hours,
        round_minutes,
    )


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
