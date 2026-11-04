"""FR-22: a spoken part of the day ("chiều nay", "tomorrow") → a time range in the user's zone.

Morning / afternoon / evening mean today. Borders are whole hours of the user's clock.
"""

from datetime import datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from metanoia_ai.domain.models import TimeRange


class SpokenPeriod(StrEnum):
    """Values the LLM may pick for `schedule_for`."""

    MORNING = "morning"
    AFTERNOON = "afternoon"
    EVENING = "evening"
    TODAY = "today"
    TOMORROW = "tomorrow"


# [start hour, end hour) of today; 24 = midnight at the end of the day.
HOURS = {
    SpokenPeriod.MORNING: (6, 12),
    SpokenPeriod.AFTERNOON: (12, 18),
    SpokenPeriod.EVENING: (18, 24),
    SpokenPeriod.TODAY: (0, 24),
}


def _at(day: datetime, hour: int, time_zone: ZoneInfo) -> datetime:
    """`hour`:00 of that day in the user's zone (24 → 00:00 of the next day)."""
    start = datetime.combine(day.date(), time(0), tzinfo=time_zone)
    return start + timedelta(hours=hour)


def period_range(period: SpokenPeriod, now: datetime, time_zone: ZoneInfo) -> TimeRange:
    """The range of the spoken period, by the user's calendar day."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    today = now.astimezone(time_zone)
    if period is SpokenPeriod.TOMORROW:
        return TimeRange(_at(today, 24, time_zone), _at(today, 48, time_zone))
    first, last = HOURS[period]
    return TimeRange(_at(today, first, time_zone), _at(today, last, time_zone))
