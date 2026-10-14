from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, time
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from metanoia_ai.domain.models import (
    ActualTimeSource,
    FocusSessionRecord,
    PlannedSlot,
    ScheduleUnit,
    ScheduleWarning,
    SkipReason,
    TimeRange,
    WarningType,
    WorkingHours,
)

SAIGON = ZoneInfo("Asia/Ho_Chi_Minh")


def at(hour: int, minute: int = 0) -> datetime:
    """Monday 2026-10-19 in Saigon time."""
    return datetime(2026, 10, 19, hour, minute, tzinfo=SAIGON)


def test_skip_reason_values_are_stable_api_strings():
    # The Backend reads these strings; changing one breaks the contract.
    assert [reason.value for reason in SkipReason] == [
        "task_not_done",
        "already_learned",
        "fixed_task",
        "no_actual_time",
        "session_running",
        "unconfirmed_time",
        "outlier",
    ]


def test_session_is_confirmed_by_default():
    session = FocusSessionRecord(
        started_at=datetime(2026, 10, 13, 13, 0, tzinfo=UTC),
        ended_at=None,
        source=ActualTimeSource.TIMER,
    )
    assert session.is_confirmed is True


def test_session_cannot_be_changed():
    session = FocusSessionRecord(
        started_at=datetime(2026, 10, 13, 13, 0, tzinfo=UTC),
        ended_at=None,
        source=ActualTimeSource.TIMER,
    )
    with pytest.raises(FrozenInstanceError):
        session.ended_at = datetime(2026, 10, 13, 14, 0, tzinfo=UTC)


# --- Schedule proposal (FR-10) ---


def test_warning_type_values_are_stable_api_strings():
    assert [t.value for t in WarningType] == [
        "not_enough_time",
        "overdue",
        "calendar_not_synced",
    ]


def test_time_range_minutes():
    assert TimeRange(at(9), at(10, 18)).minutes == 78


@pytest.mark.parametrize(("start", "end"), [(at(10), at(10)), (at(10), at(9))])
def test_time_range_end_must_be_after_start(start, end):
    with pytest.raises(ValueError):
        TimeRange(start, end)


def test_time_range_needs_time_zone():
    naive_start = datetime(2026, 10, 19, 9)  # noqa: DTZ001 - no time zone on purpose
    naive_end = datetime(2026, 10, 19, 10)  # noqa: DTZ001
    with pytest.raises(ValueError):
        TimeRange(naive_start, naive_end)


def test_working_hours():
    hours = WorkingHours(SAIGON, time(9), time(17), False, 10)
    assert hours.buffer_minutes == 10


@pytest.mark.parametrize(
    ("start", "end", "buffer"),
    [(time(17), time(9), 10), (time(9), time(9), 10), (time(9), time(17), -1)],
)
def test_working_hours_rejects_bad_values(start, end, buffer):
    with pytest.raises(ValueError):
        WorkingHours(SAIGON, start, end, False, buffer)


def make_unit(**changes) -> ScheduleUnit:
    values = {
        "task_id": uuid4(),
        "subtask_id": None,
        "title": "Write report",
        "deadline": at(17),
        "minutes": 78,
        "k_bias_used": Decimal("1.3"),
    }
    return ScheduleUnit(**{**values, **changes})


def test_schedule_unit():
    unit = make_unit()
    assert unit.minutes == 78
    assert unit.order_index is None


@pytest.mark.parametrize(
    "changes",
    [{"minutes": 0}, {"deadline": datetime(2026, 10, 19, 17)}],  # noqa: DTZ001
)
def test_schedule_unit_rejects_bad_values(changes):
    with pytest.raises(ValueError):
        make_unit(**changes)


def test_planned_slot_minutes_exclude_buffer():
    slot = PlannedSlot(uuid4(), None, at(9), at(10, 18), Decimal("1.3"))
    assert slot.proposed_minutes == 78
    assert (slot.part_index, slot.part_count) == (1, 1)


@pytest.mark.parametrize(
    ("start", "end", "part", "count"),
    [(at(10), at(9), 1, 1), (at(9), at(10), 0, 1), (at(9), at(10), 3, 2)],
)
def test_planned_slot_rejects_bad_values(start, end, part, count):
    with pytest.raises(ValueError):
        PlannedSlot(uuid4(), None, start, end, None, part, count)


def test_task_warning_needs_task_id():
    with pytest.raises(ValueError):
        ScheduleWarning(WarningType.NOT_ENOUGH_TIME, needed_minutes=600)


def test_calendar_warning_has_no_task():
    warning = ScheduleWarning(WarningType.CALENDAR_NOT_SYNCED)
    assert warning.task_id is None


@pytest.mark.parametrize(
    ("obj", "field", "value"),
    [
        (TimeRange(at(9), at(10)), "end", at(11)),
        (WorkingHours(SAIGON, time(9), time(17), False, 10), "buffer_minutes", 0),
        (make_unit(), "minutes", 1),
        (PlannedSlot(uuid4(), None, at(9), at(10), None), "start", at(8)),
        (ScheduleWarning(WarningType.OVERDUE, uuid4()), "task_id", None),
    ],
)
def test_planner_types_cannot_be_changed(obj, field, value):
    with pytest.raises(FrozenInstanceError):
        setattr(obj, field, value)
