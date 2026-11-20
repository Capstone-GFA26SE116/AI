"""FR-10d in the use case: learned capacity → day_overloaded warnings (plans/FR-10d.md T6)."""

from dataclasses import replace
from datetime import date

from metanoia_ai.domain.models import TimeRange, WarningType
from tests.unit.application.test_propose_schedule import World, at, planning, task


def worked_days(count: int, hours: int) -> tuple[TimeRange, ...]:
    """`count` past days (Oct 12, 13, …) with `hours` of confirmed work each."""
    return tuple(TimeRange(at(12 + i, 9), at(12 + i, 9 + hours)) for i in range(count))


def test_heavy_day_gets_a_warning_and_stays_feasible():
    # Capacity 2 h (5 days of 2 h); 200 × 1.3 = 260 minutes on Monday.
    data = replace(planning(task(minutes=200)), recent_sessions=worked_days(5, 2))
    result = World().run(data)

    (warning,) = [
        w for w in result.warnings if w.warning.type is WarningType.DAY_OVERLOADED
    ]
    assert warning.warning.day == date(2026, 10, 19)
    assert (warning.warning.needed_minutes, warning.warning.capacity_minutes) == (
        260,
        120,
    )
    assert warning.message.startswith("Ngày 19/10 có khoảng 4 giờ 20 phút việc")
    assert result.is_feasible is True
    assert len(result.slots) == 1
    assert result.explanation.endswith("1 ngày nhiều việc hơn mức bạn thường làm.")


def test_not_enough_timer_data_gives_no_warning():
    data = replace(planning(task(minutes=200)), recent_sessions=worked_days(4, 2))
    result = World().run(data)
    assert not [
        w for w in result.warnings if w.warning.type is WarningType.DAY_OVERLOADED
    ]


def test_day_within_capacity_gives_no_warning():
    data = replace(planning(task(minutes=60)), recent_sessions=worked_days(5, 8))
    result = World().run(data)
    assert result.warnings == ()
