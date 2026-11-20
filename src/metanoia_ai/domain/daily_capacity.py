"""FR-10d: how much a user usually works on a busy day, and which planned days go over it.

No hard daily cap (freelancers may work 12-14 h): the capacity is learned from the
user's own timer data and only used to WARN about a heavy day, never to block.
"""

import math
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from zoneinfo import ZoneInfo

from metanoia_ai.domain.models import (
    PlannedSlot,
    ScheduleWarning,
    TimeRange,
    WarningType,
)

CAPACITY_PERCENTILE = 0.8  # a busy day, but not the most extreme one
ROUND_TO_MINUTES = 15
LOOKBACK_DAYS = 60  # timer data older than this is not read
MIN_DAYS = 5  # fewer days with work → no capacity, no warning
# A day counts as a work day only with at least this much timed work: days of a few
# test clicks would teach a tiny capacity and warn about every planned day.
MIN_DAY_MINUTES = 60


def minutes_by_day(
    sessions: Iterable[TimeRange], today: date, time_zone: ZoneInfo
) -> dict[date, int]:
    """Worked minutes per local day before `today`. A session counts on its start day."""
    worked: dict[date, int] = {}
    for session in sessions:
        day = session.start.astimezone(time_zone).date()
        if day >= today:
            continue  # today is not over yet
        minutes = int((session.end - session.start).total_seconds() // 60)
        worked[day] = worked.get(day, 0) + minutes
    return worked


def daily_capacity(day_minutes: Sequence[int], min_days: int) -> int | None:
    """The 80th percentile of worked minutes per day, rounded down to 15 minutes.

    Only days with at least MIN_DAY_MINUTES count. None when fewer than `min_days`
    such days: too little to learn from.
    """
    days = sorted(m for m in day_minutes if m >= MIN_DAY_MINUTES)
    if len(days) < max(min_days, 1):
        return None
    index = math.ceil(CAPACITY_PERCENTILE * len(days)) - 1  # nearest-rank
    value = days[index]
    return max(ROUND_TO_MINUTES, value // ROUND_TO_MINUTES * ROUND_TO_MINUTES)


def overloaded_days(
    slots: Sequence[PlannedSlot],
    approved_load: Mapping[date, int],
    capacity: int | None,
    time_zone: ZoneInfo,
) -> list[ScheduleWarning]:
    """One warning per day with new slots whose total work goes over `capacity`."""
    if capacity is None:
        return []
    planned: dict[date, int] = {}
    for slot in slots:
        day = slot.start.astimezone(time_zone).date()
        minutes = int((slot.end - slot.start).total_seconds() // 60)
        planned[day] = planned.get(day, 0) + minutes
    warnings = []
    for day in sorted(planned):
        total = planned[day] + approved_load.get(day, 0)
        if total > capacity:
            warnings.append(
                ScheduleWarning(
                    WarningType.DAY_OVERLOADED,
                    needed_minutes=total,
                    day=day,
                    capacity_minutes=capacity,
                )
            )
    return warnings
