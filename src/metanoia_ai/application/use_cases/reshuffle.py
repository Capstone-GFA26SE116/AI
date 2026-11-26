"""FR-22d: three schedule proposals, computed by code (no LLM, no quota, no write).

- InsertBlock (M3, M4): urgent work from `start` to `end`.
- PostponeSlot (M7): one slot to the next working day.
- CatchUp (M10): missed slots placed again from now.

The app shows the proposal; after a "yes" it applies it through the Backend and
keeps the old times for "undo" (M18).
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from metanoia_ai.application.ports.reshuffle_repository import (
    CalendarData,
    CalendarSlot,
    ReshuffleRepository,
)
from metanoia_ai.application.use_cases.plan_overrun import SlotNotFoundError
from metanoia_ai.application.use_cases.recompute_productive_hours import (
    UserNotFoundError,
)
from metanoia_ai.domain import assistant_text as say
from metanoia_ai.domain.assistant_text import ConflictLine, MovedLine
from metanoia_ai.domain.free_time import DailyBreak, break_ranges
from metanoia_ai.domain.models import Language, TimeRange
from metanoia_ai.domain.overrun_plan import (
    BUSY_STATES,
    MOVABLE_STATES,
    NotPlaced,
    SlotMove,
    SlotState,
)
from metanoia_ai.domain.reshuffle import (
    Blocker,
    Calendar,
    ConflictKind,
    InsertPlan,
    MovableSlot,
    Rules,
    plan_catch_up,
    plan_insert,
    plan_postpone,
)

# A block may start a little before now: the user says "from 2 pm" at 2:03.
START_GRACE = timedelta(minutes=5)
LOOK_BACK = timedelta(days=1)


class BlockInvalidError(Exception):
    """The urgent block cannot be planned (422): in the past, empty, too long or too far."""


class SlotNotMovableError(Exception):
    """The slot cannot be postponed (409): Fixed, group, started, finished or cancelled."""


@dataclass(frozen=True)
class ReshuffleSettings:
    round_minutes: int  # SCHEDULE_ROUND_MINUTES
    window_days: int  # SCHEDULE_WINDOW_DAYS
    breaks: tuple[DailyBreak, ...] = ()  # SCHEDULE_LUNCH_BREAK
    max_block_minutes: int = 720  # INSERT_BLOCK_MAX_MINUTES
    look_back_days: int = 2  # CATCH_UP_LOOK_BACK_DAYS


@dataclass(frozen=True)
class InsertResult:
    title: str
    plan: InsertPlan
    summary: str
    time_zone: str
    project_id: UUID | None
    domain_id: UUID | None


@dataclass(frozen=True)
class PostponeResult:
    slot_id: UUID
    title: str
    move: SlotMove | None  # None: no free time before the deadline (not_placed)
    summary: str
    time_zone: str


@dataclass(frozen=True)
class CatchUpMove:
    move: SlotMove
    task_id: UUID | None
    subtask_id: UUID | None


@dataclass(frozen=True)
class CatchUpResult:
    moves: tuple[CatchUpMove, ...]
    not_placed: tuple[NotPlaced, ...]
    summary: str
    time_zone: str


def is_movable(slot: CalendarSlot) -> bool:
    """Plan #1: a personal Flexible slot, Approved / Due, no timer started."""
    return (
        slot.state in MOVABLE_STATES
        and not slot.is_fixed
        and not slot.is_group
        and not slot.started
    )


def to_calendar(data: CalendarData, breaks: list[TimeRange]) -> Calendar:
    """Sort the user's calendar into what moves, what is in the way, what is only busy."""
    movable, blockers, other_busy = [], [], []
    for s in data.slots:
        if s.state not in BUSY_STATES:
            continue  # proposed, skipped, missed, postponed, cancelled: not on the calendar
        time = TimeRange(s.start, s.end)
        if is_movable(s):
            movable.append(
                MovableSlot(
                    s.slot_id, s.title, time, s.deadline, s.task_id, s.subtask_id
                )
            )
        elif s.is_group and s.state is not SlotState.DONE:
            blockers.append(Blocker(ConflictKind.GROUP_SLOT, s.slot_id, s.title, time))
        elif s.state is SlotState.DONE or s.is_fixed:
            other_busy.append(time)  # a Fixed task's own time comes from fixed_tasks
        else:
            blockers.append(
                Blocker(ConflictKind.STARTED_SLOT, s.slot_id, s.title, time)
            )
    blockers += [
        Blocker(
            ConflictKind.FIXED_TASK,
            t.task_id,
            t.title,
            TimeRange(t.start, t.end),
            t.with_client,
        )
        for t in data.fixed_tasks
        if not t.is_skipped and t.end > t.start
    ]
    blockers += [
        Blocker(ConflictKind.CALENDAR_EVENT, None, e.title, TimeRange(e.start, e.end))
        for e in data.events
        if not (e.is_all_day or e.is_cancelled or e.is_created_by_metanoia)
        and e.end > e.start
    ]
    return Calendar(
        data.hours, tuple(movable), tuple(blockers), tuple(other_busy), tuple(breaks)
    )


class _Base:
    def __init__(
        self,
        repo: ReshuffleRepository,
        settings: ReshuffleSettings,
        now: Callable[[], datetime],
    ) -> None:
        self.repo = repo
        self.settings = settings
        self.now = now

    async def _load(
        self,
        user_id: UUID,
        slot_id: UUID | None = None,
        look_back: timedelta = LOOK_BACK,
    ) -> tuple[CalendarData, Calendar, Rules]:
        now = self.now()
        window_end = now + timedelta(days=self.settings.window_days + 1)
        data = await self.repo.get_calendar(
            user_id, now - look_back, window_end, slot_id
        )
        if data is None:
            raise UserNotFoundError(str(user_id))
        now = now.astimezone(data.hours.time_zone)
        breaks = break_ranges(
            data.hours, now, self.settings.window_days, self.settings.breaks
        )
        rules = Rules(now, self.settings.round_minutes, self.settings.window_days)
        return data, to_calendar(data, breaks), rules


class InsertBlock(_Base):
    async def execute(
        self,
        user_id: UUID,
        title: str,
        start: datetime,
        end: datetime,
        language: Language,
    ) -> InsertResult:
        now = self.now()
        if end <= start:
            raise BlockInvalidError("end must be after start")
        if start < now - START_GRACE:
            raise BlockInvalidError("start is in the past")
        if end - start > timedelta(minutes=self.settings.max_block_minutes):
            raise BlockInvalidError("block is too long")
        if end > now + timedelta(days=self.settings.window_days):
            raise BlockInvalidError("block is after the planning window")

        data, cal, rules = await self._load(user_id)
        block = TimeRange(start, end)
        plan = plan_insert(block, cal, rules)
        zone = data.hours.time_zone
        summary = say.insert_text(
            title,
            start,
            end,
            [MovedLine(m.title, m.new.start) for m in plan.moves],
            len(plan.not_placed),
            [ConflictLine(c.blocker.title, c.why) for c in plan.conflicts],
            plan.recommendation,
            None
            if plan.other_time is None
            else (plan.other_time.start, plan.other_time.end),
            plan.past_day_end,
            rules.now,
            zone,
            language,
        )
        return InsertResult(
            title,
            plan,
            summary,
            zone.key,
            data.default_project_id,
            data.recent_domain_id,
        )


class PostponeSlot(_Base):
    async def execute(
        self, user_id: UUID, slot_id: UUID, language: Language
    ) -> PostponeResult:
        data, cal, rules = await self._load(user_id, slot_id)
        target = data.target
        if target is None:
            raise SlotNotFoundError(str(slot_id))
        if not is_movable(target):
            raise SlotNotMovableError(target.state.value)
        slot = MovableSlot(
            target.slot_id,
            target.title,
            TimeRange(target.start, target.end),
            target.deadline,
            target.task_id,
            target.subtask_id,
        )
        move = plan_postpone(slot, cal, rules)
        zone = data.hours.time_zone
        summary = say.postpone_text(
            target.title,
            None if move is None else move.new.start,
            rules.now,
            zone,
            language,
        )
        return PostponeResult(target.slot_id, target.title, move, summary, zone.key)


class CatchUp(_Base):
    async def execute(self, user_id: UUID, language: Language) -> CatchUpResult:
        look_back = timedelta(days=self.settings.look_back_days)
        data, cal, rules = await self._load(user_id, look_back=look_back)
        since = rules.now - look_back
        rows = {
            s.slot_id: s
            for s in data.slots
            if not (s.is_fixed or s.is_group or s.started or s.task_finished)
            and s.start >= since
            and (
                s.state is SlotState.MISSED
                or (s.state in MOVABLE_STATES and s.end <= rules.now)
            )
        }
        missed = [
            MovableSlot(s.slot_id, s.title, TimeRange(s.start, s.end), s.deadline)
            for s in rows.values()
        ]
        moves, not_placed = plan_catch_up(missed, cal, rules)
        zone = data.hours.time_zone
        summary = say.catch_up_text(
            [MovedLine(m.title, m.new.start) for m in moves],
            len(not_placed),
            rules.now,
            zone,
            language,
        )
        return CatchUpResult(
            tuple(
                CatchUpMove(m, rows[m.slot_id].task_id, rows[m.slot_id].subtask_id)
                for m in moves
            ),
            not_placed,
            summary,
            zone.key,
        )
