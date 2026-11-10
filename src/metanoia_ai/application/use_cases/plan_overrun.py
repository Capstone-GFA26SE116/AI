"""FR-16b: a slot runs over → propose to extend it or to move the slots after it.

1. Read the slot, the user's other slots, Fixed tasks and Google events (no write, AC5).
2. Not the user's → SlotNotFoundError (404). Fixed, finished or not on the calendar →
   SlotNotLateError (409).
3. The domain decides extend or replan (CLAUDE.md §5); a short sentence for the
   Mobile app and the 3D assistant.

No LLM, no quota, no AIUsageLog (AC5, #10).
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from metanoia_ai.application.ports.overrun_repository import OverrunRepository
from metanoia_ai.application.use_cases.recompute_productive_hours import (
    UserNotFoundError,
)
from metanoia_ai.domain import assistant_text as say
from metanoia_ai.domain.assistant_text import MovedLine
from metanoia_ai.domain.free_time import busy_ranges
from metanoia_ai.domain.models import Language, TimeRange
from metanoia_ai.domain.overrun_plan import (
    BUSY_STATES,
    LATE_STATES,
    OtherSlot,
    OverrunPlan,
    is_movable,
    plan_overrun,
)

# Slots that started before now still matter: the late one began earlier today.
LOOK_BACK = timedelta(days=1)


class SlotNotFoundError(Exception):
    """No such slot for this user (404)."""


class SlotNotLateError(Exception):
    """The slot cannot run over: Fixed, finished, cancelled or only proposed (409)."""


@dataclass(frozen=True)
class OverrunSettings:
    round_minutes: int  # SCHEDULE_ROUND_MINUTES, like FR-10
    window_days: int  # SCHEDULE_WINDOW_DAYS, like FR-10


@dataclass(frozen=True)
class OverrunRequest:
    user_id: UUID
    slot_id: UUID
    extra_minutes: int
    language: Language


@dataclass(frozen=True)
class OverrunResult:
    slot_id: UUID
    plan: OverrunPlan
    summary: str
    time_zone: str  # the user's zone, to send times in it


class PlanOverrun:
    def __init__(
        self,
        repo: OverrunRepository,
        settings: OverrunSettings,
        now: Callable[[], datetime],
    ) -> None:
        self.repo = repo
        self.settings = settings
        self.now = now

    async def execute(self, request: OverrunRequest) -> OverrunResult:
        now = self.now()
        window_end = now + timedelta(days=self.settings.window_days)
        data = await self.repo.get_overrun_data(
            request.user_id, request.slot_id, now - LOOK_BACK, window_end
        )
        if data is None:
            raise UserNotFoundError(str(request.user_id))
        late = data.late
        if late is None:
            raise SlotNotFoundError(str(request.slot_id))
        if late.is_fixed or late.state not in LATE_STATES:
            raise SlotNotLateError(late.state.value)

        zone = data.hours.time_zone
        now = now.astimezone(zone)
        others = [
            OtherSlot(
                s.slot_id,
                s.title,
                s.start,
                s.end,
                s.deadline,
                is_movable(s.state, s.is_fixed, s.started),
            )
            for s in data.slots
            if s.slot_id != late.slot_id and s.state in BUSY_STATES
        ]
        blocked = busy_ranges(data.fixed_tasks, data.calendar_events, ())
        plan = plan_overrun(
            TimeRange(late.start, late.end),
            others,
            blocked,
            data.hours,
            now=now,
            extra_minutes=request.extra_minutes,
            round_minutes=self.settings.round_minutes,
            window_days=self.settings.window_days,
        )
        summary = say.overrun_text(
            late.title,
            plan.extended.end,
            [MovedLine(m.title, m.new.start) for m in plan.moves],
            len(plan.not_placed),
            plan.past_day_end,
            now,
            zone,
            request.language,
        )
        return OverrunResult(late.slot_id, plan, summary, zone.key)
