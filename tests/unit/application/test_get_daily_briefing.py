import asyncio
import time
from dataclasses import replace
from datetime import datetime
from datetime import time as clock
from decimal import Decimal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest

from metanoia_ai.application.ports.briefing_repository import (
    BriefingData,
    BriefingTask,
    DomainK,
)
from metanoia_ai.application.ports.quota_repository import AIFeature, Subscription
from metanoia_ai.application.ports.schedule_repository import TaskStatus
from metanoia_ai.application.ports.similar_task_repository import QueryTask
from metanoia_ai.application.use_cases.find_similar_tasks import (
    FindSimilarTasks,
    SearchMode,
    SimilarTasksResult,
)
from metanoia_ai.application.use_cases.get_daily_briefing import (
    BriefingSettings,
    GetDailyBriefing,
    UserNotFoundError,
)
from metanoia_ai.domain.briefing import (
    BiasRule,
    FixedEntry,
    ReasonCode,
    ScheduleKind,
    SlotEntry,
    SlotStatus,
)
from metanoia_ai.domain.models import (
    ActualTimeSource,
    FocusSessionRecord,
    Language,
    TaskType,
    WorkingHours,
)
from metanoia_ai.domain.similar_tasks import SimilarSummary
from tests.unit.application.fakes import (
    FakeBriefingRepository,
    FakeEmbeddingClient,
    FakeQuotaRepository,
    FakeSimilarTaskRepository,
)

SAIGON = ZoneInfo("Asia/Ho_Chi_Minh")
USER = uuid4()
DOMAIN = uuid4()


def at(day: int, hour: int, minute: int = 0) -> datetime:
    """October 2026 in Saigon time."""
    return datetime(2026, 10, day, hour, minute, tzinfo=SAIGON)


NOW = at(17, 8)  # Saturday 08:00
HOURS = WorkingHours(SAIGON, clock(8), clock(18), True, 10)  # 600 free minutes a day
SETTINGS = BriefingSettings(
    initial_k=Decimal("1.3"),
    top_count=3,
    risk_window_days=14,
    at_risk_limit=5,
    bias_rule=BiasRule(Decimal("1.1"), 3),
    similar_min_count=2,
    similar_min_ratio=1.3,
    similar_timeout_seconds=1.0,
    productive_top_hours=3,
    round_minutes=5,
)


TOMORROW_5PM = at(18, 17)


def task(
    title="Viết landing page",
    deadline=TOMORROW_5PM,
    minutes=60,
    status=TaskStatus.TODO,
    sessions=(),
) -> BriefingTask:
    return BriefingTask(
        task_id=uuid4(),
        title=title,
        project_name="Website Acme",
        domain_id=DOMAIN,
        domain_name="Coding",
        status=status,
        deadline=deadline,
        estimated_minutes=minutes,
        has_subtasks=False,
        open_subtasks=(),
        sessions=tuple(sessions),
    )


def data(**changes) -> BriefingData:
    base = BriefingData(
        hours=HOURS,
        is_free_plan=False,
        tasks=(),
        k_by_domain={},
        productive_scores={},
        has_productive_answer=False,
        slots=(),
        fixed_tasks=(),
        events=(),
        calendar_connected=True,
    )
    return replace(base, **changes)


def summary(count, done, ratio):
    return SimilarSummary(count=count, done_count=done, average_ratio=ratio)


NO_RESULTS = summary(0, 0, None)


class StubFinder:
    """FR-09 stand-in. Per task: a summary, an exception, or (seconds, summary)."""

    def __init__(self, answers=None, default=NO_RESULTS):
        self.answers = answers or {}
        self.default = default
        self.calls: list[tuple[UUID, UUID]] = []

    async def __call__(self, user_id: UUID, task_id: UUID) -> SimilarTasksResult:
        self.calls.append((user_id, task_id))
        answer = self.answers.get(task_id, self.default)
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, tuple):
            seconds, answer = answer
            await asyncio.sleep(seconds)
        return SimilarTasksResult(task_id, SearchMode.HYBRID, SAIGON, [], answer)


def build(briefing_data=None, finder=None, settings=SETTINGS):
    repo = FakeBriefingRepository()
    if briefing_data is not None:
        repo.add_user(USER, briefing_data)
    finder = finder or StubFinder()
    return GetDailyBriefing(repo, finder, settings, now=lambda: NOW), repo, finder


def run(briefing_data, finder=None, settings=SETTINGS, lang=Language.VI):
    use_case, _, _ = build(briefing_data, finder, settings)
    return asyncio.run(use_case.execute(USER, lang))


# --- basics ---


def test_user_not_found():
    use_case, _, _ = build(None)
    with pytest.raises(UserNotFoundError):
        asyncio.run(use_case.execute(USER, Language.VI))


def test_read_window_covers_today_and_the_risk_window():
    use_case, repo, _ = build(data())
    asyncio.run(use_case.execute(USER, Language.VI))
    [(user, day_start, day_end, window_end)] = repo.reads
    assert user == USER
    assert day_start <= at(17, 0) and day_end >= at(18, 0)
    assert window_end >= at(31, 8)  # 14 days


def test_date_and_times_in_user_zone():
    t = task(deadline=at(18, 17))
    result = run(data(tasks=(t,)))
    assert result.date.isoformat() == "2026-10-17"
    assert result.time_zone == SAIGON
    assert result.top_tasks[0].deadline.tzinfo == SAIGON


def test_top_task_has_reasons_as_text():
    t = task(deadline=at(18, 17), minutes=60)
    data_with_k = data(tasks=(t,), k_by_domain={DOMAIN: DomainK(Decimal("1.4"), 5)})
    [top] = (run(data_with_k)).top_tasks
    assert top.title == "Viết landing page"
    assert top.project_name == "Website Acme"
    assert top.remaining_minutes == 84  # 60 × 1.4
    assert [(r.code, r.text) for r in top.reasons] == [
        (ReasonCode.DEADLINE_SOON, "Hạn ngày mai."),
        (ReasonCode.BIAS, "Bạn thường cần thêm 40% thời gian cho Coding."),
    ]


def test_english_texts():
    t = task(deadline=at(18, 17))
    [top] = (run(data(tasks=(t,)), lang=Language.EN)).top_tasks
    assert top.reasons[0].text == "Due tomorrow."


def test_new_user_without_profile_uses_initial_k_and_no_bias():
    [top] = (run(data(tasks=(task(minutes=60),)))).top_tasks
    assert top.remaining_minutes == 78  # 60 × 1.3
    assert ReasonCode.BIAS not in [r.code for r in top.reasons]


def test_closed_and_bad_tasks_are_not_candidates():
    done = task(status=TaskStatus.DONE)
    no_deadline = replace(task(), deadline=None)
    no_estimate = replace(task(), estimated_minutes=None)
    result = run(data(tasks=(done, no_deadline, no_estimate)))
    assert result.top_tasks == ()


def test_in_progress_and_started_tasks_rank_before_plain_ones():
    plain = task("Plain", deadline=at(18, 9))
    working = task("Working", deadline=at(20, 9), status=TaskStatus.IN_PROGRESS)
    session = FocusSessionRecord(at(16, 9), at(16, 9, 30), ActualTimeSource.TIMER)
    started = task("Started", deadline=at(21, 9), sessions=[session])
    result = run(data(tasks=(plain, started, working)))
    assert [t.title for t in result.top_tasks] == ["Working", "Started", "Plain"]


def test_at_risk_list_counts_fixed_meetings_as_busy():
    # Today 08:00-18:00 = 600 free minutes, minus a 4-hour meeting = 360.
    meeting = FixedEntry(uuid4(), "Họp", at(17, 9), at(17, 13), is_skipped=False)
    big = task("Big", deadline=at(17, 18), minutes=400)  # 400 × 1.3 = 520 > 360
    result = run(data(tasks=(big,), fixed_tasks=(meeting,)))
    [risk] = result.at_risk
    assert risk.title == "Big"
    assert risk.missing_minutes == 520 - 360
    assert risk.deadline.tzinfo == SAIGON
    assert result.top_tasks[0].reasons[0].code is ReasonCode.AT_RISK
    # The meeting is also on today's schedule.
    assert [i.kind for i in result.schedule] == [ScheduleKind.FIXED]


# --- T13: similar task warning ---


def test_t13_similar_warning_conditions():
    one_slow = task("One slow", deadline=at(18, 9))
    two_slow = task("Two slow", deadline=at(18, 10))
    dropped = task("Dropped", deadline=at(18, 11))
    finder = StubFinder(
        {
            one_slow.task_id: summary(1, 1, 1.5),  # only 1 result: no warning
            two_slow.task_id: summary(2, 2, 1.4),
            dropped.task_id: summary(1, 0, None),  # 1 skipped
        }
    )
    result = run(data(tasks=(one_slow, two_slow, dropped)), finder)
    warnings = {t.title: t.similar_warning for t in result.top_tasks}
    assert warnings == {
        "One slow": None,
        "Two slow": "Các việc tương tự trước đây thường mất gấp 1,4 lần dự kiến.",
        "Dropped": "Trước đây bạn từng bỏ dở việc tương tự.",
    }


def test_failed_search_gives_no_warning_but_the_rest_works():
    t = task()
    finder = StubFinder({t.task_id: RuntimeError("db down")})
    [top] = (run(data(tasks=(t,)), finder)).top_tasks
    assert top.similar_warning is None
    assert top.reasons


def test_searches_only_for_top_tasks():
    tasks = tuple(task(deadline=at(18 + i, 9)) for i in range(5))
    use_case, _, finder = build(data(tasks=tasks))
    asyncio.run(use_case.execute(USER, Language.VI))
    assert {task_id for _, task_id in finder.calls} == {t.task_id for t in tasks[:3]}
    assert all(user == USER for user, _ in finder.calls)


def test_no_top_tasks_no_search():
    use_case, _, finder = build(data())
    asyncio.run(use_case.execute(USER, Language.VI))
    assert finder.calls == []


# --- T14: slow embedding ---


def test_t14_slow_search_is_dropped_in_time():
    slow, fast = task("Slow", deadline=at(18, 9)), task("Fast", deadline=at(18, 10))
    finder = StubFinder(
        {
            slow.task_id: (5, summary(2, 2, 2.0)),
            fast.task_id: summary(2, 2, 2.0),
        }
    )
    settings = replace(SETTINGS, similar_timeout_seconds=0.2)
    started = time.perf_counter()
    result = run(data(tasks=(slow, fast)), finder, settings)
    elapsed = time.perf_counter() - started
    assert elapsed < 1.0
    warnings = {t.title: t.similar_warning for t in result.top_tasks}
    assert warnings["Slow"] is None
    assert warnings["Fast"] is not None  # finished in time: kept
    assert len(result.top_tasks) == 2  # everything else is still there


def test_searches_run_at_the_same_time():
    tasks = tuple(task(deadline=at(18, 9 + i)) for i in range(3))
    finder = StubFinder({t.task_id: (0.3, summary(2, 2, 2.0)) for t in tasks})
    started = time.perf_counter()
    result = run(data(tasks=tasks), finder)
    assert time.perf_counter() - started < 0.8  # not 3 × 0.3 one after another
    assert all(t.similar_warning for t in result.top_tasks)


# --- T15-T18 ---


def test_t15_no_google_connection_still_gives_top():
    result = run(data(tasks=(task(),), calendar_connected=False))
    assert result.calendar_connected is False
    assert len(result.top_tasks) == 1


def test_t16_new_user_without_tasks():
    result = run(data())
    assert result.top_tasks == ()
    assert result.at_risk == ()
    assert result.schedule == ()
    assert result.productive_hint is None


def test_t17_proposed_slot_is_flagged_not_shown():
    t = task()
    proposed = SlotEntry(
        t.task_id, t.title, at(17, 14), at(17, 15), SlotStatus.PROPOSED
    )
    approved = SlotEntry(t.task_id, t.title, at(17, 9), at(17, 10), SlotStatus.APPROVED)
    result = run(data(tasks=(t,), slots=(proposed, approved)))
    assert result.has_pending_proposal is True
    assert [(i.kind, i.start) for i in result.schedule] == [
        (ScheduleKind.SLOT, at(17, 9))
    ]


def test_t18_no_active_plan_runs_like_free():
    # The repository maps "no Active plan" to is_free_plan = True (#7).
    profile = {DOMAIN: DomainK(Decimal("1.5"), 4)}
    t = task(minutes=60)
    free = run(data(tasks=(t,), k_by_domain=profile, is_free_plan=True))
    paid = run(data(tasks=(t,), k_by_domain=profile, is_free_plan=False))
    assert free.top_tasks[0].remaining_minutes == 60  # no × K
    assert paid.top_tasks[0].remaining_minutes == 90
    # The hint is still shown on the Free plan.
    assert ReasonCode.BIAS in [r.code for r in free.top_tasks[0].reasons]


def test_productive_hint_text():
    scores = {
        h: Decimal("0.9") if h in (14, 15, 16) else Decimal("0.3") for h in range(24)
    }
    result = run(data(productive_scores=scores, has_productive_answer=True))
    assert result.productive_hint is not None
    assert result.productive_hint.ranges == ((14, 17),)
    assert result.productive_hint.text == (
        "Khung giờ bạn làm tốt còn lại hôm nay: 14h–17h."
    )


# --- T19: real FR-09 use case, no LLM, one usage row per embedding ---


def test_t19_embeddings_are_logged_and_not_charged():
    tasks = tuple(task(f"Task {i}", deadline=at(18, 9 + i)) for i in range(4))
    similar_repo = FakeSimilarTaskRepository()
    for t in tasks:
        similar_repo.add_task(
            USER,
            QueryTask(t.task_id, TaskType.FLEXIBLE, t.title, "Coding", None, SAIGON),
        )
    quota = FakeQuotaRepository(now=NOW)
    subscription = Subscription(uuid4(), price=0, ai_call_limit=50, ai_calls_used=7)
    quota.add_subscription(USER, subscription, start_at=at(1, 0))
    client = FakeEmbeddingClient(*([[0.1, 0.2, 0.3, 0.4]] * 3))
    find = FindSimilarTasks(
        similar_repo,
        quota,
        client,
        dimensions=4,
        price_per_1m=0.15,
        min_similarity=0.5,
        rrf_k=60,
        branch_limit=20,
        embed_timeout_seconds=1.0,
        now=lambda: NOW,
    )

    async def finder(user_id, task_id):
        return await find.execute(user_id, task_id, 5)

    result = run(data(tasks=tasks), finder)
    assert len(result.top_tasks) == 3
    assert len(client.texts) == 3  # embedding only for the top 3
    rows = [entry for _, _, entry in quota.logs]
    assert len(rows) == 3
    assert all(r.feature is AIFeature.EMBEDDING and not r.quota_charged for r in rows)
    assert quota.calls_used[subscription.subscription_id] == 7  # no quota used
