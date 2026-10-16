import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from metanoia_ai.application.use_cases.recompute_productive_hours import (
    RecomputeProductiveHours,
    RecomputeResult,
    UserNotFoundError,
)
from metanoia_ai.domain.models import TaskType
from metanoia_ai.domain.productive_hours import (
    NEUTRAL_PRIOR,
    FocusSample,
    HourScore,
    ScoreSource,
    StopReason,
    onboarding_scores,
)
from tests.unit.application.fakes import FakeProductiveHoursRepository

USER = uuid4()
OTHER_USER = uuid4()
VN = ZoneInfo("Asia/Ho_Chi_Minh")
NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
TOP_PLAN = "Cao cấp"
SELECTED, UNSELECTED = Decimal("0.8"), Decimal("0.3")


def use_case(repo: FakeProductiveHoursRepository) -> RecomputeProductiveHours:
    return RecomputeProductiveHours(
        repo,
        prior_weight=Decimal(5),
        min_session_minutes=25,
        window_days=90,
        learning_plans=[TOP_PLAN],
        now=lambda: NOW,
    )


def run(repo: FakeProductiveHoursRepository, user=USER) -> RecomputeResult:
    return asyncio.run(use_case(repo).execute(user))


def at_vn_hour(hour: int, days_ago: int = 1) -> datetime:
    day = (NOW - timedelta(days=days_ago)).astimezone(VN).date()
    return datetime(day.year, day.month, day.day, hour, tzinfo=VN)


def session(
    hour: int = 21,
    reason: StopReason | None = StopReason.COMPLETED,
    days_ago: int = 1,
    running: bool = False,
    confirmed: bool = True,
    task_type: TaskType = TaskType.FLEXIBLE,
) -> FocusSample:
    start = at_vn_hour(hour, days_ago)
    end = None if running else start + timedelta(minutes=30)
    return FocusSample(start, end, None if running else reason, confirmed, task_type)


def onboarded_repo(plan: str | None = TOP_PLAN) -> FakeProductiveHoursRepository:
    """A user who chose "morning" at onboarding (priors stored, nothing learned)."""
    repo = FakeProductiveHoursRepository()
    repo.add_user(USER, plan_name=plan)
    priors = onboarding_scores(["morning"], SELECTED, UNSELECTED)
    repo.hours[USER] = {
        h: HourScore(h, p, p, ScoreSource.ONBOARDING) for h, p in priors.items()
    }
    return repo


def score(repo, hour: int) -> HourScore:
    return repo.hours[USER][hour]


# --- learning (top plan) ---


def test_learns_from_good_sessions():
    repo = onboarded_repo()
    for d in range(1, 7):
        repo.add_session(USER, session(21, days_ago=d))

    result = run(repo)

    assert result == RecomputeResult(hours_updated=24, sessions_used=6)
    assert score(repo, 21) == HourScore(
        21, Decimal("0.682"), Decimal("0.300"), ScoreSource.LEARNED
    )
    assert score(repo, 9).source is ScoreSource.ONBOARDING  # AC5
    assert score(repo, 9).score == Decimal("0.800")


def test_reads_sessions_of_the_last_90_days():
    repo = onboarded_repo()
    run(repo)
    assert repo.session_reads == [(USER, NOW - timedelta(days=90))]


# --- T8: twice in a row gives the same result (AC3) ---


def test_recompute_twice_gives_the_same_result():
    repo = onboarded_repo()
    repo.add_session(USER, session(9, StopReason.INTERRUPTED))
    repo.add_session(USER, session(21))

    first = run(repo)
    rows_after_first = dict(repo.hours[USER])
    second = run(repo)

    assert first == second
    assert repo.hours[USER] == rows_after_first
    assert score(repo, 9).prior == Decimal("0.800")  # the prior is not replaced


def test_learned_score_is_not_used_as_the_next_prior():
    # From scratch (#6): the stored learned score never feeds the next run.
    repo = onboarded_repo()
    repo.add_session(USER, session(21))
    run(repo)
    repo.sessions[USER].clear()
    run(repo)
    assert score(repo, 21) == HourScore(
        21, UNSELECTED, UNSELECTED, ScoreSource.ONBOARDING
    )


# --- T9: sessions that are never learned from (AC1, AC4) ---


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param(session(running=True), id="running"),
        pytest.param(session(confirmed=False), id="unconfirmed"),
        pytest.param(session(task_type=TaskType.FIXED), id="fixed-task"),
        pytest.param(session(days_ago=91), id="91-days-ago"),
    ],
)
def test_bad_sessions_are_skipped(bad):
    repo = onboarded_repo()
    repo.add_session(USER, bad)
    repo.add_session(USER, session(9))  # one good session at another hour

    result = run(repo)

    assert result.sessions_used == 1
    assert score(repo, 21).source is ScoreSource.ONBOARDING
    assert score(repo, 9).source is ScoreSource.LEARNED


def test_sessions_of_another_user_are_not_used():
    repo = onboarded_repo()
    repo.add_user(OTHER_USER, plan_name=TOP_PLAN)
    repo.add_session(OTHER_USER, session(21))

    result = run(repo)

    assert result.sessions_used == 0
    assert score(repo, 21).source is ScoreSource.ONBOARDING
    assert OTHER_USER not in repo.hours


# --- T10: not onboarded ---


def test_not_onboarded_with_sessions_uses_neutral_prior():
    repo = FakeProductiveHoursRepository()
    repo.add_user(USER, plan_name=TOP_PLAN)
    repo.add_session(USER, session(21, StopReason.INTERRUPTED))

    result = run(repo)

    assert result == RecomputeResult(hours_updated=24, sessions_used=1)
    assert score(repo, 21).prior == NEUTRAL_PRIOR
    assert score(repo, 21).score == Decimal("0.417")  # (0.5×5 + 0) / 6
    assert score(repo, 3) == HourScore(
        3, NEUTRAL_PRIOR, NEUTRAL_PRIOR, ScoreSource.ONBOARDING
    )


def test_not_onboarded_without_sessions_writes_nothing():
    repo = FakeProductiveHoursRepository()
    repo.add_user(USER, plan_name=TOP_PLAN)

    assert run(repo) == RecomputeResult(hours_updated=0, sessions_used=0)
    assert repo.saves == 0
    assert USER not in repo.hours


def test_only_bad_sessions_count_as_none():
    repo = FakeProductiveHoursRepository()
    repo.add_user(USER, plan_name=TOP_PLAN)
    repo.add_session(USER, session(running=True))
    assert run(repo).hours_updated == 0


def test_rows_made_by_the_backend_are_rewritten():
    # #18: 24 rows of 0.5 made at sign-up, no sessions → 24 rows, unchanged values.
    repo = FakeProductiveHoursRepository()
    repo.add_user(USER, plan_name=TOP_PLAN)
    repo.hours[USER] = {
        h: HourScore(h, NEUTRAL_PRIOR, NEUTRAL_PRIOR, ScoreSource.ONBOARDING)
        for h in range(24)
    }
    assert run(repo) == RecomputeResult(hours_updated=24, sessions_used=0)
    assert all(row.score == NEUTRAL_PRIOR for row in repo.hours[USER].values())


# --- T15: only the top plan learns (AC8) ---


@pytest.mark.parametrize("plan", ["Tiêu chuẩn", "Miễn phí", None])
def test_other_plans_keep_onboarding_scores(plan):
    repo = onboarded_repo(plan)
    for d in range(1, 11):
        repo.add_session(USER, session(21, days_ago=d))

    result = run(repo)

    assert result == RecomputeResult(hours_updated=24, sessions_used=0)
    assert repo.session_reads == []  # sessions are not even read
    assert all(r.score == r.prior for r in repo.hours[USER].values())
    assert all(r.source is ScoreSource.ONBOARDING for r in repo.hours[USER].values())


def test_top_plan_with_the_same_data_learns():
    repo = onboarded_repo(TOP_PLAN)
    for d in range(1, 11):
        repo.add_session(USER, session(21, days_ago=d))
    assert run(repo).sessions_used == 10
    assert score(repo, 21).score > UNSELECTED


def test_downgrade_returns_to_onboarding_scores():
    # #17: learned under the top plan, then the plan changes.
    repo = onboarded_repo(TOP_PLAN)
    repo.add_session(USER, session(21))
    run(repo)
    repo.users[USER].plan_name = "Tiêu chuẩn"
    run(repo)
    assert score(repo, 21) == HourScore(
        21, UNSELECTED, UNSELECTED, ScoreSource.ONBOARDING
    )


def test_several_learning_plans_from_config():
    repo = onboarded_repo("Doanh nghiệp")
    repo.add_session(USER, session(21))
    case = RecomputeProductiveHours(
        repo,
        prior_weight=Decimal(5),
        min_session_minutes=25,
        window_days=90,
        learning_plans=[TOP_PLAN, "Doanh nghiệp"],
        now=lambda: NOW,
    )
    assert asyncio.run(case.execute(USER)).sessions_used == 1


# --- errors ---


def test_unknown_user():
    with pytest.raises(UserNotFoundError):
        run(FakeProductiveHoursRepository())


def test_save_error_leaves_rows_unchanged():
    repo = onboarded_repo()
    repo.add_session(USER, session(21))
    before = dict(repo.hours[USER])
    repo.fail_on_save_hours = True
    with pytest.raises(RuntimeError):
        run(repo)
    assert repo.hours[USER] == before
