import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from metanoia_ai.application.use_cases.get_productive_hours import (
    GetProductiveHours,
    ProductiveHoursView,
)
from metanoia_ai.application.use_cases.recompute_productive_hours import (
    RecomputeProductiveHours,
    UserNotFoundError,
)
from metanoia_ai.application.use_cases.set_onboarding_hours import SetOnboardingHours
from metanoia_ai.domain.models import TaskType
from metanoia_ai.domain.planner import top_productive_hours
from metanoia_ai.domain.productive_hours import (
    NEUTRAL_PRIOR,
    FocusSample,
    HourScore,
    InvalidBlocksError,
    OnboardingBlock,
    ScoreSource,
    StopReason,
)
from tests.unit.application.fakes import FakeProductiveHoursRepository

USER = uuid4()
VN = ZoneInfo("Asia/Ho_Chi_Minh")
NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
TOP_PLAN = "Cao cấp"
SELECTED, UNSELECTED = Decimal("0.800"), Decimal("0.300")
TOP_COUNT = 3


def make_repo(plan: str | None = TOP_PLAN) -> FakeProductiveHoursRepository:
    repo = FakeProductiveHoursRepository()
    repo.add_user(USER, plan_name=plan)
    return repo


def onboard(repo, blocks: list[str]) -> ProductiveHoursView:
    recompute = RecomputeProductiveHours(
        repo,
        prior_weight=Decimal(5),
        min_session_minutes=25,
        window_days=90,
        learning_plans=[TOP_PLAN],
        now=lambda: NOW,
    )
    case = SetOnboardingHours(
        recompute,
        selected_score=SELECTED,
        unselected_score=UNSELECTED,
        top_count=TOP_COUNT,
    )
    return asyncio.run(case.execute(USER, blocks))


def get(repo) -> ProductiveHoursView:
    return asyncio.run(GetProductiveHours(repo, top_count=TOP_COUNT).execute(USER))


def good_session(hour: int, days_ago: int) -> FocusSample:
    day = (NOW - timedelta(days=days_ago)).astimezone(VN).date()
    start = datetime(day.year, day.month, day.day, hour, tzinfo=VN)
    return FocusSample(
        start,
        start + timedelta(minutes=30),
        StopReason.COMPLETED,
        True,
        TaskType.FLEXIBLE,
    )


# --- T7: onboarding (AC2, AC3) ---


def test_first_onboarding_writes_24_rows():
    repo = make_repo()

    view = onboard(repo, ["morning"])

    rows = repo.hours[USER]
    assert sorted(rows) == list(range(24))
    assert rows[8] == HourScore(8, SELECTED, SELECTED, ScoreSource.ONBOARDING)
    assert rows[20] == HourScore(20, UNSELECTED, UNSELECTED, ScoreSource.ONBOARDING)
    assert repo.users[USER].blocks == (OnboardingBlock.MORNING,)
    assert view.onboarded is True
    assert view.blocks == (OnboardingBlock.MORNING,)
    assert [h.hour for h in view.hours] == list(range(24))
    assert view.top_hours == (6, 7, 8)


def test_onboarding_again_changes_priors_and_keeps_timer_data():
    repo = make_repo()
    for d in range(1, 7):
        repo.add_session(USER, good_session(21, d))
    onboard(repo, ["morning"])
    assert repo.hours[USER][21].score == Decimal("0.682")  # (0.3×5 + 6) / 11

    view = onboard(repo, ["evening"])

    row_21 = repo.hours[USER][21]
    assert row_21.prior == SELECTED
    assert row_21.score == Decimal("0.909")  # (0.8×5 + 6) / 11
    assert row_21.source is ScoreSource.LEARNED
    assert repo.hours[USER][8] == HourScore(
        8, UNSELECTED, UNSELECTED, ScoreSource.ONBOARDING
    )
    assert view.blocks == (OnboardingBlock.EVENING,)
    assert view.top_hours[0] == 18 and 21 in view.top_hours


def test_onboarding_on_another_plan_does_not_learn():
    repo = make_repo("Miễn phí")
    repo.add_session(USER, good_session(21, 1))
    onboard(repo, ["morning"])
    assert repo.session_reads == []
    assert repo.hours[USER][21].source is ScoreSource.ONBOARDING


def test_onboarding_replaces_rows_made_by_the_backend():
    repo = make_repo()
    repo.hours[USER] = {
        h: HourScore(h, NEUTRAL_PRIOR, NEUTRAL_PRIOR, ScoreSource.ONBOARDING)
        for h in range(24)
    }
    onboard(repo, ["late_night"])
    assert repo.hours[USER][23].score == SELECTED
    assert repo.hours[USER][12].score == UNSELECTED


@pytest.mark.parametrize("blocks", [[], ["morning", "morning"], ["night"]])
def test_bad_answer_touches_nothing(blocks):
    repo = make_repo()
    with pytest.raises(InvalidBlocksError):
        onboard(repo, blocks)
    assert repo.saves == 0
    assert repo.users[USER].blocks is None


def test_save_error_rolls_back_the_answer_too():
    repo = make_repo()
    repo.fail_on_save_hours = True
    with pytest.raises(RuntimeError):
        onboard(repo, ["morning"])
    assert repo.users[USER].blocks is None
    assert USER not in repo.hours


def test_onboarding_unknown_user():
    with pytest.raises(UserNotFoundError):
        onboard(FakeProductiveHoursRepository(), ["morning"])


# --- T11: top_hours is FR-10's rule (AC6) ---


def test_top_hours_match_fr10():
    repo = make_repo()
    for d in range(1, 4):
        repo.add_session(USER, good_session(21, d))
    onboard(repo, ["morning", "evening"])

    view = get(repo)

    scores = {h: row.score for h, row in repo.hours[USER].items()}
    assert set(view.top_hours) == top_productive_hours(scores, TOP_COUNT)
    assert list(view.top_hours) == sorted(view.top_hours)


def test_get_returns_hours_in_order_with_sources():
    repo = make_repo()
    onboard(repo, ["evening"])
    view = get(repo)
    assert [h.hour for h in view.hours] == list(range(24))
    assert view.hours[19] == HourScore(19, SELECTED, SELECTED, ScoreSource.ONBOARDING)


# --- T16: onboarded comes from the stored answer, not from the rows ---


def test_rows_from_the_backend_but_no_answer_is_not_onboarded():
    repo = make_repo()
    repo.hours[USER] = {
        h: HourScore(h, NEUTRAL_PRIOR, NEUTRAL_PRIOR, ScoreSource.ONBOARDING)
        for h in range(24)
    }
    view = get(repo)
    assert view.onboarded is False
    assert view.blocks == ()
    assert len(view.hours) == 24
    assert view.top_hours == (0, 1, 2)  # all equal → earliest hours (FR-10 tie rule)


def test_stored_answer_means_onboarded():
    repo = make_repo()
    onboard(repo, ["morning"])
    view = get(repo)
    assert view.onboarded is True
    assert view.blocks == (OnboardingBlock.MORNING,)


def test_new_user_without_rows():
    view = get(make_repo())
    assert view == ProductiveHoursView(
        onboarded=False, blocks=(), hours=(), top_hours=()
    )


def test_get_unknown_user():
    with pytest.raises(UserNotFoundError):
        get(FakeProductiveHoursRepository())
