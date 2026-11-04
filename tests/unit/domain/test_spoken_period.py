from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from metanoia_ai.domain.spoken_period import SpokenPeriod, period_range

VN = ZoneInfo("Asia/Ho_Chi_Minh")


def at(day: int, hour: int, zone: ZoneInfo = VN) -> datetime:
    return datetime(2026, 10, day, hour, tzinfo=zone)


# T2
@pytest.mark.parametrize(
    ("period", "start", "end"),
    [
        (SpokenPeriod.MORNING, at(30, 6), at(30, 12)),
        (SpokenPeriod.AFTERNOON, at(30, 12), at(30, 18)),
        (SpokenPeriod.EVENING, at(30, 18), at(31, 0)),
        (SpokenPeriod.TODAY, at(30, 0), at(31, 0)),
        (SpokenPeriod.TOMORROW, at(31, 0), datetime(2026, 11, 1, tzinfo=VN)),
    ],
)
def test_periods_in_user_time(period, start, end) -> None:
    rng = period_range(period, at(30, 9), VN)

    assert (rng.start, rng.end) == (start, end)


def test_day_follows_the_user_zone_not_utc() -> None:
    # 23:30 UTC on the 29th is already 06:30 on the 30th in Vietnam.
    now = datetime(2026, 10, 29, 23, 30, tzinfo=UTC)

    rng = period_range(SpokenPeriod.TODAY, now, VN)

    assert rng.start == at(30, 0)
    assert rng.start.utcoffset().total_seconds() == 7 * 3600


def test_other_zone() -> None:
    tokyo = ZoneInfo("Asia/Tokyo")
    now = datetime(2026, 10, 30, 1, 0, tzinfo=UTC)  # 10:00 in Tokyo

    rng = period_range(SpokenPeriod.AFTERNOON, now, tokyo)

    assert (rng.start, rng.end) == (at(30, 12, tokyo), at(30, 18, tokyo))


def test_now_must_be_aware() -> None:
    with pytest.raises(ValueError):
        period_range(SpokenPeriod.TODAY, datetime(2026, 10, 30), VN)  # noqa: DTZ001 - no time zone on purpose
