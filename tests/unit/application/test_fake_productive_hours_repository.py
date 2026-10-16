"""The fake must behave like the real repository, or use case tests prove nothing."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from metanoia_ai.application.ports.productive_hours_repository import (
    ProductiveHoursRepository,
)
from metanoia_ai.domain.models import TaskType
from metanoia_ai.domain.productive_hours import (
    FocusSample,
    HourScore,
    OnboardingBlock,
    ScoreSource,
    StopReason,
)
from tests.unit.application.fakes import FakeProductiveHoursRepository

USER = uuid4()
OTHER_USER = uuid4()
NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)


def hour(h: int, score: str = "0.500") -> HourScore:
    value = Decimal(score)
    return HourScore(h, value, value, ScoreSource.ONBOARDING)


def make_sample(started_at: datetime = NOW) -> FocusSample:
    return FocusSample(
        started_at,
        started_at + timedelta(minutes=30),
        StopReason.COMPLETED,
        True,
        TaskType.FLEXIBLE,
    )


def test_fake_matches_the_port():
    repo: ProductiveHoursRepository = FakeProductiveHoursRepository()  # type check only
    assert repo is not None


def test_unknown_user_has_no_profile():
    assert asyncio.run(FakeProductiveHoursRepository().get_profile(USER)) is None


def test_profile_of_a_new_user():
    repo = FakeProductiveHoursRepository()
    repo.add_user(USER, plan_name="Cao cấp")
    profile = asyncio.run(repo.get_profile(USER))
    assert profile.time_zone == ZoneInfo("Asia/Ho_Chi_Minh")
    assert profile.blocks is None
    assert profile.plan_name == "Cao cấp"
    assert profile.hours == {}


def test_save_hours_then_read_them_back():
    repo = FakeProductiveHoursRepository()
    repo.add_user(USER)
    asyncio.run(repo.save_hours(USER, [hour(h) for h in range(24)]))
    asyncio.run(repo.save_hours(USER, [hour(9, "0.800")]))  # update one row
    hours = asyncio.run(repo.get_profile(USER)).hours
    assert len(hours) == 24
    assert hours[9].score == Decimal("0.800")


def test_rows_stay_with_their_user():
    repo = FakeProductiveHoursRepository()
    repo.add_user(USER)
    repo.add_user(OTHER_USER)
    asyncio.run(repo.save_hours(USER, [hour(9)]))
    assert asyncio.run(repo.get_profile(OTHER_USER)).hours == {}


def test_save_blocks():
    repo = FakeProductiveHoursRepository()
    repo.add_user(USER)
    asyncio.run(repo.save_blocks(USER, [OnboardingBlock.EVENING]))
    assert asyncio.run(repo.get_profile(USER)).blocks == (OnboardingBlock.EVENING,)


def test_sessions_only_of_the_user_and_reads_are_recorded():
    repo = FakeProductiveHoursRepository()
    mine, theirs = make_sample(), make_sample(NOW - timedelta(hours=1))
    repo.add_session(USER, mine)
    repo.add_session(OTHER_USER, theirs)
    since = NOW - timedelta(days=90)
    assert asyncio.run(repo.get_sessions(USER, since)) == [mine]
    assert repo.session_reads == [(USER, since)]


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param([hour(9), hour(9)], id="same-hour-twice"),
        pytest.param([hour(24)], id="hour-out-of-range"),
        pytest.param([hour(9, "1.001")], id="score-over-1"),
        pytest.param([hour(9, "0.5555")], id="more-than-3-decimals"),
    ],
)
def test_save_hours_rejects_what_the_db_rejects(bad):
    repo = FakeProductiveHoursRepository()
    repo.add_user(USER)
    with pytest.raises(AssertionError):
        asyncio.run(repo.save_hours(USER, bad))


def test_transaction_rolls_back_on_error():
    repo = FakeProductiveHoursRepository()
    repo.add_user(USER)

    async def save_then_fail():
        async with repo.transaction():
            await repo.save_blocks(USER, [OnboardingBlock.MORNING])
            await repo.save_hours(USER, [hour(9)])
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        asyncio.run(save_then_fail())
    profile = asyncio.run(repo.get_profile(USER))
    assert profile.blocks is None
    assert profile.hours == {}
