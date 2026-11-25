"""FR-25: suggest time slots for the Work Group work given to the caller.

1. Read the task and its parts; check access like the Backend (404 / 403 / 409).
2. The parts still to place (AC2), then the FR-10 free time and planner (AC3).
3. A template reason per slot (AC4). No LLM, no quota, writes nothing (AC5):
   the app creates the slots the member keeps through the Backend.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from metanoia_ai.application.ports.group_slot_repository import GroupSlotRepository
from metanoia_ai.application.ports.schedule_repository import ScheduleRepository
from metanoia_ai.application.use_cases.propose_schedule import work_by_day
from metanoia_ai.domain.free_time import (
    DailyBreak,
    break_ranges,
    busy_ranges,
    free_ranges,
)
from metanoia_ai.domain.group_slot_plan import (
    due_at,
    is_given_to,
    parts_to_place,
    slot_reason,
    summary_text,
)
from metanoia_ai.domain.models import Language, ScheduleWarning
from metanoia_ai.domain.planner import plan_schedule, top_productive_hours

NO_ESTIMATE = "no_estimate"


class GroupTaskMissingError(Exception):
    """No such task, or the caller is not in its group (404, like the Backend)."""


class NotAssignedError(Exception):
    """Nothing open in this task is given to the caller (403)."""


class GroupTaskBlockedError(Exception):
    """The group is archived, the task is closed or has a fixed time (409)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason  # archived / closed / fixed_time


@dataclass(frozen=True)
class GroupSlotSettings:
    window_days: int
    min_chunk_minutes: int
    round_minutes: int
    top_productive_hours: int
    breaks: tuple[DailyBreak, ...] = ()
    spread: bool = False
    max_block_minutes: int | None = None


@dataclass(frozen=True)
class SuggestedSlot:
    subtask_id: UUID | None
    title: str
    start: datetime
    end: datetime
    minutes: int
    reason: str


@dataclass(frozen=True)
class SlotWarning:
    type: str  # not_enough_time / overdue / beyond_window / no_estimate
    subtask_id: UUID | None = None
    needed_minutes: int | None = None
    free_minutes: int | None = None


@dataclass(frozen=True)
class GroupSlotSuggestion:
    task_id: UUID
    slots: tuple[SuggestedSlot, ...]
    warnings: tuple[SlotWarning, ...]
    summary: str
    time_zone: str


class SuggestGroupSlots:
    def __init__(
        self,
        groups: GroupSlotRepository,
        schedules: ScheduleRepository,
        settings: GroupSlotSettings,
        clock: Callable[[], datetime],
    ) -> None:
        self.groups = groups
        self.schedules = schedules
        self.settings = settings
        self.clock = clock

    async def execute(
        self, caller_id: UUID, task_id: UUID, language: Language
    ) -> GroupSlotSuggestion:
        task = await self.groups.get_task(task_id, caller_id)
        if task is None or not task.is_member:
            raise GroupTaskMissingError(str(task_id))
        if task.group_archived:
            raise GroupTaskBlockedError("archived")
        if task.is_closed:
            raise GroupTaskBlockedError("closed")
        if task.has_fixed_time:
            raise GroupTaskBlockedError("fixed_time")

        if not is_given_to(task.parts, caller_id):
            raise NotAssignedError(str(task_id))

        now = self.clock()
        s = self.settings
        window_end = now + timedelta(days=s.window_days)
        data = await self.schedules.get_planning_data(caller_id, now, window_end)
        zone = data.hours.time_zone
        due = due_at(task.deadline, task.group_deadline, data.hours)
        # No deadline: place as early as possible, past the window (plan #1).
        deadline = due or window_end + timedelta(days=1)
        todo = parts_to_place(task.task_id, task.parts, caller_id, deadline)
        warnings = [
            SlotWarning(NO_ESTIMATE, subtask_id=p.subtask_id) for p in todo.no_estimate
        ]
        if not todo.units:
            return self._result(task.task_id, task.title, (), warnings, zone, language)

        busy = busy_ranges(data.fixed_tasks, data.calendar_events, data.approved_slots)
        busy += break_ranges(data.hours, now, s.window_days, s.breaks)
        plan = plan_schedule(
            todo.units,
            free_ranges(data.hours, now, s.window_days, busy, s.round_minutes),
            data.hours,
            top_productive_hours(data.productive_scores, s.top_productive_hours),
            now=now,
            window_end=window_end,
            round_minutes=s.round_minutes,
            min_chunk_minutes=s.min_chunk_minutes,
            spread=s.spread,
            max_block_minutes=s.max_block_minutes,
            existing_load=work_by_day(data.approved_slots, zone),
        )
        titles = {u.subtask_id: u.title for u in todo.units}
        reason = slot_reason(due, zone, language)
        slots = [
            SuggestedSlot(
                p.subtask_id,
                titles[p.subtask_id],
                p.start.astimezone(zone),
                p.end.astimezone(zone),
                round((p.end - p.start).total_seconds() / 60),
                reason,
            )
            for p in plan.slots
        ]
        warnings += [_warning(w) for w in plan.warnings]
        return self._result(task.task_id, task.title, slots, warnings, zone, language)

    @staticmethod
    def _result(
        task_id: UUID,
        title: str,
        slots: Sequence[SuggestedSlot],
        warnings: Sequence[SlotWarning],
        zone: ZoneInfo,
        language: Language,
    ) -> GroupSlotSuggestion:
        return GroupSlotSuggestion(
            task_id,
            tuple(slots),
            tuple(warnings),
            summary_text(title, len(slots), language),
            zone.key,
        )


def _warning(w: ScheduleWarning) -> SlotWarning:
    return SlotWarning(
        w.type.value, needed_minutes=w.needed_minutes, free_minutes=w.free_minutes
    )
