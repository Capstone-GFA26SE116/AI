from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest

from metanoia_ai.domain.analytics import (
    AnalyticsRange,
    BiasConfidence,
    BiasDirection,
    BreakdownBy,
    BreakdownItem,
    CompletionStats,
    DeadlineTask,
    DomainBias,
    DoneTask,
    InsightCode,
    KUpdate,
    Period,
    TaskStatus,
    bias_sort_key,
    breakdown,
    classify_bias,
    completion_stats,
    difference_percent,
    period_for,
    select_insights,
    week_start,
    weekly_trend,
)
from metanoia_ai.domain.models import (
    ActualTimeSource,
    EstimateSource,
    FocusSessionRecord,
    TaskType,
)

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


# --- breakdown (T6, T7, T8) ---

PROJECT_A = UUID(int=1)
PROJECT_B = UUID(int=2)
PROJECT_C = UUID(int=3)
CLIENT_X = UUID(int=10)
ALL_TIME = period_for(AnalyticsRange.ALL, NOW, VN)


def session(
    minutes: int, ended: bool = True, confirmed: bool = True
) -> FocusSessionRecord:
    start = NOW - timedelta(days=1)
    return FocusSessionRecord(
        started_at=start,
        ended_at=start + timedelta(minutes=minutes) if ended else None,
        source=ActualTimeSource.TIMER,
        is_confirmed=confirmed,
    )


def done(
    estimated: int | None = 60,
    sessions: tuple[FocusSessionRecord, ...] = (),
    project: UUID = PROJECT_A,
    client: UUID | None = CLIENT_X,
    ai_estimated: int | None = None,
    completed_days_ago: float = 1,
    title: str = "Task",
) -> DoneTask:
    return DoneTask(
        task_id=uuid4(),
        title=title,
        project_id=project,
        project_name=f"Project {project.int}",
        client_id=client,
        client_name=None if client is None else "Client X",
        domain_name="Writing",
        estimated_minutes=estimated,
        ai_estimated_minutes=ai_estimated,
        completed_at=NOW - timedelta(days=completed_days_ago),
        sessions=sessions,
    )


def test_difference_percent() -> None:
    assert difference_percent(600, 750) == 25
    assert difference_percent(60, 45) == -25
    assert difference_percent(3, 4) == 33
    with pytest.raises(ValueError):
        difference_percent(0, 10)


def test_project_totals() -> None:
    tasks = [done(60, (session(90),)), done(120, (session(60), session(30)))]

    result = breakdown(tasks, BreakdownBy.PROJECT, ALL_TIME)

    assert result.items == [
        BreakdownItem(
            id=PROJECT_A,
            name="Project 1",
            task_count=2,
            missing_actual_count=0,
            estimated_minutes=180,
            actual_minutes=180,
            difference_percent=0,
        )
    ]


# T6
def test_unusable_actual_time_is_counted_as_missing() -> None:
    tasks = [
        done(60, (session(90),)),
        done(60, (session(30), session(10, ended=False))),  # still running
        done(60, (session(30, confirmed=False),)),  # not confirmed
        done(60, ()),  # no session
    ]

    item = breakdown(tasks, BreakdownBy.PROJECT, ALL_TIME).items[0]

    assert (item.task_count, item.missing_actual_count) == (1, 3)
    assert (item.estimated_minutes, item.actual_minutes) == (60, 90)


def test_group_with_only_missing_time_is_left_out() -> None:
    tasks = [done(60, (), project=PROJECT_B), done(60, (session(60),))]

    items = breakdown(tasks, BreakdownBy.PROJECT, ALL_TIME).items

    assert [i.id for i in items] == [PROJECT_A]


# T7
def test_task_rows_show_estimate_source() -> None:
    tasks = [
        done(
            90, (session(90),), ai_estimated=90, title="AI number", completed_days_ago=2
        ),
        done(
            80,
            (session(90),),
            ai_estimated=90,
            title="User changed",
            completed_days_ago=1,
        ),
        done(60, (session(60),), title="User only", completed_days_ago=3),
    ]

    items = breakdown(tasks, BreakdownBy.TASK, ALL_TIME).items

    assert [(i.name, i.estimate_source) for i in items] == [
        ("User changed", EstimateSource.USER),
        ("AI number", EstimateSource.AI),
        ("User only", EstimateSource.USER),
    ]
    assert items[0].project_name == "Project 1"
    assert items[0].domain_name == "Writing"
    assert items[0].completed_at == NOW - timedelta(days=1)


# T8
def test_client_groups_projects_without_client_together() -> None:
    tasks = [
        done(60, (session(60),), project=PROJECT_A, client=CLIENT_X),
        done(60, (session(90),), project=PROJECT_B, client=None),
        done(60, (session(30),), project=PROJECT_C, client=None),
    ]

    items = breakdown(tasks, BreakdownBy.CLIENT, ALL_TIME).items

    no_client = next(i for i in items if i.id is None)
    assert no_client.name is None
    assert (
        no_client.task_count,
        no_client.estimated_minutes,
        no_client.actual_minutes,
    ) == (
        2,
        120,
        120,
    )
    assert {i.id for i in items} == {CLIENT_X, None}


def test_breakdown_sorting_limit_range_and_bad_data() -> None:
    tasks = [
        done(100, (session(110),), project=PROJECT_A),  # +10
        done(100, (session(50),), project=PROJECT_B),  # -50
        done(
            100, (session(130),), project=PROJECT_C, completed_days_ago=40
        ),  # +30, old
        done(None, (session(30),)),  # bad data
    ]
    last_30 = period_for(AnalyticsRange.LAST_30_DAYS, NOW, VN)

    result = breakdown(tasks, BreakdownBy.PROJECT, last_30)

    assert [i.difference_percent for i in result.items] == [-50, 10]
    assert result.missing_estimate == 1
    by_task = breakdown(tasks, BreakdownBy.TASK, ALL_TIME, limit=2).items
    assert [i.completed_at for i in by_task] == [NOW - timedelta(days=1)] * 2


# --- insights (T10) ---


def domain(name: str, k: str, samples: int = 12) -> DomainBias:
    return DomainBias(uuid4(), name, classify_bias(Decimal(k), samples))


def project_item(percent: int, tasks: int = 4, name: str = "P") -> BreakdownItem:
    return BreakdownItem(uuid4(), name, tasks, 0, 100, 100 + percent, percent)


def completion(total: int, done_count: int) -> CompletionStats:
    rate = Decimal(done_count) / total if total else None
    return CompletionStats(
        total, done_count, 0, total - done_count, done_count, rate, None
    )


GOOD = completion(10, 9)


# T10
def test_insights_full_order_and_limit() -> None:
    domains = [
        domain("Writing", "1.31"),
        domain("Design", "0.60"),
        domain("Coding", "1.50", samples=6),  # learning still counts
    ]
    projects = [project_item(25, name="Landing"), project_item(-40, name="Big")]

    insights = select_insights(domains, projects, completion(10, 4))

    assert [i.code for i in insights] == [
        InsightCode.DOMAIN_SLOWER,
        InsightCode.DOMAIN_FASTER,
        InsightCode.PROJECT_UNDER,
    ]
    assert [i.domain.domain_name for i in insights[:2]] == ["Coding", "Design"]
    assert insights[2].project.name == "Big"


def test_insights_skip_unsure_and_accurate_domains() -> None:
    domains = [domain("A", "1.50", samples=4), domain("B", "1.05")]

    insights = select_insights(domains, [project_item(30)], completion(10, 5))

    assert [i.code for i in insights] == [
        InsightCode.PROJECT_OVER,
        InsightCode.LOW_COMPLETION,
    ]


def test_project_insight_needs_enough_tasks_and_gap() -> None:
    projects = [project_item(50, tasks=2), project_item(19, tasks=10)]

    assert select_insights([], projects, GOOD)[0].code is InsightCode.NOT_ENOUGH_DATA


def test_low_completion_needs_five_tasks() -> None:
    assert (
        select_insights([], [], completion(4, 0))[0].code is InsightCode.NOT_ENOUGH_DATA
    )
    assert (
        select_insights([], [], completion(5, 2))[0].code is InsightCode.LOW_COMPLETION
    )
    assert (
        select_insights([], [], completion(5, 3))[0].code is InsightCode.NOT_ENOUGH_DATA
    )


def test_insight_thresholds_from_arguments() -> None:
    insights = select_insights(
        [],
        [project_item(10, tasks=1)],
        GOOD,
        limit=1,
        project_min_percent=10,
        project_min_tasks=1,
    )

    assert [i.code for i in insights] == [InsightCode.PROJECT_OVER]
