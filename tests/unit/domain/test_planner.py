from datetime import datetime, time, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from metanoia_ai.domain.free_time import FixedTaskTime, busy_ranges, free_ranges
from metanoia_ai.domain.models import (
    ScheduleUnit,
    ScheduleWarning,
    WarningType,
    WorkingHours,
)
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


def unit(
    task_id=TASK_A, minutes=60, deadline=None, k=K, subtask_id=None, order_index=None
) -> ScheduleUnit:
    return ScheduleUnit(
        task_id=task_id,
        subtask_id=subtask_id,
        title="Task",
        deadline=deadline or at(23, 17),
        minutes=minutes,
        k_bias_used=k,
        order_index=order_index,
    )


def plan(units, busy=(), productive=frozenset(), window_days=7):
    free = free_ranges(HOURS, NOW, window_days, list(busy), round_minutes=5)
    return plan_schedule(
        units,
        free,
        HOURS,
        productive,
        now=NOW,
        window_end=NOW + timedelta(days=window_days),
        round_minutes=5,
        min_chunk_minutes=30,
    )


def fixed(start, end):
    return FixedTaskTime(start, end, is_skipped=False)


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


# --- Step 6: split, subtasks, window, warnings ---

# Monday and Tuesday: only 09:00-12:00 free (180').
MORNINGS_ONLY = busy_ranges(
    [fixed(at(19, 12), at(19, 17)), fixed(at(20, 12), at(20, 17))], [], []
)


def test_t11_task_split_over_days_in_order():
    # Deadline Tuesday 17:00: only the two 180' mornings are before it.
    result = plan([unit(minutes=300, deadline=at(20, 17))], busy=MORNINGS_ONLY)
    assert times(result) == [
        (at(19, 9), at(19, 12)),  # 180'
        (at(20, 9), at(20, 11)),  # 120'
    ]
    assert [(s.part_index, s.part_count) for s in result.slots] == [(1, 2), (2, 2)]
    assert result.warnings == ()


def test_whole_placement_is_preferred_over_split():
    # Fits whole on Wednesday, so it is not split over Monday and Tuesday.
    result = plan([unit(minutes=240, deadline=at(23, 17))], busy=MORNINGS_ONLY)
    assert times(result) == [(at(21, 9), at(21, 13))]


def test_t12_short_rest_makes_the_part_before_it_shorter():
    # Monday has 190' (09:00-12:10), Tuesday 60' (09:00-10:00).
    # Task 200' → 170' + 30', never 190' + 10'.
    busy = busy_ranges(
        [fixed(at(19, 12, 10), at(19, 17)), fixed(at(20, 10), at(20, 17))], [], []
    )
    result = plan([unit(minutes=200, deadline=at(20, 17))], busy=busy)
    assert times(result) == [
        (at(19, 9), at(19, 11, 50)),
        (at(20, 9), at(20, 9, 30)),
    ]


def test_gap_shorter_than_min_chunk_is_not_used():
    # 25' free on Monday morning, then a meeting; task must not get a 25' part.
    busy = busy_ranges(
        [fixed(at(19, 9, 25), at(19, 17)), fixed(at(20, 10), at(20, 17))], [], []
    )
    result = plan([unit(minutes=90, deadline=at(20, 17))], busy=busy)
    assert result.slots == ()
    assert result.warnings[0].type is WarningType.NOT_ENOUGH_TIME


def test_t13_subtasks_follow_order_index():
    second = unit(subtask_id=UUID(int=12), order_index=2, minutes=60)
    first = unit(subtask_id=UUID(int=11), order_index=1, minutes=60)
    result = plan([second, first])
    assert [s.subtask_id for s in result.slots] == [UUID(int=11), UUID(int=12)]


def test_subtask_never_starts_before_the_previous_one_ends():
    # Subtask 1 goes to the productive hour 14:00; subtask 2 must come after it,
    # even though 09:00 is free.
    first = unit(subtask_id=UUID(int=11), order_index=1, minutes=60)
    second = unit(subtask_id=UUID(int=12), order_index=2, minutes=60)
    result = plan([first, second], productive=frozenset({14}))
    assert times(result) == [(at(19, 14), at(19, 15)), (at(19, 15, 10), at(19, 16, 10))]


def test_t14_not_enough_time_before_deadline():
    # Monday deadline 17:00, free 10:20-17:00 = 400'. Needs 600'.
    busy = busy_ranges([fixed(at(19, 9), at(19, 10, 20))], [], [])
    result = plan([unit(minutes=600, deadline=at(19, 17))], busy=busy)
    assert result.slots == ()
    assert result.warnings == (
        ScheduleWarning(
            WarningType.NOT_ENOUGH_TIME, TASK_A, needed_minutes=600, free_minutes=400
        ),
    )


def test_not_enough_time_counts_all_subtasks_left():
    first = unit(subtask_id=UUID(int=11), order_index=1, minutes=300)
    second = unit(subtask_id=UUID(int=12), order_index=2, minutes=300)
    deadline = at(19, 17)
    result = plan([unit_with(first, deadline), unit_with(second, deadline)])
    assert result.slots == ()
    warning = result.warnings[0]
    assert (warning.needed_minutes, warning.free_minutes) == (600, 480)
    assert len(result.warnings) == 1  # one warning per task, not per subtask


def unit_with(base: ScheduleUnit, deadline: datetime) -> ScheduleUnit:
    return ScheduleUnit(
        base.task_id,
        base.subtask_id,
        base.title,
        deadline,
        base.minutes,
        base.k_bias_used,
        base.order_index,
    )


def test_t15_overdue_task_is_not_placed():
    result = plan([unit(deadline=at(18, 17))])  # Sunday, before now
    assert result.slots == ()
    assert result.warnings == (ScheduleWarning(WarningType.OVERDUE, TASK_A),)


def test_t19_deadline_after_window_places_what_fits_without_warning():
    # Window 2 days (Mon 08:00 → Wed 08:00): Monday 480' + Tuesday 240' = 720'.
    busy = busy_ranges([fixed(at(20, 9), at(20, 13))], [], [])
    result = plan([unit(minutes=1200, deadline=at(23, 17))], busy=busy, window_days=2)
    assert sum(s.proposed_minutes for s in result.slots) == 720
    assert result.warnings == ()


def test_other_tasks_are_still_placed_after_a_warning():
    late = unit(TASK_A, minutes=600, deadline=at(19, 17))
    fine = unit(TASK_B, minutes=60, deadline=at(20, 17))
    result = plan([late, fine])
    assert [s.task_id for s in result.slots] == [TASK_B]
    assert [w.task_id for w in result.warnings] == [TASK_A]
