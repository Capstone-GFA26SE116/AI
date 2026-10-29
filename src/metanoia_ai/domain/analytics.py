"""FR-20 personal analytics: pure rules, no DB, no LLM.

Numbers for the Dashboard: completion and on-time rates, the learned estimation bias
per work domain (with a weekly trend), and planned vs actual time.
All dates and week borders use the user's time zone.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from zoneinfo import ZoneInfo

from metanoia_ai.domain.models import TaskType

# Defaults; the application passes values from config (plan §2).
BIAS_MIN_SAMPLES = 5  # below: not enough data (#3)
BIAS_CONFIDENT_SAMPLES = 10  # from here: no "still learning" note (#3)
BIAS_NEUTRAL_BAND = Decimal("0.10")  # |K - 1| below this: "fairly accurate" (#4)
RATE_PLACES = Decimal("0.001")


class AnalyticsRange(StrEnum):
    """Time range picked on the Dashboard. Values are sent in the API."""

    LAST_30_DAYS = "30d"
    LAST_90_DAYS = "90d"
    ALL = "all"


RANGE_DAYS = {AnalyticsRange.LAST_30_DAYS: 30, AnalyticsRange.LAST_90_DAYS: 90}


class TaskStatus(StrEnum):
    """Task states the analytics reads (DB strings are converted in infrastructure)."""

    TODO = "todo"
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    SKIPPED = "skipped"


class BiasConfidence(StrEnum):
    """How much the learned bias of a domain can be shown (#3). Sent in the API."""

    NONE = "none"
    LEARNING = "learning"
    CONFIDENT = "confident"


class BiasDirection(StrEnum):
    """Which way the user's estimates are off (#4). Sent in the API."""

    SLOWER = "slower"
    FASTER = "faster"
    ACCURATE = "accurate"


@dataclass(frozen=True)
class Period:
    """[start, end] in the user's time zone; start is None for the "all" range."""

    start: datetime | None
    end: datetime

    def contains(self, moment: datetime) -> bool:
        return (self.start is None or self.start <= moment) and moment <= self.end


def period_for(range_: AnalyticsRange, now: datetime, time_zone: ZoneInfo) -> Period:
    """30d / 90d: from 00:00 (user's time) of today minus N days, up to now."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    local_now = now.astimezone(time_zone)
    if range_ is AnalyticsRange.ALL:
        return Period(start=None, end=local_now)
    first_day = local_now.date() - timedelta(days=RANGE_DAYS[range_])
    return Period(
        start=datetime.combine(first_day, time(0), tzinfo=time_zone), end=local_now
    )


# --- Completion (#6, #7) ---


@dataclass(frozen=True)
class DeadlineTask:
    """What the completion rate needs from one task."""

    task_type: TaskType
    status: TaskStatus
    deadline: datetime | None
    completed_at: datetime | None


@dataclass(frozen=True)
class CompletionStats:
    total: int
    done: int
    skipped: int
    overdue_open: int
    on_time_done: int
    completion_rate: Decimal | None  # None when total = 0
    on_time_rate: Decimal | None  # over done tasks; None when done = 0
    missing_deadline: int = 0  # bad data: Flexible task without deadline (skipped)
    done_without_completed_at: int = 0  # bad data: counted done, never on time


def _rate(part: int, whole: int) -> Decimal | None:
    if whole == 0:
        return None
    return (Decimal(part) / Decimal(whole)).quantize(
        RATE_PLACES, rounding=ROUND_HALF_UP
    )


def completion_stats(tasks: Iterable[DeadlineTask], period: Period) -> CompletionStats:
    """Flexible tasks whose deadline is inside the period (and not in the future)."""
    done = skipped = overdue_open = on_time = missing_deadline = no_completed_at = 0
    for task in tasks:
        if task.task_type is not TaskType.FLEXIBLE:
            continue
        if task.deadline is None:
            missing_deadline += 1
            continue
        if not period.contains(task.deadline):
            continue
        if task.status is TaskStatus.DONE:
            done += 1
            if task.completed_at is None:
                no_completed_at += 1
            elif task.completed_at <= task.deadline:
                on_time += 1
        elif task.status is TaskStatus.SKIPPED:
            skipped += 1
        else:
            overdue_open += 1
    total = done + skipped + overdue_open
    return CompletionStats(
        total=total,
        done=done,
        skipped=skipped,
        overdue_open=overdue_open,
        on_time_done=on_time,
        completion_rate=_rate(done, total),
        on_time_rate=_rate(on_time, done),
        missing_deadline=missing_deadline,
        done_without_completed_at=no_completed_at,
    )


# --- Learned bias per domain (#3, #4) ---


@dataclass(frozen=True)
class BiasView:
    confidence: BiasConfidence
    factor: Decimal | None  # current K; None when confidence is NONE
    direction: BiasDirection | None  # None when confidence is NONE
    percent: int | None  # |K - 1| x 100, rounded; None when ACCURATE or NONE
    samples: int


def classify_bias(
    k_bias: Decimal,
    samples: int,
    min_samples: int = BIAS_MIN_SAMPLES,
    confident_samples: int = BIAS_CONFIDENT_SAMPLES,
    neutral_band: Decimal = BIAS_NEUTRAL_BAND,
) -> BiasView:
    """Say how sure we are about a domain's bias, and which way it goes."""
    if samples < min_samples:
        return BiasView(BiasConfidence.NONE, None, None, None, samples)
    confidence = (
        BiasConfidence.CONFIDENT
        if samples >= confident_samples
        else BiasConfidence.LEARNING
    )
    gap = k_bias - 1
    if abs(gap) < neutral_band:
        return BiasView(confidence, k_bias, BiasDirection.ACCURATE, None, samples)
    direction = BiasDirection.SLOWER if gap > 0 else BiasDirection.FASTER
    percent = int((abs(gap) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return BiasView(confidence, k_bias, direction, percent, samples)


CONFIDENCE_ORDER = {
    BiasConfidence.CONFIDENT: 0,
    BiasConfidence.LEARNING: 1,
    BiasConfidence.NONE: 2,
}


def bias_sort_key(view: BiasView) -> tuple[int, Decimal]:
    """Confident first, then learning, then none; inside a group the biggest gap first."""
    gap = abs(view.factor - 1) if view.factor is not None else Decimal(0)
    return (CONFIDENCE_ORDER[view.confidence], -gap)


# --- Weekly trend (#8) ---


@dataclass(frozen=True)
class KUpdate:
    """One K_bias update of a domain (a KBiasUpdateLog row)."""

    created_at: datetime
    new_k: Decimal


@dataclass(frozen=True)
class WeekPoint:
    week_start: date  # Monday, in the user's time zone
    factor: Decimal  # K after the last update of that week
    samples: int  # updates in that week


def week_start(moment: datetime, time_zone: ZoneInfo) -> date:
    """Monday of the week that holds this moment, in the user's time zone."""
    day = moment.astimezone(time_zone).date()
    return day - timedelta(days=day.weekday())


def weekly_trend(
    updates: Sequence[KUpdate], period: Period, time_zone: ZoneInfo
) -> list[WeekPoint]:
    """One point per week that has updates inside the period; empty weeks get no point."""
    weeks: dict[date, list[KUpdate]] = {}
    for update in sorted(updates, key=lambda u: u.created_at):
        if period.contains(update.created_at):
            weeks.setdefault(week_start(update.created_at, time_zone), []).append(
                update
            )
    return [
        WeekPoint(week_start=week, factor=items[-1].new_k, samples=len(items))
        for week, items in sorted(weeks.items())
    ]
