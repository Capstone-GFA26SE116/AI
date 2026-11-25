"""FR-25 use case with fake repositories (T2–T4)."""

import asyncio
from dataclasses import replace
from datetime import datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from metanoia_ai.application.ports.group_slot_repository import GroupSlotTask
from metanoia_ai.application.ports.schedule_repository import PlanningData
from metanoia_ai.application.use_cases.suggest_group_slots import (
    GroupSlotSettings,
    GroupTaskBlockedError,
    GroupTaskMissingError,
    NotAssignedError,
    SuggestGroupSlots,
)
from metanoia_ai.domain.free_time import CalendarEvent, DailyBreak
from metanoia_ai.domain.group_slot_plan import GroupPart
from metanoia_ai.domain.models import Language, TimeRange, WorkingHours
from tests.unit.application.fakes import (
    FakeGroupSlotRepository,
    FakeQuotaRepository,
    FakeScheduleRepository,
)

VN = ZoneInfo("Asia/Ho_Chi_Minh")
NOW = datetime(2026, 11, 23, 8, tzinfo=VN)  # Monday 08:00
ME, OTHER, TASK = UUID(int=1), UUID(int=2), UUID(int=100)
HOURS = WorkingHours(VN, time(8), time(17), False, 15)
SETTINGS = GroupSlotSettings(
    window_days=7, min_chunk_minutes=30, round_minutes=15, top_productive_hours=0
)


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=VN)


def part(n, assignee=ME, est=60, booked=0, order=None):
    return GroupPart(
        UUID(int=n), f"Báo cáo: Phần {n}", assignee, True, est, booked, order
    )


def task(**changes) -> GroupSlotTask:
    values = {
        "task_id": TASK,
        "title": "Báo cáo",
        "is_member": True,
        "group_archived": False,
        "is_closed": False,
        "has_fixed_time": False,
        "deadline": at(25, 17),
        "parts": (part(3, order=0), part(4, order=1)),
    }
    values.update(changes)
    return GroupSlotTask(**values)


def planning(**changes) -> PlanningData:
    values = {
        "hours": HOURS,
        "tasks": (),
        "k_by_domain": {},
        "productive_scores": {},
        "fixed_tasks": (),
        "calendar_events": (),
        "calendar_synced": True,
        "approved_slots": (),
    }
    values.update(changes)
    return PlanningData(**values)


def setup(group_task=None, plan=None, settings=SETTINGS):
    groups = FakeGroupSlotRepository()
    if group_task is not None:
        groups.tasks[TASK] = group_task
    schedules = FakeScheduleRepository(FakeQuotaRepository(now=NOW))
    schedules.planning[ME] = plan or planning()
    return SuggestGroupSlots(groups, schedules, settings, lambda: NOW), schedules


def ask(use_case, lang=Language.VI):
    return asyncio.run(use_case.execute(ME, TASK, lang))


# --- T2: access ---


@pytest.mark.parametrize("group_task", [None, task(is_member=False)])
def test_missing_or_outsider_is_404(group_task):
    use_case, _ = setup(group_task)
    with pytest.raises(GroupTaskMissingError):
        ask(use_case)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"group_archived": True}, "archived"),
        ({"is_closed": True}, "closed"),
        ({"has_fixed_time": True}, "fixed_time"),
    ],
)
def test_blocked_is_409(changes, reason):
    use_case, _ = setup(task(**changes))
    with pytest.raises(GroupTaskBlockedError) as error:
        ask(use_case)
    assert error.value.reason == reason


def test_nothing_given_to_me_is_403():
    use_case, schedules = setup(task(parts=(part(3, OTHER),)))
    with pytest.raises(NotAssignedError):
        ask(use_case)
    assert schedules.planning_calls == []  # nothing read about my calendar


# --- T3: placing ---


def test_places_my_parts_in_order_around_busy_time():
    plan = planning(
        approved_slots=(TimeRange(at(23, 8), at(23, 9)),),  # a personal or group slot
        calendar_events=(CalendarEvent(at(23, 9), at(23, 10), False, False, False),),
    )
    use_case, schedules = setup(task(), plan)

    result = ask(use_case)

    assert [(s.subtask_id.int, s.start, s.end, s.minutes) for s in result.slots] == [
        (3, at(23, 10), at(23, 11), 60),
        (4, at(23, 11, 15), at(23, 12, 15), 60),
    ]
    assert result.slots[0].title == "Báo cáo: Phần 3"
    assert result.slots[0].reason.startswith("Khoảng trống trong lịch của bạn")
    assert result.summary == "Mình gợi ý 2 khung giờ cho Báo cáo."
    assert result.time_zone == "Asia/Ho_Chi_Minh"
    assert result.warnings == ()
    assert schedules.planning_calls == [(ME, NOW, NOW + timedelta(days=7))]


def test_lunch_break_is_kept_free():
    settings = replace(SETTINGS, breaks=(DailyBreak(time(12), time(13)),))
    plan = planning(approved_slots=(TimeRange(at(23, 8), at(23, 11, 30)),))
    use_case, _ = setup(task(parts=(part(3, est=90),)), plan, settings)

    [slot] = ask(use_case).slots

    assert slot.start == at(23, 13)


def test_no_estimate_and_booked_parts():
    use_case, _ = setup(task(parts=(part(3, est=None), part(4, booked=60))))

    result = ask(use_case)

    assert result.slots == ()
    assert [(w.type, w.subtask_id.int) for w in result.warnings] == [("no_estimate", 3)]
    assert result.summary == "Chưa tìm được giờ trống cho Báo cáo."


# --- T4: deadlines ---


def test_not_enough_time_before_the_deadline():
    use_case, _ = setup(task(deadline=at(23, 9), parts=(part(3, est=120),)))

    result = ask(use_case, Language.EN)

    assert result.slots == ()
    [warning] = result.warnings
    assert warning.type == "not_enough_time"
    assert warning.needed_minutes == 120
    assert result.summary == "No free time found for Báo cáo."


def test_overdue_task():
    use_case, _ = setup(task(deadline=at(22, 17)))
    assert [w.type for w in ask(use_case).warnings] == ["overdue"]


def test_no_deadline_takes_the_earliest_free_time():
    use_case, _ = setup(task(deadline=None, parts=(part(3),)))

    [slot] = ask(use_case).slots

    assert slot.start == at(23, 8)
    assert "sớm nhất" in slot.reason


def test_group_deadline_ends_at_the_end_of_the_working_day():
    from datetime import date

    use_case, _ = setup(
        task(
            deadline=None, group_deadline=date(2026, 11, 23), parts=(part(3, est=600),)
        )
    )

    result = ask(use_case)

    # 08:00–17:00 is 540 minutes: 600 does not fit before 23/11 17:00.
    assert [(w.type, w.needed_minutes) for w in result.warnings] == [
        ("not_enough_time", 600)
    ]
