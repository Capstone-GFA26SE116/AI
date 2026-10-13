from datetime import datetime, time
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from metanoia_ai.domain.free_time import FixedTaskTime, busy_ranges, free_ranges
from metanoia_ai.domain.models import ScheduleUnit, WorkingHours
from metanoia_ai.domain.planner import (
    order_units,
    plan_schedule,
    top_productive_hours,
)
from metanoia_ai.domain.schedule_duration import minutes_to_schedule

SAIGON = ZoneInfo("Asia/Ho_Chi_Minh")
# Common example from the plan: 9:00-17:00, no weekends, buffer 10', now = Monday 08:00.
HOURS = WorkingHours(SAIGON, time(9), time(17), False, 10)
K = Decimal("1.3")


def at(day: int, hour: int, minute: int = 0) -> datetime:
    """October 2026 in Saigon time. 19 = Monday, 20 = Tuesday, 21 = Wednesday."""
    return datetime(2026, 10, day, hour, minute, tzinfo=SAIGON)


NOW = at(19, 8)
TASK_A = UUID(int=1)
TASK_B = UUID(int=2)


def unit(task_id=TASK_A, minutes=60, deadline=None, k=K) -> ScheduleUnit:
    return ScheduleUnit(
        task_id=task_id,
        subtask_id=None,
        title="Task",
        deadline=deadline or at(23, 17),
        minutes=minutes,
        k_bias_used=k,
    )


def plan(units, busy=(), productive=frozenset(), window_days=7):
    free = free_ranges(HOURS, NOW, window_days, list(busy), round_minutes=5)
    return plan_schedule(units, free, HOURS, productive, round_minutes=5)


def times(result) -> list[tuple[datetime, datetime]]:
    return [(s.start, s.end) for s in result.slots]


def test_t1_one_task_paid_plan():
    minutes = minutes_to_schedule(60, [], K, NOW)
    result = plan([unit(minutes=minutes)])
    assert times(result) == [(at(19, 9), at(19, 10, 18))]
    slot = result.slots[0]
    assert slot.proposed_minutes == 78
    assert slot.k_bias_used == K


def test_t7_nearest_deadline_first():
    a = unit(TASK_A, deadline=at(21, 17))  # Wednesday
    b = unit(TASK_B, deadline=at(20, 17))  # Tuesday
    result = plan([a, b])
    assert [s.task_id for s in result.slots] == [TASK_B, TASK_A]


def test_t8_same_deadline_longer_first():
    short = unit(TASK_A, minutes=60)
    long = unit(TASK_B, minutes=120)
    result = plan([short, long])
    assert [s.task_id for s in result.slots] == [TASK_B, TASK_A]


def test_same_deadline_and_length_ordered_by_task_id():
    units = [unit(TASK_B), unit(TASK_A)]
    assert [u.task_id for u in order_units(units)] == [TASK_A, TASK_B]


def test_t9_prefers_productive_hours():
    productive = top_productive_hours(
        {14: Decimal("0.9"), 15: Decimal("0.8"), 16: Decimal("0.7")}, 3
    )
    result = plan([unit()], productive=productive)
    assert times(result) == [(at(19, 14), at(19, 15))]


def test_t10_no_productive_hours_uses_earliest_free_time():
    result = plan([unit()])
    assert times(result) == [(at(19, 9), at(19, 10))]


def test_t20_same_input_same_result():
    units = [unit(TASK_A, minutes=45), unit(TASK_B, minutes=90)]
    productive = frozenset({10, 15})
    assert plan(units, productive=productive) == plan(units, productive=productive)


def test_t37_next_slot_starts_after_buffer_on_a_round_time():
    result = plan([unit(TASK_A, minutes=78), unit(TASK_B, minutes=60)])
    assert times(result)[1][0] == at(19, 10, 30)  # 10:18 + 10' buffer → 10:30


def test_earliest_day_comes_before_productive_hours():
    # Monday has room only in the morning; productive hour 14 is full on Monday.
    busy = busy_ranges([FixedTaskTime(at(19, 12), at(19, 17), False)], [], [])
    result = plan([unit()], busy=busy, productive=frozenset({14}))
    assert times(result) == [(at(19, 9), at(19, 10))]


def test_productive_hour_partly_free_starts_inside_it():
    busy = busy_ranges([FixedTaskTime(at(19, 13), at(19, 14, 20), False)], [], [])
    result = plan([unit()], busy=busy, productive=frozenset({14}))
    assert times(result) == [(at(19, 14, 20), at(19, 15, 20))]


def test_slot_placed_before_a_productive_slot_keeps_its_buffer():
    # A goes to 14:00 first (nearer deadline). B, placed after, must end by 13:50.
    a = unit(TASK_A, minutes=60, deadline=at(20, 17))
    b = unit(TASK_B, minutes=290, deadline=at(21, 17))
    result = plan([a, b], productive=frozenset({14}))
    assert times(result) == [(at(19, 9), at(19, 13, 50)), (at(19, 14), at(19, 15))]


def test_unit_must_end_before_its_deadline():
    result = plan([unit(minutes=60, deadline=at(19, 9, 30))])
    assert result.slots == ()


def test_top_productive_hours_ties_take_the_earlier_hour():
    scores = {
        9: Decimal("0.5"),
        10: Decimal("0.8"),
        11: Decimal("0.5"),
        8: Decimal("0.5"),
    }
    assert top_productive_hours(scores, 3) == frozenset({10, 8, 9})


def test_slots_are_returned_in_time_order():
    a = unit(TASK_A, minutes=60, deadline=at(20, 17))
    b = unit(TASK_B, minutes=60, deadline=at(21, 17))
    result = plan([a, b], productive=frozenset({14}))
    assert [s.task_id for s in result.slots] == [TASK_B, TASK_A]  # B 09:00, A 14:00
