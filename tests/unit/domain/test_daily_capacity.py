"""FR-10d: learn the daily capacity, warn about heavy days (plans/FR-10d.md T1-T5)."""

from datetime import date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from metanoia_ai.domain.daily_capacity import (
    daily_capacity,
    minutes_by_day,
    overloaded_days,
)
from metanoia_ai.domain.models import (
    Language,
    PlannedSlot,
    ScheduleWarning,
    TimeRange,
    WarningType,
)
from metanoia_ai.domain.schedule_text import duration, summary, warning_message

SAIGON = ZoneInfo("Asia/Ho_Chi_Minh")
TODAY = date(2026, 11, 20)


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=SAIGON)


def slot(day, start_hour, minutes) -> PlannedSlot:
    start = at(day, start_hour)
    return PlannedSlot(
        UUID(int=1), None, start, start + timedelta(minutes=minutes), None
    )


# --- T3: minutes per day ---


def test_sessions_of_one_day_add_up():
    sessions = [
        TimeRange(at(17, 9), at(17, 11)),
        TimeRange(at(17, 14), at(17, 15, 30)),
        TimeRange(at(18, 9), at(18, 10)),
    ]
    assert minutes_by_day(sessions, TODAY, SAIGON) == {
        date(2026, 11, 17): 210,
        date(2026, 11, 18): 60,
    }


def test_today_is_left_out():
    assert minutes_by_day([TimeRange(at(20, 9), at(20, 10))], TODAY, SAIGON) == {}


def test_day_is_the_users_local_day():
    # 23:30 on the 18th in Saigon is 16:30 UTC: still the 18th for the user.
    utc = ZoneInfo("UTC")
    start = datetime(2026, 11, 18, 16, 30, tzinfo=utc)
    sessions = [TimeRange(start, start + timedelta(minutes=60))]
    assert minutes_by_day(sessions, TODAY, SAIGON) == {date(2026, 11, 18): 60}


def test_session_over_midnight_counts_on_its_start_day():
    sessions = [TimeRange(at(18, 23), at(19, 1))]
    assert minutes_by_day(sessions, TODAY, SAIGON) == {date(2026, 11, 18): 120}


# --- T1-T2: capacity ---


def test_capacity_is_the_80th_percentile_rounded_down_to_15():
    # 10 days: 80th percentile (nearest rank) = 8th smallest = 487 → 480
    days = [120, 200, 240, 300, 360, 420, 450, 487, 600, 700]
    assert daily_capacity(days, min_days=5) == 480


def test_order_of_days_does_not_matter():
    assert daily_capacity([700, 120, 487, 300, 450], 5) == daily_capacity(
        [120, 300, 450, 487, 700], 5
    )


def test_too_few_days_gives_no_capacity():
    assert daily_capacity([300, 400, 500, 600], min_days=5) is None


def test_empty_days_do_not_count():
    assert daily_capacity([0, 0, 300, 400, 500, 600], min_days=5) is None


def test_days_of_a_few_test_minutes_do_not_count():
    """Real run 2026-11-20: test sessions of 1-35 minutes a day must not teach a
    capacity of 15 minutes (every planned day would be "overloaded")."""
    assert daily_capacity([1, 4, 15, 35, 59, 59, 59], min_days=5) is None
    assert daily_capacity([60, 60, 60, 60, 60], min_days=5) == 60


# --- T4: heavy days ---


def test_day_over_capacity_is_warned_with_approved_work_counted():
    slots = [slot(23, 9, 180), slot(23, 13, 180), slot(24, 9, 120)]
    approved = {date(2026, 11, 23): 180}
    (warning,) = overloaded_days(slots, approved, 480, SAIGON)
    assert warning.type is WarningType.DAY_OVERLOADED
    assert warning.day == date(2026, 11, 23)
    assert (warning.needed_minutes, warning.capacity_minutes) == (540, 480)
    assert warning.task_id is None


def test_day_with_only_approved_work_is_not_warned():
    approved = {date(2026, 11, 23): 900}
    assert overloaded_days([slot(24, 9, 60)], approved, 480, SAIGON) == []


def test_exactly_at_capacity_is_fine():
    assert overloaded_days([slot(23, 9, 480)], {}, 480, SAIGON) == []


def test_no_capacity_no_warning():
    assert overloaded_days([slot(23, 8, 900)], {}, None, SAIGON) == []


def test_day_overloaded_needs_a_day():
    with pytest.raises(ValueError):
        ScheduleWarning(WarningType.DAY_OVERLOADED, needed_minutes=600)


# --- T5: texts ---


@pytest.mark.parametrize(
    ("minutes", "vi", "en"),
    [
        (480, "8 giờ", "8 h"),
        (510, "8 giờ 30 phút", "8 h 30 min"),
        (45, "45 phút", "45 min"),
        (0, "0 phút", "0 min"),
    ],
)
def test_duration(minutes, vi, en):
    assert duration(minutes, Language.VI) == vi
    assert duration(minutes, Language.EN) == en


HEAVY = ScheduleWarning(
    WarningType.DAY_OVERLOADED,
    needed_minutes=690,
    day=date(2026, 11, 23),
    capacity_minutes=480,
)


def test_vietnamese_message():
    text = warning_message(HEAVY, Language.VI)
    assert text.startswith("Ngày 23/11 có khoảng 11 giờ 30 phút việc")
    assert "khoảng 8 giờ" in text
    assert "K_bias" not in text and "capacity" not in text


def test_english_message():
    text = warning_message(HEAVY, Language.EN)
    assert text.startswith("23/11 has about 11 h 30 min of work")
    assert "about 8 h" in text


def test_summary_counts_heavy_days():
    assert summary(3, 3, [HEAVY], Language.VI).endswith(
        "1 ngày nhiều việc hơn mức bạn thường làm."
    )
    assert summary(3, 3, [HEAVY, HEAVY], Language.EN).endswith(
        "2 days have more work than you usually manage."
    )
