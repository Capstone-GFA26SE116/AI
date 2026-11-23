"""FR-23: rank the members of a work group for one group task.

Pure rules, no DB and no LLM. The LLM only writes a reason for the result.

Order (plan AC4):
1. not busy at the task's fixed time,
2. has enough free time before the deadline (fits),
3. the most minutes left after this task,
4. the fewest group work minutes,
5. user id (so the same data always gives the same order).
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from metanoia_ai.domain.free_time import free_ranges, merge
from metanoia_ai.domain.models import TimeRange, WorkingHours

MINUTES_PER_HOUR = 60


class GroupRole(StrEnum):
    """Role in a work group. Values are sent in the API."""

    LEADER = "leader"
    MEMBER = "member"
    VIEWER = "viewer"


@dataclass(frozen=True)
class Candidate:
    """What the ranking needs about one member. Personal details stay out."""

    user_id: UUID
    role: GroupRole
    is_current_assignee: bool
    free_minutes: int  # free work time in the window, after their other work
    busy_at_fixed_time: bool
    group_work_minutes: int  # open group work due in the window
    needed_minutes: int | None  # this task × their K; None without an estimate


@dataclass(frozen=True)
class RankedCandidate:
    candidate: Candidate
    rank: int  # 1 = best

    @property
    def fits(self) -> bool | None:
        needed = self.candidate.needed_minutes
        return None if needed is None else self.candidate.free_minutes >= needed

    @property
    def spare_minutes(self) -> int:
        """Free minutes left after this task (free minutes without an estimate)."""
        needed = self.candidate.needed_minutes or 0
        return self.candidate.free_minutes - needed


def free_minutes_in_window(
    hours: WorkingHours,
    now: datetime,
    window_end: datetime,
    busy: Sequence[TimeRange],
    committed_minutes: int,
) -> int:
    """Working minutes from now to `window_end`, minus busy time and committed work.

    `committed_minutes`: work that has no time yet (unscheduled personal tasks,
    group work without a fixed time). Never below 0.
    """
    if window_end <= now:
        return 0
    days = math.ceil((window_end - now).total_seconds() / 86_400)
    in_window = [
        TimeRange(r.start, min(r.end, window_end))
        for r in free_ranges(hours, now, days, merge(busy), round_minutes=1)
        if r.start < window_end
    ]
    free = sum(r.minutes for r in in_window)
    return max(free - committed_minutes, 0)


def overlaps(time: TimeRange, busy: Sequence[TimeRange]) -> bool:
    """True when `time` overlaps any busy range (touching ends do not count)."""
    return any(b.start < time.end and time.start < b.end for b in busy)


def needed_minutes(estimate: int | None, k: Decimal) -> int | None:
    """The task's estimate × the member's K, rounded up. None without an estimate."""
    if estimate is None:
        return None
    return math.ceil(estimate * k)


def rank_candidates(candidates: Sequence[Candidate]) -> list[RankedCandidate]:
    """Best first (see the module docstring). Viewers are never ranked."""
    eligible = [c for c in candidates if c.role is not GroupRole.VIEWER]

    def key(c: Candidate) -> tuple[bool, int, int, int, str]:
        fits = None if c.needed_minutes is None else c.free_minutes >= c.needed_minutes
        fit_order = {True: 0, None: 1, False: 2}[fits]
        spare = c.free_minutes - (c.needed_minutes or 0)
        return (
            c.busy_at_fixed_time,
            fit_order,
            -spare,
            c.group_work_minutes,
            str(c.user_id),
        )

    ordered = sorted(eligible, key=key)
    return [RankedCandidate(c, rank) for rank, c in enumerate(ordered, start=1)]


@dataclass(frozen=True)
class ReasonFacts:
    """What the LLM may use to explain one member's place. No names, no K."""

    rank: int
    candidates: int
    busy_at_fixed_time: bool
    fits: bool | None
    free_hours: float
    group_work_hours: float
    most_free_time: bool  # this member has the most free minutes of all
    least_group_work: bool  # this member has the fewest group work minutes


def reason_facts(ranked: Sequence[RankedCandidate]) -> dict[UUID, ReasonFacts]:
    """Facts per member, for the reason writer."""
    if not ranked:
        return {}
    most_free = max(r.candidate.free_minutes for r in ranked)
    least_work = min(r.candidate.group_work_minutes for r in ranked)
    return {
        r.candidate.user_id: ReasonFacts(
            rank=r.rank,
            candidates=len(ranked),
            busy_at_fixed_time=r.candidate.busy_at_fixed_time,
            fits=r.fits,
            free_hours=hours(r.candidate.free_minutes),
            group_work_hours=hours(r.candidate.group_work_minutes),
            most_free_time=r.candidate.free_minutes == most_free,
            least_group_work=r.candidate.group_work_minutes == least_work,
        )
        for r in ranked
    }


def hours(minutes: int) -> float:
    """Minutes as hours with one decimal (90 → 1.5)."""
    return round(minutes / MINUTES_PER_HOUR, 1)
