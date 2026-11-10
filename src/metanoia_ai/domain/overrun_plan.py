"""FR-16b: a slot runs over and the user needs more time → extend it or move what follows.

Pure code, no LLM (CLAUDE.md §5). Nothing is moved here: the result is a proposal the
user approves; the client then applies it through the Backend (plan #2).

- Extend: the new end (+ buffer) still fits before the next busy item → only this slot changes.
- Replan: the movable slots after it on the same day are placed again, in the same order,
  each keeping its length, from the new end (+ buffer) on; earliest free time, up to the
  window end (7 days, like FR-10). A slot that cannot end before its task deadline is not placed.

Never moved (AC3): Fixed tasks, Google events, slots already started, finished or cancelled.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from metanoia_ai.domain.free_time import free_ranges, round_up
from metanoia_ai.domain.models import TimeRange, WorkingHours
from metanoia_ai.domain.planner import after, local_date, take


class SlotState(StrEnum):
    """Every ScheduleSlot status in the DB (read 2026-11-10, plan step 0)."""

    PROPOSED = "proposed"
    APPROVED = "approved"
    DUE = "due"  # its start time came, no timer yet
    IN_PROGRESS = "in_progress"
    OVERRUN = "overrun"  # its end time passed with the timer still running
    DONE = "done"
    SKIPPED = "skipped"
    MISSED = "missed"
    POSTPONED = "postponed"
    CANCELLED = "cancelled"


# A slot that can run over: on the calendar and not finished.
LATE_STATES = frozenset(
    {SlotState.APPROVED, SlotState.DUE, SlotState.IN_PROGRESS, SlotState.OVERRUN}
)
# Slots that take time on the calendar.
BUSY_STATES = LATE_STATES | {SlotState.DONE}
# Slots that may be moved (#6, #7): not started yet.
MOVABLE_STATES = frozenset({SlotState.APPROVED, SlotState.DUE})


def is_movable(state: SlotState, is_fixed: bool, started: bool) -> bool:
    """Flexible, Approved / Due and no timer started on it (AC3)."""
    return state in MOVABLE_STATES and not is_fixed and not started


class OverrunKind(StrEnum):
    """Sent in the API."""

    EXTEND = "extend"
    REPLAN = "replan"


@dataclass(frozen=True)
class OtherSlot:
    """Another slot of the user in the window (not Cancelled / Proposed)."""

    slot_id: UUID
    title: str
    start: datetime
    end: datetime
    deadline: datetime | None  # the task deadline; None for a Fixed task
    movable: bool  # Flexible, Approved / Due, no timer started on it (#6, #7)


@dataclass(frozen=True)
class SlotMove:
    slot_id: UUID
    title: str
    old: TimeRange
    new: TimeRange


@dataclass(frozen=True)
class NotPlaced:
    """A slot that would have to move but cannot end before its task deadline (#9)."""

    slot_id: UUID
    title: str


@dataclass(frozen=True)
class OverrunPlan:
    kind: OverrunKind
    extended: TimeRange  # the late slot with its new end
    moves: tuple[SlotMove, ...]
    not_placed: tuple[NotPlaced, ...]
    past_day_end: bool  # the new end is after the end of the working day


def new_end(
    slot_end: datetime,
    now: datetime,
    extra_minutes: int,
    hours: WorkingHours,
    round_minutes: int,
) -> datetime:
    """The extra time counts from now when the slot is already over (plan §2)."""
    base = max(slot_end, now) + timedelta(minutes=extra_minutes)
    return round_up(base, round_minutes, hours.time_zone)


def plan_overrun(
    late: TimeRange,
    others: Sequence[OtherSlot],
    blocked: Sequence[TimeRange],
    hours: WorkingHours,
    *,
    now: datetime,
    extra_minutes: int,
    round_minutes: int,
    window_days: int,
) -> OverrunPlan:
    """`late`: the slot that runs over. `blocked`: Fixed tasks and Google events.

    `others`: the user's other slots in the window (not `late` itself); only the
    movable ones that start after `late` on the same day are moved.
    """
    end = new_end(late.end, now, extra_minutes, hours, round_minutes)
    extended = TimeRange(late.start, end)
    day = local_date(late.start, hours.time_zone)
    day_end = datetime.combine(day, hours.day_end, tzinfo=hours.time_zone)
    to_move = sorted(
        (
            s
            for s in others
            if s.movable
            and s.start >= late.end
            and local_date(s.start, hours.time_zone) == day
        ),
        key=lambda s: s.start,
    )
    moving = {s.slot_id for s in to_move}
    staying = [
        TimeRange(s.start, s.end) for s in others if s.slot_id not in moving
    ] + list(blocked)

    buffer = timedelta(minutes=hours.buffer_minutes)
    needed = TimeRange(late.end, end + buffer)  # the extra time and the break after it
    in_the_way = [
        r
        for r in [*staying, *(TimeRange(s.start, s.end) for s in to_move)]
        if r.start < needed.end and r.end > needed.start
    ]
    if not in_the_way:
        return OverrunPlan(OverrunKind.EXTEND, extended, (), (), end > day_end)

    moves, not_placed = _place_in_order(
        to_move,
        [*staying, extended],
        hours,
        earliest=round_up(end + buffer, round_minutes, hours.time_zone),
        now=now,
        round_minutes=round_minutes,
        window_days=window_days,
    )
    return OverrunPlan(
        OverrunKind.REPLAN, extended, tuple(moves), tuple(not_placed), end > day_end
    )


def _place_in_order(
    slots: Sequence[OtherSlot],
    busy: Sequence[TimeRange],
    hours: WorkingHours,
    *,
    earliest: datetime,
    now: datetime,
    round_minutes: int,
    window_days: int,
) -> tuple[list[SlotMove], list[NotPlaced]]:
    """Earliest free time for each slot, one after another, keeping the order (#8)."""
    gaps = free_ranges(hours, now, window_days, busy, round_minutes)
    moves: list[SlotMove] = []
    not_placed: list[NotPlaced] = []
    for slot in slots:
        length = slot.end - slot.start
        start = _first_fit(after(gaps, earliest), length, slot.deadline)
        if start is None:
            not_placed.append(NotPlaced(slot.slot_id, slot.title))
            continue
        placed = TimeRange(start, start + length)
        moves.append(
            SlotMove(slot.slot_id, slot.title, TimeRange(slot.start, slot.end), placed)
        )
        gaps = take(gaps, placed, hours, round_minutes)  # reads only start / end
        earliest = placed.end
    return moves, not_placed


def _first_fit(
    gaps: Sequence[TimeRange], length: timedelta, deadline: datetime | None
) -> datetime | None:
    for gap in gaps:
        limit = gap.end if deadline is None else min(gap.end, deadline)
        if gap.start + length <= limit:
            return gap.start
    return None
