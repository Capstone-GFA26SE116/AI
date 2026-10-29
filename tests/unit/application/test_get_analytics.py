import asyncio
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest

from metanoia_ai.application.ports.analytics_repository import AnalyticsDomainProfile
from metanoia_ai.application.use_cases.get_analytics_breakdown import (
    GetAnalyticsBreakdown,
)
from metanoia_ai.application.use_cases.get_analytics_overview import (
    AnalyticsSettings,
    GetAnalyticsOverview,
)
from metanoia_ai.application.use_cases.recompute_productive_hours import (
    UserNotFoundError,
)
from metanoia_ai.application.use_cases.start_project_chat import ProjectNotFoundError
from metanoia_ai.domain.analytics import (
    AnalyticsRange,
    BiasConfidence,
    BreakdownBy,
    DeadlineTask,
    DoneTask,
    InsightCode,
    KUpdate,
    TaskStatus,
)
from metanoia_ai.domain.models import (
    ActualTimeSource,
    EstimateSource,
    FocusSessionRecord,
    Language,
    TaskType,
)
from tests.unit.application.fakes import FakeAnalyticsRepository, FakeAnalyticsUser

VN = ZoneInfo("Asia/Ho_Chi_Minh")
NOW = datetime(2026, 10, 29, 17, 20, tzinfo=VN)
USER_A = UUID(int=1)
USER_B = UUID(int=2)
PROJECT_A = UUID(int=11)
PROJECT_B = UUID(int=12)
SETTINGS = AnalyticsSettings(
    bias_min_samples=5,
    bias_confident_samples=10,
    bias_neutral_band=Decimal("0.10"),
    project_insight_min_percent=20,
    project_insight_min_tasks=3,
    insight_limit=3,
)
D30 = AnalyticsRange.LAST_30_DAYS


def done_task(
    project: UUID,
    estimated: int,
    actual: int,
    days_ago: float = 1,
    title: str = "Write post",
    ai_estimated: int | None = None,
) -> DoneTask:
    start = NOW - timedelta(days=days_ago, hours=3)
    return DoneTask(
        task_id=uuid4(),
        title=title,
        project_id=project,
        project_name=f"Project {project.int}",
        client_id=None,
        client_name=None,
        domain_name="Writing",
        estimated_minutes=estimated,
        ai_estimated_minutes=ai_estimated,
        completed_at=NOW - timedelta(days=days_ago),
        sessions=(
            FocusSessionRecord(
                start, start + timedelta(minutes=actual), ActualTimeSource.TIMER
            ),
        ),
    )


def profile(name: str, k: str, samples: int) -> AnalyticsDomainProfile:
    updates = (
        KUpdate(datetime(2026, 10, 6, 9, tzinfo=VN), Decimal("1.36")),
        KUpdate(datetime(2026, 10, 14, 9, tzinfo=VN), Decimal(k)),
    )
    return AnalyticsDomainProfile(uuid4(), name, Decimal(k), samples, updates)


def make_repo() -> FakeAnalyticsRepository:
    repo = FakeAnalyticsRepository()
    repo.add_user(
        USER_A,
        FakeAnalyticsUser(
            time_zone=VN,
            project_ids={PROJECT_A},
            deadline_tasks=[
                DeadlineTask(
                    TaskType.FLEXIBLE,
                    TaskStatus.DONE,
                    NOW - timedelta(days=2),
                    NOW - timedelta(days=3),
                ),
                DeadlineTask(
                    TaskType.FLEXIBLE, TaskStatus.TODO, NOW - timedelta(days=1), None
                ),
            ],
            profiles=[profile("Design", "1.05", 12), profile("Writing", "1.31", 12)],
            done_tasks=[done_task(PROJECT_A, 60, 75, days_ago=d) for d in (1, 2, 3)],
        ),
    )
    repo.add_user(
        USER_B,
        FakeAnalyticsUser(
            time_zone=VN,
            project_ids={PROJECT_B},
            done_tasks=[done_task(PROJECT_B, 60, 30, title="B secret")],
        ),
    )
    return repo


def overview(repo, user=USER_A, range_=D30, lang=Language.VI):
    use_case = GetAnalyticsOverview(repo, SETTINGS, lambda: NOW)
    return asyncio.run(use_case.execute(user, range_, lang))


def by(
    repo, kind: BreakdownBy, user=USER_A, project_id=None, limit=None, lang=Language.VI
):
    use_case = GetAnalyticsBreakdown(repo, lambda: NOW)
    return asyncio.run(use_case.execute(user, kind, D30, lang, project_id, limit))


def test_overview_puts_everything_together() -> None:
    result = overview(make_repo())

    assert result.period.start == datetime(2026, 9, 29, tzinfo=VN)
    assert (result.completion.stats.total, result.completion.stats.done) == (2, 1)
    assert result.completion.text == (
        "Bạn hoàn thành 1/2 việc (50%), trong đó 1 việc xong đúng hạn."
    )
    assert [d.domain.domain_name for d in result.domains] == ["Writing", "Design"]
    writing = result.domains[0]
    assert writing.text == "Bạn thường cần thêm khoảng 30% thời gian cho Writing."
    assert [(p.week_start, p.factor) for p in writing.trend] == [
        (date(2026, 10, 5), Decimal("1.36")),
        (date(2026, 10, 12), Decimal("1.31")),
    ]
    assert [(i.code, i.text) for i in result.insights] == [
        (InsightCode.DOMAIN_SLOWER, writing.text),
        (
            InsightCode.PROJECT_OVER,
            "Dự án Project 11 tốn nhiều hơn dự kiến khoảng 25%.",
        ),
    ]


def test_overview_reads_with_the_asked_period() -> None:
    repo = make_repo()

    overview(repo, range_=AnalyticsRange.ALL, lang=Language.EN)

    assert all(p.start is None for p in repo.periods)
    assert len(repo.periods) == 3  # deadline tasks, profiles, done tasks


def test_settings_change_the_bias_class() -> None:
    repo = make_repo()
    strict = AnalyticsSettings(5, 20, Decimal("0.40"), 20, 3, 1)

    result = asyncio.run(
        GetAnalyticsOverview(repo, strict, lambda: NOW).execute(
            USER_A, D30, Language.VI
        )
    )

    assert result.domains[0].domain.view.confidence is BiasConfidence.LEARNING
    assert len(result.insights) == 1
    assert result.insights[0].code is InsightCode.PROJECT_OVER


# T11
def test_new_user_gets_zeros_and_not_enough_data() -> None:
    repo = FakeAnalyticsRepository()
    new_user = uuid4()
    repo.add_user(new_user, FakeAnalyticsUser(time_zone=VN))

    result = overview(repo, user=new_user)

    stats = result.completion.stats
    assert (stats.total, stats.done, stats.on_time_done) == (0, 0, 0)
    assert stats.completion_rate is None and stats.on_time_rate is None
    assert result.domains == ()
    assert [i.code for i in result.insights] == [InsightCode.NOT_ENOUGH_DATA]
    assert result.insights[0].text.startswith("Chưa đủ dữ liệu")
    assert by(repo, BreakdownBy.PROJECT, user=new_user).rows == ()


def test_unknown_user_is_not_found() -> None:
    with pytest.raises(UserNotFoundError):
        overview(make_repo(), user=uuid4())
    with pytest.raises(UserNotFoundError):
        by(make_repo(), BreakdownBy.TASK, user=uuid4())


# T12
def test_project_of_another_user_is_not_found() -> None:
    with pytest.raises(ProjectNotFoundError):
        by(make_repo(), BreakdownBy.TASK, user=USER_A, project_id=PROJECT_B)


def test_breakdown_by_project_names_and_text() -> None:
    result = by(make_repo(), BreakdownBy.PROJECT)

    [row] = result.rows
    assert (row.item.id, row.name, row.item.difference_percent) == (
        PROJECT_A,
        "Project 11",
        25,
    )
    assert row.text == "Tốn nhiều hơn dự kiến khoảng 25%."
    assert result.time_zone == VN


def test_breakdown_by_task_filters_project_and_limits() -> None:
    repo = make_repo()
    repo.users[USER_A].project_ids.add(PROJECT_B)
    repo.users[USER_A].done_tasks.append(done_task(PROJECT_B, 60, 60, ai_estimated=60))

    rows = by(repo, BreakdownBy.TASK, project_id=PROJECT_A, limit=2).rows

    assert len(rows) == 2
    assert all(r.item.project_name == "Project 11" for r in rows)
    other = by(repo, BreakdownBy.TASK, project_id=PROJECT_B).rows
    assert [r.item.estimate_source for r in other] == [EstimateSource.AI]


def test_no_client_group_is_named_in_the_asked_language() -> None:
    rows = by(make_repo(), BreakdownBy.CLIENT, lang=Language.EN).rows

    assert [(r.item.id, r.name) for r in rows] == [(None, "No client")]


def test_users_only_see_their_own_tasks() -> None:
    rows = by(make_repo(), BreakdownBy.TASK, user=USER_B).rows

    assert [r.name for r in rows] == ["B secret"]


def test_bad_data_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    repo = make_repo()
    user = repo.users[USER_A]
    user.deadline_tasks.append(
        DeadlineTask(TaskType.FLEXIBLE, TaskStatus.DONE, NOW - timedelta(days=1), None)
    )
    user.done_tasks.append(
        replace(done_task(PROJECT_A, 60, 60), estimated_minutes=None)
    )

    overview(repo)
    by(repo, BreakdownBy.PROJECT)

    messages = [r.getMessage() for r in caplog.records]
    assert (
        "Analytics: 0 Flexible task(s) without deadline, 1 Done without CompletedAt"
        in messages
    )
    assert "Analytics: 1 Done Flexible task(s) without estimate" in messages
