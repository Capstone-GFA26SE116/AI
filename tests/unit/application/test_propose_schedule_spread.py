"""FR-10c in the use case: project, approved load and config reach the planner (T10)."""

import asyncio
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from metanoia_ai.application.reason_writer import ReasonWriter
from metanoia_ai.application.use_cases.propose_schedule import (
    ProposeSchedule,
    work_by_day,
)
from metanoia_ai.domain.models import Language, TimeRange
from tests.unit.application.test_propose_schedule import (
    NOW,
    SAIGON,
    USER,
    World,
    at,
    planning,
    task,
)

PROJECT_A = uuid4()
PROJECT_B = uuid4()


def spread_world() -> World:
    world = World()
    writer = ReasonWriter(
        world.llm, max_retries=2, input_price_per_1m=0.1, output_price_per_1m=0.4
    )
    world.use_case = ProposeSchedule(
        world.repo,
        world.quota,
        writer,
        initial_k=Decimal("1.3"),
        window_days=7,
        min_chunk_minutes=30,
        round_minutes=5,
        top_productive_hours=3,
        clock=lambda: NOW,
        spread=True,
        max_block_minutes=180,
    )
    return world


def run(world, data):
    world.repo.planning[USER] = data
    return asyncio.run(world.use_case.execute(USER, None, Language.VI))


def test_two_big_tasks_go_to_two_days():
    due = at(21, 17)
    data = planning(
        replace(task("Code giỏ hàng", 130, due), project_id=PROJECT_A),
        replace(task("Thiết kế blog", 130, due), project_id=PROJECT_B),
    )
    result = run(spread_world(), data)
    assert sorted(s.start.astimezone(SAIGON).day for s in result.slots) == [19, 20]


def test_long_task_is_cut_into_blocks():
    data = planning(replace(task("Code thanh toán", 240), project_id=PROJECT_A))
    result = run(spread_world(), data)
    # 240 × 1.3 = 312 minutes → 2 blocks of 156
    assert [s.proposed_minutes for s in result.slots] == [156, 156]


def test_approved_work_makes_a_day_heavier():
    data = replace(
        planning(replace(task(minutes=60), project_id=PROJECT_A)),
        approved_slots=(TimeRange(at(19, 9), at(19, 14)),),
    )
    result = run(spread_world(), data)
    assert result.slots[0].start.astimezone(SAIGON).day == 20


def test_rules_off_by_default_in_the_use_case():
    due = at(21, 17)
    data = planning(task("A", 140, due), task("B", 140, due))
    result = World().run(data)
    assert [s.start.astimezone(SAIGON).day for s in result.slots] == [19, 19]


def test_work_by_day_counts_minutes_on_the_local_start_day():
    slots = [
        TimeRange(at(19, 9), at(19, 11)),
        TimeRange(at(19, 13), at(19, 13, 30)),
        TimeRange(at(20, 9), at(20, 10)),
    ]
    assert work_by_day(slots, SAIGON) == {
        date(2026, 10, 19): 150,
        date(2026, 10, 20): 60,
    }
    assert work_by_day([], SAIGON) == {}
    late = TimeRange(at(19, 23, 30), at(19, 23, 30) + timedelta(minutes=60))
    assert work_by_day([late], SAIGON) == {date(2026, 10, 19): 60}
