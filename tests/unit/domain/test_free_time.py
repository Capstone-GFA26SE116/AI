from datetime import datetime, time
from zoneinfo import ZoneInfo

from metanoia_ai.domain.free_time import (
    CalendarEvent,
    FixedTaskTime,
    busy_ranges,
    free_ranges,
    merge,
    round_up,
    running_timer_busy,
)
from metanoia_ai.domain.models import TimeRange, WorkingHours

SAIGON = ZoneInfo("Asia/Ho_Chi_Minh")
# Common example from the plan: 9:00-17:00, no weekends, buffer 10'.
HOURS = WorkingHours(SAIGON, time(9), time(17), False, 10)


def at(day: int, hour: int, minute: int = 0) -> datetime:
    """October 2026 in Saigon time. 19 = Monday, 23 = Friday, 26 = next Monday."""
    return datetime(2026, 10, day, hour, minute, tzinfo=SAIGON)


MONDAY_8AM = at(19, 8)


def free(now=MONDAY_8AM, busy=(), window_days=1):
    return free_ranges(HOURS, now, window_days, list(busy), round_minutes=5)


def event(start, end, *, all_day=False, cancelled=False, metanoia=False):
    return CalendarEvent(start, end, all_day, cancelled, metanoia)


def test_empty_day_is_free_during_working_hours():
    assert free() == [TimeRange(at(19, 9), at(19, 17))]


def test_t3_fixed_meeting_is_busy():
    busy = busy_ranges([FixedTaskTime(at(19, 9), at(19, 10), False)], [], [])
    assert free(busy=busy)[0].start == at(19, 10)


def test_t38_skipped_fixed_task_is_not_busy():
    busy = busy_ranges([FixedTaskTime(at(19, 10), at(19, 11), True)], [], [])
    assert free(busy=busy) == [TimeRange(at(19, 9), at(19, 17))]


def test_t4_only_normal_google_events_are_busy():
    events = [
        event(at(19, 13), at(19, 14)),
        event(at(19, 0), at(20, 0), all_day=True),
        event(at(19, 10), at(19, 11), cancelled=True),
    ]
    assert free(busy=busy_ranges([], events, [])) == [
        TimeRange(at(19, 9), at(19, 13)),
        TimeRange(at(19, 14), at(19, 17)),
    ]


def test_t5_approved_slot_is_busy_and_its_google_copy_is_ignored():
    approved = [TimeRange(at(19, 9), at(19, 11))]
    events = [
        event(at(19, 9), at(19, 11), metanoia=True),  # the same slot, in Google
        event(at(19, 14), at(19, 15), metanoia=True),  # not counted on its own
    ]
    assert free(busy=busy_ranges([], events, approved)) == [
        TimeRange(at(19, 11), at(19, 17))
    ]


def test_zero_length_google_event_is_ignored():
    busy = busy_ranges([], [event(at(19, 10), at(19, 10))], [])
    assert busy == []


def test_t6_now_is_rounded_up_to_5_minutes():
    assert free(now=at(19, 9, 7))[0].start == at(19, 9, 10)


def test_gap_after_busy_is_rounded_up():
    busy = busy_ranges([FixedTaskTime(at(19, 9), at(19, 10, 52), False)], [], [])
    assert free(busy=busy)[0].start == at(19, 10, 55)


def test_gap_too_small_after_rounding_is_dropped():
    # Free 10:51-10:54 only; rounded start 10:55 is past its end.
    busy = busy_ranges(
        [
            FixedTaskTime(at(19, 9), at(19, 10, 51), False),
            FixedTaskTime(at(19, 10, 54), at(19, 17), False),
        ],
        [],
        [],
    )
    assert free(busy=busy) == []


def test_t17_running_timer_is_busy_from_now():
    now = at(19, 9)
    timer = running_timer_busy(now, 65)  # 65' left, from schedule_duration
    busy = busy_ranges([], [], [], [timer])
    assert free(now=now, busy=busy)[0].start == at(19, 10, 5)


def test_t18_weekend_is_skipped():
    friday_8am = at(23, 8)
    full_friday = busy_ranges([FixedTaskTime(at(23, 9), at(23, 17), False)], [], [])
    ranges = free(now=friday_8am, busy=full_friday, window_days=7)
    assert ranges[0] == TimeRange(at(26, 9), at(26, 17))


def test_weekend_is_used_when_user_works_on_weekends():
    weekend_hours = WorkingHours(SAIGON, time(9), time(17), True, 10)
    ranges = free_ranges(weekend_hours, at(23, 18), 2, [], 5)
    assert ranges[0].start == at(24, 9)  # Saturday


def test_window_end_cuts_the_last_day():
    # Window of 1 day from Monday 12:00 ends Tuesday 12:00.
    ranges = free(now=at(19, 12))
    assert ranges == [
        TimeRange(at(19, 12), at(19, 17)),
        TimeRange(at(20, 9), at(20, 12)),
    ]


def test_merge_joins_overlapping_and_touching_ranges():
    ranges = [
        TimeRange(at(19, 13), at(19, 14)),
        TimeRange(at(19, 9), at(19, 10)),
        TimeRange(at(19, 9, 30), at(19, 11)),
        TimeRange(at(19, 14), at(19, 15)),
    ]
    assert merge(ranges) == [
        TimeRange(at(19, 9), at(19, 11)),
        TimeRange(at(19, 13), at(19, 15)),
    ]


def test_round_up_keeps_round_times_and_uses_the_user_clock():
    assert round_up(at(19, 9, 10), 5, SAIGON) == at(19, 9, 10)
    utc_time = at(19, 9, 7).astimezone(ZoneInfo("UTC"))  # 02:07 UTC
    assert round_up(utc_time, 5, SAIGON) == at(19, 9, 10)
