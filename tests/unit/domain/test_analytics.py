from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from metanoia_ai.domain.analytics import (
    AnalyticsRange,
    BiasConfidence,
    BiasDirection,
    DeadlineTask,
    KUpdate,
    Period,
    TaskStatus,
    bias_sort_key,
    classify_bias,
    completion_stats,
    period_for,
    week_start,
    weekly_trend,
)
from metanoia_ai.domain.models import TaskType

VN = ZoneInfo("Asia/Ho_Chi_Minh")
NOW = datetime(2026, 10, 29, 17, 20, tzinfo=VN)
FLEX = TaskType.FLEXIBLE


def task(
    status: TaskStatus,
    deadline_days_ago: float,
    completed_days_ago: float | None = None,
    task_type: TaskType = FLEX,
) -> DeadlineTask:
    completed = (
        None if completed_days_ago is None else NOW - timedelta(days=completed_days_ago)
    )
    return DeadlineTask(
        task_type, status, NOW - timedelta(days=deadline_days_ago), completed
    )


# --- period_for ---


def test_period_30d_starts_at_local_midnight() -> None:
    period = period_for(AnalyticsRange.LAST_30_DAYS, NOW.astimezone(UTC), VN)

    assert period.start == datetime(2026, 9, 29, 0, 0, tzinfo=VN)
    assert period.end == NOW
    assert period.end.utcoffset() == timedelta(hours=7)


def test_period_90d_and_all() -> None:
    assert period_for(AnalyticsRange.LAST_90_DAYS, NOW, VN).start == datetime(
        2026, 7, 31, tzinfo=VN
    )
    assert period_for(AnalyticsRange.ALL, NOW, VN) == Period(start=None, end=NOW)


def test_period_needs_aware_now() -> None:
    with pytest.raises(ValueError):
        period_for(AnalyticsRange.ALL, datetime(2026, 10, 29), VN)  # noqa: DTZ001 - no time zone on purpose


# --- completion (T1, T2) ---


# T1
def test_completion_counts_and_on_time() -> None:
    tasks = [
        task(TaskStatus.DONE, 5, completed_days_ago=6),  # on time
        task(TaskStatus.DONE, 5, completed_days_ago=5),  # on time (equal)
        task(TaskStatus.DONE, 3, completed_days_ago=4),  # on time
        task(TaskStatus.DONE, 1, completed_days_ago=2),  # on time
        task(TaskStatus.DONE, 8, completed_days_ago=7),  # late
        task(TaskStatus.DONE, 2),  # no CompletedAt: done, not on time
        task(TaskStatus.SKIPPED, 4),
        task(TaskStatus.TODO, 3),  # overdue, open
        task(TaskStatus.IN_PROGRESS, 1),  # overdue, open
        task(TaskStatus.TODO, -2),  # deadline not reached yet
    ]

    stats = completion_stats(tasks, period_for(AnalyticsRange.LAST_30_DAYS, NOW, VN))

    assert (stats.total, stats.done, stats.skipped, stats.overdue_open) == (9, 6, 1, 2)
    assert stats.on_time_done == 4
    assert stats.completion_rate == Decimal("0.667")
    assert stats.on_time_rate == Decimal("0.667")
    assert stats.done_without_completed_at == 1


# T2
def test_completion_ignores_fixed_and_out_of_range_tasks() -> None:
    tasks = [
        task(TaskStatus.DONE, 1, 1, task_type=TaskType.FIXED),
        task(TaskStatus.DONE, 40, 41),  # before the 30-day range
        DeadlineTask(FLEX, TaskStatus.TODO, None, None),  # bad data
    ]

    stats = completion_stats(tasks, period_for(AnalyticsRange.LAST_30_DAYS, NOW, VN))

    assert stats.total == 0
    assert stats.completion_rate is None
    assert stats.on_time_rate is None
    assert stats.missing_deadline == 1
    all_time = completion_stats(tasks, period_for(AnalyticsRange.ALL, NOW, VN))
    assert all_time.total == 1


# --- bias (T3, T4) ---


# T3
@pytest.mark.parametrize(
    ("samples", "confidence"),
    [
        (0, BiasConfidence.NONE),
        (4, BiasConfidence.NONE),
        (5, BiasConfidence.LEARNING),
        (9, BiasConfidence.LEARNING),
        (10, BiasConfidence.CONFIDENT),
    ],
)
def test_confidence_by_samples(samples: int, confidence: BiasConfidence) -> None:
    view = classify_bias(Decimal("1.31"), samples)

    assert view.confidence is confidence
    assert view.samples == samples
    if confidence is BiasConfidence.NONE:
        assert (view.factor, view.direction, view.percent) == (None, None, None)


# T4
@pytest.mark.parametrize(
    ("k", "direction", "percent"),
    [
        ("1.09", BiasDirection.ACCURATE, None),
        ("1.10", BiasDirection.SLOWER, 10),
        ("0.90", BiasDirection.FASTER, 10),
        ("0.91", BiasDirection.ACCURATE, None),
        ("1.31", BiasDirection.SLOWER, 31),
        ("0.70", BiasDirection.FASTER, 30),
    ],
)
def test_direction_and_percent(
    k: str, direction: BiasDirection, percent: int | None
) -> None:
    view = classify_bias(Decimal(k), 12)

    assert view.direction is direction
    assert view.percent == percent
    assert view.factor == Decimal(k)


def test_bias_thresholds_come_from_arguments() -> None:
    view = classify_bias(
        Decimal("1.15"),
        3,
        min_samples=2,
        confident_samples=3,
        neutral_band=Decimal("0.2"),
    )

    assert view.confidence is BiasConfidence.CONFIDENT
    assert view.direction is BiasDirection.ACCURATE


def test_bias_sort_order() -> None:
    views = [
        classify_bias(Decimal("1.30"), 2),  # none
        classify_bias(Decimal("1.20"), 6),  # learning
        classify_bias(Decimal("0.60"), 12),  # confident, gap 0.40
        classify_bias(Decimal("1.15"), 12),  # confident, gap 0.15
    ]

    ordered = sorted(views, key=bias_sort_key)

    assert [(v.confidence, v.factor) for v in ordered] == [
        (BiasConfidence.CONFIDENT, Decimal("0.60")),
        (BiasConfidence.CONFIDENT, Decimal("1.15")),
        (BiasConfidence.LEARNING, Decimal("1.20")),
        (BiasConfidence.NONE, None),
    ]


# --- weekly trend (T5) ---


# T5
def test_week_border_uses_user_time_zone() -> None:
    sunday_late = datetime(2026, 10, 18, 23, 30, tzinfo=VN)  # Sunday 16:30 UTC
    monday_early = datetime(2026, 10, 19, 0, 30, tzinfo=VN)  # Sunday 17:30 UTC
    assert sunday_late.astimezone(UTC).date() == monday_early.astimezone(UTC).date()

    updates = [
        KUpdate(monday_early.astimezone(UTC), Decimal("1.33")),
        KUpdate(sunday_late.astimezone(UTC), Decimal("1.36")),
        KUpdate(datetime(2026, 10, 13, 9, tzinfo=VN), Decimal("1.40")),
    ]

    trend = weekly_trend(updates, period_for(AnalyticsRange.ALL, NOW, VN), VN)

    assert [(p.week_start, p.factor, p.samples) for p in trend] == [
        (date(2026, 10, 12), Decimal("1.36"), 2),
        (date(2026, 10, 19), Decimal("1.33"), 1),
    ]


def test_trend_skips_updates_outside_period_and_empty_weeks() -> None:
    updates = [
        KUpdate(datetime(2026, 8, 1, tzinfo=VN), Decimal("1.2")),  # before 30d
        KUpdate(datetime(2026, 10, 1, tzinfo=VN), Decimal("1.1")),
        KUpdate(datetime(2026, 10, 27, tzinfo=VN), Decimal("1.0")),
    ]

    trend = weekly_trend(updates, period_for(AnalyticsRange.LAST_30_DAYS, NOW, VN), VN)

    assert [p.week_start for p in trend] == [date(2026, 9, 28), date(2026, 10, 26)]
    assert weekly_trend([], period_for(AnalyticsRange.ALL, NOW, VN), VN) == []


def test_week_start_is_monday() -> None:
    assert week_start(datetime(2026, 10, 25, 12, tzinfo=VN), VN) == date(2026, 10, 19)
    assert week_start(datetime(2026, 10, 26, 0, tzinfo=VN), VN) == date(2026, 10, 26)
