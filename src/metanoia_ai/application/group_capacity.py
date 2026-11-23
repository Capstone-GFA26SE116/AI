"""How much time a group member has for a piece of work (FR-23, FR-24).

Turns one member's personal planning data and group work into a ranking
`Candidate` for a window. Shared by the assignee suggestion (one existing task)
and the group plan chat (every task of a draft).
"""

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from metanoia_ai.application.ports.group_assignee_repository import (
    GroupMember,
    GroupWork,
)
from metanoia_ai.application.ports.schedule_repository import PlanningData, TaskStatus
from metanoia_ai.domain.assignee_ranking import (
    Candidate,
    best_free_times,
    calendar_gaps,
    needed_minutes,
    overlaps,
)
from metanoia_ai.domain.free_time import DailyBreak, break_ranges, busy_ranges
from metanoia_ai.domain.models import TimeRange
from metanoia_ai.domain.schedule_duration import remaining_estimate

OPEN_PERSONAL = {TaskStatus.TODO, TaskStatus.IN_PROGRESS}


def member_candidate(
    member: GroupMember,
    planning: PlanningData,
    work: GroupWork,
    *,
    now: datetime,
    window_end: datetime,
    initial_k: Decimal,
    breaks: Sequence[DailyBreak] = (),
    estimate_minutes: int | None,
    domain_id: UUID | None,
    fixed_time: TimeRange | None = None,
    assignee_id: UUID | None = None,
    extra_minutes: int = 0,
) -> Candidate:
    """The member as a candidate for work due at `window_end`.

    `extra_minutes`: work already promised to them that is not in the DB yet
    (tasks of the same draft, FR-24).
    """
    busy = busy_ranges(
        planning.fixed_tasks, planning.calendar_events, planning.approved_slots
    )
    busy += list(work.fixed_times)
    days = max(1, (window_end - now).days + 1)
    rests = break_ranges(planning.hours, now, days, breaks)
    committed = personal_unscheduled_minutes(planning, now, window_end)
    k = initial_k
    if domain_id is not None:
        k = planning.k_by_domain.get(domain_id, initial_k)
    gaps = calendar_gaps(planning.hours, now, window_end, [*busy, *rests])
    free = sum(g.minutes for g in gaps) - committed - work.open_minutes - extra_minutes
    return Candidate(
        user_id=member.user_id,
        role=member.role,
        is_current_assignee=member.user_id == assignee_id,
        free_minutes=max(free, 0),
        busy_at_fixed_time=fixed_time is not None and overlaps(fixed_time, busy),
        group_work_minutes=work.open_minutes
        + extra_minutes
        + sum(r.minutes for r in work.fixed_times if _in(r, now, window_end)),
        needed_minutes=needed_minutes(estimate_minutes, k),
        free_times=best_free_times(gaps),
        time_zone=planning.hours.time_zone,
    )


def personal_unscheduled_minutes(
    planning: PlanningData, now: datetime, window_end: datetime
) -> int:
    """The member's own open Flexible work due in the window and not on the calendar yet.

    Tasks with an approved slot are already busy time, so they are not counted twice.
    """
    return sum(
        remaining_estimate(t.estimated_minutes, t.sessions, now)
        for t in planning.tasks
        if t.status in OPEN_PERSONAL
        and not t.has_approved_slot
        and t.deadline <= window_end
    )


def _in(time_range: TimeRange, start: datetime, end: datetime) -> bool:
    return time_range.start < end and start < time_range.end
