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
from zoneinfo import ZoneInfo

from metanoia_ai.domain.free_time import free_ranges, merge
from metanoia_ai.domain.models import Language, TimeRange, WorkingHours

MINUTES_PER_HOUR = 60
UTC_ZONE = ZoneInfo("UTC")
BEST_TIMES = 2  # free times shown per member (the longest ones)
MIN_FREE_TIME_MINUTES = 30  # shorter gaps are not worth showing
WEEKDAYS = {
    Language.VI: (
        "Thứ Hai",
        "Thứ Ba",
        "Thứ Tư",
        "Thứ Năm",
        "Thứ Sáu",
        "Thứ Bảy",
        "Chủ Nhật",
    ),
    Language.EN: ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"),
}


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
    free_times: tuple[TimeRange, ...] = ()  # the longest gaps on their calendar
    time_zone: ZoneInfo = UTC_ZONE  # the member's own clock, to show times


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


def calendar_gaps(
    hours: WorkingHours,
    now: datetime,
    window_end: datetime,
    busy: Sequence[TimeRange],
) -> list[TimeRange]:
    """Gaps in the working hours from now to `window_end` that nothing is booked in."""
    if window_end <= now:
        return []
    days = math.ceil((window_end - now).total_seconds() / 86_400)
    return [
        TimeRange(r.start, min(r.end, window_end))
        for r in free_ranges(hours, now, days, merge(busy), round_minutes=1)
        if r.start < window_end
    ]


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
    gaps = calendar_gaps(hours, now, window_end, busy)
    return max(sum(g.minutes for g in gaps) - committed_minutes, 0)


def best_free_times(
    gaps: Sequence[TimeRange], count: int = BEST_TIMES
) -> tuple[TimeRange, ...]:
    """The `count` longest gaps (≥ 30 min; the earlier one wins a tie), in time order."""
    long_enough = [g for g in gaps if g.minutes >= MIN_FREE_TIME_MINUTES]
    longest = sorted(long_enough, key=lambda g: (-g.minutes, g.start))[:count]
    return tuple(sorted(longest, key=lambda g: g.start))


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
    """What the LLM may use to explain one member's place. No names, no K.

    Times are written by code in the member's clock; the LLM copies them as they are.
    """

    rank: int
    candidates: int
    busy_at_fixed_time: bool
    fits: bool | None
    free_hours: float
    group_work_hours: float
    most_free_time: bool  # this member has the most free minutes of all
    least_group_work: bool  # this member has the fewest group work minutes
    free_times: tuple[str, ...] = ()  # e.g. "Thứ Ba 24/11, 08:00–12:00"
    first_free: str | None = None  # start of the earliest of those times
    deadline: str | None = None  # e.g. "Thứ Tư 25/11, 17:00"
    is_current_assignee: bool = False
    overdue: bool = False  # the deadline has passed: no deadline text is given
    free_text: str = ""  # free_hours written for the user, e.g. "9,5 giờ"
    group_work_text: str = ""  # e.g. "2 giờ"

    def written_texts(self) -> str:
        """Every date and time the reason may quote (to catch invented ones)."""
        return " ".join([*self.free_times, self.first_free or "", self.deadline or ""])


def reason_facts(
    ranked: Sequence[RankedCandidate],
    language: Language = Language.VI,
    deadline: datetime | None = None,
    now: datetime | None = None,
) -> dict[UUID, ReasonFacts]:
    """Facts per member, for the reason writer.

    A deadline already passed (before `now`) is not shown: the facts say `overdue`
    instead, so a reason never puts a free time "before" a past deadline.
    """
    overdue = deadline is not None and now is not None and deadline <= now
    if overdue:
        deadline = None
    if not ranked:
        return {}
    most_free = max(r.candidate.free_minutes for r in ranked)
    least_work = min(r.candidate.group_work_minutes for r in ranked)
    result = {}
    for r in ranked:
        c = r.candidate
        result[c.user_id] = ReasonFacts(
            rank=r.rank,
            candidates=len(ranked),
            busy_at_fixed_time=c.busy_at_fixed_time,
            fits=r.fits,
            free_hours=hours(c.free_minutes),
            group_work_hours=hours(c.group_work_minutes),
            most_free_time=c.free_minutes == most_free,
            least_group_work=c.group_work_minutes == least_work,
            free_times=tuple(
                format_range(t, c.time_zone, language) for t in c.free_times
            ),
            first_free=(
                format_moment(c.free_times[0].start, c.time_zone, language)
                if c.free_times
                else None
            ),
            deadline=(
                None
                if deadline is None
                else format_moment(deadline, c.time_zone, language)
            ),
            is_current_assignee=c.is_current_assignee,
            overdue=overdue,
            free_text=hours_text(c.free_minutes, language),
            group_work_text=hours_text(c.group_work_minutes, language),
        )
    return result


def format_moment(moment: datetime, zone: ZoneInfo, language: Language) -> str:
    """'Thứ Ba 24/11, 08:00' / 'Tue 24/11, 08:00' on the member's clock."""
    local = moment.astimezone(zone)
    day = WEEKDAYS[language][local.weekday()]
    return f"{day} {local:%d/%m}, {local:%H:%M}"


def format_range(time: TimeRange, zone: ZoneInfo, language: Language) -> str:
    """'Thứ Ba 24/11, 08:00–12:00'; a range over midnight shows both days."""
    start, end = time.start.astimezone(zone), time.end.astimezone(zone)
    if start.date() == end.date():
        return f"{format_moment(start, zone, language)}–{end:%H:%M}"
    return (
        f"{format_moment(start, zone, language)} – {format_moment(end, zone, language)}"
    )


def hours_text(minutes: int, language: Language) -> str:
    """'9,5 giờ' / '9.5 hours', no '.0' (12 giờ, 1 hour)."""
    value = hours(minutes)
    number = f"{value:g}"
    if language is Language.VI:
        return f"{number.replace('.', ',')} giờ"
    return f"{number} hour" + ("" if value == 1 else "s")


def hours(minutes: int) -> float:
    """Minutes as hours with one decimal (90 → 1.5)."""
    return round(minutes / MINUTES_PER_HOUR, 1)
