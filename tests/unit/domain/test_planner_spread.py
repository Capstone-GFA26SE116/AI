"""FR-10c: spread work over the days, short blocks, switch jobs (plans/FR-10c.md T1-T9).

Same example as test_planner: 9:00-17:00, no weekends, buffer 10', now = Monday 08:00.
"""

from datetime import date, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from metanoia_ai.domain.free_time import busy_ranges, free_ranges
from metanoia_ai.domain.models import Placement, ScheduleUnit, WarningType
from metanoia_ai.domain.planner import block_sizes, interleave_projects, plan_schedule
from tests.unit.domain.test_planner import HOURS, NOW, K, at, fixed, times

PROJECT_A = UUID(int=100)
PROJECT_B = UUID(int=200)
CODING = UUID(int=10)
DESIGN = UUID(int=20)


def unit(
    task, minutes, deadline=None, project=PROJECT_A, domain=CODING
) -> ScheduleUnit:
    return ScheduleUnit(
        task_id=UUID(int=task),
        subtask_id=None,
        title="Task",
        deadline=deadline or at(23, 17),
        minutes=minutes,
        k_bias_used=K,
        project_id=project,
        domain_id=domain,
    )


def plan(units, busy=(), spread=True, max_block=180, load=None, window_days=7):
    free = free_ranges(HOURS, NOW, window_days, list(busy), round_minutes=5)
    return plan_schedule(
        units,
        free,
        HOURS,
        frozenset(),
        now=NOW,
        window_end=NOW + timedelta(days=window_days),
        round_minutes=5,
        min_chunk_minutes=30,
        spread=spread,
        max_block_minutes=max_block,
        existing_load=load,
    )


def days(result) -> list[int]:
    return [s.start.day for s in result.slots]


# --- T4: long work is cut into even blocks ---


@pytest.mark.parametrize(
    ("minutes", "sizes"),
    [
        (120, [120]),
        (180, [180]),
        (300, [150, 150]),
        (400, [134, 133, 133]),
        (181, [91, 90]),
    ],
)
def test_block_sizes(minutes, sizes):
    assert block_sizes(minutes, 180, 30) == sizes
    assert sum(sizes) == minutes


def test_block_sizes_off():
    assert block_sizes(500, None, 30) == [500]
    assert block_sizes(500, 0, 30) == [500]


def test_blocks_never_shorter_than_the_minimum_chunk():
    assert block_sizes(70, 40, 30) == [35, 35]
    assert block_sizes(80, 30, 30) == [40, 40]  # 3 x 27 would be under 30
    assert all(size >= 30 for size in block_sizes(100, 40, 30))


def test_t4_five_hour_task_becomes_two_blocks():
    result = plan([unit(1, 300)])
    assert [(s.end - s.start) for s in result.slots] == [timedelta(minutes=150)] * 2


# --- T5: blocks of one task in order, numbered as parts ---


def test_t5_blocks_in_order_on_different_days():
    result = plan([unit(1, 300)])
    first, second = result.slots
    assert second.start >= first.end
    assert days(result) == [19, 20]  # Monday has the first block, so Tuesday is lighter
    assert [(s.part_index, s.part_count) for s in result.slots] == [(1, 2), (2, 2)]


# --- T1-T3: spread over the days ---


def test_t1_two_three_hour_tasks_go_to_two_days():
    units = [unit(1, 180, at(21, 17)), unit(2, 180, at(21, 17))]
    assert days(plan(units, spread=False, max_block=None)) == [19, 19]  # before
    assert days(plan(units)) == [19, 20]


def test_t2_approved_work_counts_as_load():
    result = plan([unit(1, 120)], load={date(2026, 10, 19): 300})
    assert days(result) == [20]


def test_t3_deadline_day_is_the_last_choice():
    # Due Tuesday 17:00: Monday first, even though both days are empty.
    assert days(plan([unit(1, 120, at(20, 17))])) == [19]


def test_t3_deadline_day_used_when_nothing_else_fits():
    monday_busy = busy_ranges([fixed(at(19, 9), at(19, 17))], [], [])
    result = plan([unit(1, 120, at(20, 17))], busy=monday_busy)
    assert days(result) == [20]


def test_spread_slot_explains_itself():
    units = [unit(1, 180, at(21, 17)), unit(2, 180, at(21, 17))]
    second = plan(units).slots[1]
    assert second.placement is Placement.SPREAD_OUT


# --- T6-T7: switch jobs within a day ---


def test_t6_same_day_tasks_alternate_projects():
    due = at(19, 17)  # all due Monday: they must share Monday
    units = [
        unit(1, 90, due, PROJECT_A),
        unit(2, 90, due, PROJECT_A),
        unit(3, 90, due, PROJECT_B),
    ]
    order = [s.task_id.int for s in plan(units).slots]
    assert order == [1, 3, 2]


def test_t7_one_project_alternates_kinds_of_work():
    due = at(19, 17)
    units = [
        unit(1, 90, due, PROJECT_A, CODING),
        unit(2, 90, due, PROJECT_A, CODING),
        unit(3, 90, due, PROJECT_A, DESIGN),
    ]
    order = [s.task_id.int for s in plan(units).slots]
    assert order == [1, 3, 2]


def test_t7_only_one_job_left_still_back_to_back():
    due = at(19, 17)
    units = [unit(1, 90, due), unit(2, 90, due)]
    assert len(plan(units).slots) == 2


def test_interleave_keeps_earlier_deadlines_first():
    units = [
        unit(1, 60, at(19, 17), PROJECT_A),
        unit(2, 60, at(20, 17), PROJECT_A),
        unit(3, 60, at(20, 17), PROJECT_B),
    ]
    order = interleave_projects(units, ZoneInfo("Asia/Ho_Chi_Minh"))
    assert [u.task_id.int for u in order] == [1, 3, 2]


# --- T8: tight deadlines still fit; not enough time still warns ---


def test_t8_tight_deadline_still_packs_the_day():
    due = at(19, 17)  # 8 h day, 7 h of work due today
    units = [unit(1, 210, due, PROJECT_A), unit(2, 210, due, PROJECT_B)]
    result = plan(units)
    assert len(result.slots) == 4  # 2 blocks each, all on Monday
    assert set(days(result)) == {19}
    assert result.warnings == ()


def test_t8_not_enough_time_warns_like_before():
    result = plan([unit(1, 600, at(19, 17))])
    assert result.slots == ()
    assert result.warnings[0].type is WarningType.NOT_ENOUGH_TIME


# --- T9: rules off = the old planner ---


def test_t9_rules_off_give_the_old_result():
    units = [unit(1, 300), unit(2, 120, at(20, 17)), unit(3, 60)]
    old = plan(units, spread=False, max_block=None)
    assert times(old) == times(
        plan_schedule(
            units,
            free_ranges(HOURS, NOW, 7, [], round_minutes=5),
            HOURS,
            frozenset(),
            now=NOW,
            window_end=NOW + timedelta(days=7),
            round_minutes=5,
            min_chunk_minutes=30,
        )
    )
