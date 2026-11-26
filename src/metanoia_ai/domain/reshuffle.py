"""FR-22d: move Metanoia slots for urgent work, to tomorrow, or after missed slots.

Pure code, no LLM (CLAUDE.md §5). Nothing is moved here: each result is a proposal
the user approves; the app then applies it through the Backend (plan §1).

- Insert (M3, M4): a block of urgent work. Only the movable slots it covers (or that
  start within the break after it) move, each to the earliest free time after the
  block, in their old order; every other slot stays where it is (plan #3).
  Things that never move and overlap the block are conflicts, with how important
  each one is and a suggestion: another time, overwrite, or cancel.
- Postpone (M7): one slot to the earliest free time from the next working day on.
- Catch up (M10): slots that passed without work, placed again from now, in order.

Never moved (plan #1): Fixed tasks, Google events, Work Group slots, slots already
started, finished or cancelled. Every move keeps its old time (M18: undo).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from uuid import UUID

from metanoia_ai.domain.free_time import free_ranges, round_up, working_days
from metanoia_ai.domain.models import TimeRange, WorkingHours
from metanoia_ai.domain.overrun_plan import NotPlaced, SlotMove
from metanoia_ai.domain.planner import after, local_date, take


class ConflictKind(StrEnum):
    """Sent in the API."""

    FIXED_TASK = "fixed_task"
    CALENDAR_EVENT = "calendar_event"  # made on Google, not by Metanoia
    GROUP_SLOT = "group_slot"
    STARTED_SLOT = "started_slot"  # a timer was started on it


class Why(StrEnum):
    """Why a conflict is (not) important (plan #4). Sent in the API."""

    CLIENT = "client"  # a Fixed task of a project with a client
    PERSONAL = "personal"  # a Fixed task of a project without a client
    OTHER_PEOPLE = "other_people"  # a Google event may have other people in it
    GROUP = "group"  # group members wait for this work
    RUNNING = "running"  # the user already started it


class Choice(StrEnum):
    """What the user can do about conflicts (M4). Sent in the API."""

    OTHER_TIME = "other_time"
    OVERWRITE = "overwrite"
    CANCEL = "cancel"


@dataclass(frozen=True)
class Blocker:
    """Something on the calendar the AI never moves."""

    kind: ConflictKind
    ref_id: UUID | None  # the task / slot id; None for a Google event
    title: str
    time: TimeRange
    with_client: bool = False  # Fixed tasks only


@dataclass(frozen=True)
class MovableSlot:
    """A personal Flexible slot, Approved / Due, no timer started (plan #1)."""

    slot_id: UUID
    title: str
    time: TimeRange
    deadline: datetime | None
    task_id: UUID | None = None
    subtask_id: UUID | None = None


@dataclass(frozen=True)
class Conflict:
    blocker: Blocker
    important: bool
    why: Why


@dataclass(frozen=True)
class InsertPlan:
    block: TimeRange
    moves: tuple[SlotMove, ...]
    not_placed: tuple[NotPlaced, ...]
    conflicts: tuple[Conflict, ...]
    options: tuple[Choice, ...]  # empty without conflicts
    recommendation: Choice | None
    other_time: TimeRange | None
    skip_task_ids: tuple[UUID, ...]  # Fixed tasks the block would overwrite
    past_day_end: bool


@dataclass(frozen=True)
class Calendar:
    """What the planner needs about the user's calendar in the window."""

    hours: WorkingHours
    movable: tuple[MovableSlot, ...]
    blockers: tuple[Blocker, ...]
    other_busy: tuple[TimeRange, ...] = ()  # e.g. Done slots: busy, never a conflict
    breaks: tuple[TimeRange, ...] = ()  # lunch breaks (W3-01): nothing is placed there


@dataclass(frozen=True)
class Rules:
    now: datetime
    round_minutes: int
    window_days: int


def overlaps(a: TimeRange, b: TimeRange) -> bool:
    return a.start < b.end and b.start < a.end


def judge(blocker: Blocker) -> Conflict:
    """How important the thing in the way is (plan #4)."""
    match blocker.kind:
        case ConflictKind.FIXED_TASK if blocker.with_client:
            return Conflict(blocker, True, Why.CLIENT)
        case ConflictKind.FIXED_TASK:
            return Conflict(blocker, False, Why.PERSONAL)
        case ConflictKind.CALENDAR_EVENT:
            return Conflict(blocker, True, Why.OTHER_PEOPLE)
        case ConflictKind.GROUP_SLOT:
            return Conflict(blocker, True, Why.GROUP)
        case _:
            return Conflict(blocker, True, Why.RUNNING)


def plan_insert(block: TimeRange, cal: Calendar, rules: Rules) -> InsertPlan:
    hours = cal.hours
    zone = hours.time_zone
    day = local_date(block.start, zone)
    buffer = timedelta(minutes=hours.buffer_minutes)

    conflicts = tuple(judge(b) for b in cal.blockers if overlaps(b.time, block))
    with_break = TimeRange(block.start, block.end + buffer)
    affected = sorted(
        (s for s in cal.movable if overlaps(s.time, with_break)),
        key=lambda s: s.time.start,
    )
    affected_ids = {s.slot_id for s in affected}
    staying = [s.time for s in cal.movable if s.slot_id not in affected_ids]
    busy = [*staying, *(b.time for b in cal.blockers), *cal.other_busy]
    gaps = free_ranges(
        hours,
        rules.now,
        rules.window_days,
        [*busy, block, *cal.breaks],
        rules.round_minutes,
    )

    moves: list[SlotMove] = []
    not_placed: list[NotPlaced] = []
    earliest = round_up(block.end + buffer, rules.round_minutes, zone)
    for slot in affected:  # the others stay busy: nothing is pushed in a chain (#3)
        length = slot.time.end - slot.time.start
        start = _first_fit(after(gaps, earliest), length, slot.deadline)
        if start is None:
            not_placed.append(NotPlaced(slot.slot_id, slot.title))
            continue
        placed = TimeRange(start, start + length)
        moves.append(SlotMove(slot.slot_id, slot.title, slot.time, placed))
        gaps = take(gaps, placed, hours, rules.round_minutes)  # reads only start / end
        earliest = round_up(placed.end + buffer, rules.round_minutes, zone)

    other_time = None
    options: tuple[Choice, ...] = ()
    recommendation = None
    if conflicts:
        other_time = _other_time(block, cal, rules, day)
        can_overwrite = all(
            c.blocker.kind is not ConflictKind.STARTED_SLOT for c in conflicts
        )
        options = tuple(
            c
            for c, ok in (
                (Choice.OTHER_TIME, other_time is not None),
                (Choice.OVERWRITE, can_overwrite),
                (Choice.CANCEL, True),
            )
            if ok
        )
        if other_time is not None:
            recommendation = Choice.OTHER_TIME
        elif can_overwrite:
            recommendation = Choice.OVERWRITE

    day_end = datetime.combine(day, hours.day_end, tzinfo=zone)
    return InsertPlan(
        block=block,
        moves=tuple(moves),
        not_placed=tuple(not_placed),
        conflicts=conflicts,
        options=options,
        recommendation=recommendation,
        other_time=other_time,
        skip_task_ids=tuple(
            c.blocker.ref_id
            for c in conflicts
            if c.blocker.kind is ConflictKind.FIXED_TASK
            and c.blocker.ref_id is not None
        ),
        past_day_end=block.end > day_end,
    )


def _other_time(
    block: TimeRange, cal: Calendar, rules: Rules, day: date
) -> TimeRange | None:
    """The earliest free time of the same length that same day, counting only what
    never moves (movable slots would be pushed back again)."""
    fixed = [*(b.time for b in cal.blockers), *cal.other_busy, *cal.breaks]
    gaps = free_ranges(
        cal.hours, rules.now, rules.window_days, fixed, rules.round_minutes
    )
    length = block.end - block.start
    for gap in gaps:
        if local_date(gap.start, cal.hours.time_zone) != day:
            continue
        if gap.start + length <= gap.end:
            return TimeRange(gap.start, gap.start + length)
    return None


def next_working_day(
    hours: WorkingHours, after_day: date, window_days: int
) -> date | None:
    days = working_days(
        hours, after_day + timedelta(days=1), after_day + timedelta(days=window_days)
    )
    return days[0].start.date() if days else None


def plan_postpone(slot: MovableSlot, cal: Calendar, rules: Rules) -> SlotMove | None:
    """M7: the earliest free time from the next working day on (plan #8).
    None: nothing free before the task deadline in the window."""
    zone = cal.hours.time_zone
    base = max(local_date(slot.time.start, zone), local_date(rules.now, zone))
    day = next_working_day(cal.hours, base, rules.window_days)
    if day is None:
        return None
    earliest = datetime.combine(day, cal.hours.day_start, tzinfo=zone)
    busy = [
        *(s.time for s in cal.movable if s.slot_id != slot.slot_id),
        *(b.time for b in cal.blockers),
        *cal.other_busy,
        *cal.breaks,
    ]
    gaps = free_ranges(
        cal.hours, rules.now, rules.window_days, busy, rules.round_minutes
    )
    length = slot.time.end - slot.time.start
    start = _first_fit(after(gaps, earliest), length, slot.deadline)
    if start is None:
        return None
    return SlotMove(
        slot.slot_id, slot.title, slot.time, TimeRange(start, start + length)
    )


def plan_catch_up(
    missed: Sequence[MovableSlot], cal: Calendar, rules: Rules
) -> tuple[tuple[SlotMove, ...], tuple[NotPlaced, ...]]:
    """M10: missed slots again from now, in their old order (like FR-16b #8).
    `cal.movable` holds the user's other movable slots: they stay where they are."""
    missed_ids = {m.slot_id for m in missed}
    busy = [
        *(s.time for s in cal.movable if s.slot_id not in missed_ids),
        *(b.time for b in cal.blockers),
        *cal.other_busy,
        *cal.breaks,
    ]
    hours = cal.hours
    gaps = free_ranges(hours, rules.now, rules.window_days, busy, rules.round_minutes)
    moves: list[SlotMove] = []
    not_placed: list[NotPlaced] = []
    earliest = rules.now
    for slot in sorted(missed, key=lambda s: s.time.start):
        length = slot.time.end - slot.time.start
        start = _first_fit(after(gaps, earliest), length, slot.deadline)
        if start is None:
            not_placed.append(NotPlaced(slot.slot_id, slot.title))
            continue
        placed = TimeRange(start, start + length)
        moves.append(SlotMove(slot.slot_id, slot.title, slot.time, placed))
        gaps = take(gaps, placed, hours, rules.round_minutes)  # reads only start / end
        earliest = placed.end
    return tuple(moves), tuple(not_placed)


def _first_fit(
    gaps: Sequence[TimeRange], length: timedelta, deadline: datetime | None
) -> datetime | None:
    for gap in gaps:
        limit = gap.end if deadline is None else min(gap.end, deadline)
        if gap.start + length <= limit:
            return gap.start
    return None
